"""FastAPI application factory.

    uvicorn kb_platform.app:create_app --factory   # env: KB_DATABASE_URL, KB_STORAGE_DIR, KB_DENSE, KB_RETRIEVAL_MODE

OpenAPI: ``/docs`` (Swagger UI), ``/redoc``, ``/openapi.json``; a snapshot is kept in ``docs/openapi.json``
(``python -m kb_platform.cli export-openapi``).

Error contract: every error is ``{"error", "message", "request_id"}``. Validation errors list field locations and
messages only — the submitted values are NEVER echoed (a password or question must not come back in an error).
Unhandled exceptions return a generic 500; only the exception class name is logged.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__
from .audit import install_log_filter
from .db import init_db, make_engine, make_sessionmaker
from .deps import ApiError
from .rag_backend import AnswerCache, RagBackend
from .routers import admin, audit_log, auth, chat, documents, knowledge
from .settings import Settings

log = logging.getLogger("kb_platform")
RID = re.compile(r"^[A-Za-z0-9-]{1,32}$")
TAGS = [
    {"name": "auth", "description": "登录、注销、当前身份。身份只来自 Bearer Token"},
    {"name": "admin", "description": "用户、角色、部门管理（平台管理员）"},
    {"name": "knowledge", "description": "知识库、文档元数据、文档 ACL、知识库管理者"},
    {"name": "documents", "description": "文档上传、批量上传、版本、处理状态、Chunk、停用/启用/删除"},
    {"name": "index", "description": "索引代际状态与重建（平台管理员）"},
    {"name": "chat", "description": "会话与带 ACL 的知识库问答"},
    {"name": "audit", "description": "审计日志（平台管理员）"},
]


def _err(status: int, error: str, message: str, request: Request, headers: Optional[dict] = None) -> JSONResponse:
    return JSONResponse(status_code=status, headers=headers,
                        content={"error": error, "message": message,
                                 "request_id": getattr(request.state, "request_id", "")})


def create_app(settings: Optional[Settings] = None, backend: Optional[RagBackend] = None) -> FastAPI:
    settings = settings or Settings()
    install_log_filter()
    engine = make_engine(settings.database_url)
    init_db(engine)
    app = FastAPI(title="企业知识库平台 API", version=__version__, openapi_tags=TAGS,
                  description="带 ACL 的企业知识库后端：认证、身份与权限持久化、知识库/文档元数据、"
                              "ACL 约束的 RAG 问答（候选阶段过滤 + 强制引用安全层）、会话隔离与审计。")
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessionmaker = make_sessionmaker(engine)
    if backend is None:
        from .ingest.manager import IndexManager
        backend = RagBackend(index=IndexManager(settings), mode=settings.retrieval_mode,
                             cache=AnswerCache(settings.answer_cache_size, settings.answer_cache_ttl_s))
    app.state.backend = backend

    @app.middleware("http")
    async def request_id_mw(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        request.state.request_id = rid if RID.match(rid) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"  # answers are per-user: no shared HTTP caching
        return response

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError):
        return _err(exc.status, exc.error, exc.message, request, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        fields = []
        for e in exc.errors():
            loc = ".".join(str(x) for x in e.get("loc", ()) if x != "body")
            kind = "不允许的字段" if e.get("type") == "extra_forbidden" else "格式不正确"
            fields.append(f"{loc}: {kind}")
        return _err(422, "validation_error", "请求参数不合法：" + "; ".join(fields[:10]), request)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404:
            return _err(404, "not_found", "资源不存在或无权访问", request)
        if exc.status_code == 405:
            return _err(405, "method_not_allowed", "不支持的请求方法", request)
        return _err(exc.status_code, "http_error", "请求失败", request)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        log.error("unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
        return _err(500, "internal_error", "服务器内部错误", request)

    @app.get("/api/v1/health", tags=["auth"], summary="健康检查（无需登录，不含任何数据）")
    def health():
        return {"status": "ok", "version": __version__}

    for r in (auth.router, admin.router, knowledge.router, documents.router, chat.router, audit_log.router):
        app.include_router(r)
    return app
