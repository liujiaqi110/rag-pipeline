"""Load the existing ground truth and the existing retrievers, and build RAGAS samples.

Ground truth: ``eval/eval_questions.jsonl`` (also accepts .json / .csv with the same fields)::

    {"question", "expected_source", "expected_keywords", "expected_section", ["metadata_filter"]}

It is *source-level* relevance: there are no chunk-level relevance labels and no reference
answers. Optional human-written reference answers can be supplied in
``eval/reference_answers.jsonl`` as ``{"question_id" | "question", "reference"}``; they
are never generated automatically.

Retrieval: the BM25 / Dense / Hybrid search functions are imported (unchanged, read-only)
from ``scripts/10_eval_retrieval.py`` — the same code that produced the project's
historical Recall@5/10 numbers.
"""

from __future__ import annotations

import contextlib
import csv
import functools
import hashlib
import importlib.util
import json
import pickle
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .config import PROJECT_ROOT, RETRIEVAL_SCRIPT_PATH


# --------------------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------------------

@dataclass
class GroundTruthItem:
    question_id: str
    question: str
    expected_source: Optional[str]
    expected_keywords: List[str]
    expected_section: Optional[str]
    metadata_filter: Optional[Dict[str, Any]] = None
    reference: Optional[str] = None  # only from a human-written file
    raw: Dict[str, Any] = field(default_factory=dict)


def _read_records(path: Path) -> List[Dict[str, Any]]:
    suffix = path.suffix.lower()
    with open(path, encoding="utf-8-sig") as f:
        if suffix == ".jsonl":
            return [json.loads(line) for line in f if line.strip()]
        if suffix == ".json":
            data = json.load(f)
            if isinstance(data, dict):
                data = data.get("questions") or data.get("data") or []
            return list(data)
        if suffix == ".csv":
            rows = list(csv.DictReader(f))
            for r in rows:
                kw = r.get("expected_keywords")
                if isinstance(kw, str):
                    kw = kw.strip()
                    r["expected_keywords"] = (
                        json.loads(kw) if kw.startswith("[") else [k.strip() for k in kw.split("|") if k.strip()]
                    )
            return rows
    raise ValueError(f"Unsupported ground-truth format: {path.suffix}")


def make_question_id(index: int, question: str) -> str:
    """Stable id: 1-based line position + short content hash (detects edits/reordering)."""
    h = hashlib.sha1(question.encode("utf-8")).hexdigest()[:6]
    return f"q{index:03d}-{h}"


def load_ground_truth(path: Path, references_path: Optional[Path] = None) -> List[GroundTruthItem]:
    records = _read_records(Path(path))
    items: List[GroundTruthItem] = []
    for i, r in enumerate(records, 1):
        q = (r.get("question") or r.get("user_input") or "").strip()
        if not q:
            raise ValueError(f"Ground truth record {i} in {path} has no question")
        items.append(
            GroundTruthItem(
                question_id=str(r.get("question_id") or r.get("id") or make_question_id(i, q)),
                question=q,
                expected_source=r.get("expected_source") or None,
                expected_keywords=list(r.get("expected_keywords") or []),
                expected_section=r.get("expected_section") or None,
                metadata_filter=r.get("metadata_filter") or None,
                reference=(r.get("reference") or None),
                raw=r,
            )
        )
    if references_path and Path(references_path).exists():
        refs = _read_records(Path(references_path))
        by_id = {x.get("question_id"): x.get("reference") for x in refs if x.get("question_id")}
        by_q = {(x.get("question") or "").strip(): x.get("reference") for x in refs if x.get("question")}
        for it in items:
            ref = by_id.get(it.question_id) or by_q.get(it.question)
            if ref and str(ref).strip():
                it.reference = str(ref).strip()
    return items


def dataset_fingerprint(path: Path) -> Dict[str, Any]:
    data = Path(path).read_bytes()
    return {
        "path": str(Path(path).resolve().relative_to(PROJECT_ROOT)) if PROJECT_ROOT in Path(path).resolve().parents else str(path),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "bytes": len(data),
    }


