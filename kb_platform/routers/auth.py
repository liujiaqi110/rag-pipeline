from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import security
from ..audit import audit
from ..deps import ApiError, current_user, get_db, request_id
from ..models import AuthToken, User
from ..schemas import NAME, ChangePasswordIn, ErrorOut, LoginIn, MeOut, TokenOut

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
ERR = {401: {"model": ErrorOut}, 422: {"model": ErrorOut}}


def me_out(u: User) -> MeOut:
    return MeOut(username=u.username, display_name=u.display_name, roles=sorted(r.name for r in u.roles),
                 departments=sorted(d.name for d in u.departments), clearance=u.clearance, is_admin=u.is_admin)


@router.post("/login", response_model=TokenOut, responses=ERR, summary="用户名密码登录，返回 Bearer Token")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    st = request.app.state.settings
    user = security.authenticate(db, body.username, body.password, st.max_failed_logins, st.lockout_minutes)
    if user is None:
        # only an EXISTING username is recorded: a password typed into the username box must never reach the log
        exists = re.fullmatch(NAME, body.username) and db.scalar(select(User.id).where(User.username == body.username))
        actor = body.username if exists else "<unknown>"
        audit(db, action="auth.login", outcome="failure", actor=actor, request_id=request_id(request))
        raise ApiError(401, "invalid_credentials", "用户名或密码错误")  # same for unknown / wrong / inactive / locked
    raw, exp = security.issue_token(db, user, st.token_ttl_minutes)
    audit(db, action="auth.login", outcome="success", actor=user.username, request_id=request_id(request))
    return TokenOut(access_token=raw, expires_at=exp)


@router.post("/logout", status_code=204, responses=ERR, summary="注销当前 Token")
def logout(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    tok = db.get(AuthToken, request.state.token_id)
    if tok is not None:
        tok.revoked = True
    audit(db, action="auth.logout", outcome="success", actor=user.username, request_id=request_id(request))


@router.get("/me", response_model=MeOut, responses=ERR, summary="当前用户身份（只来自 Token）")
def me(user: User = Depends(current_user)):
    return me_out(user)


@router.post("/password", status_code=204, responses=ERR, summary="修改自己的密码（所有 Token 失效）")
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    if not security.verify_password(body.current_password, user.password_hash):
        audit(db, action="auth.password", outcome="failure", actor=user.username, request_id=request_id(request))
        raise ApiError(401, "invalid_credentials", "当前密码错误")
    problem = security.password_problems(body.new_password)
    if problem:
        raise ApiError(422, "weak_password", problem)
    user.password_hash = security.hash_password(body.new_password)
    security.revoke_all(db, user.id)
    audit(db, action="auth.password", outcome="success", actor=user.username, request_id=request_id(request))
