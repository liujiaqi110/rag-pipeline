#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
06_build_dense_sparse_index.py — Dense + Sparse Index Builder (v0.1)

Builds BM25 sparse index (always available, pure Python).
Dense index is a placeholder — activates when embedding model is configured.

Input:  chunks/final_chunks.jsonl
Output: index/sparse/ (BM25) + index/dense/ (placeholder)

Usage:
  python scripts/06_build_dense_sparse_index.py
  python scripts/06_build_dense_sparse_index.py --dense  # Try dense if model available

Dependencies:
  pip install rank-bm25        # For sparse BM25
  pip install sentence-transformers  # For dense embeddings
  pip install faiss-cpu        # For dense vector index (optional)
"""

import argparse
import json
import pickle
import re
import math
from collections import defaultdict
from pathlib import Path
from typing import List, Dict, Tuple, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = PROJECT_ROOT / "chunks"
INDEX_DIR = PROJECT_ROOT / "index"
FINAL_CHUNKS = CHUNKS_DIR / "final_chunks.jsonl"

# v0.7 (P0-4 prep): isolated output for experiment builds. Production default (no --output-dir) is unchanged.
PROTECTED_DIRS = [INDEX_DIR, CHUNKS_DIR, PROJECT_ROOT / "raw_docs", PROJECT_ROOT / "parsed_md",
                  PROJECT_ROOT / "clean_md", PROJECT_ROOT / "eval", PROJECT_ROOT / "ragas_eval" / "ground_truth"]
INDEX_FILES = (("sparse", "bm25_index.pkl"), ("dense", "dense_index.pkl"))


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def resolve_output_root(output_dir: Optional[str]) -> Tuple[Path, bool]:
    """Return (index root, is_production).

    None -> production index/ (legacy behaviour). An explicit --output-dir is experiment mode and must
    not resolve into (or contain) index/ or any other protected project directory, and must not already
    hold index files (no overwrite)."""
    if output_dir is None:
        return INDEX_DIR, True
    root = Path(output_dir).resolve()
    for d in PROTECTED_DIRS:
        if _is_inside(root, d) or _is_inside(d, root):
            raise ValueError(f"experiment output {root} overlaps protected directory {d}; choose e.g. experiments/<name>/index")
    existing = [str(root / sub / name) for sub, name in INDEX_FILES if (root / sub / name).exists()]
    if existing:
        raise FileExistsError(f"refusing to overwrite existing index files: {existing}")
    return root, False


def _guard_write(path: Path, allow_overwrite: bool) -> None:
    if not allow_overwrite and path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")


def tokenize(text: str) -> List[str]:
    """Simple multilingual tokenizer: CJK char-level, Latin word-level."""
    tokens = []
    # Split on whitespace first
    for segment in text.split():
        # Check if segment is mostly CJK
        cjk_count = sum(1 for ch in segment if "一" <= ch <= "鿿")
        if cjk_count > len(segment) * 0.3:
            # Character-level for CJK
            tokens.extend(ch for ch in segment if not ch.isspace())
        else:
            # Word-level for Latin
            word = re.sub(r"[^\w]", "", segment.lower())
            if word and len(word) > 1:
                tokens.append(word)
    return tokens


class SimpleBM25:
    """
    Pure Python BM25 implementation.
    No external dependencies required.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: List[Dict] = []
        self.doc_tokens: List[List[str]] = []
        self.doc_lengths: List[int] = []
        self.avg_doc_len: float = 0
        self.idf: Dict[str, float] = {}
        self.N: int = 0

    def index(self, documents: List[Dict]):
        """Build BM25 index from documents."""
        self.documents = documents
        self.doc_tokens = [tokenize(doc.get("text", "")) for doc in documents]
        self.doc_lengths = [len(tokens) for tokens in self.doc_tokens]
        self.N = len(documents)
        self.avg_doc_len = sum(self.doc_lengths) / max(self.N, 1)

        # Compute DF (document frequency) for each term
        df = defaultdict(int)
        for tokens in self.doc_tokens:
            seen = set(tokens)
            for token in seen:
                df[token] += 1

        # Compute IDF
        self.idf = {}
        for token, freq in df.items():
            self.idf[token] = math.log((self.N - freq + 0.5) / (freq + 0.5) + 1)

    def search(self, query: str, top_k: int = 10) -> List[Dict]:
        """Search and return top-k documents with scores."""
        query_tokens = tokenize(query)
        scores = []

        for idx, doc_tokens in enumerate(self.doc_tokens):
            score = 0.0
            doc_len = self.doc_lengths[idx]

            for token in query_tokens:
                if token not in self.idf:
                    continue
                tf = doc_tokens.count(token)
                numerator = tf * (self.k1 + 1)
                denominator = tf + self.k1 * (1 - self.b + self.b * doc_len / max(self.avg_doc_len, 1))
                score += self.idf[token] * numerator / max(denominator, 1e-10)

            if score > 0:
                scores.append({
                    "chunk_id": self.documents[idx].get("chunk_id"),
                    "score": score,
                    "text_preview": self.documents[idx].get("text", "")[:200],
                })

        scores.sort(key=lambda x: x["score"], reverse=True)
        return scores[:top_k]


