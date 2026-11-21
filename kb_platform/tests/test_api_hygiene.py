"""Errors, logs, audit hygiene; legacy entry point not reachable; OpenAPI contract."""

import ast
import json
import unittest
from pathlib import Path

from sqlalchemy import text

from kb_platform.app import create_app
from kb_platform.rag_backend import RagBackend
from kb_platform.settings import Settings
from kb_platform.tests.helpers import ADMIN, CANARIES, ApiCase

PKG = Path(__file__).resolve().parent.parent


class TestErrorHygiene(ApiCase):
    def test_validation_errors_never_echo_input(self):
        secret = "Sup3r-Secret-Value-" + "z" * 300
        r = self.client.post("/api/v1/auth/login", json={"username": "eng_alice", "password": secret})
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("Sup3r-Secret", r.text)
        sid = self.new_session("eng_alice")
        q = "ACLCANARY-HR-7319 " * 200
        r = self.client.post(f"/api/v1/sessions/{sid}/ask", json={"question": q}, headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 422)
        self.assertNotIn("ACLCANARY", r.text)
        self.assertEqual(set(r.json()), {"error", "message", "request_id"})

    def test_unknown_route_and_method(self):
        r = self.client.get("/api/v1/nope", headers=self.h("eng_alice"))
        self.assertEqual((r.status_code, r.json()["error"]), (404, "not_found"))
        r = self.client.put("/api/v1/auth/me", headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 405)

    def test_internal_error_is_generic(self):
        old = self.llm.responder

        def boom(m):
            raise RuntimeError("leak " + CANARIES["exec_strategy_memo.md"])
        self.llm.responder = boom
        try:
            from fastapi.testclient import TestClient
            c = TestClient(self.app, raise_server_exceptions=False)
            sid = self.new_session("eng_alice")
            r = c.post(f"/api/v1/sessions/{sid}/ask", json={"question": "发布前需要做哪些检查？"}, headers=self.h("eng_alice"))
        finally:
            self.llm.responder = old
        self.assertEqual((r.status_code, r.json()["error"]), (500, "internal_error"))
        self.assertNotIn("ACLCANARY", r.text)
        self.assertFalse(any("ACLCANARY" in line for line in self.log.lines))

    def test_index_unavailable_is_503(self):
        def missing():
            raise FileNotFoundError("index not found at C:/secret/path")
        app = create_app(Settings(database_url="sqlite://"), RagBackend(retriever_factory=missing, llm_factory=lambda: None))
        from fastapi.testclient import TestClient
        from kb_platform.cli import seed_example
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            out = seed_example(app.state.sessionmaker, Path(t) / "c.json")
            creds = json.loads((Path(t) / "c.json").read_text(encoding="utf-8"))
        c = TestClient(app)
        tok = c.post("/api/v1/auth/login", json={"username": "eng_alice", "password": creds["eng_alice"]}).json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}
        sid = c.post("/api/v1/sessions", json={"kb_id": out["kb_id"]}, headers=h).json()["id"]
        r = c.post(f"/api/v1/sessions/{sid}/ask", json={"question": "发布检查？"}, headers=h)
        self.assertEqual((r.status_code, r.json()["error"]), (503, "index_unavailable"))
        self.assertNotIn("secret/path", r.text)

    def test_request_id_and_no_store(self):
        r = self.client.get("/api/v1/health", headers={"X-Request-ID": "abc-123"})
        self.assertEqual(r.headers["X-Request-ID"], "abc-123")
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        r = self.client.get("/api/v1/health", headers={"X-Request-ID": "bad id\n<script>"})
        self.assertRegex(r.headers["X-Request-ID"], r"^[0-9a-f]{32}$")


