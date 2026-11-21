"""P3 API: upload → parse → chunk → index → retrievable, with ACL, versions, disable / delete, batch isolation,
failure recovery, missing-model states, path safety and operation permissions.

Dense here uses FakeEmbedder (hashing, NOT semantic) — real bge-m3 is tested in tests_local/test_kb_platform_real.py."""

import os
import unittest
from pathlib import Path

from sqlalchemy import select

from kb_platform.models import Chunk, Document, DocumentVersion
from kb_platform.tests.helpers import ADMIN
from kb_platform.tests.ingest_fixtures import (IngestCase, corrupt_pdf, docx_doc, md_doc, pdf_report, txt_doc)


class TestUploadAllFormats(IngestCase):
    def test_four_formats_are_indexed_and_retrievable_after_grant(self):
        files = {"runbook.md": md_doc("发布手册", "CANARY-P3-MD"), "doors.txt": txt_doc("CANARY-P3-TXT"),
                 "report.pdf": pdf_report("CANARY-P3-PDF"), "purchase.docx": docx_doc("CANARY-P3-DOCX")}
        ids = {}
        for name, data in files.items():
            r = self.upload(name, data)
            self.assertEqual(r.status_code, 201, r.text)
            body = r.json()
            self.assertEqual(body["outcome"], "created")
            self.assertEqual(body["version"]["status"], "indexed", name)
            self.assertGreater(body["version"]["chunk_count"], 0)
            self.assertEqual(body["index"]["status"], "active")
            self.assertTrue(body["index"]["dense"])
            ids[name] = body["document"]["id"]
            self.assert_index_consistent()
        # default: nobody can read a fresh upload — not even the admin who uploaded it
        r, prompt = self.prompts_for("eng_alice", "发布流程有哪些要求？")
        for c in ("CANARY-P3-MD", "CANARY-P3-TXT", "CANARY-P3-PDF", "CANARY-P3-DOCX"):
            self.assertNotIn(c, prompt + r.text)
        for name in files:
            self.grant(ids[name], departments=["engineering"])
        for q, c in (("发布流程有哪些要求？", "CANARY-P3-MD"), ("门禁 工牌 访客", "CANARY-P3-TXT"),
                     ("deployment checklist rollback", "CANARY-P3-PDF"), ("采购申请 供应商评估", "CANARY-P3-DOCX")):
            r, prompt = self.prompts_for("eng_alice", q)
            self.assertEqual(r.json()["status"], "ok", q)
            self.assertIn(c, prompt, q)
            self.assertTrue(any(x["source"].endswith(tuple(files)) for x in r.json()["citations"]))
            r2, prompt2 = self.prompts_for("hr_bob", q)        # other department: nothing
            self.assertNotIn(c, prompt2 + r2.text, q)
        # chunk list: readers see text + page / paragraph metadata
        r = self.client.get(f"/api/v1/documents/{ids['report.pdf']}/chunks", headers=self.h("eng_alice")).json()
        self.assertTrue(r["text_visible"])
        self.assertEqual({c["page"] for c in r["chunks"]} - {None}, {1, 2})
        self.assertTrue(all(c["paragraph"] for c in r["chunks"]))
        st = self.client.get(f"/api/v1/documents/{ids['purchase.docx']}/status", headers=self.h("eng_alice")).json()
        self.assertTrue(st["retrievable"])
        self.assertEqual(st["current_version"]["file_format"], "docx")
        self.assertIsNone(self.client.get(f"/api/v1/documents/{ids['report.pdf']}/status",
                                          headers=self.h("eng_alice")).json()["current_version"]["error_code"])

    def test_four_grant_kinds(self):
        did = self.upload("grants.md", md_doc("授权测试", "CANARY-GRANT", "授权专项")).json()["document"]["id"]
        q = "授权专项 有哪些要求？"
        cases = [({"all_authenticated": True}, {"eng_alice", "hr_bob", "ceo_frank"}, set()),
                 ({"departments": ["hr"]}, {"hr_bob"}, {"eng_alice", "ceo_frank"}),
                 ({"roles": ["finance_auditor"]}, {"auditor_erin"}, {"fin_dave", "eng_alice"}),
                 ({"users": ["ceo_frank"]}, {"ceo_frank"}, {"hr_bob", "eng_alice"})]
        for allow, yes, no in cases:
            self.grant(did, **allow)
            for u in yes:
                self.assertIn("CANARY-GRANT", self.prompts_for(u, q)[1], (allow, u))
            for u in no:
                r, p = self.prompts_for(u, q)
                self.assertNotIn("CANARY-GRANT", p + r.text, (allow, u))


