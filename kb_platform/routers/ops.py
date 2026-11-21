"""P5 knowledge operations API: metrics dashboard, page views, FAQ mining / review / management, knowledge gaps and
supplement tasks.

Permissions
* page views: any logged-in user records their own views (route name only).
* metrics: platform admins see everything; KB managers see the KBs they manage (the numbers and the top-question text
  are restricted to those KBs). Everyone else: 403.
* FAQ: listed / shown / edited / reviewed only by users who can MANAGE the KB AND READ every evidence document of
  that entry (``faq.can_review``). Entries the caller may not review are not returned at all — only their count.
  Member user ids are never returned.
* gaps / tasks: platform admins or managers of the gap's KB. Occurrence user ids are never returned.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..acl_store import can_manage, kb_by_id
from ..audit import audit
from ..deps import ApiError, current_user, get_db, manager_user, not_found, request_id
from ..models import Document, DocumentVersion, KbManager, KnowledgeBase, User, utcnow
from ..models_ops import FaqEntry, FaqEvent, FaqMember, GapOccurrence, KnowledgeGap, KnowledgeTask, PageView
from ..ops import faq as faq_ops
from ..ops import metrics as metrics_ops
from ..schemas import ErrorOut, JobOut, Req

router = APIRouter(prefix="/api/v1", tags=["operations"])
ERR = {401: {"model": ErrorOut}, 403: {"model": ErrorOut}, 404: {"model": ErrorOut}, 409: {"model": ErrorOut},
       422: {"model": ErrorOut}}
ROUTE = r"^[a-z0-9-]{1,40}$"


def managed_kb_ids(db: Session, user: User) -> Optional[List[str]]:
    """None = all (platform admin)."""
    if user.is_admin:
        return None
    return list(db.scalars(select(KbManager.kb_id).where(KbManager.user_id == user.id)))


# --- page views & metrics ------------------------------------------------------------------------------------------
class PageViewIn(Req):
    route: str = Field(pattern=ROUTE)


@router.post("/ops/pageview", status_code=204, responses=ERR, summary="记录一次页面访问（只记路由名）")
def pageview(body: PageViewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    db.add(PageView(user_id=user.id, route=body.route))
    db.commit()


@router.get("/ops/metrics", responses=ERR, summary="运营指标（平台管理员看全部；知识库管理者看自己管理的知识库）")
def metrics(days: int = Query(14, ge=1, le=90), kb_id: Optional[str] = Query(None, max_length=64),
            user: User = Depends(manager_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    scope = managed_kb_ids(db, user)
    if kb_id:
        if scope is not None and kb_id not in scope:
            raise not_found()
        scope = [kb_id]
    out = metrics_ops.compute(db, days=days, kb_ids=scope)
    if scope is not None:   # PV/UV are platform-wide numbers: only platform admins see them
        out["traffic"] = {"pv": None, "uv": None, "note": "页面访问数据仅平台管理员可见"} if not user.is_admin else out["traffic"]
    return out


# --- FAQ -----------------------------------------------------------------------------------------------------------
class MineIn(Req):
    kb_id: str = Field(min_length=1, max_length=64)
    min_frequency: Optional[int] = Field(default=None, ge=2, le=1000)
    similarity_threshold: Optional[float] = Field(default=None, ge=0.5, le=0.99)
    days: int = Field(default=30, ge=1, le=365)


class FaqEditIn(Req):
    question: Optional[str] = Field(default=None, min_length=2, max_length=500)
    answer: Optional[str] = Field(default=None, max_length=8000)


class ReviewIn(Req):
    note: str = Field(default="", max_length=500)


def faq_view(db: Session, f: FaqEntry, detail: bool = False) -> Dict[str, Any]:
    ok, reason = faq_ops.evidence_state(db, f)
    out = {"id": f.id, "kb_id": f.kb_id, "status": f.status, "question": f.question, "answer": f.answer,
           "citations": f.citations, "evidence": f.evidence, "evidence_valid": ok, "evidence_reason": reason,
           "frequency": f.frequency, "distinct_users": f.distinct_users, "similarity_mean": f.similarity_mean,
           "confidence": f.confidence, "answer_source": f.answer_source, "embedding_model": f.embedding_model,
           "llm_model": f.llm_model, "llm_digest": f.llm_digest, "index_generation": f.index_generation,
           "params": f.params, "revision": f.revision, "hit_count": f.hit_count, "created_at": f.created_at,
           "updated_at": f.updated_at, "reviewed_by": f.reviewed_by, "reviewed_at": f.reviewed_at,
           "review_note": f.review_note, "published_at": f.published_at}
    if detail:
        members = db.scalars(select(FaqMember).where(FaqMember.faq_id == f.id).order_by(FaqMember.similarity.desc()))
        out["members"] = [{"question": m.question, "similarity": round(m.similarity, 4), "asked_at": m.asked_at}
                          for m in members]                     # no user ids
        out["history"] = [{"ts": e.ts, "actor": e.actor, "action": e.action, "from": e.from_status, "to": e.to_status,
                           "note": e.note} for e in db.scalars(select(FaqEvent).where(FaqEvent.faq_id == f.id)
                                                                .order_by(FaqEvent.id))]
    return out


def reviewable(db: Session, user: User, faq_id: str) -> FaqEntry:
    f = db.get(FaqEntry, faq_id) if isinstance(faq_id, str) and len(faq_id) <= 64 else None
    if f is None or not faq_ops.can_review(db, user, f):
        raise not_found()               # same answer as "does not exist": no oracle about entries you cannot review
    return f


@router.post("/faq/mine", response_model=List[JobOut], responses=ERR, tags=["faq"],
             summary="挖掘候选 FAQ（后台任务）：语义聚类高频问题，只生成候选，不会自动发布")
def faq_mine(body: MineIn, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    kb = kb_by_id(db, body.kb_id)
    if kb is None or not can_manage(db, user, kb):
        raise not_found()
    if request.app.state.backend.external:
        raise ApiError(409, "external_index_mode", "外部索引模式不支持 FAQ 挖掘")
    params = body.model_dump(exclude={"kb_id"}, exclude_none=True)
    j = request.app.state.jobs.enqueue(db, "faq_mine", user.username, kb_id=kb.id, params=params)
    audit(db, action="faq.mine", outcome=j.status, actor=user.username, resource=kb.id, request_id=request_id(request))
    return [j]


@router.get("/faq", responses=ERR, tags=["faq"], summary="FAQ 列表（只返回你可以审核的条目；其余只给数量）")
def faq_list(kb_id: Optional[str] = Query(None, max_length=64),
             status: Optional[Literal["candidate", "published", "rejected", "retired"]] = None,
             user: User = Depends(manager_user), db: Session = Depends(get_db)) -> Dict[str, Any]:
    q = select(FaqEntry).order_by(FaqEntry.frequency.desc(), FaqEntry.created_at.desc())
    if kb_id:
        q = q.where(FaqEntry.kb_id == kb_id)
    if status:
        q = q.where(FaqEntry.status == status)
    items, hidden = [], 0
    for f in db.scalars(q):
        if faq_ops.can_review(db, user, f):
            items.append(faq_view(db, f))
        else:
            kb = kb_by_id(db, f.kb_id)
            hidden += 1 if (kb is not None and can_manage(db, user, kb)) else 0
    return {"items": items, "hidden_without_evidence_access": hidden}


@router.get("/faq/{faq_id}", responses=ERR, tags=["faq"], summary="FAQ 详情（成员问题、证据、审核历史）")
def faq_get(faq_id: str, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return faq_view(db, reviewable(db, user, faq_id), detail=True)


@router.patch("/faq/{faq_id}", responses=ERR, tags=["faq"], summary="编辑候选或已下线 FAQ 的问题与标准答案")
def faq_edit(faq_id: str, body: FaqEditIn, request: Request, user: User = Depends(manager_user),
             db: Session = Depends(get_db)):
    f = reviewable(db, user, faq_id)
    try:
        faq_ops.edit(db, f, user.username, body.question, body.answer)
    except faq_ops.FaqError as e:
        raise ApiError(e.status, e.code, e.message)
    audit(db, action="faq.edit", outcome="success", actor=user.username, resource=f.id, request_id=request_id(request))
    return faq_view(db, f, detail=True)


def _transition(request, db, user, faq_id, action, note):
    f = reviewable(db, user, faq_id)
    try:
        faq_ops.transition(db, f, action, user.username, note)
    except faq_ops.FaqError as e:
        raise ApiError(e.status, e.code, e.message)
    request.app.state.backend.cache.clear()
    audit(db, action=f"faq.{action}", outcome=f.status, actor=user.username, resource=f.id, request_id=request_id(request))
    return faq_view(db, f, detail=True)


@router.post("/faq/{faq_id}/publish", responses=ERR, tags=["faq"], summary="审核通过并发布（证据必须仍然有效）")
def faq_publish(faq_id: str, body: ReviewIn, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return _transition(request, db, user, faq_id, "publish", body.note)


@router.post("/faq/{faq_id}/reject", responses=ERR, tags=["faq"], summary="驳回候选 FAQ")
def faq_reject(faq_id: str, body: ReviewIn, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return _transition(request, db, user, faq_id, "reject", body.note)


@router.post("/faq/{faq_id}/retire", responses=ERR, tags=["faq"], summary="下线已发布 FAQ（立即停止命中）")
def faq_retire(faq_id: str, body: ReviewIn, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return _transition(request, db, user, faq_id, "retire", body.note)


# --- knowledge gaps & tasks ----------------------------------------------------------------------------------------
GapType = Literal["no_evidence", "low_relevance", "insufficient_evidence", "stale_knowledge"]


def gap_view(db: Session, g: KnowledgeGap, repeat: int, detail: bool = False) -> Dict[str, Any]:
    out = {"id": g.id, "kb_id": g.kb_id, "gap_type": g.gap_type, "question": g.question, "occurrences": g.occurrences,
           "distinct_users": g.distinct_users, "repeated": g.occurrences >= repeat, "status": g.status,
           "first_seen": g.first_seen, "last_seen": g.last_seen, "resolved_at": g.resolved_at,
           "verification": g.verification, "note": g.note,
           "tasks": [{"id": t.id, "title": t.title, "status": t.status} for t in
                     db.scalars(select(KnowledgeTask).where(KnowledgeTask.gap_id == g.id))]}
    if detail:
        out["occurrence_list"] = [{"question": o.question, "gap_type": o.gap_type, "ts": o.ts} for o in
                                  db.scalars(select(GapOccurrence).where(GapOccurrence.gap_id == g.id)
                                             .order_by(GapOccurrence.ts.desc()).limit(100))]
    return out


def managed_gap(db: Session, user: User, gap_id: str) -> KnowledgeGap:
    g = db.get(KnowledgeGap, gap_id) if isinstance(gap_id, str) and len(gap_id) <= 64 else None
    kb = kb_by_id(db, g.kb_id) if g else None
    if g is None or kb is None or not can_manage(db, user, kb):
        raise not_found()
    return g


@router.get("/gaps", responses=ERR, tags=["gaps"], summary="知识缺口列表（按知识库、类型、状态、时间筛选）")
def gaps_list(request: Request, kb_id: Optional[str] = Query(None, max_length=64), gap_type: Optional[GapType] = None,
              status: Optional[Literal["open", "in_progress", "resolved", "dismissed"]] = None,
              since: Optional[datetime] = None, until: Optional[datetime] = None, repeated: Optional[bool] = None,
              user: User = Depends(manager_user), db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    scope = managed_kb_ids(db, user)
    q = select(KnowledgeGap).order_by(KnowledgeGap.last_seen.desc())
    if scope is not None:
        q = q.where(KnowledgeGap.kb_id.in_(scope))
    if kb_id:
        q = q.where(KnowledgeGap.kb_id == kb_id)
    if gap_type:
        q = q.where(KnowledgeGap.gap_type == gap_type)
    if status:
        q = q.where(KnowledgeGap.status == status)
    if since:
        q = q.where(KnowledgeGap.last_seen >= since.replace(tzinfo=None))
    if until:
        q = q.where(KnowledgeGap.first_seen <= until.replace(tzinfo=None))
    rep = request.app.state.settings.gap_repeat_threshold
    out = [gap_view(db, g, rep) for g in db.scalars(q)]
    return [g for g in out if repeated is None or g["repeated"] == repeated]


@router.get("/gaps/{gap_id}", responses=ERR, tags=["gaps"], summary="知识缺口详情（出现记录不含用户身份）")
def gap_get(gap_id: str, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    return gap_view(db, managed_gap(db, user, gap_id), request.app.state.settings.gap_repeat_threshold, detail=True)


class GapUpdateIn(Req):
    status: Literal["open", "in_progress", "dismissed"]
    note: str = Field(default="", max_length=500)


@router.patch("/gaps/{gap_id}", responses=ERR, tags=["gaps"], summary="处理缺口：标记处理中 / 忽略 / 重新打开")
def gap_update(gap_id: str, body: GapUpdateIn, request: Request, user: User = Depends(manager_user),
               db: Session = Depends(get_db)):
    g = managed_gap(db, user, gap_id)
    g.status, g.note = body.status, body.note or g.note
    audit(db, action="gap.update", outcome=body.status, actor=user.username, resource=g.id, request_id=request_id(request))
    return gap_view(db, g, request.app.state.settings.gap_repeat_threshold)


class TaskIn(Req):
    title: str = Field(min_length=2, max_length=255)
    description: str = Field(default="", max_length=4000)
    assignee: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")


@router.post("/gaps/{gap_id}/tasks", status_code=201, responses=ERR, tags=["gaps"], summary="把缺口转为知识补充任务")
def gap_task(gap_id: str, body: TaskIn, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    g = managed_gap(db, user, gap_id)
    if body.assignee and db.scalar(select(User).where(User.username == body.assignee)) is None:
        raise ApiError(422, "unknown_user", "指派的用户不存在")
    t = KnowledgeTask(gap_id=g.id, kb_id=g.kb_id, title=body.title, description=body.description,
                      assignee=body.assignee, created_by=user.username)
    db.add(t)
    if g.status == "open":
        g.status = "in_progress"
    db.flush()
    audit(db, action="task.create", outcome="todo", actor=user.username, resource=t.id, request_id=request_id(request))
    return task_view(db, t)


@router.post("/gaps/{gap_id}/verify", response_model=List[JobOut], responses=ERR, tags=["gaps"],
             summary="验证缺口是否已解决（后台任务：以提问用户当前权限重新提问）")
def gap_verify(gap_id: str, request: Request, user: User = Depends(manager_user), db: Session = Depends(get_db)):
    g = managed_gap(db, user, gap_id)
    j = request.app.state.jobs.enqueue(db, "gap_verify", user.username, kb_id=g.kb_id, params={"gap_id": g.id})
    audit(db, action="gap.verify", outcome=j.status, actor=user.username, resource=g.id, request_id=request_id(request))
    return [j]


def task_view(db: Session, t: KnowledgeTask) -> Dict[str, Any]:
    g = db.get(KnowledgeGap, t.gap_id)
    d = db.get(Document, t.document_id) if t.document_id else None
    return {"id": t.id, "gap_id": t.gap_id, "kb_id": t.kb_id, "title": t.title, "description": t.description,
            "assignee": t.assignee, "status": t.status, "document_id": t.document_id,
            "document_title": (d.title or d.doc_key) if d else None, "created_by": t.created_by,
            "created_at": t.created_at, "updated_at": t.updated_at, "verified_at": t.verified_at,
            "verification": t.verification, "gap_question": g.question if g else None,
            "gap_status": g.status if g else None}


@router.get("/tasks", responses=ERR, tags=["gaps"], summary="知识补充任务列表")
def tasks_list(kb_id: Optional[str] = Query(None, max_length=64), status: Optional[str] = Query(None, max_length=16),
               user: User = Depends(manager_user), db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    scope = managed_kb_ids(db, user)
    q = select(KnowledgeTask).order_by(KnowledgeTask.created_at.desc())
    if scope is not None:
        q = q.where(KnowledgeTask.kb_id.in_(scope))
    if kb_id:
        q = q.where(KnowledgeTask.kb_id == kb_id)
    if status:
        q = q.where(KnowledgeTask.status == status)
    return [task_view(db, t) for t in db.scalars(q)]


class TaskUpdateIn(Req):
    status: Optional[Literal["todo", "in_progress", "done"]] = None
    assignee: Optional[str] = Field(default=None, pattern=r"^[A-Za-z0-9_.-]{1,64}$")
    document_id: Optional[str] = Field(default=None, max_length=64)


@router.patch("/tasks/{task_id}", responses=ERR, tags=["gaps"],
              summary="更新任务；标记完成时自动排队验证关联缺口")
def task_update(task_id: str, body: TaskUpdateIn, request: Request, user: User = Depends(manager_user),
                db: Session = Depends(get_db)):
    t = db.get(KnowledgeTask, task_id) if len(task_id) <= 64 else None
    kb = kb_by_id(db, t.kb_id) if t else None
    if t is None or kb is None or not can_manage(db, user, kb):
        raise not_found()
    if body.document_id is not None:
        d = db.get(Document, body.document_id)
        if d is None or d.kb_id != t.kb_id:
            raise ApiError(422, "unknown_document", "关联文档必须属于同一知识库")
        t.document_id = d.id
    if body.assignee is not None:
        t.assignee = body.assignee
    job = None
    if body.status is not None:
        t.status = body.status
    db.commit()
    if body.status == "done":
        job = request.app.state.jobs.enqueue(db, "gap_verify", user.username, kb_id=t.kb_id, params={"gap_id": t.gap_id})
    audit(db, action="task.update", outcome=t.status, actor=user.username, resource=t.id, request_id=request_id(request))
    db.expire_all()
    out = task_view(db, db.get(KnowledgeTask, task_id))
    out["verify_job_id"] = job.id if job else None
    return out