# --------------------------------------------------------------------------------------
# Retrieval adapter (reuses scripts/10_eval_retrieval.py without modifying it)
# --------------------------------------------------------------------------------------

def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_ORIGINAL_ST_CLASS = None  # real class, captured before any temporary patching


def _real_st_class():
    global _ORIGINAL_ST_CLASS
    if _ORIGINAL_ST_CLASS is None:
        from sentence_transformers import SentenceTransformer

        _ORIGINAL_ST_CLASS = SentenceTransformer
    return _ORIGINAL_ST_CLASS


@functools.lru_cache(maxsize=4)
def get_sentence_transformer(model_name: str, device: Optional[str] = None):
    """Load a SentenceTransformer once per process (the original scripts load it per query)."""
    return _real_st_class()(model_name, device=device)


@contextlib.contextmanager
def cached_sentence_transformer(device: Optional[str]) -> Iterator[None]:
    """Temporarily make ``sentence_transformers.SentenceTransformer(name)`` return a cached
    instance. Scoped to the retrieval call only; the original module is restored afterwards.
    Ranking logic in the original scripts is untouched — only model loading is cached."""
    import sentence_transformers as st

    original = _real_st_class()

    def factory(model_name_or_path=None, *args, **kwargs):
        return get_sentence_transformer(model_name_or_path, kwargs.get("device", device))

    st.SentenceTransformer = factory  # type: ignore[assignment]
    try:
        yield
    finally:
        st.SentenceTransformer = original  # type: ignore[assignment]


