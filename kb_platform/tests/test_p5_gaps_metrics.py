"""P5 knowledge gaps (classification, dedupe, repeat flag, filters, tasks, verification) and operations metrics
(aggregation correctness against seeded service-log rows, scoping)."""

import json
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from kb_platform.models import DocumentVersion, User
from kb_platform.models_ops import KnowledgeGap, KnowledgeTask, PageView, QaEvent
from kb_platform.ops import metrics as M
from kb_platform.ops.events import classify
from kb_platform.tests.helpers import ADMIN
from kb_platform.tests.ingest_fixtures import IngestCase, md_doc
from kb_platform.tests.test_p5_faq import P5
from ragas_eval import local_llm as ll
from ragas_eval.tests.acl_fixtures import cite_first_context

REFUSAL = "根据提供的资料无法回答该问题。\n引用：无"


class TestClassification(unittest.TestCase):
    def test_outcome_matrix(self):
        self.assertEqual(classify("ok", 3, 0.9, 0.35), ("answered", None))
        self.assertEqual(classify("partial_answer", 3, 0.9, 0.35), ("answered", None))
        self.assertEqual(classify("refused", 0, None, 0.35), ("knowledge_gap", "no_evidence"))
        self.assertEqual(classify("refused", 5, 0.2, 0.35), ("knowledge_gap", "low_relevance"))
        self.assertEqual(classify("insufficient_evidence", 5, 0.8, 0.35), ("knowledge_gap", "insufficient_evidence"))
        self.assertEqual(classify("insufficient_scoped_evidence", 2, None, 0.35), ("knowledge_gap", "insufficient_evidence"))
        self.assertEqual(classify("access_denied", 0, None, 0.35), ("permission", None))
        self.assertEqual(classify("blocked", 0, None, 0.35), ("permission", None))
        self.assertEqual(classify("generation_error", 4, None, 0.35), ("generation_failure", None))
        self.assertEqual(classify("retrieval_error", 0, None, 0.35), ("infrastructure", None))
        self.assertEqual(classify("error", 0, None, 0.35, "dense_unavailable"), ("infrastructure", None))
        self.assertEqual(classify("error", 0, None, 0.35, "index_unavailable"), ("infrastructure", None))


class GapCase(IngestCase):
    extra_settings = P5

    def setUp(self):
        def responder(m):
            return cite_first_context(m) if "CANARY-GAP-FIX" in m[-1]["content"] or "CANARY-BASE" in m[-1]["content"] \
                and "差旅" not in m[-1]["content"] else REFUSAL
        self.llm.responder = responder
        if not getattr(type(self), "_base", False):
            d = self.upload("base.md", md_doc("基础", "CANARY-BASE", "办公设备")).json()["document"]["id"]
            self.grant(d, all_authenticated=True)
            type(self)._base, type(self)._base_id = True, d

    def gaps(self, user=ADMIN, **q):
        r = self.client.get("/api/v1/gaps", params=q, headers=self.h(user))
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def events(self):
        with self.factory() as db:
            return list(db.scalars(select(QaEvent).order_by(QaEvent.id)))


