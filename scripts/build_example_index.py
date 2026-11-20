#!/usr/bin/env python3
"""Build a data directory (RAG_DATA_DIR layout) from the bundled example Markdown documents.

    python scripts/build_example_index.py                      # BM25 only (no model needed)
    python scripts/build_example_index.py --dense              # + Dense index with a LOCALLY CACHED BAAI/bge-m3
    python scripts/build_example_index.py --dense --allow-download   # let sentence-transformers download the model

Output (default build/example_data, git-ignored):
    corpus/clean_md/*.md  corpus/{draft,fixed,final}_chunks.jsonl  index/sparse/bm25_index.pkl  [index/dense/dense_index.pkl]
    BUILD_MANIFEST.json

Uses the unchanged pipeline scripts 03 → 04 → 05 → 06. Never downloads a model unless --allow-download is given.
An existing output directory is only replaced with --force AND only if it was created by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKER = "BUILD_MANIFEST.json"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(p)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--docs", default=str(ROOT / "examples" / "docs"))
    ap.add_argument("--out", default=str(ROOT / "build" / "example_data"))
    ap.add_argument("--dense", action="store_true", help="also build the Dense index (needs BAAI/bge-m3)")
    ap.add_argument("--allow-download", action="store_true", help="allow downloading the embedding model")
    ap.add_argument("--force", action="store_true", help="replace an existing output created by this script")
    a = ap.parse_args(argv)
    docs, out = Path(a.docs), Path(a.out)
    if not any(docs.glob("*.md")):
        print(f"no .md files in {docs}")
        return 2
    if out.exists() and any(out.iterdir()):
        if not (a.force and (out / MARKER).exists()):
            print(f"{out} exists; use --force (only works on a directory created by this script)")
            return 2
        shutil.rmtree(out)
    corpus = out / "corpus"
    shutil.copytree(docs, corpus / "clean_md")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    if not a.allow_download:
        env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    steps = [["scripts/03_structure_split.py", "--input", rel(corpus / "clean_md"), "--output", rel(corpus / "draft_chunks.jsonl")],
             ["scripts/04_boundary_guard.py", "--input", rel(corpus / "draft_chunks.jsonl"), "--output", rel(corpus / "fixed_chunks.jsonl")],
             ["scripts/05_context_inheritance.py", "--input", rel(corpus / "fixed_chunks.jsonl"), "--output", rel(corpus / "final_chunks.jsonl")],
             ["scripts/06_build_dense_sparse_index.py", "--input", rel(corpus / "final_chunks.jsonl"), "--output-dir", rel(out / "index")]
             + (["--dense"] if a.dense else [])]
    for s in steps:
        r = subprocess.run([sys.executable, *s], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"step failed: {s[0]}\n{r.stdout[-1500:]}\n{r.stderr[-1500:]}")
            return 1
    bm, de = out / "index" / "sparse" / "bm25_index.pkl", out / "index" / "dense" / "dense_index.pkl"
    if not bm.exists():
        print("BM25 index was not built")
        return 1
    if a.dense and not de.exists():
        print("Dense index was NOT built: BAAI/bge-m3 is not available locally. Download it yourself, or rerun with --allow-download.")
        return 1
    rows = [json.loads(l) for l in (corpus / "final_chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    man = {"built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "docs": sorted(p.name for p in docs.glob("*.md")),
           "chunks_total": len(rows), "child_chunks": sum(1 for r in rows if not r.get("is_parent")),
           "final_chunks_sha256": sha(corpus / "final_chunks.jsonl"),
           # path-independent: chunk IDs, sources and texts only (source_path differs between build locations)
           "chunks_content_sha256": hashlib.sha256(json.dumps([[r["chunk_id"], r["source_file"], r["text"]] for r in rows],
                                                              ensure_ascii=False).encode("utf-8")).hexdigest(),
           "index": {p.name: sha(p) for p in (bm, de) if p.exists()}, "dense": de.exists()}
    (out / MARKER).write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(man, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
