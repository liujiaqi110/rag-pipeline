"""Parsing, validation, cleaning and chunking (unit level, no API)."""

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from kb_platform.ingest import chunker, parsers
from kb_platform.ingest.errors import IngestError
from kb_platform.ingest.manager import safe_filename, storage_root
from kb_platform.settings import PROJECT_ROOT, Settings
from kb_platform.tests.ingest_fixtures import corrupt_pdf, docx_doc, md_doc, pdf_doc, pdf_report, txt_doc


def chunks_of(name, data, doc_key="d_test"):
    p = parsers.parse(name, data)
    text, pages = chunker.clean(p)
    return p, text, chunker.chunk_text(text, pages, doc_key, "kb://t/v1")


class TestFourFormats(unittest.TestCase):
    def test_markdown(self):
        p, _, ch = chunks_of("a.md", md_doc("发布手册", "CANARY-MD-1"))
        self.assertEqual(p.format, "markdown")
        kids = [c for c in ch if not c.get("is_parent")]
        self.assertTrue(kids and all(c["source_file"] == "d_test" for c in ch))
        self.assertIn("发布流程", {c["section_heading"].split(" > ")[-1] for c in kids})
        self.assertTrue(all(len(c["chunk_id"]) == 12 for c in ch))
        self.assertTrue(all(c["paragraph"] >= 1 for c in kids))

    def test_txt_with_page_number_line_cleaned(self):
        p, text, ch = chunks_of("b.txt", txt_doc("CANARY-TXT-1"))
        self.assertEqual(p.format, "txt")
        self.assertNotIn("第 1 页", text)           # standalone page-number line removed (rag_config header_patterns)
        self.assertIn("CANARY-TXT-1", text)
        self.assertTrue(ch)

    def test_gb18030_text(self):
        p = parsers.parse("c.txt", "中文编码测试，内容足够长。".encode("gb18030"))
        self.assertIn("中文编码测试", p.text)

    def test_pdf_pages(self):
        p, text, ch = chunks_of("r.pdf", pdf_report("CANARY-PDF-1"))
        self.assertEqual((p.format, p.page_count), ("pdf", 2))
        self.assertNotIn("Page 2", text)
        kids = [c for c in ch if not c.get("is_parent")]
        pages = {c["page"] for c in kids}
        self.assertTrue(pages <= {1, 2} and pages, pages)
        by_page = {c["page"]: c["text"] for c in kids}
        if 2 in by_page:
            self.assertIn("Budget", by_page[2])
        self.assertTrue(any("page two" in c["text"] and c["page"] == 2 for c in kids) or len(kids) == 1)

    def test_docx_headings_and_table(self):
        p, text, ch = chunks_of("d.docx", docx_doc("CANARY-DOCX-1"))
        self.assertEqual(p.format, "docx")
        self.assertIn("# 采购制度说明", text)
        self.assertIn("## 供应商评估", text)
        self.assertIn("资质 | 营业执照", text)
        self.assertTrue(any("供应商评估" in (c.get("section_heading") or "") for c in ch))


class TestValidation(unittest.TestCase):
    def err(self, name, data):
        with self.assertRaises(IngestError) as cm:
            parsers.parse(name, data)
        return cm.exception.code

    def test_rejections(self):
        self.assertEqual(self.err("x.exe", b"MZ..."), "unsupported_format")
        self.assertEqual(self.err("x.doc", b"abc"), "unsupported_format")
        self.assertEqual(self.err("x.md", b""), "empty_file")
        self.assertEqual(self.err("x.pdf", b"MZ\x90\x00 not a pdf"), "format_mismatch")
        self.assertEqual(self.err("x.txt", b"abc\x00\x01\x02"), "format_mismatch")
        self.assertEqual(self.err("x.md", pdf_doc([["hello"]])), "format_mismatch")
        self.assertEqual(self.err("x.docx", b"PK\x03\x04garbage"), "format_mismatch")
        self.assertEqual(self.err("x.pdf", corrupt_pdf()), "parse_failed")
        self.assertEqual(self.err("x.pdf", pdf_doc([[]])), "no_text")
        self.assertEqual(self.err("x.txt", b"   \n\n  "), "no_text")

    def test_docx_zip_bomb_guard(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("word/document.xml", "<w/>")
            z.writestr("word/media/big.bin", b"\0" * (parsers.MAX_DOCX_UNCOMPRESSED + 1))
        self.assertEqual(self.err("bomb.docx", buf.getvalue()), "file_too_large")

    def test_errors_do_not_leak_content(self):
        try:
            parsers.parse("x.pdf", b"%PDF-1.4 SECRET-INNER-TEXT")
        except IngestError as e:
            self.assertNotIn("SECRET", e.message)

    def test_safe_filename(self):
        self.assertEqual(safe_filename("../../etc/passwd.md"), "passwd.md")
        self.assertEqual(safe_filename("C:\\Windows\\system32\\a.md"), "a.md")
        self.assertEqual(safe_filename("a<b>:c|?*.txt"), "a_b__c___.txt")
        self.assertEqual(safe_filename(""), "document")

    def test_storage_must_be_git_ignored(self):
        with self.assertRaises(RuntimeError):
            storage_root(Settings(storage_dir=str(PROJECT_ROOT / "kb_platform" / "uploads_here")))
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(storage_root(Settings(storage_dir=t)), Path(t).resolve())
        self.assertTrue(str(storage_root(Settings())).startswith(str((PROJECT_ROOT / "build").resolve())))


class TestReusesPipelineScripts(unittest.TestCase):
    def test_chunks_identical_to_batch_scripts(self):
        """Same chunk IDs / texts / parents as scripts/build_example_index.py (03→04→05) on the public examples."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("bei", PROJECT_ROOT / "scripts" / "build_example_index.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(mod.main(["--out", str(Path(t) / "o")]), 0)
            ref = [json.loads(l) for l in (Path(t) / "o" / "corpus" / "final_chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        mine = []
        for p in sorted((PROJECT_ROOT / "examples" / "docs").glob("*.md")):
            mine += chunker.chunk_text(p.read_text(encoding="utf-8"), [], p.name, "kb://x")
        key = lambda c: (c["chunk_id"], c["source_file"], c["text"], c.get("parent_id"), bool(c.get("is_parent")))  # noqa: E731
        self.assertEqual(sorted(map(key, ref)), sorted(map(key, mine)))


if __name__ == "__main__":
    unittest.main()
