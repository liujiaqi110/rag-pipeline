"""Release retriever (v1.1 public): Dense / BM25 / RRF over an index located by RAG_DATA_DIR.

Same scoring as the research code (search_dense semantics, scripts/10_eval_retrieval.search_bm25, hybrid_rrf.rrf_fuse
k=60 pools 25/25). The index is NOT part of the public repository: set RAG_DATA_DIR to a private directory that
contains index/dense/dense_index.pkl and index/sparse/bm25_index.pkl (see README).
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from .config import PROJECT_ROOT
from .hybrid_rrf import RRFParams, rrf_fuse

# Formal service retrieval (explicit; equal to RRFParams defaults): RRF k=60, BM25 pool 25, Dense pool 25.
SERVICE_RRF = RRFParams(k=60, bm25_pool=25, dense_pool=25)
POOL = SERVICE_RRF.dense_pool


def data_dir() -> Path:
    return Path(os.environ.get("RAG_DATA_DIR") or (PROJECT_ROOT / "data")).resolve()


class Retriever:
    def __init__(self, base: Optional[Path] = None):
        from .dataset_adapter import RetrievalAdapter, get_sentence_transformer
        base = Path(base) if base else data_dir()
        bm, de = base / "index" / "sparse" / "bm25_index.pkl", base / "index" / "dense" / "dense_index.pkl"
        if not bm.exists() or not de.exists():
            raise FileNotFoundError("index not found: set RAG_DATA_DIR to the private data directory (see README)")
        self.ad = RetrievalAdapter(device="cpu", rrf_params=SERVICE_RRF, bm25_path=bm, dense_path=de)
        self.m, self.bm25, self.di = self.ad.mod, self.ad.bm25, self.ad.dense_index
        self.model = get_sentence_transformer(self.di.get("model_name", "BAAI/bge-m3"), "cpu")
        self.E = np.asarray(self.di["embeddings"])
        self.ids = list(self.di["chunk_ids"])
        self.src = list(self.di["source_files"])
        self.pos = {c: i for i, c in enumerate(self.ids)}
        self.N = len(self.ids)
        self.reset()

    def reset(self):
        self.stats = Counter()
        self._cache: Dict[str, Any] = {}

    def encode(self, text: str):
        if text not in self._cache:
            self.stats["encodes"] += 1
            self._cache[text] = self.model.encode([text], normalize_embeddings=True)[0]
        return self._cache[text]

    def dense(self, text: str, scope: Optional[str] = None, k: int = POOL) -> List[Dict[str, Any]]:
        q = self.encode(text)
        s = self.E @ q
        out = []
        for i in np.argsort(s)[::-1]:
            if s[i] <= 0:
                break
            if scope and self.src[i] != scope:
                continue
            out.append({"chunk": {"chunk_id": self.ids[i], "source_file": self.src[i]}, "dense_score": float(s[i])})
            if len(out) >= k:
                break
        return out

    def bm25_full(self, text: str, scope: Optional[str] = None):
        return self.m.search_bm25(text, self.bm25, top_k=self.N, metadata_filter={"source_file": scope} if scope else None)

    def rrf(self, text: str, scope: Optional[str] = None):
        return rrf_fuse(self.bm25_full(text, scope), self.dense(text, scope), self.ad.rrf_params)
