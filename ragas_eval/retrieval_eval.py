"""Public retrieval evaluation entry point (v1.2) for the bundled example knowledge base — or any data directory with
the same layout (``<data>/corpus/final_chunks.jsonl`` + ``<data>/index/{sparse,dense}``) and a question file in the
same format as ``examples/questions.jsonl``.

    python -m ragas_eval.retrieval_eval validate --data build/example_data
    python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25            # no model needed
    python -m ragas_eval.retrieval_eval run --data build/example_data --modes bm25,dense,rrf  # needs local bge-m3

Evidence is annotated as (document, verbatim quote); quotes are resolved to chunk IDs of the built corpus
(whitespace-insensitive), so annotations survive re-chunking. Metric definitions: ragas_eval/retrieval_metrics.py
and docs/EVALUATION.md. The example set is a FUNCTIONAL check of the pipeline — it is not the research project's
39-question evaluation set and is not an independent validation set.
"""

from __future__ import annotations

import argparse
import json
import pickle
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import retrieval_metrics as rm
from .config import PROJECT_ROOT, RETRIEVAL_SCRIPT_PATH

DEFAULT_QUESTIONS = PROJECT_ROOT / "examples" / "questions.jsonl"
POOL = 25
KS = (1, 3, 5, 10)
LABELS = {"review_type": "automated", "human_approved": False, "evaluation_status": "provisional",
          "dataset": "public example knowledge base (functional check; not an independent validation set)"}


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def load_questions(path: Path) -> List[Dict[str, Any]]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_chunks(data: Path) -> List[Dict[str, Any]]:
    p = data / "corpus" / "final_chunks.jsonl"
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if not r.get("is_parent")]  # only child chunks are indexed


def resolve(questions: List[Dict[str, Any]], chunks: List[Dict[str, Any]]):
    """-> ({qid: slots}, errors). Each evidence item becomes the set of chunk IDs (same doc) whose text contains the quote."""
    by_doc: Dict[str, List[Dict[str, Any]]] = {}
    for c in chunks:
        by_doc.setdefault(c["source_file"], []).append(c)
    out, errs, seen = {}, [], set()
    for q in questions:
        qid = q.get("id")
        if not qid or qid in seen:
            errs.append(f"{qid}: missing or duplicate id")
        seen.add(qid)
        if not q.get("question"):
            errs.append(f"{qid}: empty question")
        if not q.get("answerable"):
            if q.get("evidence_slots"):
                errs.append(f"{qid}: unanswerable question must not carry evidence")
            if not (q.get("refusal_rule") or {}).get("correct") or not (q.get("refusal_rule") or {}).get("incorrect"):
                errs.append(f"{qid}: unanswerable question needs refusal_rule.correct and .incorrect")
            continue
        if not q.get("reference_answer"):
            errs.append(f"{qid}: answerable question without reference_answer")
        if q.get("category") == "doc_scoped" and q.get("scope_doc") not in by_doc:
            errs.append(f"{qid}: scope_doc not in corpus")
        slots = []
        for s in q.get("evidence_slots") or []:
            groups = []
            for g in s.get("groups") or []:
                items = []
                for it in g:
                    hits = {c["chunk_id"] for c in by_doc.get(it.get("doc"), []) if _norm(it.get("quote")) in _norm(c["text"])}
                    if not it.get("quote") or not hits:
                        errs.append(f"{qid}/{s.get('slot_id')}: quote not found in {it.get('doc')}")
                    items.append(hits)
                groups.append(items)
            if not groups:
                errs.append(f"{qid}/{s.get('slot_id')}: slot without groups")
            slots.append(groups)
        if not slots:
            errs.append(f"{qid}: answerable question without evidence_slots")
        if q.get("category") == "cross_document":
            docs = {it.get("doc") for s in q.get("evidence_slots") or [] for g in s.get("groups") or [] for it in g}
            if len(docs) < 2:
                errs.append(f"{qid}: cross_document question with evidence from < 2 documents")
        out[qid] = slots
    return out, errs


# ---------------------------------------------------------------------------------------------------------------------
class BM25Only:
    """BM25 retrieval with the same scoring code as the service (scripts/10 search_bm25), no embedding model."""

    def __init__(self, data: Path):
        from .dataset_adapter import _load_module
        self.m = _load_module(RETRIEVAL_SCRIPT_PATH, "rag_eval_retrieval_bm25only")
        mod = self.m

        class _U(pickle.Unpickler):
            def find_class(self, module, name):
                return mod.SimpleBM25 if name == "SimpleBM25" else super().find_class(module, name)
        with open(data / "index" / "sparse" / "bm25_index.pkl", "rb") as f:
            self.bm25 = _U(f).load()

    def ranked(self, q: str, mode: str):
        res = [r for r in self.m.search_bm25(q, self.bm25, top_k=self.bm25.N) if r["bm25_score"] > 0]
        ids = [r["chunk"]["chunk_id"] for r in res]
        return ids[:10], ids[:POOL]


class ServiceRetriever:
    """Dense / RRF through the exact retriever class of the formal service (ragas_eval.rag_retriever)."""

    def __init__(self, data: Path):
        from .rag_retriever import Retriever
        self.R = Retriever(data)

    def ranked(self, q: str, mode: str):
        if mode == "dense":
            ids = [r["chunk"]["chunk_id"] for r in self.R.dense(q)]
        elif mode == "rrf":
            ids = [r["chunk"]["chunk_id"] for r in self.R.rrf(q)]
        else:
            res = [r for r in self.R.bm25_full(q) if r["bm25_score"] > 0]
            ids = [r["chunk"]["chunk_id"] for r in res][:POOL]
        return ids[:10], ids