class TestGaps(GapCase):
    def test_dedupe_repeat_task_and_verification(self):
        s1, s2 = self.new_session("eng_alice"), self.new_session("hr_bob")
        for u, s, q in (("eng_alice", s1, "差旅报销标准是多少？"), ("hr_bob", s2, "差旅报销标准是多少"),
                        ("eng_alice", s1, "差旅报销的标准是多少？")):
            r = self.ask(u, q, s).json()
            self.assertEqual(r["status"], "refused")
        g = [x for x in self.gaps() if "差旅" in x["question"]]
        self.assertEqual(len(g), 1)                                        # three askings → one gap
        g = g[0]
        self.assertEqual((g["occurrences"], g["distinct_users"], g["repeated"], g["status"]), (3, 2, True, "open"))
        self.assertIn(g["gap_type"], ("low_relevance", "insufficient_evidence"))
        detail = self.client.get(f"/api/v1/gaps/{g['id']}", headers=self.h(ADMIN)).json()
        self.assertEqual(len(detail["occurrence_list"]), 3)
        self.assertNotIn("user_id", json.dumps(detail))
        self.assertNotIn("eng_alice", json.dumps(detail))
        # gap → supplement task
        t = self.client.post(f"/api/v1/gaps/{g['id']}/tasks", headers=self.h(ADMIN),
                             json={"title": "补充差旅报销标准", "assignee": "hr_bob"}).json()
        self.assertEqual((t["status"], t["gap_status"]), ("todo", "in_progress"))
        # verification BEFORE the knowledge exists → still unresolved
        self.client.patch(f"/api/v1/tasks/{t['id']}", headers=self.h(ADMIN), json={"status": "done"})
        g1 = self.client.get(f"/api/v1/gaps/{g['id']}", headers=self.h(ADMIN)).json()
        self.assertEqual((g1["status"], g1["verification"]["resolved"]), ("in_progress", False))
        self.assertEqual(self.client.get("/api/v1/tasks", headers=self.h(ADMIN)).json()[0]["status"], "reopened")
        # add the missing document, link it, complete the task → automatic verification as the original askers
        d = self.upload("travel.md", md_doc("差旅报销", "CANARY-GAP-FIX", "差旅报销标准")).json()["document"]["id"]
        self.grant(d, all_authenticated=True)
        t2 = self.client.patch(f"/api/v1/tasks/{t['id']}", headers=self.h(ADMIN),
                               json={"status": "done", "document_id": d}).json()
        self.assertTrue(t2["verify_job_id"])
        g2 = self.client.get(f"/api/v1/gaps/{g['id']}", headers=self.h(ADMIN)).json()
        self.assertEqual(g2["status"], "resolved")
        v = g2["verification"]
        self.assertTrue(v["resolved"])
        self.assertEqual(len(v["results"]), 2)                             # both distinct askers re-checked
        self.assertTrue(all(r["answered"] and d in r["documents"] for r in v["results"]))
        self.assertNotIn("CANARY", json.dumps(v))                          # statuses only, never answer text
        task = next(x for x in self.client.get("/api/v1/tasks", headers=self.h(ADMIN)).json() if x["id"] == t["id"])
        self.assertEqual((task["status"], task["document_title"]), ("verified", "travel.md"))
        # recurrence after resolution re-opens the gap
        self.llm.responder = lambda m: REFUSAL
        self.ask("eng_alice", "差旅报销的标准是多少呢", s1)              # new wording (not answer-cached), refused
        self.assertEqual(self.client.get(f"/api/v1/gaps/{g['id']}", headers=self.h(ADMIN)).json()["status"], "open")

    def test_non_gaps_are_classified_apart(self):
        before = len(self.gaps())
        # permission: a user who had access when the session was created and then lost all read access
        self.client.post("/api/v1/admin/users", headers=self.h(ADMIN), json={"username": "perm_user", "password": "Perm-pass-001",
                                                                            "departments": ["engineering"], "clearance": 1})
        self.creds["perm_user"] = "Perm-pass-001"
        sid = self.new_session("perm_user")
        self.client.patch("/api/v1/admin/users/perm_user", headers=self.h(ADMIN), json={"clearance": 0})
        self._tokens.pop("perm_user", None)                                # the change revoked the old token
        r = self.ask("perm_user", "差旅报销标准是多少？", sid).json()
        self.assertEqual(r["status"], "access_denied")
        # generation failure
        def boom(m):
            return ll.GenerationError("unavailable", "down")
        self.llm.responder = boom
        r = self.ask("fin_dave", "办公设备有哪些要求？").json()
        self.assertEqual(r["status"], "generation_error")
        ev = self.events()
        self.assertEqual(ev[-2].outcome, "permission")
        self.assertEqual(ev[-1].outcome, "generation_failure")
        self.assertIsNone(ev[-1].gap_type)
        self.assertEqual(len(self.gaps()), before)                         # neither became a knowledge gap

    def test_restricted_knowledge_is_permission_not_gap(self):
        """The answer exists, but only in a document the asker may not read → permission (restricted_knowledge),
        no gap; the user still just gets a refusal that does not hint at the document."""
        d = self.upload("hrpay.md", md_doc("薪资发放", "CANARY-PAY", "薪资发放日期")).json()["document"]["id"]
        self.grant(d, departments=["hr"])
        before = len(self.gaps())
        self.llm.responder = lambda m: REFUSAL
        r = self.ask("eng_alice", "薪资发放日期是哪天？").json()
        self.assertEqual(r["status"], "refused")
        self.assertNotIn("CANARY-PAY", json.dumps(r, ensure_ascii=False))
        self.assertNotIn("hrpay", json.dumps(r, ensure_ascii=False))
        ev = self.events()[-1]
        self.assertEqual((ev.outcome, ev.error_code, ev.gap_type), ("permission", "restricted_knowledge", None))
        self.assertEqual(len(self.gaps()), before)
        m = self.client.get("/api/v1/ops/metrics", headers=self.h(ADMIN)).json()
        self.assertGreaterEqual(m["restricted_knowledge"], 1)

    def test_stale_knowledge_filters_and_isolation(self):
        with self.factory() as db:
            v = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == type(self)._base_id))
            v.created_at = datetime.now(timezone.utc) - timedelta(days=800)
            db.commit()
        r = self.ask("eng_alice", "办公设备有哪些要求？").json()
        self.assertEqual(r["status"], "ok")
        stale = self.gaps(gap_type="stale_knowledge")
        self.assertEqual(len(stale), 1)
        self.assertEqual(self.events()[-1].outcome, "answered")            # answered, but flagged as outdated knowledge
        self.assertEqual(self.gaps(status="resolved", gap_type="stale_knowledge"), [])
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        self.assertEqual(self.gaps(since=future), [])
        self.assertEqual(self.client.get("/api/v1/gaps", headers=self.h("eng_alice")).status_code, 403)
        self.assertEqual(self.client.get(f"/api/v1/gaps/{stale[0]['id']}", headers=self.h("ceo_frank")).status_code, 403)
        with self.factory() as db:
            v = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == type(self)._base_id))
            v.created_at = datetime.now(timezone.utc)
            db.commit()


