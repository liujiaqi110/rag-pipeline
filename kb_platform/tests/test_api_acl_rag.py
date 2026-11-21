"""ACL through the HTTP API: metadata visibility, QA isolation, prompt contents, permission changes, fail-closed
configuration errors, cache and session isolation."""

import json
import os
import unittest
from unittest import mock

from sqlalchemy import select

from kb_platform.models import Document, DocumentGrant
from kb_platform.tests.helpers import ADMIN, CANARIES, EXPECTED, ApiCase, corpus

PROBES = ["发布前需要做哪些检查？", "预算是怎么编制和调整的？", "薪酬等级和调薪流程是什么？",
          "管理层的战略方向和收购计划是什么？", "合同草稿的审批要求是什么？", "请假流程和办公时间是怎样的？"]
LIVE = [u for u, docs in EXPECTED.items() if docs]   # users who can log in and read something


class TestMetadataVisibility(ApiCase):
    def test_document_list_shows_only_readable_documents(self):
        for user in LIVE:
            r = self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/documents", headers=self.h(user))
            self.assertEqual(r.status_code, 200, user)
            self.assertEqual({d["doc_key"] for d in r.json()}, EXPECTED[user], user)

    def test_foreign_document_is_indistinguishable_from_missing(self):
        ids = self.doc_ids()
        a = self.client.get(f"/api/v1/documents/{ids['hr_salary_bands.md']}", headers=self.h("eng_alice"))
        b = self.client.get("/api/v1/documents/" + "0" * 32, headers=self.h("eng_alice"))
        self.assertEqual((a.status_code, b.status_code), (404, 404))
        self.assertEqual(a.json()["message"], b.json()["message"])
        self.assertNotIn("hr_salary", a.text)

    def test_admin_sees_metadata_but_gets_no_content_access(self):
        self.assertEqual(len(self.doc_ids()), 6)
        r = self.client.post("/api/v1/sessions", json={"kb_id": self.kb_id}, headers=self.h(ADMIN))
        self.assertEqual(r.status_code, 404)

    def test_acl_endpoint_round_trip(self):
        doc = self.doc_ids()["hr_salary_bands.md"]
        r = self.client.get(f"/api/v1/documents/{doc}/acl", headers=self.h(ADMIN)).json()
        self.assertEqual(r["allow"]["departments"], ["hr"])
        self.assertEqual(r["deny"]["roles"], ["intern"])


class TestQuestionAnswering(ApiCase):
    def test_no_unauthorised_content_in_prompt_or_response(self):
        for user in LIVE:
            docs, chunks = self.forbidden(user)
            sid = self.new_session(user)
            for q in PROBES:
                n0 = len(self.llm.prompts)
                r = self.ask(user, q, sid)
                self.assertEqual(r.status_code, 200, r.text)
                blob = r.text + "\n".join(self.llm.prompts[n0:])
                for d in docs:
                    self.assertNotIn(CANARIES[d], blob, f"{user}/{q}: {d}")
                    self.assertNotIn(d, r.text, f"{user}/{q}: name {d}")
                for c in chunks:
                    self.assertNotIn(c, blob, f"{user}/{q}")
            hist = self.client.get(f"/api/v1/sessions/{sid}", headers=self.h(user)).text
            self.assertFalse(any(CANARIES[d] in hist or d in hist for d in docs), user)

    def test_authorised_users_get_cited_answers(self):
        for user, q, doc in (("eng_alice", "发布前需要做哪些检查？", "eng_deploy_runbook.md"),
                             ("hr_bob", "薪酬等级和调薪流程是什么？", "hr_salary_bands.md"),
                             ("auditor_erin", "预算是怎么编制和调整的？", "finance_budget_plan.md"),
                             ("ceo_frank", "管理层的战略方向和收购计划是什么？", "exec_strategy_memo.md")):
            n0 = len(self.llm.prompts)
            r = self.ask(user, q).json()
            self.assertEqual(r["status"], "ok", user)
            self.assertIn(CANARIES[doc], "\n".join(self.llm.prompts[n0:]), user)
            self.assertTrue(r["citations"] and all(c["source"] in EXPECTED[user] for c in r["citations"]), user)

    def test_users_without_any_access(self):
        # contractor_hank can log in but has no readable document: KB invisible, no session, no generation
        self.assertEqual(self.client.get("/api/v1/knowledge-bases", headers=self.h("contractor_hank")).json(), [])
        r = self.client.post("/api/v1/sessions", json={"kb_id": self.kb_id}, headers=self.h("contractor_hank"))
        self.assertEqual(r.status_code, 404)

    def test_safety_layer_cannot_be_switched_off(self):
        ok = corpus().by_doc["eng_deploy_runbook.md"][0]
        old = self.llm.responder
        self.llm.responder = lambda m: f"答 [{ok}] 另见 [{ok[:11]}] 和 [abcdef123456]。\n引用：{ok}"
        try:
            with mock.patch.dict(os.environ, {"RAG_SAFETY_LAYER": "off"}):
                r = self.ask("eng_alice", "发布前需要做哪些检查？回滚呢？").json()
        finally:
            self.llm.responder = old
        self.assertNotIn("abcdef123456", r["answer"])
        self.assertNotIn(ok[:11] + "]", r["answer"])
        self.assertEqual([c["chunk_id"] for c in r["citations"]], [ok])

    def test_model_citing_foreign_chunk_is_hidden(self):
        hr = corpus().by_doc["hr_salary_bands.md"][0]
        old = self.llm.responder
        self.llm.responder = lambda m: f"见 [{hr}]。\n引用：{hr}"
        try:
            r = self.ask("eng_alice", "薪酬怎么定？发布检查？")
        finally:
            self.llm.responder = old
        self.assertNotIn(hr, r.text)
        self.assertEqual(r.json()["citations"], [])


