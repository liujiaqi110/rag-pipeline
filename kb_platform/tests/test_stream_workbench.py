"""P4 workbench backend: SSE streaming through the ACL chain, revocation / logout during streaming, ID masking,
citation source lookup, system status, department tree (no ACL inheritance), UI capability hints."""

import json
import threading
import time
import unittest

from kb_platform.tests.helpers import ADMIN
from kb_platform.tests.ingest_fixtures import IngestCase, md_doc
from kb_platform.streaming import DeltaMasker
from ragas_eval.tests.acl_fixtures import cite_first_context


def events(resp):
    out, ev = [], None
    for line in resp.iter_lines():
        if line.startswith("event: "):
            ev = line[7:]
        elif line.startswith("data: "):
            out.append((ev, json.loads(line[6:])))
    return out


class StreamCase(IngestCase):
    def setUp(self):
        self.llm.responder = cite_first_context

    def stream(self, user, question, sid=None, headers=None):
        sid = sid or self.new_session(user)
        with self.client.stream("POST", f"/api/v1/sessions/{sid}/ask/stream", json={"question": question},
                                headers=headers or self.h(user)) as r:
            return r.status_code, r.headers.get("content-type", ""), (events(r) if r.status_code == 200 else r.read()), sid


class TestSse(StreamCase):
    def test_stream_events_and_acl(self):
        a = self.upload("eng.md", md_doc("工程", "CANARY-SSE-ENG", "流式专项")).json()["document"]["id"]
        b = self.upload("hr.md", md_doc("人事", "CANARY-SSE-HR", "流式专项人事")).json()["document"]["id"]
        self.grant(a, departments=["engineering"])
        self.grant(b, departments=["hr"])
        n0 = len(self.llm.prompts)
        code, ctype, evs, sid = self.stream("eng_alice", "流式专项 有哪些要求？")
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith("text/event-stream"))
        kinds = [e for e, _ in evs]
        self.assertEqual(kinds[0], "status")
        self.assertIn("delta", kinds)
        self.assertEqual(kinds[-1], "final")
        final = evs[-1][1]
        streamed = "".join(d["text"] for e, d in evs if e == "delta")
        self.assertEqual(streamed.strip(), final["answer"].strip())
        self.assertTrue(final["citations"] and all(c["source"].endswith("eng.md") for c in final["citations"]))
        blob = json.dumps(evs, ensure_ascii=False) + "\n".join(self.llm.prompts[n0:])
        self.assertNotIn("CANARY-SSE-HR", blob)
        self.assertNotIn("hr.md", json.dumps(evs, ensure_ascii=False))
        hist = self.client.get(f"/api/v1/sessions/{sid}", headers=self.h("eng_alice")).json()
        self.assertEqual(hist["messages"][-1]["answer"], final["answer"])
        # unauthorised user: nothing of the document in any event
        code, _, evs, _ = self.stream("hr_intern_carol", "流式专项 有哪些要求？")
        self.assertNotIn("CANARY-SSE", json.dumps(evs, ensure_ascii=False))

    def test_hallucinated_ids_are_masked_in_deltas(self):
        a = self.upload("mask.md", md_doc("屏蔽", "CANARY-MASK", "屏蔽专项")).json()["document"]["id"]
        self.grant(a, departments=["engineering"])
        foreign = "5296e8a5e94b"
        self.llm.responder = lambda m: f"参见 [{foreign}] 与 [abc123def456] 以及 3000000 元。\n引用：{foreign}"
        code, _, evs, _ = self.stream("eng_alice", "屏蔽专项？")
        blob = json.dumps(evs, ensure_ascii=False)
        self.assertNotIn(foreign, blob)
        self.assertNotIn("abc123def456", blob)
        self.assertIn("3000000", blob)                              # ordinary numbers are not masked
        self.assertEqual(evs[-1][1]["citations"], [])

    def test_masker_holds_back_split_hex(self):
        m = DeltaMasker({"aaaaaa111111"})
        out = m.feed("见 [aaaaaa") + m.feed("111111] 和 [bbbb") + m.feed("bb222222]") + m.flush()
        self.assertEqual(out, "见 [aaaaaa111111] 和 [[…]]")

    def test_revocation_during_stream_stops_and_hides(self):
        a = self.upload("rev.md", md_doc("撤销", "CANARY-SSE-REV", "撤销专项")).json()["document"]["id"]
        self.grant(a, users=["fin_dave"])
        gate, started = threading.Event(), threading.Event()

        def slow(m):
            started.set()
            gate.wait(20)
            return cite_first_context(m)
        self.llm.responder = slow
        sid = self.new_session("fin_dave")
        h = self.h("fin_dave")
        result = {}

        def run():
            with self.client.stream("POST", f"/api/v1/sessions/{sid}/ask/stream", json={"question": "撤销专项？"},
                                    headers=h) as r:
                result["events"] = events(r)
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(started.wait(20))
        self.grant(a, users=["hr_bob"])                              # revoke fin_dave while the model is generating
        t.join(30)
        gate.set()
        kinds = [e for e, _ in result["events"]]
        self.assertEqual(kinds[-1], "revoked")
        self.assertNotIn("final", kinds)
        self.assertNotIn("CANARY-SSE-REV", json.dumps(result["events"], ensure_ascii=False))
        time.sleep(0.5)
        hist = self.client.get(f"/api/v1/sessions/{sid}", headers=h).json()
        self.assertTrue(all(m["hidden"] for m in hist["messages"]))

    def test_logout_during_stream(self):
        a = self.upload("out.md", md_doc("退出", "CANARY-SSE-OUT", "退出专项")).json()["document"]["id"]
        self.grant(a, users=["auditor_erin"])
        gate, started = threading.Event(), threading.Event()

        def slow(m):
            started.set()
            gate.wait(20)
            return cite_first_context(m)
        self.llm.responder = slow
        h = self.h("auditor_erin", fresh=True)
        sid = self.client.post("/api/v1/sessions", json={"kb_id": self.kb_id}, headers=h).json()["id"]
        result = {}

        def run():
            with self.client.stream("POST", f"/api/v1/sessions/{sid}/ask/stream", json={"question": "退出专项？"},
                                    headers=h) as r:
                result["events"] = events(r)
        t = threading.Thread(target=run)
        t.start()
        self.assertTrue(started.wait(20))
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=h).status_code, 204)
        t.join(30)
        gate.set()
        self.assertEqual(result["events"][-1][0], "revoked")
        self.assertEqual(self.client.get("/api/v1/sessions", headers=h).status_code, 401)
        self._tokens.pop("auditor_erin", None)

    def test_stream_errors_before_streaming(self):
        sid = self.new_session("eng_alice")
        r = self.client.post(f"/api/v1/sessions/{sid}/ask/stream", json={"question": "x"})
        self.assertEqual(r.status_code, 401)
        r = self.client.post(f"/api/v1/sessions/{sid}/ask/stream", json={"question": "x"}, headers=self.h("hr_bob"))
        self.assertEqual(r.status_code, 404)
        r = self.client.post(f"/api/v1/sessions/{sid}/ask/stream", json={"question": "x", "user_id": "hr_bob"},
                             headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 422)