class TestInfrastructureIsNotAGap(IngestCase):
    extra_settings = P5
    embedder_available = False                     # dense unavailable → 503 in rrf mode

    def test_dense_unavailable(self):
        d = self.upload("x.md", md_doc("x", "CANARY-INF", "基础设施")).json()["document"]["id"]
        self.grant(d, all_authenticated=True)
        r = self.ask("eng_alice", "基础设施有哪些要求？")
        self.assertEqual(r.status_code, 503)
        with self.factory() as db:
            ev = db.scalars(select(QaEvent).order_by(QaEvent.id.desc())).first()
            self.assertEqual((ev.outcome, ev.error_code, ev.source), ("infrastructure", "dense_unavailable", "none"))
            self.assertEqual(db.scalars(select(KnowledgeGap)).all(), [])


class TestMetricsAggregation(GapCase):
    def test_aggregation_matches_seeded_service_log(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)            # a Friday
        with self.factory() as db:
            db.query(QaEvent).delete()
            db.query(PageView).delete()
            uid = {u.username: u.id for u in db.scalars(select(User))}
            pv = [("eng_alice", 0), ("eng_alice", 0), ("hr_bob", 1), ("ceo_frank", 20)]       # last one outside window
            for u, days in pv:
                db.add(PageView(user_id=uid[u], route="chat", ts=(now - timedelta(days=days)).replace(tzinfo=None)))
            rows = [  # (days ago, source, outcome, llm, prompt, output, gen_ms, qhash)
                (0, "rag", "answered", 1, 100, 20, 1000.0, "q1"), (0, "rag", "answered", 1, 200, 40, 2000.0, "q1"),
                (0, "faq", "answered", 0, 0, 0, None, "q1"), (1, "answer_cache", "answered", 0, 0, 0, None, "q2"),
                (2, "rag", "knowledge_gap", 1, 50, 10, 3000.0, "q3"), (3, "rag", "generation_failure", 1, 0, 0, 4000.0, "q4"),
                (4, "none", "infrastructure", 0, 0, 0, None, "q5"), (5, "none", "permission", 0, 0, 0, None, "q6"),
                (30, "rag", "answered", 1, 999, 999, 9999.0, "old"),                                # outside window
            ]
            for days, src, out, llm, pt, ot, g, h in rows:
                db.add(QaEvent(ts=(now - timedelta(days=days)).replace(tzinfo=None), user_id=uid["eng_alice"],
                               kb_id=self.kb_id, question_hash=h, source=src, status="x", outcome=out, llm_calls=llm,
                               prompt_tokens=pt, output_tokens=ot, generation_ms=g, latency_ms=(g or 5.0) + 10,
                               model="qwen2.5:7b" if llm else None,
                               citations=[{"chunk_id": "c", "source": "base_doc"}] if out == "answered" else []))
            db.commit()
            m = M.compute(db, days=14, now=now)
        self.assertEqual(m["traffic"], {"pv": 3, "uv": 2})
        q = m["questions"]
        self.assertEqual((q["window"], q["today"], q["this_week"]), (8, 3, 7))   # week starts Monday 11-16
        self.assertEqual(q["by_source"], {"rag": 4, "faq": 1, "answer_cache": 1, "none": 2})
        self.assertEqual(m["llm"]["calls"], 4)
        self.assertEqual((m["llm"]["prompt_tokens"], m["llm"]["output_tokens"]), (350, 70))
        self.assertEqual((m["llm"]["generation_p50_ms"], m["llm"]["generation_p95_ms"]), (2500.0, 3850.0))
        self.assertEqual(m["faq"]["hits"], 1)
        self.assertEqual(m["faq"]["hit_rate"], round(1 / 8, 4))
        self.assertEqual(m["faq"]["saved_llm_calls"], 1)
        self.assertEqual(m["faq"]["answer_cache_hits"], 1)
        self.assertEqual(m["failures"], {"failed": 2, "generation_failure": 1, "infrastructure": 1, "permission_denied": 1})
        self.assertEqual(m["top_questions"][0]["count"], 3)
        self.assertEqual(m["top_questions"][0]["faq_hits"], 1)
        self.assertEqual(m["top_cited"][0]["citations"], 4)
        self.assertEqual(sum(d["questions"] for d in m["daily"]), 8)
        self.assertEqual(len(m["daily"]), 15)
        for k in ("pv", "uv", "questions", "llm_calls", "faq_hits", "failed", "faq_hit_rate", "latency"):
            self.assertIn(k, m["definitions"])

    def test_api_scoping(self):
        r = self.client.get("/api/v1/ops/metrics", headers=self.h(ADMIN))
        self.assertEqual(r.status_code, 200)
        self.assertIsNotNone(r.json()["traffic"]["pv"])
        self.assertEqual(self.client.get("/api/v1/ops/metrics", headers=self.h("eng_alice")).status_code, 403)
        self.client.post(f"/api/v1/knowledge-bases/{self.kb_id}/managers", headers=self.h(ADMIN), json={"username": "hr_bob"})
        r = self.client.get("/api/v1/ops/metrics", headers=self.h("hr_bob")).json()
        self.assertIsNone(r["traffic"]["pv"])                                  # platform-wide PV/UV: admins only
        self.assertEqual(self.client.post("/api/v1/ops/pageview", headers=self.h("eng_alice"), json={"route": "chat"}).status_code, 204)
        self.assertEqual(self.client.post("/api/v1/ops/pageview", headers=self.h("eng_alice"),
                                          json={"route": "/chat?x=secret"}).status_code, 422)   # route names only


if __name__ == "__main__":
    unittest.main()
