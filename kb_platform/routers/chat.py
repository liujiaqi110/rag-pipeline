"""Chat sessions and ACL-protected question answering.

* A session belongs to exactly one user (taken from the token) and one knowledge base. Another user's session id is a
  404, identical to a non-existent id.
* Every question is answered by the phase-1 ACL service under a policy built from the database for THIS user and THIS
  knowledge base (``acl_store.policy_for``). A broken policy → ``access_denied`` (fail closed) + audit ``policy_error``.
* Each stored answer carries the scope key (principal + policy fingerprint) it was produced under. When reading a
  session later, answers produced under a different scope (permissions changed since) are hidden.
* Only the user-facing part (answer, citations, status) is returned; the phase-1 debug/audit block never leaves the
  server. The audit log stores a question hash and counts, never the question or answer text.
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ragas_eval.acl import PolicyError
from ragas_eval.kb_service import DENIED_TEXT

from fastapi.responses import StreamingResponse

from ..acl_store import kb_by_id, policy_for, readable_documents, scope_key
from ..streaming import StreamSession
from ..audit import audit, question_fingerprint
from ..deps import ApiError, current_user, get_db, not_found, request_id
from ..models import ChatMessage, ChatSession, User
from ..schemas import AskIn, AskOut, ErrorOut, MessageOut, SessionDetailOut, SessionIn, SessionOut

router = APIRouter(prefix="/api/v1/sessions", tags=["chat"])
ERR = {401: {"model": ErrorOut}, 404: {"model": ErrorOut}, 422: {"model": ErrorOut}, 503: {"model": ErrorOut}}
HIDDEN_TEXT = "该回答生成后权限已变更，内容已隐藏。"


def own_session(db: Session, user: User, sid: str) -> ChatSession:
    s = db.get(ChatSession, sid) if isinstance(sid, str) and len(sid) <= 64 else None
    if s is None or s.user_id != user.id:
        raise not_found()
    return s


def _session_out(s: ChatSession) -> SessionOut:
    return SessionOut(id=s.id, kb_id=s.kb_id, title=s.title, created_at=s.created_at)


@router.post("", response_model=SessionOut, status_code=201, responses=ERR, summary="在可访问的知识库中创建会话")
def create_session(body: SessionIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    kb = kb_by_id(db, body.kb_id)
    if kb is None or not readable_documents(db, user, kb)[0]:  # admins too: content access needs a grant
        raise not_found()
    s = ChatSession(user_id=user.id, kb_id=kb.id, title=body.title)
    db.add(s)
    db.flush()
    audit(db, action="chat.session.create", outcome="success", actor=user.username, resource=s.id,
          request_id=request_id(request), detail={"kb_id": kb.id})
    return _session_out(s)


@router.get("", response_model=List[SessionOut], responses=ERR, summary="我的会话列表（只含自己的）")
def list_sessions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.created_at.desc()))
    return [_session_out(s) for s in rows]


@router.get("/{session_id}", response_model=SessionDetailOut, responses=ERR,
            summary="会话详情；权限变更前生成的回答会被隐藏")
def get_session(session_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = own_session(db, user, session_id)
    kb = kb_by_id(db, s.kb_id)
    try:
        current = scope_key(policy_for(db, user, kb), user.username) if kb else ""
    except PolicyError:
        current = ""
    msgs = []
    for m in s.messages:
        visible = bool(current) and m.scope_key == current
        msgs.append(MessageOut(id=m.id, question=m.question, answer=m.answer if visible else HIDDEN_TEXT,
                               citations=m.citations if visible else [], status=m.status, created_at=m.created_at,
                               hidden=not visible))
    return SessionDetailOut(**_session_out(s).model_dump(), messages=msgs)


@router.delete("/{session_id}", status_code=204, responses=ERR, summary="删除自己的会话")
def delete_session(session_id: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = own_session(db, user, session_id)
    db.delete(s)
    audit(db, action="chat.session.delete", outcome="success", actor=user.username, resource=session_id,
          request_id=request_id(request))


@router.post("/{session_id}/ask", response_model=AskOut, responses=ERR, summary="在会话中提问（ACL + 强制安全层）")
def ask(session_id: str, body: AskIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s = own_session(db, user, session_id)
    st = request.app.state.settings
    if len(body.question) > st.max_question_chars:
        raise ApiError(422, "question_too_long", f"问题不能超过 {st.max_question_chars} 个字符")
    prep = _prepare(request, db, user, s, body.question)
    if prep["policy"] is None:
        user_resp = {"answer": DENIED_TEXT, "citations": [], "status": "access_denied"}
        summary = {"status": "access_denied", "reason": "policy_error", "llm_calls": 0, "scope_key": ""}
        cached = False
    else:
        try:
            user_resp, summary, cached = request.app.state.backend.ask(
                prep["policy"], user.username, s.kb_id, body.question, retriever=prep["retriever"],
                generation=prep["generation"])
        except FileNotFoundError:
            audit(db, action="chat.ask", outcome="error", actor=user.username, resource=s.id,
                  request_id=request_id(request), detail={**prep["fp"], "error": "index_unavailable"})
            raise ApiError(503, "index_unavailable", "知识库索引不可用，请联系管理员")
    m = _store(db, user, s, body.question, user_resp, summary, cached, request_id(request), prep["fp"])
    return AskOut(session_id=s.id, message_id=m.id, answer=m.answer, citations=m.citations, status=m.status,
                  notice=user_resp.get("notice"))


def _prepare(request: Request, db: Session, user: User, s: ChatSession, question: str) -> dict:
    """Shared by /ask and /ask/stream: policy for THIS user + KB (None = fail closed), active index generation."""
    st = request.app.state.settings
    if len(question) > st.max_question_chars:
        raise ApiError(422, "question_too_long", f"问题不能超过 {st.max_question_chars} 个字符")
    kb = kb_by_id(db, s.kb_id)
    fp = question_fingerprint(question)
    rid = request_id(request)
    try:
        policy = policy_for(db, user, kb) if kb else None
    except PolicyError:
        policy = None
    out = {"policy": policy, "retriever": None, "generation": "external", "fp": fp,
           "scope_key": scope_key(policy, user.username) if policy else ""}
    backend = request.app.state.backend
    if policy is not None and not backend.external:
        gen = backend.index.active_generation(db)
        if gen is None:
            audit(db, action="chat.ask", outcome="error", actor=user.username, resource=s.id, request_id=rid,
                  detail={**fp, "error": "index_unavailable"})
            raise ApiError(503, "index_unavailable", "知识库索引尚未构建，请联系管理员")
        out["retriever"], out["generation"] = backend.index.retriever(gen), gen.id
        if backend.mode == "rrf" and not out["retriever"].has_dense:
            audit(db, action="chat.ask", outcome="error", actor=user.username, resource=s.id, request_id=rid,
                  detail={**fp, "error": "dense_unavailable", "generation": gen.id})
            raise ApiError(503, "dense_unavailable", "向量检索不可用：当前索引没有 Dense 部分或本地向量模型无法加载。"
                                                     "安装模型后由管理员执行 POST /api/v1/index/rebuild")
    return out


def _store(db: Session, user: User, s: ChatSession, question: str, user_resp: dict, summary: dict, cached: bool,
           rid: str, fp: dict, hidden: bool = False) -> ChatMessage:
    m = ChatMessage(session_id=s.id, question=question, answer=user_resp.get("answer", ""),
                    citations=user_resp.get("citations") or [], status=user_resp.get("status") or "",
                    scope_key="revoked" if hidden else summary.get("scope_key", ""))
    db.add(m)
    db.flush()
    audit(db, action="chat.ask", outcome=("revoked" if hidden else user_resp.get("status")) or "unknown",
          actor=user.username, resource=s.id, request_id=rid,
          detail={**fp, **{k: v for k, v in summary.items() if k != "scope_key"}, "cached": cached,
                  "citations": len(user_resp.get("citations") or []), "kb_id": s.kb_id})
    return m


@router.post("/{session_id}/ask/stream", responses=ERR, summary="流式提问（SSE）：同一 ACL 链路；最终答案以 final 事件为准",
             response_class=StreamingResponse)
def ask_stream(session_id: str, body: AskIn, request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    """``text/event-stream`` events: ``status`` → ``delta``* → ``final`` (validated answer + citations, replaces the
    streamed text) | ``revoked`` (permissions / login changed: stop and discard) | ``error``."""
    s = own_session(db, user, session_id)
    prep = _prepare(request, db, user, s, body.question)
    app, rid, raw_token = request.app, request_id(request), getattr(request.state, "raw_token", "")
    sm, uid, sid, kb_id = app.state.sessionmaker, user.id, s.id, s.kb_id

    def still_valid() -> bool:
        from .. import security
        from ..models import User as U
        with sm() as db2:
            res = security.resolve_token(db2, raw_token, app.state.jwt)
            if res is None or res[0].id != uid:
                return False
            kb = kb_by_id(db2, kb_id)
            try:
                return kb is not None and scope_key(policy_for(db2, db2.get(U, uid), kb), res[0].username) == prep["scope_key"]
            except PolicyError:
                return False

    def finish(result, hidden: bool) -> dict:
        user_resp, summary, cached = result
        with sm() as db2:
            from ..models import User as U
            u, ss = db2.get(U, uid), db2.get(ChatSession, sid)
            m = _store(db2, u, ss, body.question, user_resp, summary, cached, rid, prep["fp"], hidden=hidden)
            db2.commit()
            return AskOut(session_id=sid, message_id=m.id, answer=m.answer, citations=m.citations, status=m.status,
                          notice=user_resp.get("notice")).model_dump(mode="json")

    if prep["policy"] is None:
        def denied(wrap):
            return ({"answer": DENIED_TEXT, "citations": [], "status": "access_denied"},
                    {"status": "access_denied", "reason": "policy_error", "llm_calls": 0, "scope_key": ""}, False)
        run = denied
    else:
        def run(wrap):
            return app.state.backend.ask(prep["policy"], user.username, kb_id, body.question,
                                         retriever=prep["retriever"], generation=prep["generation"], llm_wrapper=wrap)
    stream = StreamSession(run, still_valid, finish)
    return StreamingResponse(stream.events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