class TestVersionsDisableDelete(IngestCase):
    def test_new_version_replaces_old_content(self):
        r = self.upload("policy.md", md_doc("制度", "CANARY-V1-OLD", "版本专项"))
        did = r.json()["document"]["id"]
        self.grant(did, departments=["engineering"])
        q = "版本专项 要求是什么？"
        self.assertIn("CANARY-V1-OLD", self.prompts_for("eng_alice", q)[1])
        r = self.client.post(f"/api/v1/documents/{did}/versions", files={"file": ("policy.md", md_doc("制度", "CANARY-V2-NEW", "版本专项"))},
                             headers=self.h(ADMIN))
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual((r.json()["outcome"], r.json()["version"]["version_no"], r.json()["version"]["status"]),
                         ("new_version", 2, "indexed"))
        r, p = self.prompts_for("eng_alice", q)
        self.assertIn("CANARY-V2-NEW", p)
        self.assertNotIn("CANARY-V1-OLD", p + r.text)
        vs = self.client.get(f"/api/v1/documents/{did}/versions", headers=self.h(ADMIN)).json()
        self.assertEqual([(v["version_no"], v["status"]) for v in vs], [(2, "indexed"), (1, "superseded")])
        ch = self.client.get(f"/api/v1/documents/{did}/chunks", headers=self.h("eng_alice")).json()
        self.assertEqual(ch["version_no"], 2)
        self.assertTrue(all("CANARY-V1-OLD" not in (c["text"] or "") for c in ch["chunks"]))
        # old version chunks: not readable by readers
        old = self.client.get(f"/api/v1/documents/{did}/chunks?version_id={vs[1]['id']}", headers=self.h("eng_alice"))
        self.assertEqual(old.status_code, 404)
        # re-uploading identical content is a no-op
        r = self.client.post(f"/api/v1/documents/{did}/versions", files={"file": ("policy.md", md_doc("制度", "CANARY-V2-NEW", "版本专项"))},
                             headers=self.h(ADMIN))
        self.assertEqual(r.json()["outcome"], "unchanged")
        self.assert_index_consistent()

    def test_disable_enable_delete(self):
        did = self.upload("ops.md", md_doc("运维", "CANARY-OPS", "运维专项")).json()["document"]["id"]
        self.grant(did, all_authenticated=True)
        q = "运维专项 要求？"
        self.assertIn("CANARY-OPS", self.prompts_for("eng_alice", q)[1])
        st = self.client.post(f"/api/v1/documents/{did}/disable", headers=self.h(ADMIN)).json()
        self.assertEqual((st["status"], st["retrievable"]), ("disabled", False))
        r, p = self.prompts_for("eng_alice", q)
        self.assertNotIn("CANARY-OPS", p + r.text)
        self.assertEqual(self.client.get(f"/api/v1/documents/{did}", headers=self.h("eng_alice")).status_code, 404)
        self.assert_index_consistent()
        st = self.client.post(f"/api/v1/documents/{did}/enable", headers=self.h(ADMIN)).json()
        self.assertTrue(st["retrievable"])
        self.assertIn("CANARY-OPS", self.prompts_for("eng_alice", q)[1])
        r = self.client.delete(f"/api/v1/documents/{did}", headers=self.h(ADMIN))
        self.assertEqual((r.status_code, r.json()["status"]), (200, "active"))
        r, p = self.prompts_for("eng_alice", q)
        self.assertNotIn("CANARY-OPS", p + r.text)
        with self.factory() as db:
            vids = [v.id for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == did))]
            self.assertEqual(db.scalar(select(Chunk).where(Chunk.version_id.in_(vids))), None)
        self.assertFalse((self.storage / "uploads" / did).exists())
        self.assertEqual(self.client.delete(f"/api/v1/documents/{did}", headers=self.h(ADMIN)).status_code, 404)
        self.assert_index_consistent()

    def test_revocation_invalidates_cache_and_history(self):
        did = self.upload("secret.md", md_doc("机密", "CANARY-REVOKE", "撤权专项")).json()["document"]["id"]
        self.grant(did, users=["hr_bob"])
        sid = self.new_session("hr_bob")
        q = "撤权专项 要求？"
        a1 = self.ask("hr_bob", q, sid).json()
        self.assertTrue(a1["citations"])
        self.ask("hr_bob", q, sid)                            # cached
        self.grant(did, users=["fin_dave"])                   # revoke bob
        n0 = len(self.llm.prompts)
        a3 = self.ask("hr_bob", q, sid)
        self.assertNotIn("CANARY-REVOKE", a3.text + "\n".join(self.llm.prompts[n0:]))
        self.assertFalse(any(c["chunk_id"] in {x["chunk_id"] for x in a1["citations"]} for c in a3.json()["citations"]))
        hist = self.client.get(f"/api/v1/sessions/{sid}", headers=self.h("hr_bob")).json()
        self.assertTrue(all(m["hidden"] for m in hist["messages"][:2]))


