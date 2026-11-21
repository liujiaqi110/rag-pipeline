"""Knowledge bases, document metadata and document ACL.

Visibility of METADATA follows the same ACL as content: a normal user only sees knowledge bases in which they can
read at least one document, and only the documents they can read. Anything else is a 404 with the same message as a
non-existent id (no existence oracle). Platform admins see all metadata and edit ACLs, but reading CONTENT through
the QA API still requires an ACL grant like everyone else.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..acl_store import acl_version, all_kbs, bump_acl_version, can_manage, kb_by_id, readable_documents
from ..audit import audit
from ..deps import ApiError, admin_user, current_user, get_db, manager_user, not_found, request_id
from ..models import Department, Document, DocumentGrant, KbManager, KnowledgeBase, Role, User
from ..schemas import (DocumentAclIn, DocumentAclOut, DocumentIn, DocumentOut, DocumentUpdateIn, ErrorOut,
                       KnowledgeBaseIn, KnowledgeBaseOut, ManagerIn)

router = APIRouter(prefix="/api/v1", tags=["knowledge"])
ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut},
       422: {"model": ErrorOut}}


def visible_kb(db: Session, user: User, kb_id: str) -> KnowledgeBase:
    kb = kb_by_id(db, kb_id)
    if kb is None:
        raise not_found()
    if not can_manage(db, user, kb) and not readable_documents(db, user, kb)[0]:
        raise not_found()
    return kb


def visible_doc(db: Session, user: User, doc_id: str) -> Document:
    d = db.get(Document, doc_id) if isinstance(doc_id, str) and len(doc_id) <= 64 else None
    if d is None:
        raise not_found()
    if not can_manage(db, user, d.kb) and d not in readable_documents(db, user, d.kb)[0]:
        raise not_found()
    return d


def managed_doc(db: Session, user: User, doc_id: str) -> Document:
    """Document the user may OPERATE on (upload / edit / delete / ACL). Not managed -> 404 (no oracle)."""
    d = db.get(Document, doc_id) if isinstance(doc_id, str) and len(doc_id) <= 64 else None
    if d is None or not can_manage(db, user, d.kb):
        raise not_found()
    return d


def managed_kb(db: Session, user: User, kb_id: str) -> KnowledgeBase:
    kb = kb_by_id(db, kb_id)
    if kb is None or not can_manage(db, user, kb):
        raise not_found()
    return kb


@router.post("/knowledge-bases", response_model=KnowledgeBaseOut, status_code=201, responses=ERR,
             summary="创建知识库（管理员）")
def create_kb(body: KnowledgeBaseIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    kb = KnowledgeBase(name=body.name, description=body.description)
    db.add(kb)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "conflict", "知识库名称已存在")
    audit(db, action="kb.create", outcome="success", actor=admin.username, resource=kb.id, request_id=request_id(request))
    return kb


@router.get("/knowledge-bases", response_model=List[KnowledgeBaseOut], responses=ERR,
            summary="知识库列表（普通用户只看到至少有一篇可读文档的知识库）")
def list_kbs(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [kb for kb in all_kbs(db) if can_manage(db, user, kb) or readable_documents(db, user, kb)[0]]


@router.get("/knowledge-bases/{kb_id}", response_model=KnowledgeBaseOut, responses=ERR, summary="知识库详情")
def get_kb(kb_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return visible_kb(db, user, kb_id)


@router.post("/knowledge-bases/{kb_id}/documents", response_model=DocumentOut, status_code=201, responses=ERR,
             summary="登记外部索引文档的元数据（知识库管理者）。新文档没有任何授权，默认所有人不可见")
def create_doc(kb_id: str, body: DocumentIn, request: Request, admin: User = Depends(manager_user),
               db: Session = Depends(get_db)):
    kb = managed_kb(db, admin, kb_id)
    d = Document(kb_id=kb.id, doc_key=body.doc_key, title=body.title, classification=body.classification,
                 owner_department=body.owner_department)
    db.add(d)
    try:
        bump_acl_version(db)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "conflict", "该文档已登记")
    audit(db, action="document.create", outcome="success", actor=admin.username, resource=d.id,
          request_id=request_id(request), detail={"kb_id": kb.id})
    return d


@router.get("/knowledge-bases/{kb_id}/documents", response_model=List[DocumentOut], responses=ERR,
            summary="文档列表（普通用户只看到自己可读的文档）")
def list_docs(kb_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    kb = visible_kb(db, user, kb_id)
    if can_manage(db, user, kb):
        return sorted(kb.documents, key=lambda d: d.doc_key)
    return sorted(readable_documents(db, user, kb)[0], key=lambda d: d.doc_key)


@router.get("/documents/{doc_id}", response_model=DocumentOut, responses=ERR, summary="文档元数据")
def get_doc(doc_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return visible_doc(db, user, doc_id)


@router.patch("/documents/{doc_id}", response_model=DocumentOut, responses=ERR,
              summary="修改文档元数据 / 停用 / 归档（知识库管理者）。状态变化立即生效并重建索引")
def update_doc(doc_id: str, body: DocumentUpdateIn, request: Request, admin: User = Depends(manager_user),
               db: Session = Depends(get_db)):
    d = managed_doc(db, admin, doc_id)
    if d.status == "deleted":
        raise not_found()
    data = body.model_dump(exclude_unset=True)
    status_changed = "status" in data and data["status"] != d.status
    for k, v in data.items():
        setattr(d, k, v)
    bump_acl_version(db)
    audit(db, action="document.update", outcome="success", actor=admin.username, resource=d.id,
          request_id=request_id(request), detail={"fields": sorted(data)})
    if status_changed and d.source == "upload":
        from .documents import reindex
        reindex(request, db, admin.username)
    return d


def _acl_view(db: Session, d: Document) -> DocumentAclOut:
    allow = {"users": [], "roles": [], "departments": [], "all_authenticated": False}
    deny = {"users": [], "roles": [], "departments": []}
    key = {"user": "users", "role": "roles", "department": "departments"}
    for g in d.grants:
        if g.subject_type == "all_authenticated":
            allow["all_authenticated"] = True
        else:
            (allow if g.effect == "allow" else deny)[key[g.subject_type]].append(g.subject_value)
    for b in (allow, deny):
        for k in ("users", "roles", "departments"):
            b[k].sort()
    return DocumentAclOut(document_id=d.id, allow=allow, deny=deny, acl_version=acl_version(db))


@router.get("/documents/{doc_id}/acl", response_model=DocumentAclOut, responses=ERR, summary="查看文档 ACL（知识库管理者）")
def get_acl(doc_id: str, admin: User = Depends(manager_user), db: Session = Depends(get_db)):
    return _acl_view(db, managed_doc(db, admin, doc_id))


@router.put("/documents/{doc_id}/acl", response_model=DocumentAclOut, responses=ERR,
            summary="整体替换文档 ACL（知识库管理者）。主体必须已存在；拒绝优先于允许")
def put_acl(doc_id: str, body: DocumentAclIn, request: Request, admin: User = Depends(manager_user),
            db: Session = Depends(get_db)):
    d = managed_doc(db, admin, doc_id)
    if d.status == "deleted":
        raise not_found()
    checks = ((User, User.username, body.allow.users + body.deny.users, "用户"),
              (Role, Role.name, body.allow.roles + body.deny.roles, "角色"),
              (Department, Department.name, body.allow.departments + body.deny.departments, "部门"))
    for model, col, names, label in checks:
        names = set(names)
        if names:
            found = set(db.scalars(select(col).where(col.in_(names))))
            if names - found:
                raise ApiError(422, "unknown_subject", f"ACL 中有不存在的{label}：{', '.join(sorted(names - found))}")
    d.grants.clear()
    db.flush()
    rows = []
    for effect, subj in (("allow", body.allow), ("deny", body.deny)):
        for t, vals in (("user", subj.users), ("role", subj.roles), ("department", subj.departments)):
            rows += [DocumentGrant(effect=effect, subject_type=t, subject_value=v) for v in sorted(set(vals))]
    if body.allow.all_authenticated:
        rows.append(DocumentGrant(effect="allow", subject_type="all_authenticated", subject_value=""))
    d.grants.extend(rows)
    bump_acl_version(db)
    audit(db, action="document.acl.replace", outcome="success", actor=admin.username, resource=d.id,
          request_id=request_id(request), detail={"count": len(rows)})
    return _acl_view(db, d)


# --- knowledge-base managers (P3) -----------------------------------------------------------------------------------
@router.get("/knowledge-bases/{kb_id}/managers", response_model=List[str], responses=ERR,
            summary="知识库管理者列表（平台管理员）")
def list_managers(kb_id: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    kb = kb_by_id(db, kb_id)
    if kb is None:
        raise not_found()
    ids = [m.user_id for m in db.scalars(select(KbManager).where(KbManager.kb_id == kb.id))]
    return sorted(db.scalars(select(User.username).where(User.id.in_(ids)))) if ids else []


@router.post("/knowledge-bases/{kb_id}/managers", status_code=204, responses=ERR,
             summary="指定知识库管理者（平台管理员）。管理权限不包含阅读权限")
def add_manager(kb_id: str, body: ManagerIn, request: Request, admin: User = Depends(admin_user),
                db: Session = Depends(get_db)):
    kb = kb_by_id(db, kb_id)
    u = db.scalar(select(User).where(User.username == body.username))
    if kb is None or u is None:
        raise not_found()
    if db.get(KbManager, (kb.id, u.id)) is None:
        db.add(KbManager(kb_id=kb.id, user_id=u.id))
    audit(db, action="kb.manager.add", outcome="success", actor=admin.username, resource=kb.id,
          request_id=request_id(request), detail={"target_user": u.username})


@router.delete("/knowledge-bases/{kb_id}/managers/{username}", status_code=204, responses=ERR,
               summary="移除知识库管理者（平台管理员）")
def remove_manager(kb_id: str, username: str, request: Request, admin: User = Depends(admin_user),
                   db: Session = Depends(get_db)):
    kb = kb_by_id(db, kb_id)
    u = db.scalar(select(User).where(User.username == username))
    m = db.get(KbManager, (kb.id, u.id)) if kb and u else None
    if m is None:
        raise not_found()
    db.delete(m)
    audit(db, action="kb.manager.remove", outcome="success", actor=admin.username, resource=kb.id,
          request_id=request_id(request), detail={"target_user": u.username})
