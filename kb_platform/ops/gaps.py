"""Knowledge gaps: merge similar unanswered questions, count occurrences, convert to supplement tasks, verify.

Merging: a new occurrence joins an existing gap of the same knowledge base when the question embeddings are similar
(``KB_GAP_MERGE_THRESHOLD``) or the normalised questions are identical (fallback when no embedding model). A resolved
or dismissed gap that occurs again is re-opened (the problem is evidently not solved).

Verification re-asks the gap's question AS the users who hit it (at most 3, their CURRENT permissions, real pipeline,
no answer cache bypass needed — the index generation changes when documents are added). The gap is resolved only if
every sampled user now gets an answered status with citations. Only statuses, citation counts and cited document ids /
versions are stored — never the answer text.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..acl_store import kb_by_id, policy_for
from ..models import User, utcnow
from ..models_ops import GapOccurrence, KnowledgeGap, KnowledgeTask
from .common import docs_by_key, embed, normalize

OPEN = ("open", "in_progress")


def _cos(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return float(sum(x * y for x, y in zip(a, b)))


def add_occurrence(db: Session, backend: Any, settings: Any, kb_id: str, gap_type: str, question: str, user_id: int,
                   event_id: int) -> KnowledgeGap:
    try:
        vec = embed(backend, [question])[0].tolist()
    except Exception:  # noqa: BLE001 — no embedding model: exact-text merge only
        vec = []
    norm = normalize(question)
    best, best_sim = None, 0.0
    for g in db.scalars(select(KnowledgeGap).where(KnowledgeGap.kb_id == kb_id)):
        sim = 1.0 if normalize(g.question) == norm else _cos(vec, g.embedding or [])
        if sim > best_sim:
            best, best_sim = g, sim
    if best is None or best_sim < settings.gap_merge_threshold:
        best = KnowledgeGap(kb_id=kb_id, gap_type=gap_type, question=question.strip()[:2000], embedding=vec,
                            occurrences=0, distinct_users=0, status="open")
        db.add(best)
        db.flush()
    elif best.status in ("resolved", "dismissed"):
        best.status, best.resolved_at = "open", None
        best.note = "已处理的缺口再次出现，自动重新打开"
    db.add(GapOccurrence(gap_id=best.id, qa_event_id=event_id, user_id=user_id, question=question.strip()[:2000],
                         gap_type=gap_type))
    db.flush()
    occ = list(db.scalars(select(GapOccurrence).where(GapOccurrence.gap_id == best.id)))
    best.occurrences = len(occ)
    best.distinct_users = len({o.user_id for o in occ})
    best.gap_type = Counter(o.gap_type for o in occ).most_common(1)[0][0]
    best.last_seen = utcnow()
    return best


def verify(db: Session, backend: Any, settings: Any, gap_id: Optional[str], actor: str) -> Dict[str, Any]:
    g = db.get(KnowledgeGap, gap_id) if gap_id else None
    if g is None:
        return {"failed": True, "error_code": "gap_not_found", "error_message": "缺口不存在"}
    kb = kb_by_id(db, g.kb_id)
    occ = list(db.scalars(select(GapOccurrence).where(GapOccurrence.gap_id == g.id).order_by(GapOccurrence.ts.desc())))
    users: List[int] = []
    for o in occ:
        if o.user_id not in users:
            users.append(o.user_id)
    results = []
    gen_id, retriever = "external", None
    if not backend.external:
        gen = backend.index.active_generation(db)
        if gen is None:
            return {"failed": True, "error_code": "index_unavailable", "error_message": "索引尚未构建"}
        retriever, gen_id = backend.index.retriever(gen), gen.id
    for i, uid in enumerate(users[:3], 1):
        u = db.get(User, uid)
        if u is None or not u.active:
            results.append({"user": f"用户{i}", "status": "user_inactive", "answered": False})
            continue
        try:
            pol = policy_for(db, u, kb)
        except Exception:  # noqa: BLE001
            results.append({"user": f"用户{i}", "status": "access_denied", "answered": False})
            continue
        user_resp, summary, _ = backend.ask(pol, u.username, g.kb_id, g.question, retriever=retriever, generation=gen_id)
        cites = user_resp.get("citations") or []
        docs = docs_by_key(db, [c.get("source") for c in cites])
        ok = user_resp.get("status") in ("ok", "partial_answer") and bool(cites)
        results.append({"user": f"用户{i}", "status": user_resp.get("status"), "answered": ok, "citations": len(cites),
                        "documents": sorted({d.id for d in docs.values()}),
                        "versions": sorted({d.current_version_id for d in docs.values() if d.current_version_id})})
    resolved = bool(results) and all(r["answered"] for r in results)
    g.verification = {"at": utcnow().isoformat(), "by": actor, "generation": gen_id, "resolved": resolved,
                      "results": results}
    tasks = list(db.scalars(select(KnowledgeTask).where(KnowledgeTask.gap_id == g.id)))
    if resolved:
        g.status, g.resolved_at = "resolved", utcnow()
        for t in tasks:
            if t.status in ("done", "in_progress", "todo"):
                t.status, t.verified_at = "verified", utcnow()
            t.verification = g.verification
    else:
        if g.status == "open" and tasks:
            g.status = "in_progress"
        for t in tasks:
            if t.status == "done":
                t.status = "reopened"
            t.verification = g.verification
    db.flush()
    return {"gap_id": g.id, "resolved": resolved, "checked_users": len(results)}