class TestBatchAndFailures(IngestCase):
    def test_batch_isolates_bad_files(self):
        files = [("files", ("ok1.md", md_doc("批量一", "CANARY-B1"))), ("files", ("ok2.txt", txt_doc("CANARY-B2"))),
                 ("files", ("bad.pdf", corrupt_pdf())), ("files", ("evil.exe", b"MZ\x90\x00")),
                 ("files", ("empty.md", b"")), ("files", ("ok3.docx", docx_doc("CANARY-B3"))),
                 ("files", ("big.md", b"a" * (512 * 1024 + 1)))]
        r = self.client.post(f"/api/v1/knowledge-bases/{self.kb_id}/documents/batch-upload", files=files, headers=self.h(ADMIN))
        self.assertEqual(r.status_code, 200, r.text)
        res = {x["filename"]: x for x in r.json()["results"]}
        self.assertEqual({k for k, v in res.items() if v["ok"]}, {"ok1.md", "ok2.txt", "ok3.docx"})
        self.assertEqual((res["bad.pdf"]["status"], res["bad.pdf"]["error_code"]), ("failed", "parse_failed"))
        self.assertEqual(res["evil.exe"]["error_code"], "unsupported_format")
        self.assertEqual(res["empty.md"]["error_code"], "empty_file")
        self.assertEqual(res["big.md"]["error_code"], "file_too_large")
        self.assertEqual(r.json()["index"]["status"], "active")
        for k in ("ok1.md", "ok2.txt", "ok3.docx"):
            self.assertEqual(res[k]["status"], "indexed")
        self.assert_index_consistent()
        too_many = [("files", (f"f{i}.md", md_doc("x", f"C{i}"))) for i in range(9)]
        r = self.client.post(f"/api/v1/knowledge-bases/{self.kb_id}/documents/batch-upload", files=too_many, headers=self.h(ADMIN))
        self.assertEqual(r.status_code, 413)

    def test_index_build_failure_keeps_old_version_and_recovers(self):
        did = self.upload("keep.md", md_doc("保持", "CANARY-KEEP-V1", "恢复专项")).json()["document"]["id"]
        self.grant(did, departments=["engineering"])
        q = "恢复专项 要求？"
        self.assertIn("CANARY-KEEP-V1", self.prompts_for("eng_alice", q)[1])
        with self.factory() as db:
            gen_before = self.index.active_generation(db).id
        for stage in ("before_write", "after_write"):
            self.index.fail_next_build = stage
            r = self.client.post(f"/api/v1/documents/{did}/versions", files={"file": ("keep.md", md_doc("保持", f"CANARY-KEEP-{stage}", "恢复专项"))},
                                 headers=self.h(ADMIN))
            self.assertEqual(r.status_code, 201)
            self.assertEqual((r.json()["index"]["status"], r.json()["version"]["status"]), ("failed", "index_failed"))
            self.assertEqual(r.json()["index"]["generation_id"], gen_before)
            # (a different wording per stage: the unchanged generation would otherwise answer from the cache)
            r, p = self.prompts_for("eng_alice", f"{q} {stage}")
            self.assertIn("CANARY-KEEP-V1", p)                  # old version still served
            self.assertNotIn(f"CANARY-KEEP-{stage}", p)
            self.assertEqual(list((self.storage / "index").glob(".tmp-*")), [])
            self.assert_index_consistent()
        r = self.client.post("/api/v1/index/rebuild", headers=self.h(ADMIN))
        self.assertEqual(r.json()["status"], "active")
        r, p = self.prompts_for("eng_alice", q)
        self.assertIn("CANARY-KEEP-after_write", p)              # newest pending version wins
        self.assertNotIn("CANARY-KEEP-V1", p)
        st = self.client.get("/api/v1/index/status", headers=self.h(ADMIN)).json()
        self.assertEqual(st["pending_versions"], 0)
        self.assert_index_consistent()

    def test_parse_failure_on_new_version_keeps_current(self):
        did = self.upload("guide.md", md_doc("指南", "CANARY-GUIDE-V1", "指南专项")).json()["document"]["id"]
        self.grant(did, departments=["engineering"])
        r = self.client.post(f"/api/v1/documents/{did}/versions", files={"file": ("guide.pdf", corrupt_pdf())}, headers=self.h(ADMIN))
        self.assertEqual((r.status_code, r.json()["version"]["status"], r.json()["version"]["error_code"]),
                         (201, "failed", "parse_failed"))
        self.assertIn("CANARY-GUIDE-V1", self.prompts_for("eng_alice", "指南专项 要求？")[1])