def evaluate(data: Path, questions_path: Path, modes: List[str], k_reason: int = 5) -> Dict[str, Any]:
    qs = load_questions(questions_path)
    chunks = load_chunks(data)
    slots, errs = resolve(qs, chunks)
    if errs:
        raise SystemExit("annotation errors:\n  " + "\n  ".join(errs))
    retr: Dict[str, Any] = {}
    for mode in modes:
        if mode == "bm25":
            retr[mode] = BM25Only(data)
        else:
            retr.setdefault("_service", None)
            if retr["_service"] is None:
                retr["_service"] = ServiceRetriever(data)
            retr[mode] = retr["_service"]
    src = {c["chunk_id"]: c["source_file"] for c in chunks}
    report: Dict[str, Any] = {"labels": LABELS, "recorded_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "questions_file": questions_path.name, "n_questions": len(qs),
                              "n_answerable": sum(1 for q in qs if q.get("answerable")), "chunks": len(chunks),
                              "modes": {}, "failure_reason_k": k_reason}
    for mode in modes:
        per = []
        for q in qs:
            if not q.get("answerable"):
                continue
            top, pool = retr[mode].ranked(q["question"], mode)
            m = rm.question_metrics(top, slots[q["id"]], KS)
            fr = rm.failure_reason(top, pool, slots[q["id"]], k_reason)
            per.append({"id": q["id"], "category": q.get("category"), **m, "failure_reason": fr["reason"],
                        "missing_slots": [q["evidence_slots"][i]["slot_id"] for i in fr["missing_slots"]],
                        "top5": top[:5], "top5_docs": [src.get(c, "?") for c in top[:5]]})
        agg = rm.aggregate([{k: v for k, v in r.items() if k not in ("top5", "top5_docs", "missing_slots")} for r in per])
        by_cat: Dict[str, Any] = {}
        for cat in sorted({r["category"] for r in per}):
            rows = [r for r in per if r["category"] == cat]
            by_cat[cat] = rm.aggregate([{k: v for k, v in r.items() if k not in ("top5", "top5_docs", "missing_slots")} for r in rows])
        reasons: Dict[str, int] = {}
        for r in per:
            reasons[r["failure_reason"]] = reasons.get(r["failure_reason"], 0) + 1
        report["modes"][mode] = {"aggregate": agg, "by_category": by_cat, "failure_reasons_at_k": reasons, "per_question": per}
    return report


def to_markdown(rep: Dict[str, Any]) -> str:
    L = ["# 示例知识库检索评估", "",
         f"> review_type={rep['labels']['review_type']}，human_approved={str(rep['labels']['human_approved']).lower()}，"
         f"evaluation_status={rep['labels']['evaluation_status']}。公开示例只是功能验证集：不是研究项目的 39 题评估集，也不能证明泛化能力。", "",
         f"题目 {rep['n_questions']}（可答 {rep['n_answerable']}），子 Chunk {rep['chunks']}。失败原因按 top-{rep['failure_reason_k']}（=生成上下文大小）判断。", "",
         "| 模式 | Hit@1 | Hit@5 | Recall@5 | Recall@10 | MRR@10 | nDCG@5 | nDCG@10 | SlotFullCov@5 | SlotFullCov@10 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for mode, v in rep["modes"].items():
        a = v["aggregate"]
        L.append(f"| {mode} | {a['hit@1']} | {a['hit@5']} | {a['recall@5']} | {a['recall@10']} | {a['mrr@10']} | {a['ndcg@5']} | {a['ndcg@10']} | {a['slot_fullcov@5']} | {a['slot_fullcov@10']} |")
    for mode, v in rep["modes"].items():
        L += ["", f"## {mode}：逐题结果", "", f"失败原因统计：{v['failure_reasons_at_k']}", "",
              "| 题 | 类别 | Hit@5 | Recall@5 | MRR@10 | SlotFullCov@5 | 完成名次 | 失败原因 | 缺失证据槽 |", "|---|---|---|---|---|---|---|---|---|"]
        for r in v["per_question"]:
            L.append(f"| {r['id']} | {r['category']} | {r['hit@5']} | {r['recall@5']} | {r['mrr@10']} | {r['slot_fullcov@5']} | "
                     f"{r['completion_rank'] or '—'} | {r['failure_reason']} | {' '.join(r['missing_slots']) or '—'} |")
    L += ["", "指标定义见 `docs/EVALUATION.md`。"]
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m ragas_eval.retrieval_eval")
    ap.add_argument("command", choices=["validate", "run"])
    ap.add_argument("--data", required=True, help="data directory (corpus/final_chunks.jsonl + index/)")
    ap.add_argument("--questions", default=str(DEFAULT_QUESTIONS))
    ap.add_argument("--modes", default="bm25,dense,rrf")
    ap.add_argument("--out", default=None, help="write retrieval_eval.json / .md here")
    a = ap.parse_args(argv)
    data, qp = Path(a.data), Path(a.questions)
    if a.command == "validate":
        qs = load_questions(qp)
        slots, errs = resolve(qs, load_chunks(data))
        cats = sorted({q.get("category") for q in qs})
        print(json.dumps({"questions": len(qs), "answerable": sum(1 for q in qs if q.get("answerable")), "categories": cats,
                          "resolved_items": sum(len(it) > 0 for s in slots.values() for sl in s for g in sl for it in g),
                          "errors": errs}, ensure_ascii=False, indent=2))
        return 1 if errs else 0
    rep = evaluate(data, qp, [m.strip() for m in a.modes.split(",") if m.strip()])
    if a.out:
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "retrieval_eval.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        (out / "retrieval_eval.md").write_text(to_markdown(rep), encoding="utf-8")
    print(json.dumps({m: v["aggregate"] | {"failure_reasons": v["failure_reasons_at_k"]} for m, v in rep["modes"].items()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
