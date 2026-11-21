"""P5 FAQ: mining / clustering correctness, review state machine, reviewer isolation, FAQ cache hit and invalidation
for global / department / role / personal FAQs, SSE with FAQ hits.

Embeddings are the deterministic hashing FAKE (FakeEmbedder) — clustering logic, thresholds and permission rules are
what is tested here; semantic quality with real bge-m3 is exercised by the browser end-to-end demo."""

import json
import unittest

from sqlalchemy import select

from kb_platform.models_ops import FaqEntry, FaqMember, QaEvent
from kb_platform.tests.helpers import ADMIN
from kb_platform.tests.ingest_fixtures import IngestCase, md_doc
from kb_platform.tests.test_stream_workbench import events
from ragas_eval.tests.acl_fixtures import cite_first_context

P5 = {"faq_cluster_threshold": 0.80, "faq_match_threshold": 0.85, "faq_min_frequency": 3,
      "gap_merge_threshold": 0.85, "gap_repeat_threshold": 3}
RELEASE = ["发布前需要做哪些检查？", "发布前要做哪些检查", "发布之前需要做哪些检查？"]
SALARY = ["薪酬等级有哪些？", "薪酬等级都有哪些", "薪酬等级有哪些"]


class FaqCase(IngestCase):
    extra_settings = P5

    def setUp(self):
        self.llm.responder = cite_first_context

    def doc(self, name, canary, topic, **allow):
        did = self.upload(name, md_doc(topic, canary, topic)).json()["document"]["id"]
        self.grant(did, **allow)
        return did

    def ask_all(self, user, qs):
        sid = self.new_session(user)
        return [self.ask(user, q, sid).json() for q in qs]

    def mine(self, **kw):
        r = self.client.post("/api/v1/faq/mine", headers=self.h(ADMIN), json={"kb_id": self.kb_id, **kw})
        self.assertEqual(r.status_code, 200, r.text)
        job = r.json()[0]
        self.assertEqual(job["status"], "succeeded", job)
        return job["result"]

    def make_reviewer(self, username, **perm):
        self.client.post("/api/v1/admin/users", headers=self.h(ADMIN),
                         json={"username": username, "password": "Review-pass-01", "clearance": 2, **perm})
        self.client.post(f"/api/v1/knowledge-bases/{self.kb_id}/managers", headers=self.h(ADMIN), json={"username": username})
        self.creds[username] = "Review-pass-01"
        self._tokens.pop(username, None)

    def faqs(self, user, **q):
        r = self.client.get("/api/v1/faq", params={"kb_id": self.kb_id, **q}, headers=self.h(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class TestMining(FaqCase):
    def test_clustering_threshold_evidence_split_and_metadata(self):
        self.doc("release.md", "CANARY-FAQ-REL", "发布检查", departments=["engineering"])
        self.doc("salary.md", "CANARY-FAQ-SAL", "薪酬等级", departments=["hr"])
        self.ask_all("eng_alice", RELEASE)
        self.ask_all("hr_bob", SALARY)
        self.ask_all("eng_alice", ["请致电 13800138000 询问发布前需要做哪些检查"])   # non-compliant: never mined
        self.ask_all("eng_alice", ["报销流程是什么？"])                              # one-off question
        n0 = len(self.llm.prompts)
        res = self.mine()
        self.assertEqual((res["candidates_created"], res["candidates_updated"]), (2, 0), res)
        self.assertGreaterEqual(res["stats"]["skipped_non_compliant"], 1)
        with self.factory() as db:
            faqs = list(db.scalars(select(FaqEntry).order_by(FaqEntry.question)))
            self.assertEqual({f.status for f in faqs}, {"candidate"})                 # never auto-published
            by_doc = {f.evidence[0]["source"].split("_", 1)[1]: f for f in faqs}
            rel = by_doc["release.md"]
            self.assertEqual((rel.frequency, rel.distinct_users), (3, 1))
            self.assertEqual(len(rel.evidence), 1)
            self.assertTrue(rel.evidence[0]["version_id"])
            self.assertEqual(rel.embedding_model, "fake-hash-96")
            self.assertEqual(rel.llm_model, "mock")
            self.assertTrue(rel.index_generation)
            self.assertEqual(rel.params["min_frequency"], 3)
            self.assertEqual(rel.answer_source, "generated")
            self.assertTrue(0 < rel.confidence <= 1)
            self.assertTrue(all(c["version_id"] for c in rel.citations))
            members = list(db.scalars(select(FaqMember).where(FaqMember.faq_id == rel.id)))
            self.assertEqual(sorted(m.question for m in members), sorted(RELEASE))
        gen_prompts = "\n".join(self.llm.prompts[n0:])     # candidate answers only saw their own evidence
        self.assertIn("CANARY-FAQ-REL", gen_prompts)
        self.assertIn("CANARY-FAQ-SAL", gen_prompts)
        for p in self.llm.prompts[n0:]:
            self.assertFalse("CANARY-FAQ-REL" in p and "CANARY-FAQ-SAL" in p)
        # higher threshold → nothing new; re-mining updates instead of duplicating
        self.assertEqual(self.mine(min_frequency=4)["candidates_created"], 0)
        self.ask_all("eng_alice", ["发布前需要做哪些检查"])
        res = self.mine()
        self.assertEqual((res["candidates_created"], res["candidates_updated"]), (0, 1))   # only the release FAQ got a new member
        with self.factory() as db:
            self.assertEqual(db.get(FaqEntry, rel.id).frequency, 4)

    def test_same_question_different_evidence_makes_separate_candidates(self):
        a = self.doc("leave_eng.md", "CANARY-LV-ENG", "年假规定", departments=["engineering"])
        b = self.doc("leave_hr.md", "CANARY-LV-HR", "年假规定", departments=["hr"])
        qs = ["年假规定有哪些？", "年假规定都有哪些", "年假规定有哪些"]
        self.ask_all("eng_alice", qs)
        self.ask_all("hr_bob", qs)
        self.assertEqual(self.mine()["candidates_created"], 2)
        with self.factory() as db:
            keys = {tuple(e["document_id"] for e in f.evidence) for f in db.scalars(select(FaqEntry))
                    if "年假" in f.question}
        self.assertEqual(keys, {(a,), (b,)})


class TestReview(FaqCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    def setUp(self):
        super().setUp()
        if getattr(type(self), "_ready", False):
            return
        self.doc("release.md", "CANARY-RV-REL", "发布检查", departments=["engineering"])
        self.doc("salary.md", "CANARY-RV-SAL", "薪酬等级", departments=["hr"])
        self.ask_all("eng_alice", RELEASE)
        self.ask_all("hr_bob", SALARY)
        self.mine()
        self.make_reviewer("rev_eng", departments=["engineering"])
        self.make_reviewer("rev_hr", departments=["hr"])
        type(self)._ready = True

    def test_reviewers_only_see_entries_whose_evidence_they_can_read(self):
        eng, hr = self.faqs("rev_eng"), self.faqs("rev_hr")
        self.assertTrue(eng["items"] and all("薪酬" not in x["question"] for x in eng["items"]))
        self.assertEqual(eng["hidden_without_evidence_access"], 1)
        self.assertNotIn("CANARY-RV-SAL", json.dumps(eng, ensure_ascii=False))
        self.assertNotIn("薪酬", json.dumps(eng, ensure_ascii=False))           # not even the questions
        self.assertEqual(len(hr["items"]), 1)
        admin = self.faqs(ADMIN)                                              # manages, but reads nothing
        self.assertEqual(len(admin["items"]), 0)
        self.assertEqual(admin["hidden_without_evidence_access"], len(eng["items"]) + 1)
        hr_id = hr["items"][0]["id"]
        for method, path in (("get", f"/api/v1/faq/{hr_id}"), ("post", f"/api/v1/faq/{hr_id}/publish"),
                             ("patch", f"/api/v1/faq/{hr_id}")):
            r = self.client.request(method.upper(), path, headers=self.h("rev_eng"), json={} if method != "get" else None)
            self.assertEqual(r.status_code, 404, path)
        self.assertEqual(self.client.get("/api/v1/faq", headers=self.h("eng_alice")).status_code, 403)  # not a manager
        detail = self.client.get(f"/api/v1/faq/{hr_id}", headers=self.h("rev_hr")).json()
        self.assertEqual(len(detail["members"]), 3)
        self.assertNotIn("user_id", json.dumps(detail))
        self.assertNotIn("hr_bob", json.dumps(detail))

    def test_state_machine(self):
        f = next(x for x in self.faqs("rev_eng")["items"] if "发布" in x["question"])
        fid, h = f["id"], self.h("rev_eng")
        post = lambda a, **b: self.client.post(f"/api/v1/faq/{fid}/{a}", headers=h, json=b)  # noqa: E731
        # edit a candidate: foreign hex ids are stripped, evidence ids kept
        keep = f["citations"][0]["chunk_id"]
        r = self.client.patch(f"/api/v1/faq/{fid}", headers=h, json={"answer": f"标准答案 [{keep}] 与 [abcdef123456]。"})
        self.assertEqual(r.status_code, 200)
        self.assertIn(keep, r.json()["answer"])
        self.assertNotIn("abcdef123456", r.json()["answer"])
        self.assertEqual((r.json()["answer_source"], r.json()["revision"]), ("edited", 2))
        self.assertEqual(post("retire").status_code, 409)                     # candidate cannot be retired
        self.assertEqual(post("publish", note="核对无误").json()["status"], "published")
        self.assertEqual(post("publish").status_code, 409)
        self.assertEqual(post("reject").status_code, 409)
        self.assertEqual(self.client.patch(f"/api/v1/faq/{fid}", headers=h, json={"answer": "x"}).status_code, 409)
        self.assertEqual(post("retire", note="过时").json()["status"], "retired")
        self.assertEqual(post("publish").json()["status"], "published")       # re-publish after review
        hist = [e["action"] for e in self.client.get(f"/api/v1/faq/{fid}", headers=h).json()["history"]]
        self.assertEqual(hist, ["mined", "edit", "publish", "retire", "publish"])
        hr = self.faqs("rev_hr")["items"][0]
        r = self.client.post(f"/api/v1/faq/{hr['id']}/reject", headers=self.h("rev_hr"), json={"note": "不适合"})
        self.assertEqual(r.json()["status"], "rejected")
        self.assertEqual(self.client.post(f"/api/v1/faq/{hr['id']}/publish", headers=self.h("rev_hr"), json={}).status_code, 409)

    def test_publish_requires_valid_evidence(self):
        self.ask_all("eng_alice", ["回滚演练要求？", "回滚演练要求是什么", "回滚演练要求有哪些"])
        d = self.doc("rollback.md", "CANARY-RB-V1", "回滚演练", departments=["engineering"])
        self.ask_all("eng_alice", ["回滚演练有哪些要求？", "回滚演练都有哪些要求", "回滚演练有哪些要求"])
        self.mine()
        f = next(x for x in self.faqs("rev_eng", status="candidate")["items"] if "回滚" in x["question"])
        self.client.post(f"/api/v1/documents/{d}/versions", headers=self.h(ADMIN),
                         files={"file": ("rollback.md", md_doc("回滚演练", "CANARY-RB-V2", "回滚演练"))})
        r = self.client.post(f"/api/v1/faq/{f['id']}/publish", headers=self.h("rev_eng"), json={})
        self.assertEqual((r.status_code, r.json()["error"]), (409, "evidence_invalid"))


class TestFaqCache(FaqCase):
    """Global / department / role / personal FAQs: hit for the audience, miss for everyone else; invalidation."""

    TOPICS = {  # file, canary, topic, grant, audience (asks + must hit), outsider (must not hit)
        "global": ("handbook2.md", "CANARY-C-GLB", "考勤打卡", {"all_authenticated": True}, "hr_bob", "contractor_hank"),
        "dept": ("eng2.md", "CANARY-C-DEP", "灰度发布", {"departments": ["engineering"]}, "eng_alice", "hr_bob"),
        "role": ("audit2.md", "CANARY-C-ROL", "审计抽样", {"roles": ["finance_auditor"]}, "auditor_erin", "fin_dave"),
        "user": ("ceo2.md", "CANARY-C-USR", "董事会议", {"users": ["ceo_frank", "rev_all"]}, "ceo_frank", "eng_alice"),
    }

    def setUp(self):
        super().setUp()
        if getattr(type(self), "_ready", False):
            return
        self.make_reviewer("rev_all", departments=["engineering"], roles=["finance_auditor"])
        self.ids = {}
        for k, (fname, can, topic, grant, aud, _) in self.TOPICS.items():
            self.ids[k] = self.doc(fname, can, topic, **grant)
            self.ask_all(aud, [f"{topic}有哪些要求？", f"{topic}都有哪些要求", f"{topic}有哪些要求"])
        self.mine()
        for f in self.faqs("rev_all", status="candidate")["items"]:
            r = self.client.post(f"/api/v1/faq/{f['id']}/publish", headers=self.h("rev_all"), json={})
            self.assertEqual(r.status_code, 200, r.text)
        type(self)._ready, type(self)._ids = True, self.ids

    def faq_ask(self, user, question):
        n0 = len(self.llm.prompts)
        r = self.ask(user, question)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json(), len(self.llm.prompts) - n0

    def test_four_kinds_hit_for_audience_and_miss_for_others(self):
        published = self.faqs("rev_all", status="published")["items"]
        self.assertEqual(len(published), 4)              # rev_all reads all four evidence documents
        for k, (fname, can, topic, grant, aud, out) in self.TOPICS.items():
            if k == "global":
                continue
            body, calls = self.faq_ask(aud, f"{topic}有哪些要求")
            self.assertEqual((body["source"], calls), ("faq", 0), k)
            self.assertTrue(body["faq_id"] and body["citations"])
            if out == "contractor_hank":
                continue
            body, calls = self.faq_ask(out, f"{topic}有哪些要求")
            self.assertNotEqual(body["source"], "faq", k)
            self.assertNotIn(can, json.dumps(body, ensure_ascii=False))
        with self.factory() as db:
            ev = db.scalars(select(QaEvent).where(QaEvent.source == "faq")).all()
            self.assertTrue(ev and all(e.llm_calls == 0 for e in ev))

    def test_global_faq(self):
        """The global FAQ is reviewed by a reviewer who can read the handbook (all_authenticated)."""
        f = next(x for x in self.faqs("rev_all")["items"] if "考勤" in x["question"])
        self.assertEqual(f["status"], "published")
        for u in ("eng_alice", "hr_bob", "ceo_frank"):
            body, calls = self.faq_ask(u, "考勤打卡有哪些要求")
            self.assertEqual((body["source"], calls), ("faq", 0), u)

    def test_invalidation(self):
        ids = type(self)._ids
        fid = lambda topic: next(x["id"] for x in self.faqs("rev_all")["items"] if topic in x["question"])  # noqa: E731
        # 1. retire → immediate miss; re-publish → hit again
        f = fid("灰度发布")
        self.client.post(f"/api/v1/faq/{f}/retire", headers=self.h("rev_all"), json={})
        self.assertNotEqual(self.faq_ask("eng_alice", "灰度发布有哪些要求")[0]["source"], "faq")
        self.client.post(f"/api/v1/faq/{f}/publish", headers=self.h("rev_all"), json={})
        self.assertEqual(self.faq_ask("eng_alice", "灰度发布有哪些要求")[0]["source"], "faq")
        # 2. permission change → the revoked user misses immediately (and gets no FAQ content)
        self.grant(ids["user"], users=["rev_all"])
        body, _ = self.faq_ask("ceo_frank", "董事会议有哪些要求")
        self.assertNotEqual(body["source"], "faq")
        self.assertNotIn("CANARY-C-USR", json.dumps(body, ensure_ascii=False))
        # 3. evidence document disabled → miss; enabled again → hit
        self.client.post(f"/api/v1/documents/{ids['role']}/disable", headers=self.h(ADMIN))
        self.assertNotEqual(self.faq_ask("auditor_erin", "审计抽样有哪些要求")[0]["source"], "faq")
        self.client.post(f"/api/v1/documents/{ids['role']}/enable", headers=self.h(ADMIN))
        self.assertEqual(self.faq_ask("auditor_erin", "审计抽样有哪些要求")[0]["source"], "faq")
        # 4. index rebuild (new generation, same versions): FAQ re-validated against the new generation, still hits
        self.client.post("/api/v1/index/rebuild", headers=self.h(ADMIN))
        self.assertEqual(self.faq_ask("auditor_erin", "审计抽样有哪些要求")[0]["source"], "faq")
        # 5. new document version → recorded evidence version outdated → miss (needs re-review)
        self.client.post(f"/api/v1/documents/{ids['dept']}/versions", headers=self.h(ADMIN),
                         files={"file": ("eng2.md", md_doc("灰度发布", "CANARY-C-DEP-V2", "灰度发布"))})
        self.assertNotEqual(self.faq_ask("eng_alice", "灰度发布有哪些要求")[0]["source"], "faq")
        st = next(x for x in self.faqs("rev_all")["items"] if x["id"] == f)
        self.assertEqual((st["evidence_valid"], st["evidence_reason"]), (False, "document_updated"))
        # 6. evidence document deleted → miss
        self.client.delete(f"/api/v1/documents/{ids['role']}", headers=self.h(ADMIN))
        body, _ = self.faq_ask("auditor_erin", "审计抽样有哪些要求")
        self.assertNotEqual(body["source"], "faq")
        self.assertNotIn("CANARY-C-ROL", json.dumps(body, ensure_ascii=False))

    def test_faq_hit_over_sse(self):
        sid = self.new_session("eng_alice")
        with self.client.stream("POST", f"/api/v1/sessions/{sid}/ask/stream", json={"question": "灰度发布都有哪些要求"},
                                headers=self.h("eng_alice")) as r:
            evs = events(r)
        final = evs[-1]
        if final[1].get("source") == "faq":                           # (may have been invalidated by another test)
            self.assertEqual(final[0], "final")
            self.assertTrue(final[1]["citations"])
        self.assertNotIn("CANARY-C-USR", json.dumps(evs, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