class TestDuplicatesAndSafety(IngestCase):
    def test_duplicate_name_and_content(self):
        r = self.upload("same.md", md_doc("重复", "CANARY-DUP"))
        self.assertEqual(r.status_code, 201)
        did = r.json()["document"]["id"]
        r = self.upload("same.md", md_doc("重复", "CANARY-DUP-2"))
        self.assertEqual((r.status_code, r.json()["error"]), (409, "duplicate_name"))
        r = self.upload("other-name.md", md_doc("重复", "CANARY-DUP"))
        self.assertEqual((r.status_code, r.json()["error"]), (409, "duplicate_content"))
        r = self.upload("same.md", md_doc("重复", "CANARY-DUP-2"), on_duplicate="skip")
        self.assertEqual(r.json()["outcome"], "skipped")
        r = self.upload("same.md", md_doc("重复", "CANARY-DUP-2"), on_duplicate="new_version")
        self.assertEqual((r.json()["outcome"], r.json()["document"]["id"], r.json()["version"]["version_no"]),
                         ("new_version", did, 2))
        with self.factory() as db:
            self.assertEqual(len(list(db.scalars(select(Document).where(Document.original_filename == "same.md")))), 1)

    def test_paths_cannot_escape_storage(self):
        for name in ("../../../outside.md", "..\\..\\win.md", "/etc/abs.md", "C:\\Windows\\abs2.md"):
            r = self.upload(name, md_doc("路径", f"CANARY-{abs(hash(name))}"))
            self.assertEqual(r.status_code, 201, r.text)
            d = r.json()["document"]
            self.assertNotIn("/", d["original_filename"])
            self.assertNotIn("\\", d["original_filename"])
        root = self.storage.resolve()
        for p in root.rglob("*"):
            self.assertIn(root, p.resolve().parents)
        stored = {p.name for p in (root / "uploads").rglob("original.*")}
        self.assertEqual(stored, {"original.md"})
        self.assertFalse((root.parent / "outside.md").exists())

    def test_size_limit(self):
        r = self.upload("huge.md", b"a" * (512 * 1024 + 1))
        self.assertEqual((r.status_code, r.json()["error"]), (413, "file_too_large"))
        r = self.upload("x.exe", b"MZ")
        self.assertEqual(r.status_code, 415)


