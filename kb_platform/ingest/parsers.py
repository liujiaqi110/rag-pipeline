"""Format detection, validation and text extraction for Markdown / TXT / PDF / DOCX.

* The format is decided by the extension AND checked against the content (magic bytes / UTF-8 decoding); a mismatch is
  rejected (``format_mismatch``), so e.g. an executable renamed to .pdf never reaches a parser.
* DOCX is a ZIP archive: the declared uncompressed size is checked before parsing (zip-bomb guard).
* Output is Markdown-ish text plus a page map ``[(char_offset, page_no), ...]`` (PDF only) so chunks can carry pages.
* DOCX headings (Title / Heading 1..6 styles) become ``#`` headings so the existing heading-aware chunker can use them;
  tables become ``a | b | c`` lines.
* Scanned PDFs without a text layer fail with ``no_text`` (OCR is not supported); encrypted PDFs with ``pdf_encrypted``.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .errors import IngestError

FORMATS = {".md": "markdown", ".markdown": "markdown", ".txt": "txt", ".pdf": "pdf", ".docx": "docx"}
MAX_DOCX_UNCOMPRESSED = 64 * 1024 * 1024


@dataclass
class ParsedDocument:
    text: str
    format: str
    pages: List[Tuple[int, int]] = field(default_factory=list)  # (start offset in text, 1-based page number)
    page_count: Optional[int] = None


def detect_format(filename: str, data: bytes) -> str:
    m = re.search(r"(\.[A-Za-z0-9]+)$", filename or "")
    ext = m.group(1).lower() if m else ""
    fmt = FORMATS.get(ext)
    if fmt is None:
        raise IngestError("unsupported_format", "只支持 .md / .txt / .pdf / .docx 文件", 415)
    if not data:
        raise IngestError("empty_file", "文件为空")
    if fmt == "pdf" and not data.startswith(b"%PDF-"):
        raise IngestError("format_mismatch", "文件内容不是 PDF", 415)
    if fmt == "docx":
        if not data.startswith(b"PK"):
            raise IngestError("format_mismatch", "文件内容不是 DOCX", 415)
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = set(z.namelist())
                if "word/document.xml" not in names:
                    raise IngestError("format_mismatch", "文件内容不是 DOCX", 415)
                if sum(i.file_size for i in z.infolist()) > MAX_DOCX_UNCOMPRESSED:
                    raise IngestError("file_too_large", "DOCX 解压后过大", 413)
        except zipfile.BadZipFile:
            raise IngestError("format_mismatch", "文件内容不是 DOCX", 415) from None
    if fmt in ("markdown", "txt"):
        if b"\x00" in data[:8192]:
            raise IngestError("format_mismatch", "文本文件中含有二进制内容", 415)
        if data.startswith((b"%PDF-", b"PK\x03\x04", b"MZ", b"\x7fELF")):
            raise IngestError("format_mismatch", "文件内容与扩展名不符", 415)
    return fmt


def _decode_text(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise IngestError("decode_failed", "无法识别文本编码（支持 UTF-8 / GB18030）")


def _parse_pdf(data: bytes) -> ParsedDocument:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise IngestError("parser_unavailable", "服务器缺少 PDF 解析依赖（pypdf）", 503) from None
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise IngestError("pdf_encrypted", "不支持加密的 PDF")
        parts, pages, pos = [], [], 0
        for i, page in enumerate(reader.pages, 1):
            t = (page.extract_text() or "").strip()
            if not t:
                continue
            if parts:
                parts.append("\n\n")
                pos += 2
            pages.append((pos, i))
            parts.append(t)
            pos += len(t)
        n = len(reader.pages)
    except IngestError:
        raise
    except Exception:  # noqa: BLE001 — malformed PDF: never leak parser internals
        raise IngestError("parse_failed", "PDF 解析失败，文件可能已损坏") from None
    text = "".join(parts)
    if not text.strip():
        raise IngestError("no_text", "PDF 中没有可提取的文字（扫描件需要先 OCR）")
    return ParsedDocument(text=text, format="pdf", pages=pages, page_count=n)


_HEADING_STYLE = re.compile(r"^(?:Heading|标题)\s*([1-6])$", re.I)


def _parse_docx(data: bytes) -> ParsedDocument:
    try:
        import docx  # python-docx
    except ImportError:
        raise IngestError("parser_unavailable", "服务器缺少 DOCX 解析依赖（python-docx）", 503) from None
    try:
        d = docx.Document(io.BytesIO(data))
        out: List[str] = []
        body = d.element.body
        for child in body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                p = docx.text.paragraph.Paragraph(child, d)
                t = p.text.strip()
                if not t:
                    continue
                style = (p.style.name if p.style is not None else "") or ""
                m = _HEADING_STYLE.match(style)
                if style.lower() == "title":
                    out.append("# " + t)
                elif m:
                    out.append("#" * int(m.group(1)) + " " + t)
                else:
                    out.append(t)
            elif tag == "tbl":
                tbl = docx.table.Table(child, d)
                rows = [" | ".join(c.text.strip() for c in r.cells) for r in tbl.rows]
                rows = [r for r in rows if r.replace("|", "").strip()]
                if rows:
                    out.append("\n".join(rows))
    except Exception:  # noqa: BLE001
        raise IngestError("parse_failed", "DOCX 解析失败，文件可能已损坏") from None
    text = "\n\n".join(out)
    if not text.strip():
        raise IngestError("no_text", "DOCX 中没有文字内容")
    return ParsedDocument(text=text, format="docx")


def parse(filename: str, data: bytes) -> ParsedDocument:
    fmt = detect_format(filename, data)
    if fmt == "pdf":
        return _parse_pdf(data)
    if fmt == "docx":
        return _parse_docx(data)
    text = _decode_text(data)
    if not text.strip():
        raise IngestError("no_text", "文件中没有文字内容")
    return ParsedDocument(text=text, format=fmt)
