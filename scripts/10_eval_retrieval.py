#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
10_eval_retrieval.py — LEGACY retrieval evaluator (v0.3.x). Kept as a historical implementation.

What is still used from this file (unchanged code, imported by ragas_eval.dataset_adapter / rag_retriever):
  * SimpleBM25 (to unpickle the BM25 index), search_bm25, search_dense — the scoring of the formal service.

What is LEGACY and must not be used as the recommended method:
  * search_hybrid_legacy_union_safe (alias: search_hybrid) — the v0.3.2 "union_safe" rule. It is NOT RRF and NOT a
    weighted fusion. Known defects (diagnosed in v0.9, see docs/experiments/retrieval_ab.md):
      1. ordering key is (bm25_rank or 999, dense_rank or 999): BM25 rank dominates, Dense only breaks ties;
      2. a chunk first added in the Dense loop keeps bm25_rank=None even when it is in the BM25 top-25,
         so candidates found by BOTH retrievers can be sorted after every BM25 candidate;
      3. Dense-only candidates get 999 and are truncated away at top-k;
      4. zero-score BM25 candidates are not filtered; the function mutates its input dicts.
    The formal service uses RRF instead: ragas_eval.hybrid_rrf.rrf_fuse, k=60, BM25/Dense pools 25/25.
  * compute_source_hit (formerly named compute_recall): returns True if ANY of the top-k chunks comes from a file
    whose name contains expected_source. This is a **Source Hit@K**, not a standard Recall@K. Chunk-level
    Recall@K / MRR / nDCG@K / evidence-slot coverage are in ragas_eval.retrieval_metrics.

Usage (legacy question format with expected_source / expected_keywords; index under ./index):
  python scripts/10_eval_retrieval.py