class TestOperationPermissions(IngestCase):
    def test_only_managers_operate_and_managing_is_not_reading(self):
        r = self.upload("p.md", md_doc("权限", "CANARY-PERM"), user="eng_alice")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/knowledge-bases/{self.kb_id}/managers", headers=self.h("hr_bob"),
                                          json={"username": "hr_bob"}).status_code, 403)
        r = self.client.post(f"/api/v1/knowledge-bases/{self.kb_id}/managers", headers=self.h(ADMIN), json={"username": "hr_bob"})
        self.assertEqual(r.status_code, 204)
        r = self.upload("p.md", md_doc("权限", "CANARY-PERM", "管理专项"), user="hr_bob",
                        **{"user_id": "ceo_frank", "created_by": "ceo_frank"})
        self.assertEqual(r.status_code, 201, r.text)
        did = r.json()["document"]["id"]
        self.assertEqual(r.json()["version"]["created_by"], "hr_bob")      # identity from the token only
        # manager can see metadata, but no text and no answers without a grant
        ch = self.client.get(f"/api/v1/documents/{did}/chunks", headers=self.h("hr_bob")).json()
        self.assertFalse(ch["text_visible"])
        self.assertTrue(all(c["text"] is None for c in ch["chunks"]))
        r, p = self.prompts_for("hr_bob", "管理专项 要求？")
        self.assertNotIn("CANARY-PERM", p + r.text)
        # manager of this KB only: another KB is invisible; index operations stay admin-only
        kb2 = self.client.post("/api/v1/knowledge-bases", headers=self.h(ADMIN), json={"name": "第二知识库"}).json()["id"]
        self.assertEqual(self.upload("q.md", md_doc("x", "C-X"), user="hr_bob", kb=kb2).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/index/rebuild", headers=self.h("hr_bob")).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/index/status", headers=self.h("hr_bob")).status_code, 403)
        # non-manager cannot disable / delete / version / change ACL (404 = not even visible as manageable)
        for method, path in (("post", f"/api/v1/documents/{did}/disable"), ("delete", f"/api/v1/documents/{did}"),
                             ("put", f"/api/v1/documents/{did}/acl"), ("post", f"/api/v1/documents/{did}/versions")):
            r = self.client.request(method.upper(), path, headers=self.h("eng_alice"), json={} if method == "put" else None)
            self.assertEqual(r.status_code, 403, path)
        self.client.delete(f"/api/v1/knowledge-bases/{self.kb_id}/managers/hr_bob", headers=self.h(ADMIN))
        self.assertEqual(self.client.post(f"/api/v1/documents/{did}/disable", headers=self.h("hr_bob")).status_code, 403)


class TestMissingModel(IngestCase):
    embedder_available = False

    def test_bm25_only_generation_is_explicit_and_recoverable(self):
        r = self.upload("m.md", md_doc("模型", "CANARY-MODEL", "模型专项"))
        self.assertEqual(r.status_code, 201)
        b = r.json()
        self.assertEqual((b["version"]["status"], b["index"]["dense"], b["index"]["dense_error"]),
                         ("indexed", False, "embedding_model_unavailable"))
        did = b["document"]["id"]
        self.grant(did, departments=["engineering"])
        r = self.ask("eng_alice", "模型专项 要求？")
        self.assertEqual((r.status_code, r.json()["error"]), (503, "dense_unavailable"))   # never a silent BM25 fallback
        st = self.client.get(f"/api/v1/documents/{did}/status", headers=self.h("eng_alice")).json()
        self.assertFalse(st["retrievable"])
        s = self.client.get("/api/v1/index/status", headers=self.h(ADMIN)).json()
        self.assertEqual(s["embedder"], {"available": False, "reason": "embedding_model_unavailable"})
        type(self).embedder_available_now = True                                        # "install the model"
        self.index._embedder = None
        r = self.client.post("/api/v1/index/rebuild", headers=self.h(ADMIN)).json()
        self.assertEqual((r["status"], r["dense"]), ("active", True))
        r, p = self.prompts_for("eng_alice", "模型专项 要求？")
        self.assertEqual(r.json()["status"], "ok")
        self.assertIn("CANARY-MODEL", p)


class TestDenseRequired(IngestCase):
    dense_mode = "required"
    embedder_available = False

    def test_required_dense_fails_build_without_activating(self):
        r = self.upload("r.md", md_doc("必需", "CANARY-REQ"))
        self.assertEqual(r.status_code, 201)
        self.assertEqual((r.json()["index"]["status"], r.json()["index"]["error_code"], r.json()["version"]["status"]),
                         ("failed", "embedding_model_unavailable", "index_failed"))
        with self.factory() as db:
            self.assertIsNone(self.index.active_generation(db))


class TestExplicitBm25Mode(IngestCase):
    retrieval_mode = "bm25"
    dense_mode = "off"

    def test_bm25_mode_answers_with_acl(self):
        did = self.upload("b.md", md_doc("词法", "CANARY-BM25", "词法专项")).json()["document"]["id"]
        self.grant(did, departments=["hr"])
        r, p = self.prompts_for("hr_bob", "词法专项 要求？")
        self.assertEqual(r.json()["status"], "ok")
        self.assertIn("CANARY-BM25", p)
        r, p = self.prompts_for("eng_alice", "词法专项 要求？")
        self.assertNotIn("CANARY-BM25", p + r.text)
        a = self.client.get("/api/v1/audit?action=chat.ask&actor=hr_bob&limit=1", headers=self.h(ADMIN)).json()[0]
        self.assertEqual(a["detail"]["mode"], "bm25")


if __name__ == "__main__":
    unittest.main()
