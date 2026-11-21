"""P3 document files: upload (single / batch), new versions, processing status, chunks, enable / disable / delete,
index generations (status / rebuild).

Permissions: every write needs OPERATION permission on the document's knowledge base (platform admin or KB manager,
``acl_store.can_manage``); a document that is not manageable is a 404 (no existence oracle). Operating on a document
never grants reading it — chunk TEXT is only returned to users with an ACL read grant, and a freshly uploaded
document has no grant at all (default deny). Index builds never touch ACL: what a user can retrieve is still decided
per request by the phase-1 engine from the database policy.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..acl_store import bump_acl_version, can_manage, readable_documents
from ..audit import audit
from ..deps import ApiError, admin_user, current_user, get_db, manager_user, not_found, request_id
from ..ingest.errors import IngestError
from ..ingest.manager import INDEXABLE, Ingestor, safe_filename
from ..models import Chunk, Document, DocumentVersion, IndexGeneration, User
from ..schemas import (NAME, BatchItem, BatchOut, ChunkListOut, ChunkOut, Classification, DocumentOut,
                       DocumentStatusOut, ErrorOut, GenerationOut, IndexInfo, IndexStatusOut, UploadOut, VersionOut)
from .knowledge import managed_doc, managed_kb, visible_doc

router = APIRouter(prefix="/api/v1", tags=["documents"])
ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut},
       413: {"model": ErrorOut}, 415: {"model": ErrorOut}, 422: {"model": ErrorOut}, 503: {"model": ErrorOut}}
OnDuplicate = Literal["reject", "new_version", "skip"]


def _platform(request: Request):
    backend = request.app.state.backend
    if backend.external:
        raise ApiError(409, "external_index_mode", "当前服务使用外部离线索引（RAG_DATA_DIR），不支持在线上传")
    return backend.index


def reindex(request: Request, db: Session, actor: str) -> IndexInfo:
    """Build + activate a new generation. Failure keeps the old one and is reported, never raised to the client."""
    idx = _platform(request)
    try:
        g = idx.build(db, actor)
        request.app.state.backend.cache.clear()
        audit(db, action="index.build", outcome="success", actor=actor, resource=g.id, request_id=request_id(request),
              detail={"chunks": g.chunk_count, "dense": g.dense, "error_code": g.dense_error})
        return IndexInfo(status="active", generation_id=g.id, dense=g.dense, dense_error=g.dense_error)
    except IngestError as e:
        audit(db, action="index.build", outcome="failure", actor=actor, request_id=request_id(request),
              detail={"error_code": e.code})
        active = idx.active_generation(db)
        return IndexInfo(status="failed", error_code=e.code, message=e.message,
                         generation_id=active.id if active else None, dense=active.dense if active else None)


async def _read(upload: UploadFile, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise IngestError("file_too_large", f"文件超过 {limit // (1024 * 1024)} MB 上限", 413)
    return data


def _ingest_one(request: Request, db: Session, kb, user: User, filename: str, data: bytes, title: str,
                classification: str, owner_department: Optional[str], on_duplicate: str, target: Optional[Document] = None):
    """Returns (outcome, document, version). Raises IngestError for rejections (nothing is written then)."""
    idx = _platform(request)
    ing = Ingestor(request.app.state.settings, idx)
    from ..ingest import parsers
    parsers.detect_format(filename, data)               # validate before anything is persisted
    import hashlib
    sha = hashlib.sha256(data).hexdigest()
    if target is None:
        same = ing.find_same_name(db, kb, filename)
        if same is not None:
            if on_duplicate == "reject":
                raise IngestError("duplicate_name", "同名文档已存在；如需更新请上传新版本（on_duplicate=new_version）", 409)
            if on_duplicate == "skip":
                return "skipped", same, None
            target = same
        else:
            dup = ing.find_same_content(db, kb, sha)
            if dup is not None:
                if on_duplicate == "skip":
                    return "skipped", dup, None
                raise IngestError("duplicate_content", "相同内容的文档已存在", 409)
    if target is not None:
        cur = db.get(DocumentVersion, target.current_version_id) if target.current_version_id else None
        latest = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == target.id)
                            .order_by(DocumentVersion.version_no.desc()).limit(1)).first()
        if (cur and cur.sha256 == sha) or (latest and latest.sha256 == sha and latest.status in ("chunked", "indexed")):
            return "unchanged", target, latest
        v = ing.add_version(db, target, filename, data, user.username)
        ing.process(db, target, v)
        db.commit()
        return "new_version", target, v
    d = ing.new_document(db, kb, filename, title, classification, owner_department)
    v = ing.add_version(db, d, filename, data, user.username)
    ing.process(db, d, v)
    db.commit()
    return "created", d, v


def _ingest_error(e: IngestError) -> ApiError:
    return ApiError(e.status, e.code, e.message)


@router.post("/knowledge-bases/{kb_id}/documents/upload", response_model=UploadOut, status_code=201, responses=ERR,
             summary="上传单个文档（.md/.txt/.pdf/.docx），自动解析、切片、建索引。新文档默认无人可读")
async def upload(kb_id: str, request: Request, file: UploadFile = File(...), title: str = Form("", max_length=255),
                 classification: Classification = Form("internal"),
                 owner_department: Optional[str] = Form(None, pattern=NAME),
                 on_duplicate: OnDuplicate = Query("reject"), build_index: bool = Query(True),
                 user: User = Depends(manager_user), db: Session = Depends(get_db)):
    kb = managed_kb(db, user, kb_id)
    name = safe_filename(file.filename or "")
    try:
        data = await _read(file, request.app.state.settings.max_upload_bytes)
        outcome, d, v = _ingest_one(request, db, kb, user, name, data, title, classification, owner_department, on_duplicate)
    except IngestError as e:
        db.rollback()
        audit(db, action="document.upload", outcome="rejected", actor=user.username, resource=kb.id,
              request_id=request_id(request), detail={"error_code": e.code})
        raise _ingest_error(e)
    info = IndexInfo(status="skipped")
    if build_index and v is not None and v.status == "chunked":
        info = reindex(request, db, user.username)
    db.refresh(d)
    if v is not None:
        db.refresh(v)
    audit(db, action="document.upload", outcome=outcome, actor=user.username, resource=d.id, request_id=request_id(request),
          detail={"kb_id": kb.id, "version_no": v.version_no if v else None, "status": v.status if v else None,
                  "error_code": v.error_code if v else None})
    return UploadOut(outcome=outcome, document=DocumentOut.model_validate(d),
                     version=VersionOut.model_validate(v) if v else None, index=info)


@router.post("/knowledge-bases/{kb_id}/documents/batch-upload", response_model=BatchOut, responses=ERR,
             summary="批量上传；单个文件失败不影响其他文件，全部处理后统一建一次索引")
async def batch_upload(kb_id: str, request: Request, files: List[UploadFile] = File(...),
                       classification: Classification = Form("internal"),
                       on_duplicate: OnDuplicate = Query("reject"), build_index: bool = Query(True),
                       user: User = Depends(manager_user), db: Session = Depends(get_db)):
    kb = managed_kb(db, user, kb_id)
    st = request.app.state.settings
    if len(files) > st.max_batch_files:
        raise ApiError(413, "too_many_files", f"一次最多上传 {st.max_batch_files} 个文件")
    results, any_chunked = [], False
    for f in files:
        name = safe_filename(f.filename or "")
        try:
            data = await _read(f, st.max_upload_bytes)
            outcome, d, v = _ingest_one(request, db, kb, user, name, data, "", classification, None, on_duplicate)
            ok = v is None or v.status != "failed"
            any_chunked |= bool(v is not None and v.status == "chunked")
            results.append(BatchItem(filename=name, ok=ok, outcome=outcome, document_id=d.id, version_id=v.id if v else None,
                                     status=v.status if v else None, error_code=v.error_code if v else None,
                                     message=v.error_message if v else None))
        except IngestError as e:
            db.rollback()
            results.append(BatchItem(filename=name, ok=False, error_code=e.code, message=e.message))
    info = reindex(request, db, user.username) if (build_index and any_chunked) else IndexInfo(status="skipped")
    for r in results:   # report the post-index status
        if r.version_id:
            r.status = db.get(DocumentVersion, r.version_id).status
    ok = sum(1 for r in results if r.ok)
    audit(db, action="document.batch_upload", outcome="success" if ok == len(results) else "partial", actor=user.username,
          resource=kb.id, request_id=request_id(request), detail={"files": str(len(results)), "ok": ok, "failed": len(results) - ok})
    return BatchOut(results=results, succeeded=ok, failed=len(results) - ok, index=info)


@router.post("/documents/{doc_id}/versions", response_model=UploadOut, status_code=201, responses=ERR,
             summary="上传文档新版本；新版本建索引成功后才替换旧版本，失败时旧版本继续服务")
async def new_version(doc_id: str, request: Request, file: UploadFile = File(...), build_index: bool = Query(True),
                      user: User = Depends(manager_user), db: Session = Depends(get_db)):
    d = managed_doc(db, user, doc_id)
    if d.status == "deleted" or d.source != "upload":
        raise not_found()
    name = safe_filename(file.filename or "")
    try:
        data = await _read(file, request.app.state.settings.max_upload_bytes)
        outcome, d, v = _ingest_one(request, db, d.kb, user, name, data, "", d.classification, None, "new_version", target=d)
    except IngestError as e:
        db.rollback()
        raise _ingest_error(e)
    info = IndexInfo(status="skipped")
    if build_index and v is not None and v.status == "chunked":
        info = reindex(request, db, user.username)
    db.refresh(d)
    db.refresh(v)
    audit(db, action="document.version", outcome=outcome, actor=user.username, resource=d.id, request_id=request_id(request),
          detail={"version_no": v.version_no, "status": v.status, "error_code": v.error_code})
    return UploadOut(outcome=outcome, document=DocumentOut.model_validate(d), version=VersionOut.model_validate(v), index=info)


@router.get("/documents/{doc_id}/versions", response_model=List[VersionOut], responses=ERR, summary="版本列表（知识库管理者）")
def list_versions(doc_id: str, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    d = managed_doc(db, user, doc_id)
    return list(db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == d.id)
                           .order_by(DocumentVersion.version_no.desc())))


def _retrievable(request: Request, db: Session, d: Document) -> tuple:
    backend = request.app.state.backend
    if backend.external:
        return d.status == "active", None, None
    gen = backend.index.active_generation(db)
    cur = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
    ok = bool(gen and d.status == "active" and cur and cur.status == "indexed" and cur.generation_id is not None
              and (backend.mode == "bm25" or gen.dense))
    return ok, gen.id if gen else None, gen.dense if gen else None


@router.get("/documents/{doc_id}/status", response_model=DocumentStatusOut, responses=ERR,
            summary="文档处理与索引状态（可读用户或管理者）")
def doc_status(doc_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = visible_doc(db, user, doc_id)
    cur = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
    latest = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == d.id)
                        .order_by(DocumentVersion.version_no.desc()).limit(1)).first()
    ok, gen_id, dense = _retrievable(request, db, d)
    return DocumentStatusOut(document_id=d.id, status=d.status, source=d.source,
                             current_version=VersionOut.model_validate(cur) if cur else None,
                             latest_version=VersionOut.model_validate(latest) if latest else None,
                             retrievable=ok, active_generation_id=gen_id, dense=dense)


@router.get("/documents/{doc_id}/chunks", response_model=ChunkListOut, responses=ERR,
            summary="Chunk 列表（含来源、页码、段落）。正文只返回给有阅读权限的用户")
def list_chunks(doc_id: str, version_id: Optional[str] = Query(None, max_length=64),
                include_parents: bool = Query(False), limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = visible_doc(db, user, doc_id)
    readable = d in readable_documents(db, user, d.kb)[0]
    manage = can_manage(db, user, d.kb)
    vid = d.current_version_id
    if version_id and version_id != vid:
        if not manage:          # non-current versions: managers only (old content may be superseded on purpose)
            raise not_found()
        vid = version_id
    v = db.get(DocumentVersion, vid) if vid else None
    if v is None or v.document_id != d.id:
        raise not_found()
    q = select(Chunk).where(Chunk.version_id == v.id)
    if not include_parents:
        q = q.where(Chunk.is_parent.is_(False))
    total = db.scalar(select(func.count()).select_from(q.subquery()))
    show_text = readable and v.id == d.current_version_id
    rows = db.scalars(q.order_by(Chunk.ordinal).limit(limit).offset(offset))
    return ChunkListOut(document_id=d.id, version_id=v.id, version_no=v.version_no, total=total, text_visible=show_text,
                        chunks=[ChunkOut(chunk_id=c.chunk_id, ordinal=c.ordinal, is_parent=c.is_parent, parent_id=c.parent_id,
                                         section_heading=c.section_heading, page=c.page, paragraph=c.paragraph,
                                         char_count=c.char_count, text=c.text if show_text else None) for c in rows])


def _set_status(request: Request, db: Session, user: User, doc_id: str, status: str) -> DocumentStatusOut:
    d = managed_doc(db, user, doc_id)
    if d.status == "deleted":
        raise not_found()
    if d.status != status:
        d.status = status
        bump_acl_version(db)
        db.commit()
        audit(db, action=f"document.{'enable' if status == 'active' else 'disable'}", outcome="success",
              actor=user.username, resource=d.id, request_id=request_id(request))
        if d.source == "upload":
            reindex(request, db, user.username)
    return doc_status(doc_id, request, user, db)


@router.post("/documents/{doc_id}/disable", response_model=DocumentStatusOut, responses=ERR,
             summary="停用文档：立即不再参与问答，并从下一代索引中移除")
def disable(doc_id: str, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return _set_status(request, db, user, doc_id, "disabled")


@router.post("/documents/{doc_id}/enable", response_model=DocumentStatusOut, responses=ERR, summary="重新启用文档")
def enable(doc_id: str, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return _set_status(request, db, user, doc_id, "active")


@router.delete("/documents/{doc_id}", response_model=IndexInfo, responses=ERR,
               summary="删除文档：立即失效；索引重建成功后清除 Chunk 与文件")
def delete_doc(doc_id: str, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    d = managed_doc(db, user, doc_id)
    if d.status == "deleted":
        raise not_found()
    d.status = "deleted"
    bump_acl_version(db)
    db.commit()
    audit(db, action="document.delete", outcome="success", actor=user.username, resource=d.id, request_id=request_id(request))
    if d.source == "upload":
        return reindex(request, db, user.username)
    return IndexInfo(status="skipped")


# --- index generations ----------------------------------------------------------------------------------------------
@router.get("/index/status", response_model=IndexStatusOut, responses=ERR, tags=["index"], summary="索引状态（平台管理员）")
def index_status(request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    idx = _platform(request)
    gens = list(db.scalars(select(IndexGeneration).order_by(IndexGeneration.created_at.desc()).limit(10)))
    act = idx.active_generation(db)
    pending = db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.status.in_(INDEXABLE)))
    failed = db.scalar(select(func.count()).select_from(DocumentVersion).where(DocumentVersion.status == "failed"))
    st = request.app.state.settings
    return IndexStatusOut(retrieval_mode=request.app.state.backend.mode, dense_mode=st.dense_mode,
                          embedder=idx.embedder_status(), active=GenerationOut.model_validate(act) if act else None,
                          recent=[GenerationOut.model_validate(g) for g in gens], pending_versions=pending or 0,
                          failed_versions=failed or 0)


@router.post("/index/rebuild", response_model=IndexInfo, responses=ERR, tags=["index"],
             summary="从数据库重建索引（平台管理员）：恢复失败的构建、补建 Dense、清除已删除文档")
def index_rebuild(request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return reindex(request, db, admin.username)
