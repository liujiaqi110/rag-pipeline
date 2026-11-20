#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
03_structure_split.py — Structure-Aware Chunker (v0.1)

Splits cleaned Markdown into chunks by:
  1. Heading boundaries (# ## ### ...)
  2. Paragraph breaks
  3. Fixed-size fallback (RecursiveCharacterTextSplitter equivalent)

Output: chunks/draft_chunks.jsonl

Usage:
  python scripts/03_structure_split.py
"""

import argparse
import json
import re
import hashlib
from pathlib import Path
from typing import List, Dict, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLEAN_MD = PROJECT_ROOT / "clean_md"
CHUNKS_DIR = PROJECT_ROOT / "chunks"
DRAFT_CHUNKS = CHUNKS_DIR / "draft_chunks.jsonl"

# Defaults (overridden by config if available)
CHUNK_SIZE = 800
CHUNK_OVERLAP = 160
MIN_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 1200


def load_config():
    try:
        import yaml
        config_path = PROJECT_ROOT / "config" / "rag_config.yaml"
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                return yaml.safe_load(f).get("chunking", {})
    except Exception:
        pass
    return {}


def split_by_headings(text: str) -> List[Dict]:
    """Split text by markdown headings. Each section gets its heading path."""
    # v0.1.1: normalize CRLF → LF to prevent \r from breaking blank line detection
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    sections = []
    lines = text.split("\n")
    current_lines = []
    heading_stack: List[str] = []  # Current heading path
    current_heading = ""

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading_match:
            # Save previous section
            if current_lines:
                section_text = "\n".join(current_lines).strip()
                if section_text:
                    sections.append({
                        "text": section_text,
                        "heading": current_heading or "root",
                        "heading_path": list(heading_stack),
                    })

            level = len(heading_match.group(1))
            heading_text = heading_match.group(2).strip()
            # Update heading stack
            heading_stack = heading_stack[:level - 1] + [heading_text]
            current_heading = " > ".join(heading_stack)
            current_lines = [line]
        else:
            current_lines.append(line)

    # Last section
    if current_lines:
        section_text = "\n".join(current_lines).strip()
        if section_text:
            sections.append({
                "text": section_text,
                "heading": current_heading or "root",
                "heading_path": list(heading_stack) if heading_stack else [],
            })

    return sections


def split_by_paragraphs(text: str) -> List[str]:
    """Split text by paragraph breaks (double newlines)."""
    paragraphs = re.split(r"\n\s*\n", text)
    return [p.strip() for p in paragraphs if p.strip()]


def fixed_size_split(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> List[str]:
    """
    Recursive character splitter equivalent.
    Tries to split on natural boundaries: \n\n > \n > 。 > . > space > char.
    """
    if len(text) <= size:
        return [text] if text.strip() else []

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))

        if end < len(text):
            # Try to find a natural break point
            for sep in ["\n\n", "\n", "。", ". ", " ", ""]:
                pos = text.rfind(sep, start, end)
                if pos > start + size // 2:
                    end = pos + len(sep)
                    break

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap

    return chunks


def _fix_heading_adhesion(text: str) -> str:
    """v0.1.1: ensure blank line after every markdown heading."""
    lines = text.split("\n")
    fixed = []
    for i, line in enumerate(lines):
        fixed.append(line)
        # If this is a heading line and the next line is non-empty content
        if re.match(r"^#{1,6}\s+", line) and i + 1 < len(lines):
            next_line = lines[i + 1]
            if next_line.strip() and not next_line.startswith("#"):
                fixed.append("")  # insert blank line separator
    return "\n".join(fixed)


def chunk_document(filepath: Path) -> List[Dict]:
    """Chunk a single document."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    # v0.1.1: normalize CRLF + fix heading adhesion
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _fix_heading_adhesion(text)
    source_name = filepath.name
    chunks = []
    chunk_idx = 0

    # Level 1: Split by headings
    sections = split_by_headings(text)

    for section in sections:
        section_text = section["text"]
        heading = section["heading"]
        heading_path = section["heading_path"]

        # Level 2: Split by paragraphs within each heading section
        paragraphs = split_by_paragraphs(section_text)

        # v0.1.1: merge heading-only paragraphs with the next paragraph
        merged_paragraphs = []
        i = 0
        while i < len(paragraphs):
            para = paragraphs[i]
            if re.match(r"^#{1,6}\s+", para) and i + 1 < len(paragraphs):
                # Heading followed by body → merge with \n\n
                merged_paragraphs.append(para + "\n\n" + paragraphs[i + 1])
                i += 2
            else:
                merged_paragraphs.append(para)
                i += 1
        paragraphs = merged_paragraphs

        for para in paragraphs:
            # Level 3: Fixed-size split if paragraph is too large
            if len(para) > MAX_CHUNK_SIZE:
                sub_chunks = fixed_size_split(para)
            else:
                sub_chunks = [para]

            for sub in sub_chunks:
                if len(sub) < MIN_CHUNK_SIZE and chunks:
                    # Merge with previous chunk
                    chunks[-1]["text"] += "\n\n" + sub
                    continue

                chunk_id = hashlib.md5(
                    f"{source_name}:{chunk_idx}:{sub[:50]}".encode()
                ).hexdigest()[:12]

                chunks.append({
                    "chunk_id": chunk_id,
                    "source_file": source_name,
                    "source_path": str(filepath),
                    "page": None,  # Filled by 05
                    "title": None,  # Filled by 05
                    "section_heading": heading,
                    "parent_heading": heading_path[-2] if len(heading_path) >= 2 else (heading_path[-1] if heading_path else None),
                    "language": "auto",  # Filled by 05
                    "text": sub,
                    "char_count": len(sub),
                    "chunk_index": chunk_idx,
                })
                chunk_idx += 1

    # Merge undersized chunks at the end
    return chunks


def main():
    parser = argparse.ArgumentParser(description="Structure-aware chunking")
    parser.add_argument("--input", default=str(CLEAN_MD), help="Input directory")
    parser.add_argument("--output", default=str(DRAFT_CHUNKS), help="Output JSONL")
    parser.add_argument("--size", type=int, default=CHUNK_SIZE, help="Target chunk size")
    parser.add_argument("--overlap", type=int, default=CHUNK_OVERLAP, help="Chunk overlap")
    args = parser.parse_args()

    input_dir = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("03_structure_split.py — Structure-Aware Chunker v0.1")
    print("=" * 60)
    print(f"Input:     {input_dir}")
    print(f"Output:    {output_path}")
    print(f"Chunk size: {args.size}, Overlap: {args.overlap}")
    print()

    files = sorted(input_dir.glob("*.md"))
    if not files:
        print("No .md files. Run 02_clean_text.py first.")
        return

    all_chunks = []
    for fp in files:
        chunks = chunk_document(fp)
        all_chunks.extend(chunks)
        print(f"  {fp.name}: {len(chunks)} chunks")

    # Write JSONL
    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"\nDone. {len(all_chunks)} total chunks -> {output_path}")


if __name__ == "__main__":
    main()