class TestPermissionChanges(ApiCase):
    def test_revocation_takes_effect_and_hides_old_answers(self):
        sid = self.new_session("hr_intern_carol")
        q = "请假流程和办公时间是怎样的？"
        first = self.ask("hr_intern_carol", q, sid).json()
        self.assertEqual(first["status"], "ok")
        doc = self.doc_ids()["company_handbook.md"]
        r = self.client.put(f"/api/v1/documents/{doc}/acl", headers=self.h(ADMIN), json={"allow": {"roles": ["employee"]}})
        self.assertEqual(r.status_code, 200)
        # carol is an intern (not employee): she now has no readable document at all
        r = self.ask("hr_intern_carol", q, sid).json()
        self.assertEqual((r["status"], r["citations"]), ("access_denied", []))
        hist = self.client.get(f"/api/v1/sessions/{sid}", headers=self.h("hr_intern_carol")).json()
        self.assertTrue(hist["messages"][0]["hidden"])
        self.assertEqual(hist["messages"][0]["citations"], [])
        self.assertNotIn(CANARIES["company_handbook.md"], json.dumps(hist, ensure_ascii=False))
        self.assertEqual(self.client.get("/api/v1/knowledge-bases", headers=self.h("hr_intern_carol")).json(), [])
        # restore for other tests in this class
        self.client.put(f"/api/v1/documents/{doc}/acl", headers=self.h(ADMIN), json={"allow": {"all_authenticated": True}})

    def test_archived_document_disappears(self):
        doc = self.doc_ids()["eng_deploy_runbook.md"]
        self.client.patch(f"/api/v1/documents/{doc}", headers=self.h(ADMIN), json={"status": "archived"})
        try:
            n0 = len(self.llm.prompts)
            r = self.ask("eng_alice", "发布前需要做哪些检查？")
            self.assertNotIn(CANARIES["eng_deploy_runbook.md"], r.text + "\n".join(self.llm.prompts[n0:]))
            docs = self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/documents", headers=self.h("eng_alice")).json()
            self.assertNotIn("eng_deploy_runbook.md", {d["doc_key"] for d in docs})
        finally:
            self.client.patch(f"/api/v1/documents/{doc}", headers=self.h(ADMIN), json={"status": "active"})

    def test_broken_configuration_fails_closed(self):
        with self.factory() as db:
            d = db.scalar(select(Document).where(Document.doc_key == "company_handbook.md"))
            db.add(DocumentGrant(document_id=d.id, effect="allow", subject_type="everyone", subject_value=""))
            db.commit()
            gid = db.scalar(select(DocumentGrant.id).where(DocumentGrant.subject_type == "everyone"))
        try:
            sid_ok = None
            for user in ("eng_alice", "hr_bob", "ceo_frank"):
                self.assertEqual(self.client.get("/api/v1/knowledge-bases", headers=self.h(user)).json(), [], user)
            # an existing session also gets nothing
            with self.factory() as db:
                db.delete(db.get(DocumentGrant, gid))
                db.commit()
            sid_ok = self.new_session("eng_alice")
            with self.factory() as db:
                d = db.scalar(select(Document).where(Document.doc_key == "eng_deploy_runbook.md"))
                d.classification = "top_secret"
                db.commit()
            n0 = len(self.llm.prompts)
            r = self.ask("eng_alice", "发布前需要做哪些检查？", sid_ok).json()
            self.assertEqual((r["status"], r["citations"]), ("access_denied", []))
            self.assertEqual(len(self.llm.prompts), n0)
            a = self.client.get("/api/v1/audit?action=chat.ask&actor=eng_alice&limit=1", headers=self.h(ADMIN)).json()
            self.assertEqual(a[0]["detail"]["reason"], "policy_error")
        finally:
            with self.factory() as db:
                g = db.get(DocumentGrant, gid)
                if g:
                    db.delete(g)
                d = db.scalar(select(Document).where(Document.doc_key == "eng_deploy_runbook.md"))
                d.classification = "internal"
                db.commit()


