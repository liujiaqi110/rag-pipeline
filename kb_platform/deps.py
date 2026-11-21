"""FastAPI dependencies: DB session, request id, authenticated user (bearer token only), admin guard, errors."""

from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from . import security
from .models import User

bearer = HTTPBearer(auto_error=False, description="POST /api/v1/auth/login 获得的 access_token")


class ApiError(Exception):
    """Errors with a fixed, user-safe message. Never put document names, chunk text or internals in ``message``."""

    def __init__(self, status: int, error: str, message: str, headers: dict | None = None):
        self.status, self.error, self.message, self.headers = status, error, message, headers


NOT_FOUND = ("not_found", "资源不存在或无权访问")  # one message for both: no existence oracle


def not_found() -> ApiError:
    return ApiError(404, *NOT_FOUND)


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.sessionmaker()
    try:
        yield db
    finally:
        db.close()


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "")


def current_user(request: Request, cred: HTTPAuthorizationCredentials | None = Depends(bearer),
                 db: Session = Depends(get_db)) -> User:
    if cred is None or (cred.scheme or "").lower() != "bearer":
        raise ApiError(401, "unauthenticated", "需要登录", {"WWW-Authenticate": "Bearer"})
    res = security.resolve_token(db, cred.credentials, request.app.state.jwt)
    if res is None:
        raise ApiError(401, "unauthenticated", "登录已失效，请重新登录", {"WWW-Authenticate": "Bearer"})
    user, tok = res
    request.state.actor = user.username
    request.state.token_id = tok.id
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise ApiError(403, "forbidden", "需要平台管理员权限")
    return user


def manager_user(user: User = Depends(current_user), db: Session = Depends(get_db)) -> User:
    """Platform admin or manager of at least one knowledge base (per-KB checks follow in the endpoint)."""
    from .acl_store import manages_any
    if not manages_any(db, user):
        raise ApiError(403, "forbidden", "需要知识库管理权限")
    return user
