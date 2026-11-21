"""Operations metrics — aggregated from the database only (``page_views``, ``qa_events`` service log, knowledge
tables, FAQ / gap tables). Every number below has a fixed definition that is returned with the data.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Chunk, Document, DocumentVersion, IndexGeneration, KnowledgeBase, utcnow
from ..models_ops import FaqEntry, KnowledgeGap, KnowledgeTask, PageView, QaEvent

DEFINITIONS = {
    "pv": "页面访问次数：工作台每次路由切换记录一行（只记路由名），统计窗口内的行数",
    "uv": "独立访问用户数：统计窗口内有页面访问记录的不同用户数",
    "questions": "用户提问次数：qa_events 行数（每个问题一行，无论结果如何）",
    "llm_calls": "模型调用次数：qa_events.llm_calls 之和（FAQ 命中和答案缓存命中为 0）",
    "faq_hits": "FAQ 命中次数：source=faq 的提问数；也等于节省的模型调用次数",
    "answer_cache_hits": "答案缓存命中次数：source=answer_cache 的提问数（同一用户权限范围内的重复问题）",
    "failed": "失败请求：outcome 为 generation_failure（模型失败）或 infrastructure（检索/索引/服务故障）的提问",
    "permission_denied": "权限受限：outcome=permission（无可读文档、策略错误或权限闸门拦截），不算知识缺口",
    "knowledge_gap_events": "知识缺口事件：outcome=knowledge_gap 的提问，或回答但引用知识过期（stale_knowledge）",
    "faq_hit_rate": "FAQ 命中率 = faq_hits / questions",
    "tokens": "Token：本地模型返回的 prompt_eval_count / eval_count 之和，只统计真实调用",
    "latency": "模型响应时间 = 生成耗时（generation_ms，仅真实模型调用）；端到端延迟 = 服务端处理一次提问的总耗时",
    "totals": "知识库数、未删除文档数、Chunk 数（当前活动索引代际中的子 Chunk 数）",
}


def _pct(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    k = (len(v) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(v) - 1)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 2)


def _day(ts: datetime) -> str:
    return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).date().isoformat()


def compute(db: Session, days: int = 14, kb_ids: Optional[List[str]] = None, now: Optional[datetime] = None,
            top_n: int = 10) -> Dict[str, Any]:
    now = now or utcnow()
    start = now - timedelta(days=days)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=today.weekday())
    qe = select(QaEvent).where(QaEvent.ts >= start.replace(tzinfo=None))
    if kb_ids is not None:
        qe = qe.where(QaEvent.kb_id.in_(kb_ids))
    events = list(db.scalars(qe))
    pv_rows = list(db.scalars(select(PageView).where(PageView.ts >= start.replace(tzinfo=None))))

    def aware(t):
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)

    by_src = Counter(e.source for e in events)
    by_outcome = Counter(e.outcome for e in events)
    llm = sum(e.llm_calls for e in events)
    gen_ms = [e.generation_ms for e in events if e.llm_calls > 0 and e.generation_ms is not None]
    e2e_ms = [e.latency_ms for e in events if e.latency_ms is not None]
    n = len(events)

    # daily series
    series = defaultdict(lambda: {"questions": 0, "llm_calls": 0, "prompt_tokens": 0, "output_tokens": 0,
                                  "faq_hits": 0, "failed": 0, "gen_ms": []})
    for e in events:
        s = series[_day(e.ts)]
        s["questions"] += 1
        s["llm_calls"] += e.llm_calls
        s["prompt_tokens"] += e.prompt_tokens
        s["output_tokens"] += e.output_tokens
        s["faq_hits"] += e.source == "faq"
        s["failed"] += e.outcome in ("generation_failure", "infrastructure")
        if e.llm_calls and e.generation_ms is not None:
            s["gen_ms"].append(e.generation_ms)
    daily = []
    for i in range(days, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        s = series.get(d) or {"questions": 0, "llm_calls": 0, "prompt_tokens": 0, "output_tokens": 0, "faq_hits": 0,
                              "failed": 0, "gen_ms": []}
        daily.append({"date": d, "questions": s["questions"], "llm_calls": s["llm_calls"],
                      "prompt_tokens": s["prompt_tokens"], "output_tokens": s["output_tokens"], "faq_hits": s["faq_hits"],
                      "failed": s["failed"], "p50_ms": _pct(s["gen_ms"], 0.5), "p95_ms": _pct(s["gen_ms"], 0.95)})

    # top questions (by normalised-question hash; display text = most recent wording of that question)
    from ..models import ChatMessage
    counts = Counter(e.question_hash for e in events if e.question_hash)
    latest_msg = {}
    for e in sorted(events, key=lambda x: x.id):
        if e.message_id:
            latest_msg[e.question_hash] = e.message_id
    top_q = []
    for h, c in counts.most_common(top_n):
        m = db.get(ChatMessage, latest_msg.get(h)) if latest_msg.get(h) else None
        evs = [e for e in events if e.question_hash == h]
        top_q.append({"question": m.question if m else "（未保存原文的提问）", "count": c,
                      "faq_hits": sum(e.source == "faq" for e in evs),
                      "unanswered": sum(e.outcome == "knowledge_gap" for e in evs)})

    # top cited knowledge (document level, by validated citations of answered questions)
    cite = Counter()
    for e in events:
        for s in {c.get("source") for c in (e.citations or []) if c.get("source")}:
            cite[s] += 1
    docs = {d.doc_key: d for d in db.scalars(select(Document).where(Document.doc_key.in_(list(cite))))} if cite else {}
    top_cited = [{"document_id": docs[k].id if k in docs else None, "title": (docs[k].title if k in docs else k),
                  "citations": v} for k, v in cite.most_common(top_n)]

    # totals
    kb_q = select(func.count()).select_from(KnowledgeBase)
    doc_q = select(func.count()).select_from(Document).where(Document.status != "deleted")
    if kb_ids is not None:
        kb_q = kb_q.where(KnowledgeBase.id.in_(kb_ids))
        doc_q = doc_q.where(Document.kb_id.in_(kb_ids))
    gen = db.scalar(select(IndexGeneration).where(IndexGeneration.status == "active"))
    if kb_ids is None:
        chunks = gen.chunk_count if gen else 0
    else:
        chunks = db.scalar(select(func.count()).select_from(Chunk).join(Document, Document.current_version_id == Chunk.version_id)
                           .where(Document.kb_id.in_(kb_ids), Document.status == "active", Chunk.is_parent.is_(False))) or 0

    gq = select(KnowledgeGap)
    tq = select(KnowledgeTask)
    fq = select(FaqEntry)
    if kb_ids is not None:
        gq, tq, fq = gq.where(KnowledgeGap.kb_id.in_(kb_ids)), tq.where(KnowledgeTask.kb_id.in_(kb_ids)), fq.where(FaqEntry.kb_id.in_(kb_ids))
    gaps = list(db.scalars(gq))
    tasks = list(db.scalars(tq))
    faqs = list(db.scalars(fq))
    faq_hits = by_src.get("faq", 0)
    return {
        "window": {"days": days, "start": start.isoformat(), "end": now.isoformat()},
        "definitions": DEFINITIONS,
        "traffic": {"pv": len(pv_rows), "uv": len({p.user_id for p in pv_rows})},
        "totals": {"knowledge_bases": db.scalar(kb_q) or 0, "documents": db.scalar(doc_q) or 0, "chunks": chunks},
        "questions": {"window": n, "today": sum(aware(e.ts) >= today for e in events),
                      "this_week": sum(aware(e.ts) >= week for e in events),
                      "by_source": dict(by_src), "by_outcome": dict(by_outcome)},
        "llm": {"calls": llm, "prompt_tokens": sum(e.prompt_tokens for e in events),
                "output_tokens": sum(e.output_tokens for e in events),
                "generation_p50_ms": _pct(gen_ms, 0.5), "generation_p95_ms": _pct(gen_ms, 0.95),
                "end_to_end_p50_ms": _pct(e2e_ms, 0.5), "end_to_end_p95_ms": _pct(e2e_ms, 0.95),
                "models": dict(Counter(e.model for e in events if e.model and e.llm_calls))},
        "faq": {"hits": faq_hits, "hit_rate": round(faq_hits / n, 4) if n else 0.0, "saved_llm_calls": faq_hits,
                "answer_cache_hits": by_src.get("answer_cache", 0),
                "by_status": dict(Counter(f.status for f in faqs))},
        "failures": {"failed": by_outcome.get("generation_failure", 0) + by_outcome.get("infrastructure", 0),
                     "generation_failure": by_outcome.get("generation_failure", 0),
                     "infrastructure": by_outcome.get("infrastructure", 0),
                     "permission_denied": by_outcome.get("permission", 0)},
        "gaps": {"total": len(gaps), "by_status": dict(Counter(g.status for g in gaps)),
                 "by_type": dict(Counter(g.gap_type for g in gaps)),
                 "tasks": dict(Counter(t.status for t in tasks)),
                 "resolution_rate": round(sum(g.status == "resolved" for g in gaps) / len(gaps), 4) if gaps else 0.0},
        "daily": daily,
        "top_questions": top_q,
        "top_cited": top_cited,
    }
