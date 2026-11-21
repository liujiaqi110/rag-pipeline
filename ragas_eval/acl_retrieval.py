"""ACL-constrained retrieval: BM25 / Dense / RRF restricted to an AccessScope at the CANDIDATE stage.

Why not filter the final top-5? Post-filtering lets unauthorised chunks occupy BM25/Dense pool slots and RRF ranks,
starves the context of authorised evidence (top-5 can shrink to 0), and keeps unauthorised text in memory next to the
prompt builder. Here instead:

* BM25  — the ORIGINAL ``search_bm25`` runs over a :class:`~ragas_eval.acl.FilteredBM25View` that only contains
          authorised chunks: unauthorised chunks are never scored.
* Dense — ``Retriever.dense(..., allowed=...)`` skips unauthorised chunk IDs while the pool is being filled, before the
          k cut-off, so the 25-slot pool is filled with authorised candidates only.
* RRF   — the unchanged ``rrf_fuse`` fuses the two authorised pools (k=60, 25/25).
* Every stage output is re-checked (``assert_within_scope``); a violation aborts the request (AclViolation).

``AclRetriever.bm25`` is the filtered view, so everything built from ``retriever.bm25.documents`` downstream
(pipeline texts/sources, the safety layer's document list for source-scope detection, corpus IDs for citation
classification) only ever knows authorised chunks. A base retriever whose ``dense`` cannot take ``allowed`` is
rejected (fail closed — no silent fallback to post-filtering).
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional

from .acl import AccessScope, AclViolation, FilteredBM25View, assert_within_scope
from .hybrid_rrf import RRFParams, rrf_fuse


class AclRetriever:
    def __init__(self, base: Any, scope: AccessScope, rrf_params: Optional[RRFParams] = None):
        if "allowed" not in inspect.signature(base.dense).parameters:
            raise AclViolation("base retriever cannot apply ACL at candidate stage")
        self.base, self.scope = base, scope
        self.bm25 = FilteredBM25View(base.bm25, scope)
        self.rrf_params = rrf_params or getattr(getattr(base, "ad", None), "rrf_params", None) or RRFParams()
        self._search_bm25 = base.m.search_bm25

    # pipeline contract ------------------------------------------------------------------------------------------------
    @property
    def stats(self):
        return getattr(self.base, "stats", {})

    def reset(self):
        if hasattr(self.base, "reset"):
            self.base.reset()

    def dense(self, text: str, scope: Optional[str] = None, k: Optional[int] = None) -> List[Dict[str, Any]]:
        k = k if k is not None else self.rrf_params.dense_pool
        out = self.base.dense(text, scope, k, allowed=self.scope.allowed_chunks)
        assert_within_scope((r["chunk"].get("chunk_id") for r in out), self.scope, "dense_candidates")
        return out

    def bm25_full(self, text: str, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.bm25.N:
            return []
        out = self._search_bm25(text, self.bm25, top_k=self.bm25.N,
                                metadata_filter={"source_file": scope} if scope else None)
        assert_within_scope((r["chunk"].get("chunk_id") for r in out), self.scope, "bm25_candidates")
        return out

    def rrf(self, text: str, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.scope.allowed_chunks:
            return []
        fused = rrf_fuse(self.bm25_full(text, scope), self.dense(text, scope), self.rrf_params)
        assert_within_scope((r["chunk"].get("chunk_id") for r in fused), self.scope, "rrf_fused")
        return fused
