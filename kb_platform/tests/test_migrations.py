"""Alembic migrations: legacy databases are upgraded IN PLACE with all data kept (never dropped / recreated).

* ``legacy_schemas/p2_f7c64f6.sql`` / ``p3_01cfd9a.sql`` are the exact schemas the P2 / P3 code created with
  ``create_all`` (frozen DDL). Data is inserted with plain SQL as those versions stored it, then the CURRENT app
  starts on that file and must see everything: logins with the old password hashes, old tokens, ACL decisions,
  chat history, P3 versions / chunks / index generations.
* The migrated schema must equal the current models (autogenerate diff is empty).
* Step-by-step upgrade / downgrade / re-upgrade through the revisions.
"""

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text

from kb_platform import migrate, security
from kb_platform.db import Base, make_engine

HERE = Path(__file__).resolve().parent / "legacy_schemas"
NOW = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc).isoformat(sep=" ")
LATER = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(sep=" ")
PW_HASH = security.hash_password("Legacy-pass-001")
OLD_TOKEN = "legacy-opaque-token-value-0000000000000000001"


def p2_rows(con, p3_schema=False):
    x = con.execute
    x("INSERT INTO acl_state (id, version) VALUES (1, 7)")
    for i, (r, d) in enumerate((("employee", "engineering"), ("intern", "hr")), 1):
        x("INSERT INTO roles (id, name, description) VALUES (?, ?, '')", (i, r))
        x("INSERT INTO departments (id, name, description) VALUES (?, ?, 'dept')", (i, d))
    x("INSERT INTO users (id, username, display_name, password_hash, clearance, active, is_admin, failed_logins, created_at)"
      " VALUES (1, 'old_alice', 'Alice', ?, 1, 1, 0, 0, ?)", (PW_HASH, NOW))
    x("INSERT INTO users (id, username, display_name, password_hash, clearance, active, is_admin, failed_logins, created_at)"
      " VALUES (2, 'old_admin', 'Admin', ?, 3, 1, 1, 0, ?)", (PW_HASH, NOW))
    x("INSERT INTO user_roles VALUES (1, 1)")
    x("INSERT INTO user_departments VALUES (1, 1)")
    x("INSERT INTO auth_tokens (id, token_sha256, user_id, created_at, expires_at, revoked) VALUES (1, ?, 1, ?, ?, 0)",
      (security.token_digest(OLD_TOKEN), NOW, LATER))
    x("INSERT INTO knowledge_bases (id, name, description, created_at) VALUES ('kb1', '旧知识库', '', ?)", (NOW,))
    if p3_schema:  # the P3 create_all schema has documents.source NOT NULL without a server default
        x("INSERT INTO documents (id, kb_id, doc_key, title, classification, owner_department, status, created_at, updated_at, source)"
          " VALUES ('doc1', 'kb1', 'eng_runbook.md', 'Runbook', 'internal', 'engineering', 'active', ?, ?, 'metadata')", (NOW, NOW))
    else:
        x("INSERT INTO documents (id, kb_id, doc_key, title, classification, owner_department, status, created_at, updated_at)"
          " VALUES ('doc1', 'kb1', 'eng_runbook.md', 'Runbook', 'internal', 'engineering', 'active', ?, ?)", (NOW, NOW))
    x("INSERT INTO document_grants (id, document_id, effect, subject_type, subject_value) VALUES (1, 'doc1', 'allow', 'department', 'engineering')")
    x("INSERT INTO chat_sessions (id, user_id, kb_id, title, created_at) VALUES ('s1', 1, 'kb1', '旧会话', ?)", (NOW,))
    x("INSERT INTO chat_messages (id, session_id, question, answer, citations, status, scope_key, created_at)"
      " VALUES (1, 's1', '旧问题', '旧回答', '[]', 'ok', 'k', ?)", (NOW,))
    x("INSERT INTO audit_log (id, ts, request_id, actor, action, resource, outcome, detail) VALUES (1, ?, 'r', 'old_alice', 'auth.login', '', 'success', '{}')", (NOW,))


def p3_rows(con):
    x = con.execute
    x("UPDATE documents SET source='metadata'")
    x("INSERT INTO documents (id, kb_id, doc_key, title, classification, status, created_at, updated_at, source, original_filename, current_version_id)"
      " VALUES ('doc2', 'kb1', 'aa_up.md', 'Up', 'internal', 'active', ?, ?, 'upload', 'up.md', 'v1')", (NOW, NOW))
    x("INSERT INTO document_versions (id, document_id, version_no, original_filename, file_format, size_bytes, sha256, storage_relpath, status, chunk_count, created_by, created_at, generation_id)"
      " VALUES ('v1', 'doc2', 1, 'up.md', 'markdown', 10, 'abc', 'uploads/doc2/1/original.md', 'indexed', 1, 'old_admin', ?, 'g1')", (NOW,))
    x("INSERT INTO chunks (id, version_id, chunk_id, ordinal, is_parent, char_count, text, meta) VALUES (1, 'v1', 'c0ffee000001', 0, 0, 4, '旧文本', '{}')")
    x("INSERT INTO index_generations (id, status, created_at, chunk_count, document_count, dense, built_by) VALUES ('g1', 'active', ?, 1, 1, 0, 'old_admin')", (NOW,))
    x("INSERT INTO kb_managers VALUES ('kb1', 1)")


def legacy_db(schema: str, fill) -> Path:
    f = Path(tempfile.mkdtemp(prefix="kbmig_")) / "legacy.db"
    con = sqlite3.connect(f)
    con.executescript((HERE / schema).read_text(encoding="utf-8"))
    fill(con)
    con.commit()
    con.close()
    return f


