"""Formal local question-answering entry point (v1.0.3 integration).

    python -m ragas_eval.rag_service "问题"                 # safety layer per switch (see below)
    python -m ragas_eval.rag_service "问题" --no-safety     # force off (regression / rollback)
    python -m ragas_eval.rag_service "问题" --safety        # force on

Chain: Query → Retrieval (index from RAG_DATA_DIR; mode rrf)
       → Context (top-5, 6000 chars) → Generation (local Ollama qwen2.5:7b, prompt rag_v1.0 unchanged)
       → Safety layer (rag_safety: exact citation IDs, full-refusal citation cleanup, source-attribution guard)
       → Response.

Safety switch resolution (first match wins): explicit argument → env RAG_SAFETY_LAYER=on|off →
config/safety_layer.json {"enabled": true|false} → off.

The user-facing response contains only the answer text, the validated citations and a status. Blocked / invalid
IDs are kept only in the audit block (never in `user_response`).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import rag_pipeline as rp
from . import rag_safety as rs
from .config import PROJECT_ROOT

SAFETY_CONFIG = PROJECT_ROOT / "config" / "safety_layer.json"
DEFAULT_MODE = "rrf"


def resolve_safety(explicit: Optional[bool] = None, config_path: Path = SAFETY_CONFIG) -> Dict[str, Any]:
    if explicit is not None:
        return {"enabled": bool(explicit), "source": "argument"}
    env = os.environ.get("RAG_SAFETY_LAYER", "").strip().lower()
    if env in ("on", "1", "true"):
        return {"enabled": True, "source": "env RAG_SAFETY_LAYER"}
    if env in ("off", "0", "false"):
        return {"enabled": False, "source": "env RAG_SAFETY_LAYER"}
    if config_path.exists():
        try:
            return {"enabled": bool(json.loads(config_path.read_text(encoding="utf-8")).get("enabled")), "source": str(config_path.name)}
        except (ValueError, OSError):
            return {"enabled": False, "source": "config unreadable -> off"}
    return {"enabled": False, "source": "default off"}


class RagService:
    def __init__(self, retriever=None, llm=None, mode: str = DEFAULT_MODE, corpus_ids: Optional[set] = None):
        if retriever is None:
            from .rag_retriever import Retriever
            retriever = Retriever()
        if llm is None:
            from .local_llm import OllamaClient
            llm = OllamaClient()
        self.R, self.llm, self.mode = retriever, llm, mode
        self.corpus_ids = corpus_ids if corpus_ids is not None else {d["chunk_id"] for d in retriever.bm25.documents}
        self.sources = {d["chunk_id"]: d["source_file"] for d in retriever.bm25.documents}

    def answer(self, question: str, safety: Optional[bool] = None) -> Dict[str, Any]:
        sw = resolve_safety(safety)
        cfg = rp.PipelineConfig(mode=self.mode)
        if hasattr(self.R, "reset"):
            self.R.reset()
        if sw["enabled"]:
            out = rs.SafeRagPipeline(self.R, self.llm, cfg, self.corpus_ids).answer(question)
            resp, safety_report = out["response"], out["safety"]
        else:
            resp, safety_report = rp.RagPipeline(self.R, self.llm, cfg).answer(question).to_dict(), None
        citations = [{"chunk_id": c, "source": self.sources.get(c, "")} for c in resp.get("valid_citations") or []]
        user = {"question": question, "answer": resp.get("answer", ""), "citations": citations, "status": resp.get("status")}
        if resp.get("status") == "insufficient_scoped_evidence":
            user["notice"] = "检索上下文中没有所指定文档的内容（记录为检索失败）"
        audit = {"safety_layer": sw, "safety": safety_report, "mode": self.mode, "prompt_version": resp.get("prompt_version"),
                 "prompt_sha256": rp.prompt_hash(), "retrieved_ids": resp.get("retrieved_ids"), "context_ids": resp.get("context_ids"),
                 "raw_answer": resp.get("raw_answer"), "blocked_citations": resp.get("blocked_citations"),
                 "timings_ms": resp.get("timings_ms"), "tokens": resp.get("tokens"), "model": resp.get("model"),
                 "error_kind": resp.get("error_kind")}
        return {"user_response": user, "audit": audit}


def main(argv=None) -> int:
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m ragas_eval.rag_service")
    ap.add_argument("question")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--safety", dest="safety", action="store_true", default=None)
    g.add_argument("--no-safety", dest="safety", action="store_false")
    ap.add_argument("--audit", action="store_true", help="also print the audit block")
    a = ap.parse_args(argv)
    out = RagService().answer(a.question, safety=a.safety)
    print(json.dumps(out if a.audit else out["user_response"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