class TestWorkbenchEndpoints(StreamCase):
    def test_citation_detail_respects_acl_and_version(self):
        d = self.upload("cite.md", md_doc("引用", "CANARY-CITE-V1", "引用专项")).json()["document"]["id"]
        self.grant(d, departments=["engineering"])
        code, _, evs, _ = self.stream("eng_alice", "引用专项？")
        cid = evs[-1][1]["citations"][0]["chunk_id"]
        r = self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/chunks/{cid}", headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("CANARY-CITE-V1", r.json()["text"])
        self.assertEqual(r.json()["document_id"], d)
        self.assertEqual(self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/chunks/{cid}",
                                         headers=self.h("hr_bob")).status_code, 404)
        self.client.post(f"/api/v1/documents/{d}/versions", files={"file": ("cite.md", md_doc("引用", "CANARY-CITE-V2", "引用专项x"))},
                         headers=self.h(ADMIN))
        r = self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/chunks/{cid}", headers=self.h("eng_alice"))
        self.assertTrue(r.status_code == 404 or "CANARY-CITE-V1" not in r.text)     # old text never served
        self.client.post(f"/api/v1/documents/{d}/disable", headers=self.h(ADMIN))
        self.assertEqual(self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/chunks/{cid}",
                                         headers=self.h("eng_alice")).status_code, 404)

    def test_system_status(self):
        r = self.client.get("/api/v1/system/status", headers=self.h(ADMIN))
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertTrue(b["database"]["ok"])
        self.assertEqual(b["database"]["revision"], b["database"]["head"])
        self.assertIn("token_format", b["settings"])
        self.assertNotIn(self.app.state.jwt.secret.decode(), r.text)
        self.assertEqual(self.client.get("/api/v1/system/status", headers=self.h("eng_alice")).status_code, 403)

    def test_department_tree_has_no_acl_inheritance(self):
        h = self.h(ADMIN)
        self.assertEqual(self.client.post("/api/v1/admin/departments", headers=h,
                                          json={"name": "rnd", "description": "研发中心"}).status_code, 201)
        r = self.client.post("/api/v1/admin/departments", headers=h, json={"name": "rnd_platform", "parent": "rnd"})
        self.assertEqual((r.status_code, r.json()["parent"]), (201, "rnd"))
        self.assertEqual(self.client.patch("/api/v1/admin/departments/rnd", headers=h, json={"parent": "rnd_platform"}).json()["error"],
                         "department_cycle")
        self.assertEqual(self.client.post("/api/v1/admin/departments", headers=h, json={"name": "x", "parent": "nope"}).status_code, 422)
        self.client.post("/api/v1/admin/users", headers=h, json={"username": "plat_user", "password": "Plat-pass-001",
                                                                "departments": ["rnd_platform"], "clearance": 1})
        d = self.upload("tree.md", md_doc("树", "CANARY-TREE", "树形专项")).json()["document"]["id"]
        self.grant(d, departments=["rnd"])                           # grant to the PARENT department only
        self.creds["plat_user"] = "Plat-pass-001"
        docs = self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/documents", headers=self.h("plat_user")).json()
        self.assertNotIn(d, {x["id"] for x in docs})                # child department does NOT inherit the grant
        r, p = self.prompts_for("plat_user", "树形专项？")
        self.assertNotIn("CANARY-TREE", p + r.text)

    def test_me_capabilities(self):
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.h("eng_alice")).json()["managed_kb_ids"], [])
        self.assertIn(self.kb_id, self.client.get("/api/v1/auth/me", headers=self.h(ADMIN)).json()["managed_kb_ids"])


if __name__ == "__main__":
    unittest.main()
