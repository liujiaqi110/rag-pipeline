#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
04_boundary_guard.py — Boundary Guard (v0.1; v0.7 safe-extension fix P0-1/P0-2)

Fixes chunk boundaries:
  1. Extends truncated sentences to nearest sentence end
  2. Prepends context when dependency words appear at chunk start

Input:  chunks/draft_chunks.jsonl
Output: chunks/fixed_chunks.jsonl

Usage:
  python scripts/04_boundary_guard.py
"""

import argparse
import json
import re
from pathlib import Path
from typing import List, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHUNKS_DIR = PROJECT_ROOT / "chunks"
DRAFT_CHUNKS = CHUNKS_DIR / "draft_chunks.jsonl"
FIXED_CHUNKS = CHUNKS_DIR / "fixed_chunks.jsonl"

# Sentence-ending punctuation by language
SENTENCE_END = {
    "zh": set("。！？；："),
    "en": set(".!?;:"),
    "ja": set("。！？"),
}
ALL_SENTENCE_ENDS = set()
for chars in SENTENCE_END.values():
    ALL_SENTENCE_ENDS.update(chars)

# Words that signal dependency on previous context
DEPENDENCY_WORDS = {
    "zh": ["因此", "所以", "上述", "该方法", "这个", "它", "其", "此外", "另外", "总之", "综上所述", "换句话说", "基于此", "由此"],
    "en": ["this", "that", "therefore", "however", "thus", "hence", "consequently", "furthermore", "moreover", "in summary", "accordingly", "based on this", "it", "they"],
    "ja": ["これ", "それ", "その", "この", "あの", "したがって", "そのため", "さらに", "また", "このため", "これにより"],
}
ALL_DEP_WORDS = []
for words in DEPENDENCY_WORDS.values():
    ALL_DEP_WORDS.extend(words)

MAX_EXTENSION_CHARS = 300
PREPEND_SENTENCES = 2


def has_sentence_end(text: str) -> bool:
    """Check if text ends with a sentence-ending punctuation."""
    if not text:
        return True
    last = text.rstrip()[-1] if text.rstrip() else ""
    CLOSE_QUOTES = set(")>」』】》")
    return last in ALL_SENTENCE_ENDS or last in CLOSE_QUOTES or last in ('"', "'")


def find_sentence_end(text: str, max_chars: int = MAX_EXTENSION_CHARS) -> int:
    """Find the nearest sentence-ending position within max_chars."""
    search = text[:max_chars]
    best = -1
    for i, ch in enumerate(search):
        if ch in ALL_SENTENCE_ENDS:
            best = i + 1  # Include the punctuation
    return best if best > 0 else -1


def extract_last_sentences(text: str, n: int = PREPEND_SENTENCES) -> str:
    """Extract the last n sentences from text."""
    if not text:
        return ""
    # Split on sentence boundaries
    parts = re.split(r"(?<=[。！？；：\.\!\?\;\:])", text)
    parts = [p for p in parts if p.strip()]
    return "".join(parts[-n:]).strip() if parts else ""


def starts_with_dependency(text: str) -> bool:
    """Check if text starts with a dependency word."""
    stripped = text.lstrip()
    for word in ALL_DEP_WORDS:
        if stripped.startswith(word):
            return True
    return False


# --- v0.7 (P0-1 / P0-2): safe boundary extension -------------------------------------------
# Only strong sentence terminators. ':' / '：' are never sentence ends (model names like
# "qwen2.5:7b", "key: value"). ASCII terminators count only when followed by whitespace / end,
# so "<!-- image -->", "1.27.2", URLs and identifiers are never split.
SAFE_CJK_ENDS = set("。！？；")
SAFE_ASCII_ENDS = set(".!?;")
# Scanning the next chunk stops at the first structural boundary (paragraph, table row, heading,
# code fence, HTML comment): text is never borrowed across such a boundary.
STRUCTURE_STOP = re.compile(r"\n\s*\n|\n\s*\||\n\s*#|```|<!--|-->"
                            # v0.7b: a new list item / numbered heading also ends the block
                            r"|\n\s*(?:[-*+•]|o\s|\d{1,3}[.)、]\s?)")
MIN_REMAINING_CHARS = 20  # never shrink the next chunk below this much real text


def _real_text_len(text: str) -> int:
    return len(re.sub(r"<!--.*?-->|\s", "", text, flags=re.S))


def _in_url_or_code(text: str, pos: int) -> bool:
    """True if position pos sits inside a URL token or an inline `code` span."""
    if text[:pos].count("`") % 2 == 1:
        return True
    token_start = max(text.rfind(" ", 0, pos), text.rfind("\n", 0, pos)) + 1
    token = text[token_start:pos + 1].lower()
    return token.startswith(("http://", "https://", "www.")) or "://" in token


def find_safe_sentence_end(text: str, max_chars: int = MAX_EXTENSION_CHARS) -> int:
    """Position just after the NEAREST safe sentence end within the first structural block, or -1."""
    window = text[:max_chars]
    stop = STRUCTURE_STOP.search(window)
    if stop:
        window = window[:stop.start()]
    for i, ch in enumerate(window):
        if ch in SAFE_CJK_ENDS:
            if not _in_url_or_code(window, i):
                return i + 1
        elif ch in SAFE_ASCII_ENDS:
            nxt = window[i + 1] if i + 1 < len(window) else ""
            prev = window[i - 1] if i > 0 else ""
            if nxt and not nxt.isspace():
                continue  # "1.27", "a.b", "<!--", "!important"
            if ch == "." and prev.isdigit():
                continue  # ordinals ("2. 学习重点"), versions, decimals: never a sentence end (v0.7b)
            if ch == "!" and prev == "<":
                continue
            if _in_url_or_code(window, i):
                continue
            return i + 1
    return -1


def _last_line(text: str) -> str:
    lines = [l for l in text.rstrip().split("\n") if l.strip()]
    return lines[-1].strip() if lines else ""


def _first_line(text: str) -> str:
    lines = [l for l in text.lstrip().split("\n") if l.strip()]
    return lines[0].strip() if lines else ""


def unsafe_to_extend(chunk: Dict, next_chunk: Dict) -> str:
    """Reason why text must NOT be borrowed from next_chunk into chunk ('' = safe)."""
    if chunk.get("source_file") != next_chunk.get("source_file"):
        return "cross_document"
    text, next_text = chunk["text"], next_chunk["text"]
    if text.count("```") % 2 == 1:
        return "inside_code_block"
    if _last_line(text).startswith("|"):
        return "inside_table"
    if re.search(r"<!-*$|<!--(?:(?!-->).)*$", text, flags=re.S):
        return "inside_html_comment"
    first = _first_line(next_text)
    if first.startswith(("|", "```", "<!--", "-->", "#")) or re.match(r"^-+\s*image\s*-->", first):
        return "next_starts_with_structure"
    return ""


def fix_chunks(chunks: List[Dict]) -> List[Dict]:
    """Apply boundary guard to all chunks.

    v0.7: text is only borrowed from the next chunk of the SAME document, only up to the nearest
    safe sentence end inside the next chunk's first structural block, never from/into tables,
    code blocks or HTML comments, and never if it would leave the next chunk (almost) empty.
    When a boundary cannot be fixed safely it is left unchanged (boundary_skip_reason records why).
    """
    fixed = []

    for i, chunk in enumerate(chunks):
        text = chunk["text"]
        modified = False

        # 1. Extend truncated sentences (same document only, safe positions only)
        if not has_sentence_end(text) and i + 1 < len(chunks):
            next_chunk = chunks[i + 1]
            next_text = next_chunk["text"]
            reason = unsafe_to_extend(chunk, next_chunk)
            if not reason:
                end_pos = find_safe_sentence_end(next_text)
                if end_pos <= 0:
                    reason = "no_safe_sentence_end"
                elif _real_text_len(next_text[end_pos:]) < MIN_REMAINING_CHARS:
                    reason = "would_empty_next_chunk"
                else:
                    borrowed = next_text[:end_pos]
                    text = text + borrowed
                    chunks[i + 1]["text"] = next_text[end_pos:].lstrip()
                    modified = True
            if reason:
                chunk["boundary_skip_reason"] = reason

        # 2. Check dependency words at start (context only from the same document)
        if (starts_with_dependency(text) and i > 0
                and fixed[-1].get("source_file") == chunk.get("source_file")):
            prev_chunk = fixed[-1]
            context = extract_last_sentences(prev_chunk["text"])
            if context:
                chunk["context_from_previous"] = context
                modified = True
            else:
                chunk["context_from_previous"] = ""

        chunk["text"] = text
        chunk["boundary_fixed"] = modified
        fixed.append(chunk)

    return fixed


def main():
    parser = argparse.ArgumentParser(description="Boundary guard for chunks")
    parser.add_argument("--input", default=str(DRAFT_CHUNKS), help="Input JSONL")
    parser.add_argument("--output", default=str(FIXED_CHUNKS), help="Output JSONL")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print("04_boundary_guard.py — Boundary Guard v0.1")
    print("=" * 60)

    if not input_path.exists():
        print(f"Input not found: {input_path}")
        print("Run 03_structure_split.py first.")
        return

    chunks = []
    with open(input_path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))

    print(f"Loaded {len(chunks)} draft chunks")

    fixed = fix_chunks(chunks)
    fixed_count = sum(1 for c in fixed if c.get("boundary_fixed"))

    with open(output_path, "w", encoding="utf-8") as f:
        for chunk in fixed:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")

    print(f"Fixed: {fixed_count} chunk(s) had boundary issues")
    print(f"Output: {output_path}")


if __name__ == "__main__":
    main()
