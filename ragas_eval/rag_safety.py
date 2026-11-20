"""v1.0.3 software safety layer for the RAG pipeline (no prompt change, no retrieval change, 0 paid / remote calls).

Three independent, deterministic guards:

1. strict_citations   — every citation-like token (inside [...] or on the “引用：” line, any length 6–20 hex chars, plus
                        bare 12-hex IDs) must EXACTLY equal an ID of the context given to the model. No prefix / substring
                        matching, no auto-completion. Invalid tokens are blocked in the text and in structured fields,
                        and the final text is re-scanned to prove nothing invalid remains.
2. classify_refusal   — full_refusal | partial | answer | uncertain, without relying on the literal “引用：无”.
                        Only full refusals lose their citations; uncertain cases are NOT modified, only flagged.
3. source_scope       — when the question explicitly asks about one named document (alias derived from the corpus
                        file names, never from GT), only context chunks from that document may be used. If the top-k
                        context has none, no generation happens and a fixed "context insufficient" answer is returned,
                        recorded as a retrieval failure (not as success).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from . import query_decomposition as qd
from . import rag_pipeline as rp

HEXTOK = re.compile(r"(?<![0-9A-Za-z])[0-9a-fA-F]{6,20}(?![0-9A-Za-z])")
BRACKET = re.compile(r"\[([^\]\n]{1,300})\]")
CITE_LINE = re.compile(r"(引用[：:])([^\n]*)")
BLOCK_MARK = "[已拦截的无效引用]"


def _looks_like_id(tok: str) -> bool:
    return bool(re.search(r"\d", tok)) and bool(re.search(r"[a-fA-F]", tok))


def citation_candidates(text: str) -> List[str]:
    """Citation-like tokens: hex tokens inside [...] or on the 引用 line, plus any bare 12-hex token in the text."""
    toks = []
    for seg in BRACKET.findall(text):
        toks += [t for t in HEXTOK.findall(seg) if _looks_like_id(t)]
    for _, line in CITE_LINE.findall(text):
        toks += [t for t in HEXTOK.findall(line) if _looks_like_id(t)]
    toks += [t for t in rp.CITE_RE.findall(text)]
    return list(dict.fromkeys(toks))


def classify_invalid(tok: str, allowed: List[str], corpus_ids: Optional[set]) -> str:
    if any(a.startswith(tok) for a in allowed):
        return "truncated_prefix_of_context_id"
    if any(tok.startswith(a) for a in allowed) or any(a in tok for a in allowed):
        return "extended_context_id"
    if corpus_ids is not None and tok in corpus_ids:
        return "real_chunk_not_in_context"
    return "fabricated"


def strict_citations(text: str, allowed: List[str], corpus_ids: Optional[set] = None) -> Dict[str, Any]:
    allowed_set = set(allowed)
    cands = citation_candidates(text)
    valid = [t for t in cands if t in allowed_set]
    invalid = {t: classify_invalid(t, allowed, corpus_ids) for t in cands if t not in allowed_set}
    clean = text
    for t in sorted(invalid, key=len, reverse=True):
        clean = re.sub(rf"(?<![0-9A-Za-z]){re.escape(t)}(?![0-9A-Za-z])", BLOCK_MARK, clean)
    remaining = [t for t in citation_candidates(clean) if t not in allowed_set]
    return {"found": cands, "valid": valid, "invalid": invalid, "blocked": sorted(invalid), "text": clean,
            "remaining_invalid_after": remaining}


REFUSAL_SENT = re.compile(r"(无法(直接)?(回答|找到|确定|提供|得知|获知|根据)|(资料|材料|上下文)[^。\n]{0,16}(没有|未|并未|并没有)[^。\n]{0,8}"
                          r"(提供|提到|提及|说明|给出|包含|涉及|指出|找到)|没有(找到|相关信息)|" + re.escape(rp.REFUSAL_CORE) + ")")


def _sentences(text: str) -> List[str]:
    body = re.sub(r"引用[：:].*$", "", text, flags=re.S)
    body = re.sub(r"资料未说明[：:]\s*(……|…|\.{3})\s*$", "", body, flags=re.M)
    out: List[str] = []
    for s in re.split(r"[。\n；！？]", body):
        s = s.strip(" ，,")
        if not s:
            continue
        if len(re.sub(r"\[[^\]]*\]", "", s).strip()) >= 2:
            out.append(s)
        elif out and BRACKET.search(s):  # citation brackets placed after the full stop belong to the previous sentence
            out[-1] = out[-1] + " " + s
    return out


def classify_refusal(text: str) -> Dict[str, Any]:
    sents = _sentences(text)
    ref = [s for s in sents if REFUSAL_SENT.search(s)]
    other = [s for s in sents if not REFUSAL_SENT.search(s)]
    has_cites = bool(citation_candidates(text))
    if not ref:
        kind = "answer"
    elif not other:
        kind = "full_refusal"
    elif any(BRACKET.search(s) for s in other):
        kind = "partial"
    elif has_cites:
        kind = "uncertain"  # refusal + uncited extra sentences + citations elsewhere: do not delete, flag
    else:
        kind = "partial"
    return {"kind": kind, "refusal_sentences": len(ref), "other_sentences": len(other)}


def clean_full_refusal(text: str) -> str:
    sents = [re.sub(r"\[[^\]]*\]", "", s).strip(" ，,") for s in _sentences(text)]
    return "。".join(s for s in sents if s) + "。\n\n引用：无"


# ---------------------------------------------------------------------------------------------------------------------
SCOPE_AFTER = r"\s*(中|里|内|之中|当中|里面)"
SCOPE_BEFORE = r"(根据|按照|依据|基于|在)\s*《?"


def detect_source_scope(question: str, doc_names: List[str]) -> Optional[Dict[str, Any]]:
    """Strict single-document scope: a corpus-derived alias that the question uses AS THE SOURCE
    ("<alias> 中/里", "根据 <alias>", "（<alias>）"). Mentions like "第33课和智能体课分别…" are not a source scope."""
    aliases = qd.doc_aliases(doc_names)
    hits = []
    for a, d in sorted(aliases.items(), key=lambda x: -len(x[0])):
        for m in re.finditer(re.escape(a), question):
            after, before = question[m.end():], question[:m.start()]
            if re.match(SCOPE_AFTER, after) or re.search(SCOPE_BEFORE + r"$", before) or (before.endswith(("（", "(")) and after.startswith(("）", ")"))):
                hits.append((a, d))
    docs = sorted({d for _, d in hits})
    if len(docs) != 1:
        return None  # none, or ambiguous -> no strict scope (never guess)
    return {"doc": docs[0], "alias": next(a for a, d in hits if d == docs[0])}


def scoped_insufficient_text(doc: str) -> str:
    name = re.sub(r"\.(md|pdf|docx)$", "", doc)
    return f"当前检索到的资料中没有来自《{name}》的内容，无法根据该文档回答这个问题。\n\n引用：无"


# ---------------------------------------------------------------------------------------------------------------------
@dataclass
class SafetyReport:
    citation: Dict[str, Any] = field(default_factory=dict)
    refusal_kind: str = "answer"
    refusal_citations_removed: List[str] = field(default_factory=list)
    scope: Optional[Dict[str, Any]] = None
    scope_action: str = "none"           # none | restricted_context | insufficient_no_generation
    retrieval_failure: bool = False
    out_of_scope_citations: List[str] = field(default_factory=list)


class SafeRagPipeline:
    """RagPipeline (rag_v1.0 prompt, unchanged) + the three guards. Not the production default."""

    def __init__(self, retriever, llm, cfg: rp.PipelineConfig, corpus_ids: Optional[set] = None):
        self.inner = rp.RagPipeline(retriever, llm, cfg)
        self.corpus_ids = corpus_ids
        self.docs = sorted({d["source_file"] for d in retriever.bm25.documents})

    def answer(self, question: str) -> Dict[str, Any]:
        rep = SafetyReport()
        scope = detect_source_scope(question, self.docs)
        rep.scope = scope
        inner = self.inner
        if scope:
            try:
                ids = inner.retrieve(question)
            except Exception as e:  # noqa: BLE001 — same error class as the unguarded pipeline
                r = rp.RagResponse(question=question, mode=inner.cfg.mode, status="retrieval_error", error_kind=type(e).__name__)
                return {"response": r.to_dict(), "safety": asdict(rep)}
            top = ids[: inner.cfg.top_k]
            scoped = [c for c in top if inner.sources.get(c) == scope["doc"]]
            if not scoped:
                r = rp.RagResponse(question=question, mode=inner.cfg.mode, status="insufficient_scoped_evidence",
                                   answer=scoped_insufficient_text(scope["doc"]), raw_answer="", retrieved_ids=top, refusal=True)
                rep.scope_action, rep.retrieval_failure = "insufficient_no_generation", True
                return {"response": r.to_dict(), "safety": asdict(rep)}
            rep.scope_action = "restricted_context"
            orig = inner.retrieve
            inner.retrieve = lambda q, _s=scoped: _s  # same retrieved chunks, filtered to the named document
            try:
                r = inner.answer(question)
            finally:
                inner.retrieve = orig
        else:
            r = inner.answer(question)
        d = r.to_dict()
        if r.status in ("retrieval_error", "generation_error", "insufficient_evidence"):
            return {"response": d, "safety": asdict(rep)}
        sc = strict_citations(r.raw_answer, r.context_ids, self.corpus_ids)
        rep.citation = {k: sc[k] for k in ("found", "valid", "invalid", "remaining_invalid_after")}
        text = sc["text"]
        rk = classify_refusal(r.raw_answer)
        rep.refusal_kind = rk["kind"]
        valid = sc["valid"]
        if rk["kind"] == "full_refusal":
            rep.refusal_citations_removed = valid
            text, valid = clean_full_refusal(text), []
        if scope:
            rep.out_of_scope_citations = [c for c in valid if inner.sources.get(c) != scope["doc"]]
        d.update(answer=text, valid_citations=valid, blocked_citations=sc["blocked"], cited_ids=sc["found"],
                 refusal=rk["kind"] == "full_refusal", uncited=(rk["kind"] in ("answer", "partial") and not valid))
        if rk["kind"] == "full_refusal":
            d["status"] = "refused"
        elif d["status"] == "refused":  # inner detector said refusal but the answer also carries cited content
            d["status"] = "partial_answer"
        return {"response": d, "safety": asdict(rep)}