"""

import argparse, json, pickle, re
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = PROJECT_ROOT / "eval"
QUESTIONS_PATH = EVAL_DIR / "eval_questions.jsonl"
REPORT_PATH = EVAL_DIR / "retrieval_eval_report.md"
BM25_PATH = PROJECT_ROOT / "index" / "sparse" / "bm25_index.pkl"
DENSE_PATH = PROJECT_ROOT / "index" / "dense" / "dense_index.pkl"


class SimpleBM25:
    def __init__(self, k1=1.5, b=0.75):
        self.k1 = k1; self.b = b
        self.documents = []; self.doc_tokens = []; self.doc_lengths = []
        self.avg_doc_len = 0; self.idf = {}; self.N = 0
    def tokenize(self, text):
        tokens = []
        for seg in text.split():
            cjk = sum(1 for ch in seg if "一" <= ch <= "鿿")
            if cjk > len(seg) * 0.3:
                tokens.extend(ch for ch in seg if not ch.isspace())
            else:
                w = __import__('re').sub(r"[^\w]", "", seg.lower())
                if w and len(w) > 1: tokens.append(w)
        return tokens


def load_questions(path: Path) -> List[Dict]:
    qs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip(): qs.append(json.loads(line))
    return qs


def search_bm25(query, bm25, top_k=10, metadata_filter=None):
    results = []
    for doc, tokens in zip(bm25.documents, bm25.doc_tokens):
        if metadata_filter:
            skip = False
            for k, v in metadata_filter.items():
                if isinstance(v, str) and v not in str(doc.get(k, "")):
                    skip = True; break
            if skip: continue
        score = 0.0; doc_len = len(tokens)
        for token in bm25.tokenize(query):
            if token not in bm25.idf: continue
            tf = tokens.count(token)
            num = tf * (bm25.k1 + 1)
            denom = tf + bm25.k1 * (1 - bm25.b + bm25.b * doc_len / max(bm25.avg_doc_len, 1))
            score += bm25.idf[token] * num / max(denom, 1e-10)
        results.append({"chunk": doc, "bm25_score": round(score, 4)})
    results.sort(key=lambda x: x["bm25_score"], reverse=True)
    return results[:top_k]


def load_dense_index() -> Optional[Dict]:
    if not DENSE_PATH.exists(): return None
    try:
        with open(DENSE_PATH, "rb") as f: return pickle.load(f)
    except Exception: return None


def search_dense(query, dense_index, top_k=25, metadata_filter=None):
    try:
        from sentence_transformers import SentenceTransformer
        import numpy as np
    except ImportError: return None
    try:
        model = SentenceTransformer(dense_index.get("model_name", "BAAI/bge-m3"))
    except Exception: return None
    q_emb = model.encode([query], normalize_embeddings=True)[0]
    scores = np.dot(dense_index["embeddings"], q_emb)
    order = np.argsort(scores)[::-1]
    results = []
    for idx in order:
        s = float(scores[idx])
        if s <= 0: continue
        src = dense_index["source_files"][idx]
        if metadata_filter:
            skip = False
            for k, v in metadata_filter.items():
                if k == "source_file" and not (v in src or src in v): skip = True
            if skip: continue
        results.append({
            "chunk": {
                "chunk_id": dense_index["chunk_ids"][idx],
                "parent_id": dense_index["parent_ids"][idx],
                "source_file": src,
                "section_heading": dense_index["section_headings"][idx],
                "text": dense_index["texts"][idx],
            },
            "bm25_score": 0.0, "dense_score": round(s, 4),
        })
        if len(results) >= top_k: break
    return results


def search_hybrid_legacy_union_safe(query, bm25, dense_index, top_k=10, metadata_filter=None):
    """LEGACY v0.3.2 union_safe hybrid (NOT RRF): union BM25(top5) + Dense(top5) + remaining top-25 candidates,
    dedup, sort by (bm25_rank or 999, dense_rank or 999). Kept unchanged for reproducibility; see module docstring
    for the known defects. Use ragas_eval.hybrid_rrf.rrf_fuse (k=60, pools 25/25) instead."""
    bm25_r = search_bm25(query, bm25, top_k=25, metadata_filter=metadata_filter)
    if not dense_index:
        return bm25_r[:top_k]
    dense_r = search_dense(query, dense_index, top_k=25, metadata_filter=metadata_filter)
    if not dense_r:
        return bm25_r[:top_k]

    seen_ids = set(); union = []
    for rank, r in enumerate(bm25_r[:5], 1):
        cid = r["chunk"].get("chunk_id")
        if not cid or cid in seen_ids: continue
        seen_ids.add(cid); r["bm25_rank"] = rank; union.append(r)
    for rank, r in enumerate(dense_r[:5], 1):
        cid = r["chunk"].get("chunk_id")
        if not cid or cid in seen_ids: continue
        seen_ids.add(cid); r["dense_rank"] = rank; union.append(r)
    # fill remaining
    bm_map = {r["chunk"].get("chunk_id"): (i+1, r) for i, r in enumerate(bm25_r) if r["chunk"].get("chunk_id")}
    de_map = {r["chunk"].get("chunk_id"): (i+1, r) for i, r in enumerate(dense_r) if r["chunk"].get("chunk_id")}
    for cid in set(bm_map.keys()) | set(de_map.keys()):
        if cid in seen_ids: continue
        br, b_r = bm_map.get(cid, (None, None))
        dr, d_r = de_map.get(cid, (None, None))
        r = b_r or d_r
        if r: r["bm25_rank"] = br; r["dense_rank"] = dr; union.append(r)
    # sort: BM25 rank preferred for tie-breaking
    union.sort(key=lambda x: (x.get("bm25_rank") or 999, x.get("dense_rank") or 999))
    return union[:top_k]


search_hybrid = search_hybrid_legacy_union_safe  # backward-compatible name used by dataset_adapter ("hybrid" = legacy)
LEGACY_HYBRID_LABEL = "Hybrid (legacy union_safe, NOT RRF)"


# --- Scoring ---

def compute_source_hit(results, expected_source, k):
    """Source Hit@K: True if any top-k chunk's source_file contains expected_source. NOT a chunk-level Recall@K."""
    if not expected_source: return False
    for r in results[:k]:
        if expected_source in r["chunk"].get("source_file", ""): return True
    return False


def compute_kw_hit(results, expected_keywords, k):
    if not expected_keywords: return 1.0
    texts = " ".join(r["chunk"].get("text", "").lower() for r in results[:k])
    return sum(1 for kw in expected_keywords if kw.lower() in texts) / len(expected_keywords)