def counts(path: Path) -> dict:
    con = sqlite3.connect(path)
    tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table' and name != 'alembic_version'")]
    out = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in tabs}
    con.close()
    return out


class TestLegacyUpgrade(unittest.TestCase):
    def assert_schema_is_current(self, engine):
        with engine.connect() as c:
            self.assertEqual(compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata), [])
        self.assertEqual(migrate.current(engine), migrate.head())

    def test_p2_database_is_upgraded_with_all_data(self):
        f = legacy_db("p2_f7c64f6.sql", p2_rows)
        before = counts(f)
        engine = make_engine(f"sqlite:///{f}")
        migrate.upgrade(engine)
        self.assert_schema_is_current(engine)
        after = counts(f)
        for t, n in before.items():
            self.assertEqual(after[t], n, t)              # nothing lost — incl. user_departments (FK cascade trap)
        for t in ("document_versions", "chunks", "index_generations", "kb_managers", "jobs"):
            self.assertEqual(after[t], 0)
        with engine.connect() as c:
            self.assertEqual(c.execute(text("select source, original_filename, current_version_id from documents")).one(),
                             ("metadata", None, None))
            self.assertEqual(c.execute(text("select kind from auth_tokens")).scalar(), "opaque")
            self.assertIsNone(c.execute(text("select parent_id from departments where id=1")).scalar())
        engine.dispose()
        self.check_app_works(f)

    def test_p3_database_is_upgraded_with_all_data(self):
        def fill(con):
            p2_rows(con, p3_schema=True)
            p3_rows(con)
        f = legacy_db("p3_01cfd9a.sql", fill)
        before = counts(f)
        engine = make_engine(f"sqlite:///{f}")
        migrate.upgrade(engine)
        self.assert_schema_is_current(engine)
        after = counts(f)
        for t, n in before.items():
            self.assertEqual(after[t], n, t)
        with engine.connect() as c:
            self.assertEqual(c.execute(text("select text from chunks")).scalar(), "旧文本")
            self.assertEqual(c.execute(text("select status from index_generations")).scalar(), "active")
        engine.dispose()
        self.check_app_works(f)

    def test_upgrade_is_idempotent(self):
        f = legacy_db("p2_f7c64f6.sql", p2_rows)
        e = make_engine(f"sqlite:///{f}")
        migrate.upgrade(e)
        c1 = counts(f)
        migrate.upgrade(e)
        self.assertEqual(counts(f), c1)
        e.dispose()

    def check_app_works(self, f: Path):
        """The current application runs on the migrated file and sees the legacy data."""
        import warnings
        warnings.filterwarnings("ignore")
        from fastapi.testclient import TestClient
        from kb_platform.app import create_app
        from kb_platform.rag_backend import RagBackend
        from kb_platform.settings import Settings
        app = create_app(Settings(database_url=f"sqlite:///{f}", storage_dir=str(f.parent / "storage")),
                         RagBackend(retriever_factory=lambda: None, llm_factory=lambda: None))
        c = TestClient(app)
        h_old = {"Authorization": f"Bearer {OLD_TOKEN}"}
        me = c.get("/api/v1/auth/me", headers=h_old)                     # P2 token still valid
        self.assertEqual(me.status_code, 200, me.text)
        self.assertEqual((me.json()["roles"], me.json()["departments"]), (["employee"], ["engineering"]))
        tok = c.post("/api/v1/auth/login", json={"username": "old_alice", "password": "Legacy-pass-001"})
        self.assertEqual(tok.status_code, 200)                           # legacy scrypt hash verifies
        h = {"Authorization": f"Bearer {tok.json()['access_token']}"}
        docs = c.get("/api/v1/knowledge-bases/kb1/documents", headers=h).json()
        self.assertIn("eng_runbook.md", {d["doc_key"] for d in docs})     # ACL from migrated grants
        hist = c.get("/api/v1/sessions/s1", headers=h).json()
        self.assertEqual(hist["messages"][0]["question"], "旧问题")
        app.state.engine.dispose()


class TestStepwise(unittest.TestCase):
    def test_upgrade_downgrade_reupgrade(self):
        f = Path(tempfile.mkdtemp(prefix="kbmig_")) / "step.db"
        e = make_engine(f"sqlite:///{f}")
        self.assertEqual(migrate.upgrade(e, "0001_p2_baseline"), "0001_p2_baseline")
        with e.begin() as c:
            raw = c.connection.driver_connection
            p2_rows(raw)
        self.assertEqual(migrate.upgrade(e, "0002_p3_ingestion"), "0002_p3_ingestion")
        with e.begin() as c:
            p3_rows(c.connection.driver_connection)
        n3 = counts(f)
        self.assertEqual(migrate.upgrade(e), migrate.head())
        self.assertEqual({k: v for k, v in counts(f).items() if k in n3}, n3)
        self.assertEqual(migrate.downgrade(e, "0002_p3_ingestion"), "0002_p3_ingestion")
        self.assertEqual(counts(f), n3)                                   # downgrade of P4 keeps P2/P3 data
        self.assertEqual(migrate.upgrade(e), migrate.head())
        self.assertEqual({k: v for k, v in counts(f).items() if k in n3}, n3)
        e.dispose()

    def test_legacy_detection(self):
        self.assertEqual(migrate.legacy_revision({"users", "roles"}), "0001_p2_baseline")
        self.assertEqual(migrate.legacy_revision({"users", "document_versions"}), "0002_p3_ingestion")
        self.assertIsNone(migrate.legacy_revision({"users", "alembic_version"}))
        self.assertIsNone(migrate.legacy_revision(set()))


if __name__ == "__main__":
    unittest.main()