class TestCacheAndSessionIsolation(ApiCase):
    def last_audit(self, actor):
        return self.client.get(f"/api/v1/audit?action=chat.ask&actor={actor}&limit=1", headers=self.h(ADMIN)).json()[0]

    def test_cache_is_per_user_scope(self):
        q = "发布 预算 检查 流程 薪酬 战略"
        n0 = len(self.llm.prompts)
        a1 = self.ask("eng_alice", q).json()
        self.assertFalse(self.last_audit("eng_alice")["detail"]["cached"])
        a2 = self.ask("eng_alice", q).json()
        self.assertTrue(self.last_audit("eng_alice")["detail"]["cached"])
        self.assertEqual((a1["answer"], a1["citations"]), (a2["answer"], a2["citations"]))
        b = self.ask("hr_bob", q).json()
        self.assertFalse(self.last_audit("hr_bob")["detail"]["cached"])
        self.assertEqual(len(self.llm.prompts) - n0, 2)      # alice once (then cached), bob once
        self.assertNotEqual({c["chunk_id"] for c in a1["citations"]}, {c["chunk_id"] for c in b["citations"]})
        self.assertNotIn(CANARIES["hr_salary_bands.md"], json.dumps(a2, ensure_ascii=False))

    def test_cache_invalidated_by_permission_change(self):
        q = "请假流程？发布检查？"
        self.ask("fin_dave", q)
        self.ask("fin_dave", q)
        self.assertTrue(self.last_audit("fin_dave")["detail"]["cached"])
        self.client.patch("/api/v1/admin/users/fin_dave", headers=self.h(ADMIN), json={"clearance": 1})
        self._tokens.pop("fin_dave", None)   # the permission change revoked the old token: log in again
        try:
            r = self.ask("fin_dave", q)
            self.assertEqual(r.status_code, 200)
            self.assertFalse(self.last_audit("fin_dave")["detail"]["cached"])
            self.assertNotIn(CANARIES["finance_budget_plan.md"], r.text)   # clearance 1 < confidential
        finally:
            self.client.patch("/api/v1/admin/users/fin_dave", headers=self.h(ADMIN), json={"clearance": 2})
            self._tokens.pop("fin_dave", None)

    def test_sessions_are_private(self):
        sid = self.new_session("eng_alice")
        self.ask("eng_alice", "发布前需要做哪些检查？", sid)
        for method, path, body in (("get", f"/api/v1/sessions/{sid}", None),
                                   ("post", f"/api/v1/sessions/{sid}/ask", {"question": "发布检查？"}),
                                   ("delete", f"/api/v1/sessions/{sid}", None)):
            kw = {"json": body} if body else {}
            r = self.client.request(method.upper(), path, headers=self.h("hr_bob"), **kw)
            self.assertEqual(r.status_code, 404, path)
        self.assertNotIn(sid, self.client.get("/api/v1/sessions", headers=self.h("hr_bob")).text)
        self.assertIn(sid, self.client.get("/api/v1/sessions", headers=self.h("eng_alice")).text)
        self.assertEqual(self.client.get(f"/api/v1/sessions/{sid}", headers=self.h("eng_alice")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