def find_source_rank(results, expected_source):
    """v0.3.2: find the rank (1-based) of expected_source in results."""
    if not expected_source: return None
    for i, r in enumerate(results, 1):
        if expected_source in r["chunk"].get("source_file", ""): return i
    return None


def missing_keywords(results, expected_keywords, k=10):
    """v0.3.2: which keywords are missing from top-k texts."""
    if not expected_keywords: return []
    top_texts = " ".join(r["chunk"].get("text", "").lower() for r in results[:k])
    return [kw for kw in expected_keywords if kw.lower() not in top_texts]


def classify_failure(bm_passed, de_passed, hy_passed, bm_rank, de_rank, hy_rank,
                     missing_kw, src, has_dense):
    """v0.3.2: classify the failure type."""
    if has_dense and bm_passed and not hy_passed:
        return "hybrid_regression_from_bm25"
    if has_dense and de_passed and not hy_passed:
        return "hybrid_regression_from_dense"
    if src and hy_rank is None:
        if missing_kw: return "source_and_keywords_failed"
        return "source_not_retrieved"
    if missing_kw:
        return "keywords_not_matched"
    return "eval_expectation_mismatch"


def score_mode(results, q, top_k, mode, available):
    src = q.get("expected_source", ""); kw = q.get("expected_keywords", [])
    r5 = compute_source_hit(results, src, 5); r10 = compute_source_hit(results, src, 10)
    kw10 = compute_kw_hit(results, kw, 10)
    top_srcs = list(dict.fromkeys(r["chunk"].get("source_file", "?") for r in results[:5]))
    rank = find_source_rank(results, src)
    missing = missing_keywords(results, kw, 10) if kw else []

    fails = []
    if not available: fails.append(f"{mode} not available")
    elif src:
        if not r10: fails.append(f"expected_source '{src[:40]}' not in top-{top_k}")
        if kw and kw10 < 0.5: fails.append(f"keyword hit {kw10:.0%} < 50%")
    else:
        if kw and kw10 < 0.6: fails.append(f"keyword hit {kw10:.0%} < 60%")

    return {
        f"{mode}_source_hit5": r5, f"{mode}_source_hit10": r10,
        f"{mode}_kw10": round(kw10, 2),
        f"{mode}_sources": top_srcs, f"{mode}_all_sources": top_srcs,
        f"{mode}_passed": len(fails) == 0, f"{mode}_fail": "; ".join(fails),
        f"{mode}_rank": rank, f"{mode}_missing_kw": missing,
    }


def evaluate_all_modes(questions, bm25, dense_index, top_k=10):
    results = []
    for q in questions:
        mf = q.get("metadata_filter")
        bm_r = search_bm25(q["question"], bm25, top_k=top_k, metadata_filter=mf)
        de_r = search_dense(q["question"], dense_index, top_k=top_k, metadata_filter=mf) if dense_index else None
        hy_r = search_hybrid(q["question"], bm25, dense_index, top_k=top_k, metadata_filter=mf) if dense_index else bm_r[:top_k]

        de_avail = dense_index is not None
        r = {"question": q["question"], "expected_source": q.get("expected_source", ""),
             "expected_keywords": q.get("expected_keywords", []),
             "expected_section": q.get("expected_section")}
        r.update(score_mode(bm_r, q, top_k, "bm25", True))
        r.update(score_mode(de_r or [], q, top_k, "dense", de_avail))
        r.update(score_mode(hy_r, q, top_k, "hybrid", de_avail))

        # v0.3.2: failure type classification
        r["failure_type"] = classify_failure(
            r["bm25_passed"], r.get("dense_passed", False), r.get("hybrid_passed", False),
            r["bm25_rank"], r.get("dense_rank"), r.get("hybrid_rank"),
            r.get("hybrid_missing_kw", []), r["expected_source"], de_avail,
        ) if not (r["bm25_passed"] and r.get("hybrid_passed", True)) else ""
        results.append(r)
    return results