def build_sparse_index(chunks: List[Dict], output_dir: Path, allow_overwrite: bool = True):
    """Build BM25 sparse index."""
    _guard_write(output_dir / "bm25_index.pkl", allow_overwrite)
    print("\n--- Sparse Index (BM25) ---")
    bm25 = SimpleBM25()
    bm25.index(chunks)
    print(f"  Documents indexed: {bm25.N}")
    print(f"  Vocabulary size:   {len(bm25.idf)}")
    print(f"  Avg doc length:    {bm25.avg_doc_len:.1f} tokens")

    # Save
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "bm25_index.pkl"
    with open(index_path, "wb") as f:
        pickle.dump(bm25, f)
    print(f"  Saved: {index_path}")

    # Quick self-test
    if chunks:
        sample = chunks[0]["text"][:100]
        results = bm25.search(sample, top_k=3)
        print(f"  Self-test: query='{sample[:50]}...' -> {len(results)} results")


def build_retrieval_text(chunk: Dict) -> str:
    """v0.3: build text for embedding with section context."""
    section = chunk.get("section_heading", "")
    text = chunk.get("text", "")
    if section and section != "root":
        return section + "\n\n" + text
    return text


def build_dense_index(chunks: List[Dict], output_dir: Path, allow_overwrite: bool = True):
    """
    v0.3: Build real dense embedding index using sentence-transformers.
    Saves dense_index.pkl with embeddings + metadata for retrieval.
    """
    # checked before the model is loaded (the encode step below swallows exceptions)
    _guard_write(output_dir / "dense_index.pkl", allow_overwrite)
    print("\n--- Dense Index (v0.3) ---")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load config
    cfg_embed = {}
    try:
        import yaml
        config_path = PROJECT_ROOT / "config" / "rag_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
            cfg_embed = cfg.get("embedding", {})
    except Exception:
        pass

    model_name = cfg_embed.get("model_name", "BAAI/bge-m3")
    fallback_name = cfg_embed.get("fallback_model_name",
                                   "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    normalize = cfg_embed.get("normalize_embeddings", True)
    batch_size = cfg_embed.get("batch_size", 16)

    # Check dependency
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print("  SKIP: sentence-transformers not installed.")
        print("  Install: pip install sentence-transformers")
        print(f"  Then re-run: python scripts/06_build_dense_sparse_index.py --dense")
        return

    # Try loading model
    model = None
    for name in [model_name, fallback_name]:
        try:
            print(f"  Loading: {name}")
            model = SentenceTransformer(name)
            print(f"  Model loaded: {name}")
            break
        except Exception as e:
            print(f"  Failed to load {name}: {e}")
            continue

    if model is None:
        print("  ERROR: Could not load any embedding model. Dense index NOT built.")
        return

    # Build retrieval texts
    texts = [build_retrieval_text(c) for c in chunks]
    print(f"  Encoding {len(texts)} documents (batch_size={batch_size})...")

    try:
        import numpy as np
        embeddings = model.encode(
            texts,
            show_progress_bar=True,
            batch_size=batch_size,
            normalize_embeddings=normalize,
        )
        dim = embeddings.shape[1]

        # Build index dict
        dense_index = {
            "embeddings": embeddings,
            "chunk_ids": [c.get("chunk_id") for c in chunks],
            "parent_ids": [c.get("parent_id") for c in chunks],
            "source_files": [c.get("source_file") for c in chunks],
            "section_headings": [c.get("section_heading") for c in chunks],
            "texts": texts,
            "model_name": name,
            "normalize_embeddings": normalize,
            "embedding_dim": dim,
            "num_documents": len(chunks),
        }

        output_path = output_dir / "dense_index.pkl"
        with open(output_path, "wb") as f:
            pickle.dump(dense_index, f)

        print(f"  Dense index built:")
        print(f"    Model: {name}")
        print(f"    Documents: {len(chunks)}")
        print(f"    Dimension: {dim}")
        print(f"    Normalized: {normalize}")
        print(f"    Saved: {output_path}")

    except Exception as e:
        print(f"  FAILED to build dense index: {e}")
        print(f"  BM25 index is unaffected.")


def main():
    parser = argparse.ArgumentParser(description="Build dense + sparse index")
    parser.add_argument("--input", default=str(FINAL_CHUNKS), help="Input JSONL")
    parser.add_argument("--dense", action="store_true", help="Attempt dense index")
    parser.add_argument("--output-dir", default=None,
                        help="v0.7: experiment mode — write index files under this directory (sparse/, dense/). "
                             "Must not be or overlap index/ or other protected dirs; existing index files are never overwritten. "
                             "Omit for the legacy production behaviour (writes index/).")
    parser.add_argument("--check-paths", action="store_true",
                        help="v0.7: only validate input/output paths and print the plan; build nothing")
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.is_dir():
        input_path = input_path / "final_chunks.jsonl"
    try:
        out_root, production = resolve_output_root(args.output_dir)
    except (ValueError, FileExistsError) as e:
        print(f"ERROR: {e}")
        raise SystemExit(2)

    print("=" * 60)
    print("06_build_dense_sparse_index.py — Index Builder v0.1")
    print("=" * 60)
    print(f"Input:  {input_path}")
    print(f"Output: {out_root} ({'PRODUCTION index/' if production else 'experiment, no-overwrite'})")
    if args.check_paths:
        print(f"  sparse -> {out_root / 'sparse' / 'bm25_index.pkl'}")
        print(f"  dense  -> {out_root / 'dense' / 'dense_index.pkl'} ({'requested' if args.dense else 'not requested'})")
        print(f"  input exists: {input_path.exists()}")
        print("check-paths only: nothing built")
        return

    if not input_path.exists():
        print(f"Input not found: {input_path}")
        print("Run 05_context_inheritance.py first.")
        return

    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))

    # v0.1.1: default to child-only indexing
    index_parents = False
    try:
        import yaml
        cfg_path = PROJECT_ROOT / "config" / "rag_config.yaml"
        if cfg_path.exists():
            with open(cfg_path, encoding="utf-8") as f_cfg:
                cfg = yaml.safe_load(f_cfg)
            index_parents = cfg.get("index", {}).get("index_parent_chunks", False)
    except Exception:
        pass

    total_chunks = len(chunks)
    if not index_parents:
        chunks = [c for c in chunks if not c.get("is_parent")]
    print(f"Loaded {len(chunks)} chunks for indexing ({total_chunks} total, "
          f"parents={'included' if index_parents else 'excluded'})")

    # Always build sparse (no deps needed)
    build_sparse_index(chunks, out_root / "sparse", allow_overwrite=production)

    # Dense is optional
    if args.dense:
        build_dense_index(chunks, out_root / "dense", allow_overwrite=production)
    else:
        print("\n--- Dense Index ---")
        print("  SKIP: use --dense flag to attempt dense index build.")
        print(f"  Run: python scripts/06_build_dense_sparse_index.py --dense")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
