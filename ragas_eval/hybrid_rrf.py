"""Reciprocal Rank Fusion of BM25 and Dense — the retrieval fusion used by the formal service (``rag_service`` →
``rag_retriever.Retriever.rrf``) with the defaults below: k=60, BM25 pool 25, Dense pool 25 (introduced in v0.5,
selected as the service default after the v0.9 A/B; see docs/experiments/retrieval_ab.md).

The legacy ``search_hybrid`` in ``scripts/10_eval_retrieval.py`` is a different, defective rule ("union_safe",
BM25-rank-first) and is NOT RRF.

    RRF(d) = Σ_i 1 / (k + rank_i(d))      over the BM25 list and the Dense list

* Candidates come from the ORIGINAL, unmodified ``search_bm25`` / ``search_dense`` functions.
* Lists are merged by ``chunk_id``; a chunk present in only one list still gets that list's term.
* Only ranks are fused — raw BM25 / cosine scores are never mixed.
* Deterministic order: RRF score desc → best single-list rank asc → chunk_id asc.
* No question-specific rules.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Sequence


@dataclass(frozen=True)
class RRFParams:
    k: int = 60
    bm25_pool: int = 25  # candidates taken from BM25 (same as the original Hybrid's internal 25)
    dense_pool: int = 25  # candidates taken from Dense
    drop_zero_bm25: bool = True  # BM25 docs with score 0 have an arbitrary (index-order) rank
    text_source: str = "bm25"  # "bm25" = final_chunks text, "dense" = dense-index text (has extra heading line)

    def label(self) -> str:
        return f"rrf_k{self.k}_b{self.bm25_pool}_d{self.dense_pool}"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def rrf_fuse(bm25_results: Sequence[Dict[str, Any]], dense_results: Sequence[Dict[str, Any]],
             params: RRFParams, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
    """Fuse two ranked candidate lists. Inputs are not mutated."""
    if params.k <= 0:
        raise ValueError("RRF k must be > 0")
    if params.text_source not in ("bm25", "dense"):
        raise ValueError("text_source must be 'bm25' or 'dense'")

    bm = [r for r in bm25_results if not (params.drop_zero_bm25 and (r.get("bm25_score") or 0) <= 0)]
    bm = bm[: params.bm25_pool]
    de = list(dense_results[: params.dense_pool])

    entries: Dict[str, Dict[str, Any]] = {}
    for source, lst in (("bm25", bm), ("dense", de)):
        for rank, r in enumerate(lst, 1):
            cid = r["chunk"].get("chunk_id")
            if not cid:
                continue
            e = entries.setdefault(cid, {"bm25_rank": None, "dense_rank": None, "bm25_chunk": None,
                                         "dense_chunk": None, "bm25_score": None, "dense_score": None})
            if e[f"{source}_rank"] is not None:  # duplicate id inside one list: keep best rank
                continue
            e[f"{source}_rank"] = rank
            e[f"{source}_chunk"] = r["chunk"]
            e[f"{source}_score"] = r.get(f"{source}_score")

    fused = []
    for cid, e in entries.items():
        score = sum(1.0 / (params.k + e[f"{s}_rank"]) for s in ("bm25", "dense") if e[f"{s}_rank"] is not None)
        ranks = [e[f"{s}_rank"] for s in ("bm25", "dense") if e[f"{s}_rank"] is not None]
        preferred = e["bm25_chunk"] if params.text_source == "bm25" else e["dense_chunk"]
        chunk = dict(preferred or e["bm25_chunk"] or e["dense_chunk"])
        fused.append({
            "chunk": chunk,
            "rrf_score": score,
            "bm25_rank": e["bm25_rank"], "dense_rank": e["dense_rank"],
            "bm25_score": e["bm25_score"] or 0.0, "dense_score": e["dense_score"] or 0.0,
            "retrieval_origin": "both" if len(ranks) == 2 else ("bm25" if e["bm25_rank"] else "dense"),
            "text_source": params.text_source if preferred is not None else
            ("bm25" if e["bm25_chunk"] is not None else "dense"),
            "_tie": (min(ranks), cid),
        })
    fused.sort(key=lambda x: (-x["rrf_score"], x["_tie"][0], x["_tie"][1]))
    for x in fused:
        x.pop("_tie")
        x["rrf_score"] = round(x["rrf_score"], 8)
    return fused[:top_k] if top_k else fused


def search_hybrid_rrf(mod: Any, question: str, bm25_index: Any, dense_index: Dict[str, Any], params: RRFParams,
                      top_k: int, metadata_filter: Optional[Dict[str, Any]] = None,
                      bm25_results: Optional[List[Dict[str, Any]]] = None,
                      dense_results: Optional[List[Dict[str, Any]]] = None) -> Optional[List[Dict[str, Any]]]:
    """Run the original retrievers (or reuse precomputed full lists) and fuse with RRF.

    ``mod`` is the loaded ``scripts/10_eval_retrieval.py`` module. Returns None if dense is unavailable
    (never silently falls back to BM25)."""
    if bm25_results is None:
        bm25_results = mod.search_bm25(question, bm25_index, top_k=params.bm25_pool + _zero_slack(bm25_index),
                                       metadata_filter=metadata_filter)
    if dense_results is None:
        dense_results = mod.search_dense(question, dense_index, top_k=params.dense_pool,
                                         metadata_filter=metadata_filter)
    if dense_results is None:
        return None
    return rrf_fuse(bm25_results, dense_results, params, top_k=top_k)


def _zero_slack(bm25_index: Any) -> int:
    # ask for the whole index so zero-score filtering can't shrink the pool below bm25_pool
    return int(getattr(bm25_index, "N", 0) or 0)