class RetrievalAdapter:
    """BM25 / Dense / Hybrid retrieval via the existing evaluation script."""

    def __init__(self, device: Optional[str] = "cpu", script_path: Path = RETRIEVAL_SCRIPT_PATH,
                 rrf_params: Any = None, bm25_path: Optional[Path] = None, dense_path: Optional[Path] = None):
        """``bm25_path`` / ``dense_path`` (v0.7, optional): load an isolated experiment index instead of
        ``index/``. Only the file location changes; the search functions are the same unchanged code."""
        from .hybrid_rrf import RRFParams

        self.device = device
        self.rrf_params = rrf_params or RRFParams()
        self.mod = _load_module(script_path, "rag_eval_retrieval_v032")
        self.bm25_path = Path(bm25_path) if bm25_path else self.mod.BM25_PATH
        self.dense_path = Path(dense_path) if dense_path else self.mod.DENSE_PATH
        self.bm25 = self._load_bm25(self.bm25_path)
        if dense_path is None:
            self.dense_index = self.mod.load_dense_index()
        else:
            with open(self.dense_path, "rb") as f:  # same as load_dense_index(), but fails loudly
                self.dense_index = pickle.load(f)
        self.script_path = script_path

    def _load_bm25(self, path: Path):
        mod = self.mod

        class _Unpickler(pickle.Unpickler):
            def find_class(self, module, name):
                # Index was pickled from a script run as __main__.
                if name == "SimpleBM25":
                    return mod.SimpleBM25
                return super().find_class(module, name)

        with open(path, "rb") as f:
            return _Unpickler(f).load()

    @property
    def available_strategies(self) -> Dict[str, bool]:
        has_dense = self.dense_index is not None
        # "hybrid" = LEGACY union_safe (not RRF, known defects); "hybrid_rrf" = RRF, the formal service fusion.
        return {"bm25": True, "dense": has_dense, "hybrid": has_dense, "hybrid_rrf": has_dense}

    def describe(self) -> Dict[str, Any]:
        d = {
            "retrieval_code": str(self.script_path.relative_to(PROJECT_ROOT)),
            "bm25_documents": getattr(self.bm25, "N", None),
            "dense_available": self.dense_index is not None,
        }
        if self.dense_index is not None:
            d.update(
                dense_model=self.dense_index.get("model_name"),
                dense_documents=self.dense_index.get("num_documents"),
                dense_dim=self.dense_index.get("embedding_dim"),
            )
        return d

    def retrieve(self, strategy: str, question: str, top_k: int,
                 metadata_filter: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Return ranked results ``[{"chunk": {...}, "bm25_score"?, "dense_score"?}, ...]``."""
        if not self.available_strategies.get(strategy):
            raise RuntimeError(f"Strategy '{strategy}' unavailable (dense index missing)")
        m = self.mod
        ctx = cached_sentence_transformer(self.device) if strategy != "bm25" else contextlib.nullcontext()
        with ctx:
            if strategy == "bm25":
                res = m.search_bm25(question, self.bm25, top_k=top_k, metadata_filter=metadata_filter)
            elif strategy == "dense":
                res = m.search_dense(question, self.dense_index, top_k=top_k, metadata_filter=metadata_filter)
            elif strategy == "hybrid":
                # The original search_hybrid silently falls back to BM25 when dense fails.
                # Probe dense first so a degraded hybrid is reported as an error, not as hybrid.
                probe = m.search_dense(question, self.dense_index, top_k=1, metadata_filter=metadata_filter)
                if probe is None:
                    raise RuntimeError("dense component unavailable; hybrid would silently degrade to BM25")
                res = m.search_hybrid(question, self.bm25, self.dense_index, top_k=top_k,
                                      metadata_filter=metadata_filter)
            elif strategy == "hybrid_rrf":
                from .hybrid_rrf import search_hybrid_rrf

                res = search_hybrid_rrf(m, question, self.bm25, self.dense_index, self.rrf_params,
                                        top_k=top_k, metadata_filter=metadata_filter)
                if res is None:
                    raise RuntimeError("dense component unavailable; hybrid_rrf refuses to degrade to BM25")
            else:
                raise ValueError(f"Unknown strategy {strategy}")
        if res is None:
            raise RuntimeError(f"{strategy} retrieval returned None (sentence-transformers/model unavailable)")
        return res[:top_k]


# --------------------------------------------------------------------------------------
# RAGAS sample
# --------------------------------------------------------------------------------------

@dataclass
class EvalSample:
    question_id: str
    strategy: str
    experiment_version: str
    user_input: str
    retrieved_contexts: List[str]
    retrieved_chunk_ids: List[str]
    retrieved_sources: List[str]
    expected_source: Optional[str]
    expected_keywords: List[str]
    response: Optional[str] = None
    reference: Optional[str] = None
    context_k: Optional[int] = None
    retrieval_ranked: List[Dict[str, Any]] = field(default_factory=list)  # full top-k, for metrics

    @property
    def sample_key(self) -> str:
        return f"{self.question_id}|{self.strategy}|k{self.context_k}"

    def ragas_fields(self) -> Dict[str, Any]:
        return {
            "user_input": self.user_input,
            "retrieved_contexts": self.retrieved_contexts,
            "response": self.response,
            "reference": self.reference,
        }

    def to_record(self) -> Dict[str, Any]:
        d = asdict(self)
        d.pop("retrieval_ranked", None)
        return d


def build_sample(item: GroundTruthItem, strategy: str, ranked: List[Dict[str, Any]],
                 context_k: int, experiment_version: str) -> EvalSample:
    top = ranked[:context_k]
    return EvalSample(
        question_id=item.question_id,
        strategy=strategy,
        experiment_version=experiment_version,
        user_input=item.question,
        retrieved_contexts=[r["chunk"].get("text", "") for r in top],
        retrieved_chunk_ids=[r["chunk"].get("chunk_id") for r in ranked],
        retrieved_sources=[r["chunk"].get("source_file", "") for r in ranked],
        expected_source=item.expected_source,
        expected_keywords=item.expected_keywords,
        reference=item.reference,
        context_k=context_k,
        retrieval_ranked=ranked,
    )


def make_adapter(cfg) -> "RetrievalAdapter":
    """RetrievalAdapter configured from EvalConfig (incl. experimental hybrid_rrf parameters)."""
    from .hybrid_rrf import RRFParams

    return RetrievalAdapter(device=cfg.embedding_device,
                            rrf_params=RRFParams(k=cfg.rrf_k, bm25_pool=cfg.rrf_bm25_pool,
                                                 dense_pool=cfg.rrf_dense_pool))
