"""Directory administration (platform admins only). Admin rights manage identities and metadata; they do NOT grant
document read access, and ``is_admin`` itself can only be set from the CLI (no API privilege escalation)."""

from __future__ import annotations

from typing import List, Type

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import security
from ..acl_store import bump_acl_version
from ..audit import audit
from ..deps import ApiError, admin_user, get_db, not_found, request_id
from ..models import Department, Role, User
from ..schemas import ErrorOut, NamedIn, NamedOut, UserCreateIn, UserOut, UserUpdateIn

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut},
       422: {"model": ErrorOut}}


def user_out(u: User) -> UserOut:
    return UserOut(id=u.id, username=u.username, display_name=u.display_name, roles=sorted(r.name for r in u.roles),
                   departments=sorted(d.name for d in u.departments), clearance=u.clearance, active=u.active,
                   is_admin=u.is_admin)


def _resolve(db: Session, model: Type, names: List[str], kind: str):
    names = sorted(set(names))
    rows = list(db.scalars(select(model).where(model.name.in_(names)))) if names else []
    missing = sorted(set(names) - {r.name for r in rows})
    if missing:
        raise ApiError(422, f"unknown_{kind}", f"不存在的{'角色' if kind == 'role' else '部门'}：{', '.join(missing)}")
    return rows


def _named(model: Type, kind: str, label: str):
    def create(body: NamedIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
        db.add(model(name=body.name, description=body.description))
        try:
            bump_acl_version(db)
            db.flush()
        except IntegrityError:
            db.rollback()
            raise ApiError(409, "conflict", f"{label}已存在")
        row = db.scalar(select(model).where(model.name == body.name))
        audit(db, action=f"admin.{kind}.create", outcome="success", actor=admin.username, resource=body.name,
              request_id=request_id(request))
        return row

    def list_(admin: User = Depends(admin_user), db: Session = Depends(get_db)):
        return list(db.scalars(select(model).order_by(model.name)))

    router.add_api_route(f"/{kind}s", create, methods=["POST"], response_model=NamedOut, status_code=201,
                         responses=ERR, summary=f"创建{label}", name=f"create_{kind}")
    router.add_api_route(f"/{kind}s", list_, methods=["GET"], response_model=List[NamedOut], responses=ERR,
                         summary=f"{label}列表", name=f"list_{kind}s")


_named(Role, "role", "角色")
_named(Department, "department", "部门")


@router.post("/users", response_model=UserOut, status_code=201, responses=ERR, summary="创建用户")
def create_user(body: UserCreateIn, request: Request, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    problem = security.password_problems(body.password)
    if problem:
        raise ApiError(422, "weak_password", problem)
    u = User(username=body.username, display_name=body.display_name, password_hash=security.hash_password(body.password),
             clearance=body.clearance, roles=_resolve(db, Role, body.roles, "role"),
             departments=_resolve(db, Department, body.departments, "department"))
    db.add(u)
    try:
        bump_acl_version(db)
        db.flush()
    except IntegrityError:
        db.rollback()
        raise ApiError(409, "conflict", "用户名已存在")
    audit(db, action="admin.user.create", outcome="success", actor=admin.username, resource=u.username,
          request_id=request_id(request))
    return user_out(u)


@router.get("/users", response_model=List[UserOut], responses=ERR, summary="用户列表")
def list_users(limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
               admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [user_out(u) for u in db.scalars(select(User).order_by(User.username).limit(limit).offset(offset))]


@router.get("/users/{username}", response_model=UserOut, responses=ERR, summary="查看用户")
def get_user(username: str, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        raise not_found()
    return user_out(u)


@router.patch("/users/{username}", response_model=UserOut, responses=ERR,
              summary="修改用户的角色、部门、密级、启用状态或重置密码（变更后旧 Token 立即失效）")
def update_user(username: str, body: UserUpdateIn, request: Request, admin: User = Depends(admin_user),
                db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        raise not_found()
    changed = sorted(k for k, v in body.model_dump(exclude_unset=True).items() if v is not None)
    if body.display_name is not None:
        u.display_name = body.display_name
    if body.roles is not None:
        u.roles = _resolve(db, Role, body.roles, "role")
    if body.departments is not None:
        u.departments = _resolve(db, Department, body.departments, "department")
    if body.clearance is not None:
        u.clearance = body.clearance
    if body.active is not None:
        u.active = body.active
    if body.password is not None:
        problem = security.password_problems(body.password)
        if problem:
            raise ApiError(422, "weak_password", problem)
        u.password_hash = security.hash_password(body.password)
    if set(changed) & {"roles", "departments", "clearance", "active", "password"}:
        security.revoke_all(db, u.id)  # permissions changed: force a new login
    bump_acl_version(db)
    audit(db, action="admin.user.update", outcome="success", actor=admin.username, resource=u.username,
          request_id=request_id(request), detail={"fields": changed})
    return user_out(u)
