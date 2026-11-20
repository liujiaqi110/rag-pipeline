#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
05_context_inheritance.py — Context Inheritance (v0.1)

Enriches each chunk with metadata and heading context.
Supports parent-child chunking for better retrieval+context.

Input:  chunks/fixed_chunks.jsonl
Output: chunks/final_chunks.jsonl

Usage:
  python scripts/05_context_inheritance.py
"""

import argparse
import json
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = PROJECT_ROOT / "chunks"
FIXED_CHUNKS = CHUNKS_DIR / "fixed_chunks.jsonl"
FINAL_CHUNKS = CHUNKS_DIR / "final_chunks.jsonl"

CLEANING_VERSION = "v0.1"

REQUIRED_METADATA = [
    "chunk_id", "source_file", "source_path", "page",
    "title", "section_heading", "parent_heading",
    "language", "cleaning_version", "parent_id",
]

PARENT_SIZE = 2400
CHILD_SIZE = 800


def detect_language(text: str) -> str:
    """Simple language detection based on character ranges."""
    cjk = len(re.findall(r"[一-鿿]", text))
    kana = len(re.findall(r"[぀-ヿ]", text))
    latin = len(re.findall(r"[a-zA-Z]", text))

    if kana > cjk * 0.3:
        return "ja"
    if cjk > latin:
        return "zh"
    return "en"


def extract_title_from_heading(heading: str) -> str:
    """Extract document title from the top-level heading."""
    if not heading or heading == "root":
        return "Untitled"
    parts = heading.split(" > ")
    return parts[0] if parts else "Untitled"


def build_parent_child(chunks: List[Dict], source_file: str) -> List[Dict]:
    """Group child chunks under parent sections."""
    result = []

    # Group by section
    sections: Dict[str, List[Dict]] = {}
    for chunk in chunks:
        key = chunk.get("section_heading", "root")
        if key not in sections:
            sections[key] = []
        sections[key].append(chunk)

    parent_idx = 0
    for section_heading, section_chunks in sections.items():
        parent_id = hashlib.md5(
            f"{source_file}:parent:{parent_idx}".encode()
        ).hexdigest()[:12]

        # Create parent chunk (full section text)
        parent_text = "\n\n".join(c["text"] for c in section_chunks)
        parent_chunk = {
            "chunk_id": parent_id,
            "parent_id": None,
            "is_parent": True,
            "source_file": source_file,
            "section_heading": section_heading,
            "text": parent_text[:PARENT_SIZE],
            "char_count": len(parent_text[:PARENT_SIZE]),
        }
        result.append(parent_chunk)

        # Create child chunks
        for child in section_chunks:
            child["parent_id"] = parent_id
            child["is_parent"] = False
        result.extend(section_chunks)
        parent_idx += 1

    return result


def enrich_chunks(chunks: List[Dict]) -> List[Dict]:
    """Add metadata and heading context to each chunk."""
    enriched = []

    for i, chunk in enumerate(chunks):
        # Language detection
        lang = detect_language(chunk["text"])

        # Extract title from heading
        title = extract_title_from_heading(chunk.get("section_heading", ""))

        # Fill metadata
        chunk["language"] = lang
        chunk["title"] = chunk.get("title") or title
        chunk["cleaning_version"] = CLEANING_VERSION
        chunk["parent_id"] = chunk.get("parent_id")
        chunk["page"] = chunk.get("page")
        chunk["source_path"] = chunk.get("source_path", "")

        # Regenerate chunk_id to be stable
        chunk["chunk_id"] = hashlib.md5(
            f"{chunk['source_file']}:{i}:{chunk.get('section_heading', 'root')}".encode()
        ).hexdigest()[:12]

        # v0.1.1: validate heading consistency — chunk text should not start with
        # a different section's heading
        section = chunk.get("section_heading", "")
        text = chunk.get("text", "")
        heading_in_text = re.match(r"^#{1,6}\s+(.+)", text.lstrip())
        if heading_in_text and section and section != "root":
            heading_in_section = section.split(" > ")[-1] if " > " in section else section
            text_heading = heading_in_text.group(1).strip() if heading_in_text else ""
            # If chunk text has a heading that doesn't match its section, flag it
            if text_heading and text_heading not in heading_in_section:
                chunk["heading_mismatch"] = True
                chunk["heading_mismatch_detail"] = (
                    f"chunk section='{heading_in_section}' but text starts with heading '{text_heading}'"
                )
            else:
                chunk["heading_mismatch"] = False

        # Prepend heading context to chunk text for retrieval
        if section and section != "root":
            prefix = f"[{section}] "
            if not chunk["text"].startswith(prefix):
                chunk["text"] = prefix + chunk["text"]

        # Ensure all required metadata exists
        for field in REQUIRED_METADATA:
            if field not in chunk:
                chunk[field] = None

        enriched.append(chunk)

    return enriched


def main():
    parser = argparse.ArgumentParser(description="Context inheritance for chunks")
    parser.add_argument("--input", default=str(FIXED_CHUNKS), help="Input JSONL")
    parser.add_argument("--output", default=str(FINAL_CHUNKS), help="Output JSONL")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("05_context_inheritance.py — Context Inheritance v0.1")
    print("=" * 60)

    if not input_path.exists():
        print(f"Input not found: {input_path}")
        print("Run 04_boundary_guard.py first.")
        return

    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} fixed chunks")

    # Group by source file
    by_source: Dict[str, List[Dict]] = {}
    for chunk in chunks:
        src = chunk.get("source_file", "unknown")
        by_source.setdefault(src, []).append(chunk)

    all_final = []
    for src, src_chunks in by_source.items():
        # Enrich metadata
        enriched = enrich_chunks(src_chunks)
        # Build parent-child
        final = build_parent_child(enriched, src)
        all_final.extend(final)
        parent_count = sum(1 for c in final if c.get("is_parent"))
        child_count = sum(1 for c in final if not c.get("is_parent"))
        print(f"  {src}: {parent_count} parents + {child_count} children")

    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in all_final:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"\nDone. {len(all_final)} total chunks -> {output_path}")


if __name__ == "__main__":
    main()
