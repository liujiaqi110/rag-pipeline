from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import admin_user, get_db
from ..models import AuditLog, User
from ..schemas import AuditOut, ErrorOut

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


@router.get("", response_model=List[AuditOut], responses={401: {"model": ErrorOut}, 403: {"model": ErrorOut}},
            summary="审计日志（管理员）。只含哈希、计数与结果，不含 Token、密码、问题或回答原文")
def list_audit(action: Optional[str] = Query(None, max_length=64), actor: Optional[str] = Query(None, max_length=64),
               limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
               admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    q = select(AuditLog).order_by(AuditLog.id.desc())
    if action:
        q = q.where(AuditLog.action == action)
    if actor:
        q = q.where(AuditLog.actor == actor)
    return list(db.scalars(q.limit(limit).offset(offset)))
