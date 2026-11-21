"""Cleaning + structure chunking, REUSING the existing pipeline code unchanged.

    cleaned text → scripts/03 chunk_document → scripts/04 fix_chunks → scripts/05 enrich_chunks + build_parent_child

This is exactly what ``scripts/build_example_index.py`` runs (03 → 04 → 05) for one document; a test checks that the
chunk IDs and texts are identical to the batch scripts. The only additions are metadata the scripts leave empty:

* ``page``      — PDF page of the chunk start (located by searching the chunk text in the cleaned document),
* ``paragraph`` — 1-based paragraph ordinal (blank-line separated) of the chunk start,
* ``source_path`` — replaced by ``kb://<document id>/v<version>`` (the scripts write the temp file path, which must not
  leak into chunk metadata).

Cleaning (``rag_config.yaml`` → ``cleaning``) is deliberately conservative: NFC, line endings, trailing spaces,
standalone page-number lines (``header_patterns``), broken PDF lines, blank-line runs. Footer / watermark patterns
("机密", "Copyright", "草稿" …) are NOT applied because they would delete real content.
"""

from __future__ import annotations

import re
import tempfile
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ragas_eval.config import PROJECT_ROOT
from ragas_eval.dataset_adapter import _load_module

from .parsers import ParsedDocument

SENT_END = tuple("。！？；：.!?;:）)】」\"'")


@lru_cache(maxsize=1)
def pipeline_modules():
    s = PROJECT_ROOT / "scripts"
    return (_load_module(s / "03_structure_split.py", "kb_ingest_03"),
            _load_module(s / "04_boundary_guard.py", "kb_ingest_04"),
            _load_module(s / "05_context_inheritance.py", "kb_ingest_05"))


@lru_cache(maxsize=1)
def page_number_patterns() -> List[re.Pattern]:
    pats = [r"第\s*\d+\s*页", r"Page\s*\d+", r"\d+\s*/\s*\d+"]
    try:
        import yaml
        cfg = yaml.safe_load((PROJECT_ROOT / "config" / "rag_config.yaml").read_text(encoding="utf-8"))
        pats = cfg.get("cleaning", {}).get("header_patterns") or pats
    except Exception:  # noqa: BLE001 — yaml missing / unreadable: built-in copy of the same patterns
        pass
    return [re.compile(rf"^\s*(?:{p})\s*$", re.I) for p in pats]


def _is_cjk(ch: str) -> bool:
    return "一" <= ch <= "鿿"


def _merge_pdf_lines(text: str) -> str:
    out = []
    for para in re.split(r"\n{2,}", text):
        lines = [l for l in para.split("\n")]
        merged = lines[0] if lines else ""
        for nxt in lines[1:]:
            if not merged or merged.endswith(SENT_END) or re.match(r"^\s*(#|[-*•]|\d+[.、)])", nxt):
                merged += "\n" + nxt
            else:
                joiner = "" if (_is_cjk(merged[-1]) and nxt[:1] and _is_cjk(nxt[0])) else " "
                merged += joiner + nxt.lstrip()
        out.append(merged)
    return "\n\n".join(out)


def clean_segment(text: str, pdf: bool = False) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    pats = page_number_patterns()
    lines = [l.rstrip() for l in text.split("\n")]
    lines = [l for l in lines if not any(p.match(l) for p in pats)]
    text = "\n".join(lines)
    if pdf:
        text = _merge_pdf_lines(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def clean(doc: ParsedDocument) -> Tuple[str, List[Tuple[int, int]]]:
    if not doc.pages:
        return clean_segment(doc.text, pdf=doc.format == "pdf"), []
    bounds = [p[0] for p in doc.pages] + [len(doc.text)]
    parts, pages, pos = [], [], 0
    for (start, page_no), end in zip(doc.pages, bounds[1:]):
        seg = clean_segment(doc.text[start:end], pdf=True)
        if not seg:
            continue
        if parts:
            parts.append("\n\n")
            pos += 2
        pages.append((pos, page_no))
        parts.append(seg)
        pos += len(seg)
    return "".join(parts), pages


def _locate(text: str, core: str, cursor: int) -> Optional[int]:
    core = core.strip()
    for probe in (core[:40], core[len(core) // 2: len(core) // 2 + 30], core[:15]):
        probe = probe.strip()
        if len(probe) < 4:
            continue
        i = text.find(probe, cursor)
        if i < 0:
            i = text.find(probe)
        if i >= 0:
            return i
    return None


def _page_at(pages: List[Tuple[int, int]], offset: int) -> Optional[int]:
    page = None
    for start, no in pages:
        if start <= offset:
            page = no
        else:
            break
    return page


def chunk_text(cleaned: str, pages: List[Tuple[int, int]], doc_key: str, source_ref: str) -> List[Dict[str, Any]]:
    """Run the unchanged 03 → 04 → 05 code on one document. ``doc_key`` becomes ``source_file`` (and therefore the
    input of the chunk-ID hash), so IDs are unique per document."""
    m03, m04, m05 = pipeline_modules()
    with tempfile.TemporaryDirectory(prefix="kb_chunk_") as tmp:
        p = Path(tmp) / doc_key
        p.write_text(cleaned, encoding="utf-8")
        draft = m03.chunk_document(p)
    fixed = m04.fix_chunks(draft)
    final = m05.build_parent_child(m05.enrich_chunks(fixed), doc_key)
    cursor = 0
    first_child_page: Dict[str, Optional[int]] = {}
    for c in final:
        c["source_path"] = source_ref
        if c.get("is_parent"):
            continue
        core = c["text"]
        sec = c.get("section_heading")
        if sec and sec != "root" and core.startswith(f"[{sec}] "):
            core = core[len(sec) + 3:]
        off = _locate(cleaned, core, cursor)
        if off is not None:
            cursor = off
            c["page"] = _page_at(pages, off) if pages else None
            c["paragraph"] = cleaned.count("\n\n", 0, off) + 1
        else:
            c["page"], c["paragraph"] = None, None
        first_child_page.setdefault(c.get("parent_id"), c["page"])
    for c in final:
        if c.get("is_parent"):
            c["page"] = first_child_page.get(c["chunk_id"])
            c.setdefault("paragraph", None)
    return final
