"""Synthetic test files in all four formats (generated in memory — no external or private documents) and an app
harness with a temp storage directory.

The embedding model used here is a deterministic HASHING FAKE (``FakeEmbedder``) — it exercises the Dense code path
(index layout, candidate-stage ACL, RRF) but is NOT a semantic model. Real bge-m3 runs are in
``tests_local/test_kb_platform_real.py`` and are reported separately.
"""

from __future__ import annotations

import io
import json
import shutil
import tempfile
import zlib
from pathlib import Path
from typing import List

from kb_platform.ingest.manager import IndexManager
from kb_platform.rag_backend import AnswerCache, RagBackend
from kb_platform.settings import Settings
from kb_platform.tests.helpers import ADMIN, ApiCase
from ragas_eval.tests.acl_fixtures import RecordingLLM, embed


class FakeEmbedder:
    """Hashing bag-of-characters vectors (NOT semantic). Same call signature as SentenceTransformer.encode."""

    name = "fake-hash-96"

    def __init__(self):
        self.calls = 0

    def encode(self, texts, batch_size=16, normalize_embeddings=True, show_progress_bar=False):
        import numpy as np
        self.calls += 1
        return np.asarray([embed(t) for t in texts], dtype="float32")


def md_doc(title: str, canary: str, topic: str = "发布流程") -> bytes:
    return (f"# {title}\n\n本文是为测试编写的合成文档，标记：{canary}。\n\n## {topic}\n\n"
            f"{topic}要求先完成自动化测试，再进行同行评审，最后在预发环境演练回滚。标记 {canary} 用于检测泄漏。"
            f"这一段文字足够长，可以形成独立的 Chunk，用于验证检索与引用流程是否正确工作。\n\n"
            f"## 预算与审批\n\n与{topic}相关的预算由部门负责人审批，超过百分之十的变更需要提交调整单。{canary}\n").encode("utf-8")


def txt_doc(canary: str) -> bytes:
    return (f"办公室门禁说明\n\n员工使用工牌进入办公区，访客需要前台登记。标记 {canary}。\n\n"
            f"第 1 页\n\n夜间进入需要提前申请，安保人员会核对申请记录与工牌信息，发布流程不涉及门禁。{canary}\n").encode("utf-8")


def _pdf_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def pdf_doc(pages: List[List[str]]) -> bytes:
    """Minimal valid PDF (Helvetica, one text line per entry). ASCII only: CJK needs embedded fonts."""
    objs = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>")
    for i, lines in enumerate(pages):
        content = "BT /F1 12 Tf 72 720 Td 14 TL " + " ".join(f"({_pdf_escape(l)}) Tj T*" for l in lines) + " ET"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> "
                    f"/Contents {4 + 2 * i} 0 R >>")
        objs.append(("stream", content))
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        if isinstance(o, tuple):
            data = o[1].encode("latin-1")
            out.write(f"{i} 0 obj\n<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream\nendobj\n")
        else:
            out.write(f"{i} 0 obj\n{o}\nendobj\n".encode())
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def pdf_report(canary: str) -> bytes:
    long1 = ("The deployment checklist requires automated tests, peer review and a rehearsed rollback before any "
             "release window is booked by the on-call engineer.")
    long2 = ("Budget changes above ten percent need an adjustment form approved by the finance lead before the "
             "quarterly archive is closed.")
    return pdf_doc([["Quarterly Engineering Report", f"Marker {canary} page one.", long1, long1],
                    ["Page 2", "Budget Section", f"Marker {canary} page two.", long2, long2]])