def generate_report(results, output_path, dense_available):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(results)
    bm_p = sum(1 for r in results if r["bm25_passed"])
    de_p = sum(1 for r in results if r.get("dense_passed"))
    hy_p = sum(1 for r in results if r.get("hybrid_passed"))

    # v0.2 failure check
    v02_fixed = 0
    for r in results:
        if ("chunk_size" in r["question"] or "Python" in r["question"]):
            if r["hybrid_passed"]: v02_fixed += 1

    # Best mode
    best_mode = "bm25"
    best_rate = bm_p / max(total, 1)
    if dense_available:
        if de_p / max(total, 1) > best_rate: best_mode = "dense"; best_rate = de_p / max(total, 1)
        if hy_p / max(total, 1) > best_rate: best_mode = "hybrid"; best_rate = hy_p / max(total, 1)

    hybrid_vs_bm25 = "ABOVE" if hy_p >= bm_p else "BELOW (regression)"
    reg_from_bm25 = sum(1 for r in results if r.get("failure_type") == "hybrid_regression_from_bm25")
    reg_from_dense = sum(1 for r in results if r.get("failure_type") == "hybrid_regression_from_dense")
    improved = sum(1 for r in results if not r["bm25_passed"] and r.get("hybrid_passed"))

    lines = [
        f"# Retrieval Evaluation Report",
        f"**Generated**: {now}  **Pipeline**: LEGACY v0.3.2 evaluator (Hybrid = union_safe, NOT RRF; SourceHit = file-level hit, NOT Recall)",
        "", "## Mode Comparison", "",
        f"| Mode | Passed | Pass Rate |",
        f"|------|--------|-----------|",
        f"| BM25-only | {bm_p}/{total} | {bm_p/max(total,1):.0%} |",
    ]
    if dense_available:
        lines.extend([
            f"| Dense-only | {de_p}/{total} | {de_p/max(total,1):.0%} |",
            f"| {LEGACY_HYBRID_LABEL} | {hy_p}/{total} | {hy_p/max(total,1):.0%} |",
        ])
    else:
        lines.extend(["| Dense-only | N/A | index not available |",
                       "| Hybrid | N/A | index not available |"])
    lines.extend([
        "", f"**Best mode**: {best_mode} ({best_rate:.0%})",
        f"**Hybrid vs BM25**: {hybrid_vs_bm25}",
        f"**v0.2 failures fixed**: {v02_fixed}/2",
        f"**Regressed from BM25**: {reg_from_bm25}",
        f"**Regressed from Dense**: {reg_from_dense}",
        f"**Improved (BM25->Hybrid)**: {improved}",
        f"**Regressed questions**: {sum(1 for r in results if r['bm25_passed'] and not r.get('hybrid_passed'))}",
        f"**Improved questions**: {sum(1 for r in results if not r['bm25_passed'] and r.get('hybrid_passed'))}",
        "", "## Per-Question Results", "",
    ])

    for i, r in enumerate(results):
        bm_ok = "PASS" if r["bm25_passed"] else "FAIL"
        de_ok = ("PASS" if r.get("dense_passed") else "FAIL") if dense_available else "N/A"
        hy_ok = ("PASS" if r.get("hybrid_passed") else "FAIL") if dense_available else "N/A"
        lines.extend([
            f"### {i+1}. {r['question']}", "",
            f"| Mode | Result | SourceHit@5 | SourceHit@10 | KW@10 | Top Sources |",
            f"|------|--------|----------|-----------|-------|-------------|",
            f"| BM25 | {bm_ok} | {r['bm25_source_hit5']} | {r['bm25_source_hit10']} | {r['bm25_kw10']:.0%} | {', '.join(r['bm25_sources'][:3])} |",
        ])
        if dense_available:
            lines.append(f"| Dense | {de_ok} | {r['dense_source_hit5']} | {r['dense_source_hit10']} | {r['dense_kw10']:.0%} | {', '.join(r.get('dense_sources',[])[:3])} |")
            lines.append(f"| Hybrid | {hy_ok} | - | - | - | {', '.join(r.get('hybrid_sources',[])[:3])} |")
        for mode in ["bm25", "dense", "hybrid"]:
            fk = f"{mode}_fail"
            if r.get(fk) and (mode != "dense" or dense_available):
                lines.append(f"- **{mode} fail**: {r[fk]}")
            rk = r.get(f"{mode}_rank")
            mk = r.get(f"{mode}_missing_kw", [])
            if rk is not None: lines.append(f"- **{mode} source_rank**: {rk}")
            if mk: lines.append(f"- **{mode} missing_keywords**: {', '.join(mk)}")
        if r.get("failure_type"):
            lines.append(f"- **failure_type**: {r['failure_type']}")
        lines.append("")

    lines.extend(["## Recommendation", ""])
    if hy_p >= total * 0.9:
        lines.append("- Legacy hybrid pass rate >= 90% (legacy metric; not a recommendation)")
    elif hy_p >= bm_p:
        lines.append(f"- Hybrid improves over BM25 ({hy_p}/{total} vs {bm_p}/{total}) — continue tuning")
    else:
        lines.append(f"- ⚠️ Hybrid regresses below BM25 ({hy_p}/{total} vs {bm_p}/{total})")
        lines.append("- Suggestions: increase safe_protect_top, try weighted fusion, or check embedding quality")

    report = "\n".join(lines)
    output_path.write_text(report, encoding="utf-8")
    print(f"Report saved: {output_path}")


