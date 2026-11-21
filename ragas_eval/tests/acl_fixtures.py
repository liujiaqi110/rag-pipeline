"""Shared fixtures for the ACL tests: synthetic corpora, a numpy-free retriever with the exact Retriever contract,
and a recording LLM. Standard library only (runs in CI: no embedding model, no Ollama, no private data)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import pickle
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

from ragas_eval import local_llm as ll
from ragas_eval.config import PROJECT_ROOT
from ragas_eval.dataset_adapter import _load_module
from ragas_eval.hybrid_rrf import RRFParams, rrf_fuse

ACL_DIR = PROJECT_ROOT / "examples" / "acl_corpus"
ACL_DOCS = ACL_DIR / "docs"
ACL_POLICY = ACL_DIR / "acl_policy.json"
CANARIES = {"company_handbook.md": "ACLCANARY-PUB-1001", "eng_deploy_runbook.md": "ACLCANARY-ENG-4821",
            "hr_salary_bands.md": "ACLCANARY-HR-7319", "finance_budget_plan.md": "ACLCANARY-FIN-5547",
            "exec_strategy_memo.md": "ACLCANARY-EXEC-9902", "legal_unlabeled_draft.md": "ACLCANARY-LEGAL-0613"}
# expected access matrix of examples/acl_corpus/acl_policy.json
EXPECTED = {
    "eng_alice": {"company_handbook.md", "eng_deploy_runbook.md"},
    "hr_bob": {"company_handbook.md", "hr_salary_bands.md"},
    "hr_intern_carol": {"company_handbook.md"},
    "fin_dave": {"company_handbook.md", "finance_budget_plan.md"},
    "auditor_erin": {"company_handbook.md", "finance_budget_plan.md"},
    "ceo_frank": {"company_handbook.md", "exec_strategy_memo.md"},
    "former_gina": set(),
    "contractor_hank": set(),
    "nobody_unknown": set(),
}

_MOD = None


def retrieval_module():
    global _MOD
    if _MOD is None:
        _MOD = _load_module(PROJECT_ROOT / "scripts" / "10_eval_retrieval.py", "rag_eval_retrieval_acl_tests")
    return _MOD


def bm25_from_chunks(chunks: List[Dict[str, Any]]):
    """Same construction as scripts/06 SimpleBM25.index, into the scripts/10 class used at query time."""
    m = retrieval_module()
    bm = m.SimpleBM25()
    bm.documents = chunks
    bm.doc_tokens = [bm.tokenize(c.get("text", "")) for c in chunks]
    bm.doc_lengths = [len(t) for t in bm.doc_tokens]
    bm.N = len(chunks)
    bm.avg_doc_len = sum(bm.doc_lengths) / max(bm.N, 1)
    df = Counter(t for toks in bm.doc_tokens for t in set(toks))
    bm.idf = {t: math.log((bm.N - f + 0.5) / (f + 0.5) + 1) for t, f in df.items()}
    return bm


def embed(text: str, dim: int = 96) -> List[float]:
    """Deterministic toy embedding (hashed character unigrams + bigrams, L2-normalised)."""
    v = [0.0] * dim
    chars = [c for c in text if not c.isspace()]
    for g in chars + [a + b for a, b in zip(chars, chars[1:])]:
        v[int(hashlib.md5(g.encode("utf-8")).hexdigest(), 16) % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class FakeBaseRetriever:
    """Pure-Python stand-in with the same public contract as rag_retriever.Retriever
    (bm25 / m / ad.rrf_params / ids / src / stats / reset / encode / dense(allowed=) / bm25_full / rrf)."""

    def __init__(self, chunks: List[Dict[str, Any]], dense_src_override: Optional[Dict[str, str]] = None):
        self.m = retrieval_module()
        self.bm25 = bm25_from_chunks(chunks)
        self.ad = type("Ad", (), {"rrf_params": RRFParams(k=60, bm25_pool=25, dense_pool=25)})()
        self.ids = [c["chunk_id"] for c in chunks]
        self.src = [(dense_src_override or {}).get(c["chunk_id"], c["source_file"]) for c in chunks]
        self.E = [embed(c.get("text", "")) for c in chunks]
        self.N = len(self.ids)
        self.resets = 0
        self.reset()

    def reset(self):
        self.stats = Counter()
        self._cache: Dict[str, Any] = {}
        self.resets += 1

    def encode(self, text):
        if text not in self._cache:
            self.stats["encodes"] += 1
            self._cache[text] = embed(text)
        return self._cache[text]

    def dense(self, text, scope=None, k=25, allowed=None):
        q = self.encode(text)
        s = [sum(a * b for a, b in zip(row, q)) for row in self.E]
        out = []
        for i in sorted(range(self.N), key=lambda i: -s[i]):
            if s[i] <= 0:
                break
            if scope and self.src[i] != scope:
                continue
            if allowed is not None and self.ids[i] not in allowed:
                continue
            out.append({"chunk": {"chunk_id": self.ids[i], "source_file": self.src[i]}, "dense_score": float(s[i])})
            if len(out) >= k:
                break
        return out

    def bm25_full(self, text, scope=None):
        return self.m.search_bm25(text, self.bm25, top_k=self.N, metadata_filter={"source_file": scope} if scope else None)

    def rrf(self, text, scope=None):
        return rrf_fuse(self.bm25_full(text, scope), self.dense(text, scope), self.ad.rrf_params)


class RecordingLLM:
    """Records every prompt it receives; answers with a responder(messages) -> text."""

    def __init__(self, responder=None):
        self.prompts: List[str] = []
        self.responder = responder or cite_first_context

    def chat(self, messages, options, fmt=None):
        self.prompts.append("\n".join(m["content"] for m in messages))
        return ll.MockLLM(self.responder).chat(messages, options)


def cite_first_context(messages) -> str:
    import re
    ids = re.findall(r"<资料 编号=\"([0-9a-f]{12})\"", messages[-1]["content"])
    return f"根据资料作答 [{ids[0]}]。\n引用：{ids[0]}" if ids else "根据提供的资料无法回答该问题。\n引用：无"


class AclCorpus:
    """examples/acl_corpus built with the UNCHANGED pipeline scripts (03→06, BM25) into a temp dir."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="acl_corpus_"))
        spec = importlib.util.spec_from_file_location("build_example_index_acl", PROJECT_ROOT / "scripts" / "build_example_index.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        rc = mod.main(["--docs", str(ACL_DOCS), "--out", str(self.tmp / "data")])
        if rc != 0:
            raise RuntimeError(f"acl corpus build failed rc={rc}")
        m = retrieval_module()

        class _U(pickle.Unpickler):
            def find_class(self, module, name):
                return m.SimpleBM25 if name == "SimpleBM25" else super().find_class(module, name)

        with open(self.tmp / "data" / "index" / "sparse" / "bm25_index.pkl", "rb") as f:
            built = _U(f).load()
        self.chunks = list(built.documents)
        self.by_doc: Dict[str, List[str]] = {}
        for c in self.chunks:
            self.by_doc.setdefault(c["source_file"], []).append(c["chunk_id"])

    def retriever(self) -> FakeBaseRetriever:
        return FakeBaseRetriever(self.chunks)

    def close(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


def load_policy_dict() -> Dict[str, Any]:
    return json.loads(ACL_POLICY.read_text(encoding="utf-8"))
