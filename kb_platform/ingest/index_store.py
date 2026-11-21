"""Index generations on disk + the retriever that serves one.

A generation directory has the SAME layout as an ``RAG_DATA_DIR`` (``index/sparse/bm25_index.pkl``,
``index/dense/dense_index.pkl``) and is built with the existing code:

* BM25  — ``scripts/06`` ``SimpleBM25.index`` over child chunks (``index_parent_chunks: false``), pickled as 06 does;
* Dense — same dict layout as ``scripts/06 build_dense_index`` (embeddings, chunk_ids, parent_ids, source_files,
          section_headings, texts, model_name, …) with ``scripts/06 build_retrieval_text``; embeddings are cached per
          document version so a rebuild only encodes new versions.

:class:`PlatformRetriever` subclasses ``rag_retriever.Retriever`` and only replaces how files and the model are
loaded, so ``dense`` (with the P1 ``allowed`` candidate filter), ``bm25_full`` and ``rrf`` are the unchanged
production methods. A BM25-only generation raises :class:`DenseUnavailable` from ``dense``/``rrf`` — it never
silently degrades RRF to BM25.
"""

from __future__ import annotations

import json
import os
import pickle
import shutil
from collections import Counter
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from ragas_eval.config import PROJECT_ROOT
from ragas_eval.dataset_adapter import _load_module
from ragas_eval.rag_retriever import SERVICE_RRF, Retriever


class DenseUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=1)
def m06():
    import sys
    mod = _load_module(PROJECT_ROOT / "scripts" / "06_build_dense_sparse_index.py", "kb_index_06")
    sys.modules.setdefault("kb_index_06", mod)  # pickle needs an importable module name (06 itself pickles from __main__)
    return mod


@lru_cache(maxsize=1)
def m10():
    return _load_module(PROJECT_ROOT / "scripts" / "10_eval_retrieval.py", "kb_index_10")


def retrieval_texts(children: List[Dict[str, Any]]) -> List[str]:
    return [m06().build_retrieval_text(c) for c in children]


def write_generation(gen_dir: Path, children: List[Dict[str, Any]], embeddings: Any = None,
                     model_name: Optional[str] = None) -> Dict[str, Any]:
    """Write a complete generation into ``gen_dir`` (must not exist). Caller renames it into place."""
    gen_dir.mkdir(parents=True, exist_ok=False)
    (gen_dir / "index" / "sparse").mkdir(parents=True)
    bm = m06().SimpleBM25()
    bm.index(children)
    with open(gen_dir / "index" / "sparse" / "bm25_index.pkl", "wb") as f:
        pickle.dump(bm, f)
    dense = embeddings is not None and len(children) > 0
    if dense:
        (gen_dir / "index" / "dense").mkdir(parents=True)
        di = {"embeddings": embeddings, "chunk_ids": [c.get("chunk_id") for c in children],
              "parent_ids": [c.get("parent_id") for c in children], "source_files": [c.get("source_file") for c in children],
              "section_headings": [c.get("section_heading") for c in children], "texts": retrieval_texts(children),
              "model_name": model_name, "normalize_embeddings": True, "embedding_dim": int(embeddings.shape[1]),
              "num_documents": len(children)}
        with open(gen_dir / "index" / "dense" / "dense_index.pkl", "wb") as f:
            pickle.dump(di, f)
    man = {"chunks": len(children), "documents": len({c["source_file"] for c in children}), "dense": dense,
           "dense_model": model_name if dense else None}
    (gen_dir / "MANIFEST.json").write_text(json.dumps(man, ensure_ascii=False), encoding="utf-8")
    return man


class PlatformRetriever(Retriever):
    """Retriever over one generation directory (methods inherited unchanged from rag_retriever.Retriever)."""

    def __init__(self, gen_dir: Path, model: Any = None):  # noqa: D401 — deliberately not calling Retriever.__init__
        mod = m10()

        class _U(pickle.Unpickler):
            def find_class(self, module, name):
                return mod.SimpleBM25 if name == "SimpleBM25" else super().find_class(module, name)

        with open(Path(gen_dir) / "index" / "sparse" / "bm25_index.pkl", "rb") as f:
            self.bm25 = _U(f).load()
        self.m = mod
        self.ad = SimpleNamespace(rrf_params=SERVICE_RRF)
        dp = Path(gen_dir) / "index" / "dense" / "dense_index.pkl"
        self.has_dense = dp.exists() and model is not None
        if self.has_dense:
            import numpy as np
            with open(dp, "rb") as f:
                self.di = pickle.load(f)
            self.model = model
            self.E = np.asarray(self.di["embeddings"])
            self.ids, self.src = list(self.di["chunk_ids"]), list(self.di["source_files"])
        else:
            self.di, self.model, self.E = None, None, None
            self.ids = [d["chunk_id"] for d in self.bm25.documents]
            self.src = [d["source_file"] for d in self.bm25.documents]
        self.pos = {c: i for i, c in enumerate(self.ids)}
        self.N = len(self.ids)
        self.stats = Counter()
        self._cache: Dict[str, Any] = {}

    def dense(self, text, scope=None, k=SERVICE_RRF.dense_pool, allowed=None):
        if not self.has_dense:
            raise DenseUnavailable("this index generation has no Dense index")
        return super().dense(text, scope, k, allowed=allowed)

    def rrf(self, text, scope=None):
        if not self.has_dense:
            raise DenseUnavailable("this index generation has no Dense index")
        return super().rrf(text, scope)


def safe_rmtree(path: Path, root: Path) -> None:
    """Delete ``path`` only if it is strictly inside ``root``."""
    path, root = Path(path).resolve(), Path(root).resolve()
    if path != root and root in path.parents and path.exists():
        shutil.rmtree(path, ignore_errors=True)


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