def main():
    p = argparse.ArgumentParser(description="LEGACY retrieval evaluator (SourceHit@K, union_safe hybrid)")
    p.add_argument("--questions", default=str(QUESTIONS_PATH))
    p.add_argument("--top_k", type=int, default=10)
    a = p.parse_args()

    print("=" * 60)
    print("10_eval_retrieval.py — LEGACY retrieval evaluator (Hybrid = union_safe, NOT RRF)")
    print("=" * 60)

    qp = Path(a.questions)
    if not qp.exists(): print(f"Not found: {qp}"); return
    if not BM25_PATH.exists(): print(f"Not found: {BM25_PATH}"); return

    qs = load_questions(qp)
    print(f"Loaded {len(qs)} questions")
    with open(BM25_PATH, "rb") as f: bm25 = pickle.load(f)
    print(f"BM25: {bm25.N} documents")

    di = load_dense_index()
    da = di is not None
    if da:
        print(f"Dense: {di['num_documents']} docs, dim={di['embedding_dim']}, model={di['model_name']}")
    else:
        print("Dense: NOT AVAILABLE")

    results = evaluate_all_modes(qs, bm25, di, top_k=a.top_k)

    # Console: show all 3 modes per question
    for r in results:
        bm_s = "PASS" if r["bm25_passed"] else "FAIL"
        de_s = ("PASS" if r.get("dense_passed") else "FAIL") if da else "N/A"
        hy_s = ("PASS" if r.get("hybrid_passed") else "FAIL") if da else "N/A"
        print(f"\n  [{bm_s} | {de_s} | {hy_s}] {r['question'][:80]}")
        print(f"    BM25:    SourceHit@5={r['bm25_source_hit5']} SourceHit@10={r['bm25_source_hit10']} KW={r['bm25_kw10']:.0%}  src={', '.join(r['bm25_sources'][:2])}")
        if da:
            print(f"    Dense:   SourceHit@5={r['dense_source_hit5']} SourceHit@10={r['dense_source_hit10']} KW={r['dense_kw10']:.0%}  src={', '.join(r.get('dense_sources',[])[:2])}")
            print(f"    Hybrid (legacy union_safe):  src={', '.join(r.get('hybrid_sources',[])[:2])}")
        for mode in ["bm25", "dense", "hybrid"]:
            fk = f"{mode}_fail"
            if r.get(fk) and (mode != "dense" or da):
                print(f"    {mode.upper()} FAIL: {r[fk]}")

    generate_report(results, REPORT_PATH, da)

    bm_p = sum(1 for r in results if r["bm25_passed"])
    print(f"\nBM25: {bm_p}/{len(qs)} ({bm_p/max(len(qs),1):.0%})")
    if da:
        de_p = sum(1 for r in results if r.get("dense_passed"))
        hy_p = sum(1 for r in results if r.get("hybrid_passed"))
        print(f"Dense: {de_p}/{len(qs)} ({de_p/max(len(qs),1):.0%})")
        print(f"Hybrid (legacy union_safe): {hy_p}/{len(qs)} ({hy_p/max(len(qs),1):.0%})")


if __name__ == "__main__":
    main()