def docx_doc(canary: str) -> bytes:
    import docx
    d = docx.Document()
    d.add_heading("采购制度说明", level=1)
    d.add_paragraph(f"本文是合成的采购制度文档，标记 {canary}。采购申请需要部门负责人审批，金额较大时还需要财务复核。"
                    "这一段内容足够长，可以独立成为一个检索片段，用于验证 DOCX 解析与切片。")
    d.add_heading("供应商评估", level=2)
    d.add_paragraph(f"供应商评估包括资质、报价和交付记录三项，评估结果每年更新一次。{canary} 评估由采购部牵头，"
                    "使用部门和财务部共同打分，得分低于六十分的供应商进入观察名单，连续两年进入观察名单的供应商"
                    "将被移出合格供应商目录。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text, t.cell(0, 1).text = "项目", "要求"
    t.cell(1, 0).text, t.cell(1, 1).text = "资质", "营业执照"
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def corrupt_pdf() -> bytes:
    return b"%PDF-1.4\n1 0 obj << /Type /Catalog >> garbage" + zlib.compress(b"x" * 100)


class IngestCase(ApiCase):
    """ApiCase (P2 seeded users) + platform-managed index in a temp storage dir + FakeEmbedder."""

    dense_mode = "auto"
    jobs_mode = "inline"          # P3 tests: jobs run synchronously through the job runner (same code path)
    extra_settings: dict = {}     # P5: per-class overrides (thresholds …)

    @classmethod
    def database_url(cls):
        return "sqlite://"
    retrieval_mode = "rrf"
    embedder_available = True

    @classmethod
    def setUpClass(cls):
        cls.storage = Path(tempfile.mkdtemp(prefix="kb_storage_"))
        cls.embedder = FakeEmbedder()
        cls.embedder_available_now = cls.embedder_available

        def factory():
            from kb_platform.ingest.errors import EmbeddingUnavailable
            if not cls.embedder_available_now:
                raise EmbeddingUnavailable()
            return cls.embedder
        cls.settings_obj = Settings(database_url=cls.database_url(), storage_dir=str(cls.storage), dense_mode=cls.dense_mode,
                                    retrieval_mode=cls.retrieval_mode, max_upload_bytes=512 * 1024, max_batch_files=8,
                                    jobs_mode=cls.jobs_mode, **cls.extra_settings)
        cls.index = IndexManager(cls.settings_obj, embedder_factory=factory)
        cls.llm = RecordingLLM()
        backend = RagBackend(index=cls.index, llm_factory=lambda: cls.llm, mode=cls.retrieval_mode,
                             cache=AnswerCache(256, 300))
        super().setUpClass_with(cls.settings_obj, backend)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.storage, ignore_errors=True)

    # helpers ---------------------------------------------------------------------------------------------------------
    def upload(self, name, data, user=ADMIN, kb=None, **params):
        return self.client.post(f"/api/v1/knowledge-bases/{kb or self.kb_id}/documents/upload", params=params,
                                files={"file": (name, data)}, headers=self.h(user))

    def grant(self, doc_id, **allow):
        r = self.client.put(f"/api/v1/documents/{doc_id}/acl", headers=self.h(ADMIN), json={"allow": allow})
        self.assertEqual(r.status_code, 200, r.text)

    def prompts_for(self, user, question):
        n0 = len(self.llm.prompts)
        r = self.ask(user, question)
        return r, "\n".join(self.llm.prompts[n0:])

    def assert_index_consistent(self):
        """Invariant: active generation == chunks of current versions of ACTIVE uploaded documents."""
        from sqlalchemy import select
        from kb_platform.models import Chunk, Document, DocumentVersion
        with self.factory() as db:
            gen = self.index.active_generation(db)
            want = set()
            for d in db.scalars(select(Document).where(Document.source == "upload", Document.status == "active")):
                if d.current_version_id:
                    v = db.get(DocumentVersion, d.current_version_id)
                    self.assertEqual(v.status, "indexed")
                    want |= {(d.doc_key, c.chunk_id) for c in db.scalars(select(Chunk).where(
                        Chunk.version_id == v.id, Chunk.is_parent.is_(False)))}
            if gen is None:
                self.assertEqual(want, set())
                return
            r = self.index.retriever(gen)
            have = {(x["source_file"], x["chunk_id"]) for x in r.bm25.documents}
            self.assertEqual(have, want)
            if gen.dense:
                self.assertEqual(set(zip(r.src, r.ids)), want)


__all__ = ["IngestCase", "FakeEmbedder", "md_doc", "txt_doc", "pdf_doc", "pdf_report", "docx_doc", "corrupt_pdf", "json"]
