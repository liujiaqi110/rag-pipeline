"""Heavy LOCAL functional check (not run in CI): real question answering on the bundled example knowledge base through
the formal service chain (RRF retrieval → local Ollama qwen2.5:7b → safety layer).

    python scripts/build_example_index.py --dense
    python -m ragas_eval.example_qa --data build/example_data --out build/example_qa

Deterministic checks only (no LLM judge, no answer-quality score):
  * answerable: status ok/partial_answer, ≥1 citation, every citation is in the retrieved context (exact ID),
    and whether at least one citation points to an annotated evidence chunk;
  * unanswerable: status refused and no citations;
  * doc-scoped: citations come only from the scoped document.
Results are labelled automated / provisional. Requires Ollama with qwen2.5:7b already pulled (never pulled here).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import retrieval_eval as re_
from . import retrieval_metrics as rm


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m ragas_eval.example_qa")
    ap.add_argument("--data", required=True)
    ap.add_argument("--questions", default=str(re_.DEFAULT_QUESTIONS))
    ap.add_argument("--out", default=None)
    a = ap.parse_args(argv)
    data = Path(a.data)
    from .local_llm import OllamaClient
    from .rag_retriever import Retriever
    from .rag_service import RagService
    llm = OllamaClient()
    info = llm.model_info()                      # raises model_missing instead of pulling
    svc = RagService(retriever=Retriever(data), llm=llm)
    qs = re_.load_questions(Path(a.questions))
    slots, errs = re_.resolve(qs, re_.load_chunks(data))
    if errs:
        raise SystemExit("annotation errors: " + "; ".join(errs))
    rows = []
    for q in qs:
        t0 = time.perf_counter()
        out = svc.answer(q["question"], safety=True)
        u, au = out["user_response"], out["audit"]
        cited = [c["chunk_id"] for c in u["citations"]]
        ctx = set(au.get("context_ids") or [])
        row = {"id": q["id"], "category": q.get("category"), "status": u["status"], "citations": cited,
               "citations_in_context": all(c in ctx for c in cited), "blocked_citations": au.get("blocked_citations") or [],
               "seconds": round(time.perf_counter() - t0, 2)}
        if q.get("answerable"):
            rel = rm.relevant_chunks(slots[q["id"]])
            row["evidence_in_context"] = bool(rel & ctx)
            row["cites_evidence"] = bool(rel & set(cited))
            ok = u["status"] in ("ok", "partial_answer") and bool(cited) and row["citations_in_context"]
            if q.get("scope_doc"):
                row["scope_respected"] = all(c["source"] == q["scope_doc"] for c in u["citations"])
                ok = ok and row["scope_respected"]
            row["check"] = "pass" if ok else "fail"
        else:
            row["check"] = "pass" if (u["status"] == "refused" and not cited) else "fail"
        row["answer"] = u["answer"]
        rows.append(row)
        print(f"{q['id']} {row['status']} {row['check']}", flush=True)
    summary = {"labels": re_.LABELS | {"check_type": "deterministic functional checks; answer quality NOT scored"},
               "model": {k: info.get(k) for k in ("model", "digest", "quantization", "ollama_version")},
               "questions": len(rows), "passed": sum(r["check"] == "pass" for r in rows),
               "answerable_passed": sum(r["check"] == "pass" for r in rows if r["category"] != "unanswerable"),
               "answerable": sum(1 for r in rows if r["category"] != "unanswerable"),
               "refusals_passed": sum(r["check"] == "pass" for r in rows if r["category"] == "unanswerable"),
               "unanswerable": sum(1 for r in rows if r["category"] == "unanswerable"),
               "cites_annotated_evidence": sum(1 for r in rows if r.get("cites_evidence")),
               "invalid_citations_left": sum(1 for r in rows if not r["citations_in_context"]),
               "blocked_citation_events": sum(len(r["blocked_citations"]) for r in rows)}
    if a.out:
        o = Path(a.out)
        o.mkdir(parents=True, exist_ok=True)
        (o / "example_qa.json").write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["passed"] == summary["questions"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