class TestLogsAndAudit(ApiCase):
    def test_no_tokens_passwords_or_texts_in_logs_or_audit(self):
        import logging
        tok = self.token("hr_bob", fresh=True)
        question = "薪酬等级和调薪流程是什么？请详细说明。"
        r = self.ask("hr_bob", question)
        self.assertEqual(r.status_code, 200)
        answer = r.json()["answer"]
        self.login("hr_bob", "wrong-but-long-password-1")
        self.login(self.creds["eng_alice"], "x")                       # password typed into the username box
        logging.getLogger("kb_platform").info("debug Authorization: Bearer %s password=%s", tok, self.creds["hr_bob"])
        logs = "\n".join(self.log.lines)
        with self.factory() as db:
            audit_dump = json.dumps([list(map(str, row)) for row in db.execute(text("SELECT * FROM audit_log"))],
                                    ensure_ascii=False)
        for blob, where in ((logs, "logs"), (audit_dump, "audit")):
            self.assertNotIn(tok, blob, where)
            self.assertNotIn(self.creds["hr_bob"], blob, where)
            self.assertNotIn(self.creds["eng_alice"], blob, where)
            self.assertNotIn("wrong-but-long-password-1", blob, where)
            for c in CANARIES.values():
                self.assertNotIn(c, blob, where)
        self.assertNotIn(question, audit_dump)
        self.assertNotIn(answer[:20], audit_dump)
        self.assertIn("question_sha256", audit_dump)
        self.assertIn("[REDACTED]", logs)

    def test_audit_records_security_events(self):
        self.login("eng_alice", "definitely-wrong-1")
        self.ask("eng_alice", "发布前需要做哪些检查？")
        rows = self.client.get("/api/v1/audit?limit=200", headers=self.h(ADMIN)).json()
        actions = {(r["action"], r["outcome"]) for r in rows}
        self.assertIn(("auth.login", "failure"), actions)
        self.assertIn(("auth.login", "success"), actions)
        self.assertIn(("chat.ask", "ok"), actions)
        ask = next(r for r in rows if r["action"] == "chat.ask")
        self.assertEqual(set(ask["detail"]) - {"question_sha256", "question_chars", "status", "llm_calls",
                                               "authorized_documents", "authorized_chunks", "policy_version",
                                               "error", "cached", "citations", "kb_id", "reason"}, set())


class TestLegacyEntryNotExposed(unittest.TestCase):
    def test_no_module_imports_the_legacy_service(self):
        for p in PKG.rglob("*.py"):
            if "tests" in p.parts:
                continue
            tree = ast.parse(p.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [f"{node.module}.{a.name}" for a in node.names] + [node.module or ""]
                self.assertFalse(any("rag_service" in n for n in names), f"{p.name}: {names}")

    def test_backend_only_builds_the_acl_service(self):
        from ragas_eval.acl import AclPolicy
        from ragas_eval.kb_service import KnowledgeBaseService
        b = RagBackend(retriever_factory=lambda: __import__("ragas_eval.tests.acl_fixtures", fromlist=["x"]).FakeBaseRetriever([]),
                       llm_factory=lambda: None)
        svc = b.service(AclPolicy({"policy_version": "t", "users": {}, "documents": {}}))
        self.assertIsInstance(svc, KnowledgeBaseService)

    def test_routes_are_the_documented_set(self):
        app = create_app(Settings(database_url="sqlite://"))
        paths = set(app.openapi()["paths"])
        self.assertFalse(any("legacy" in p or "rag_service" in p or "no-safety" in p for p in paths))
        spec = app.openapi()
        public = {("/api/v1/health", "get"), ("/api/v1/auth/login", "post")}
        for path, ops in spec["paths"].items():
            for method, op in ops.items():
                if (path, method) in public:
                    continue
                self.assertTrue(op.get("security"), f"{method} {path} must require the bearer token")


class TestOpenApiSnapshot(unittest.TestCase):
    def test_docs_openapi_json_is_current(self):
        snap = PKG.parent / "docs" / "openapi.json"
        self.assertTrue(snap.exists(), "run: python -m kb_platform.cli export-openapi")
        spec = create_app(Settings(database_url="sqlite://")).openapi()
        self.assertEqual(json.loads(snap.read_text(encoding="utf-8")), json.loads(json.dumps(spec)),
                         "docs/openapi.json is stale: python -m kb_platform.cli export-openapi")


if __name__ == "__main__":
    unittest.main()
