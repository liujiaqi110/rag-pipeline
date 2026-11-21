"""Service log: one ``qa_events`` row per user question + outcome classification + knowledge-gap capture.

Outcome classes are kept strictly apart (they are NOT all "gaps"):

    answered            status ok / partial_answer (incl. FAQ and answer-cache hits)
    knowledge_gap       refused / insufficient_evidence / insufficient_scoped_evidence with documents the user CAN read
                        gap_type: no_evidence (nothing retrieved) | low_relevance (best dense score < threshold)
                                  | insufficient_evidence (context retrieved, model could not answer from it)
    permission          access_denied / blocked (no readable document, policy error, ACL gate) — never a gap;
                        also an unanswered question whose best evidence lies in documents the user may NOT read
                        (error_code ``restricted_knowledge``): the knowledge exists, access is the issue. Decided
                        server-side for operations statistics only — the user's answer is unchanged and never hints
                        at the restricted document
    generation_failure  generation_error (model failed)                                     — never a gap
    infrastructure      retrieval_error, index_unavailable, dense_unavailable, internal errors — never a gap

Answered questions whose cited documents are older than ``KB_KNOWLEDGE_MAX_AGE_DAYS`` are additionally recorded as a
``stale_knowledge`` gap ("知识过期"). Repeated gaps are flagged when their occurrence count reaches the threshold.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..models import Document, DocumentVersion, utcnow
from ..models_ops import QaEvent
from .common import docs_by_key, qhash

GAP_STATUSES = {"refused", "insufficient_evidence", "insufficient_scoped_evidence"}
PERMISSION_STATUSES = {"access_denied", "blocked"}
INFRA_CODES = {"index_unavailable", "dense_unavailable", "internal_error", "retrieval_error"}


def classify(status: str, retrieved: int, top_score: Optional[float], low_threshold: float,
             error_code: Optional[str] = None) -> Tuple[str, Optional[str]]:
    if error_code in INFRA_CODES or status == "retrieval_error":
        return "infrastructure", None
    if status == "generation_error":
        return "generation_failure", None
    if status in PERMISSION_STATUSES:
        return "permission", None
    if status in GAP_STATUSES:
        if retrieved == 0:
            return "knowledge_gap", "no_evidence"
        if top_score is not None and top_score < low_threshold:
            return "knowledge_gap", "low_relevance"
        return "knowledge_gap", "insufficient_evidence"
    return "answered", None


RESTRICTED_MARGIN = 0.05


def restricted_evidence(backend: Any, policy: Any, username: str, retriever: Any, question: str,
                        authorised_top: Optional[float], threshold: float) -> bool:
    """True if the most relevant chunk in the WHOLE index belongs to a document outside the user's scope and is
    clearly more relevant than anything the user can read."""
    if retriever is None or not getattr(retriever, "has_dense", False):
        return False
    try:
        svc = backend.service(policy, retriever)
        scope = svc.scope_for(username)
        best = retriever.dense(question, None, 1)            # unrestricted — result never leaves this function
        if not best:
            return False
        cid, score = best[0]["chunk"]["chunk_id"], float(best[0]["dense_score"])
        return (not scope.allows_chunk(cid) and score >= threshold
                and score - (authorised_top or 0.0) >= RESTRICTED_MARGIN)
    except Exception:  # noqa: BLE001
        return False


def top_dense_score(backend: Any, policy: Any, username: str, retriever: Any, question: str) -> Optional[float]:
    """Best authorised dense similarity (only computed for unanswered questions; None without a Dense index)."""
    if retriever is None or not getattr(retriever, "has_dense", False):
        return None
    try:
        from ragas_eval.acl_retrieval import AclRetriever
        svc = backend.service(policy, retriever)
        res = AclRetriever(retriever, svc.scope_for(username)).dense(question, k=1)
        return float(res[0]["dense_score"]) if res else 0.0
    except Exception:  # noqa: BLE001
        return None


def stale_citations(db: Session, citations: List[dict], max_age_days: int) -> bool:
    if max_age_days <= 0 or not citations:
        return False
    limit = utcnow() - timedelta(days=max_age_days)
    docs = docs_by_key(db, [c.get("source") for c in citations])
    for d in docs.values():
        v = db.get(DocumentVersion, d.current_version_id) if d.current_version_id else None
        when = (v.created_at if v else d.updated_at)
        if when is not None and when.replace(tzinfo=limit.tzinfo) < limit:
            return True
    return False


def record(db: Session, *, settings: Any, backend: Any, user: Any, kb_id: str, question: str, status: str,
           source: str, summary: Optional[Dict[str, Any]] = None, session_id: Optional[str] = None,
           message_id: Optional[int] = None, citations: Optional[List[dict]] = None, latency_ms: Optional[float] = None,
           error_code: Optional[str] = None, faq_id: Optional[str] = None, policy: Any = None,
           retriever: Any = None) -> QaEvent:
    summary = summary or {}
    retrieved = int(summary.get("retrieved") or 0)
    top = None
    if status in GAP_STATUSES and retrieved > 0 and policy is not None:
        top = top_dense_score(backend, policy, user.username, retriever, question)
    outcome, gap_type = classify(status, retrieved, top, settings.low_relevance_threshold, error_code)
    if outcome == "knowledge_gap" and policy is not None and restricted_evidence(
            backend, policy, user.username, retriever, question, top, settings.low_relevance_threshold):
        outcome, gap_type, error_code = "permission", None, "restricted_knowledge"
    if outcome == "answered" and source != "faq" and stale_citations(db, citations or [], settings.knowledge_max_age_days):
        gap_type = "stale_knowledge"
    ev = QaEvent(user_id=user.id, kb_id=kb_id, session_id=session_id, message_id=message_id,
                 question_hash=qhash(question), source=source, status=status, outcome=outcome, gap_type=gap_type,
                 error_code=error_code, llm_calls=int(summary.get("llm_calls") or 0),
                 prompt_tokens=int(summary.get("prompt_tokens") or 0), output_tokens=int(summary.get("output_tokens") or 0),
                 generation_ms=summary.get("generation_ms"), latency_ms=latency_ms,
                 model=(summary.get("model") or None), faq_id=faq_id, generation=summary.get("generation"),
                 top_score=top, retrieved=retrieved,
                 citations=[{"chunk_id": c.get("chunk_id"), "source": c.get("source"), "version_id": c.get("version_id")}
                            for c in (citations or [])])
    db.add(ev)
    db.flush()
    if gap_type:
        from .gaps import add_occurrence
        add_occurrence(db, backend, settings, kb_id, gap_type, question, user.id, ev.id)
    return ev
