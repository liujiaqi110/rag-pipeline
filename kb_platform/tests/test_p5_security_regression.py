"""P5 security regression for P4 findings (each reproduced first, then fixed):

1. SSE deltas showed bare 12-hex tokens without a digit+letter mix ("abcdefabcdef", "123456789012") that the final
   validation then blocked — the stream must never show what the final answer removes.
2. Citation lookups were not pinned to the answered version: after a document update, an old answer's citation (same
   chunk ID in the new version) displayed the NEW text. Citations now carry ``version_id`` and lookups honour it.
3. Migrated databases pass SQLite integrity and foreign-key checks.
"""

import json
import tempfile
import unittest
from pathlib import Path

from kb_platform.streaming import DeltaMasker
from kb_platform.tests.helpers import ADMIN
from kb_platform.tests.ingest_fixtures import md_doc
from kb_platform.tests.test_stream_workbench import StreamCase


class TestDeltaMasking(unittest.TestCase):
    def test_every_foreign_12_hex_token_is_masked(self):
        for tok in ("abcdefabcdef", "123456789012", "a1b2c3d4e5f6"):
            m = DeltaMasker({"aaaaaa111111"})
            out = m.feed(f"见 [{tok}] 与 [aaaaaa111111]。") + m.flush()
            self.assertNotIn(tok, out, tok)
            self.assertIn("aaaaaa111111", out)

    def test_ordinary_numbers_survive(self):
        m = DeltaMasker(set())
        out = m.feed("预算 3000000 元，订单 1234567890123，比例 12.5%。") + m.flush()
        self.assertEqual(out, "预算 3000000 元，订单 1234567890123，比例 12.5%。")


class TestStreamAndCitations(StreamCase):
    def test_stream_never_shows_what_final_blocks(self):
        d = self.upload("sec.md", md_doc("安全", "CANARY-SEC", "安全专项")).json()["document"]["id"]
        self.grant(d, departments=["engineering"])
        self.llm.responder = lambda m: "参见 [abcdefabcdef] 和 [123456789012]。\n引用：abcdefabcdef"
        code, _, evs, _ = self.stream("eng_alice", "安全专项？")
        deltas = "".join(x["text"] for e, x in evs if e == "delta")
        for tok in ("abcdefabcdef", "123456789012"):
            self.assertNotIn(tok, deltas)
            self.assertNotIn(tok, evs[-1][1]["answer"])

    def test_citations_are_pinned_to_the_answered_version(self):
        d = self.upload("pin.md", md_doc("固定", "CANARY-PIN-V1", "固定专项")).json()["document"]["id"]
        self.grant(d, departments=["engineering"])
        code, _, evs, sid = self.stream("eng_alice", "固定专项？")
        cite = evs[-1][1]["citations"][0]
        self.assertTrue(cite["version_id"])
        url = f"/api/v1/knowledge-bases/{self.kb_id}/chunks/{cite['chunk_id']}"
        r = self.client.get(url, params={"version_id": cite["version_id"]}, headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 200)
        self.assertIn("CANARY-PIN-V1", r.json()["text"])
        hist = self.client.get(f"/api/v1/sessions/{sid}", headers=self.h("eng_alice")).json()
        self.assertEqual(hist["messages"][-1]["citations"][0]["version_id"], cite["version_id"])
        # update the document: same structure → same chunk IDs in v2
        self.client.post(f"/api/v1/documents/{d}/versions", files={"file": ("pin.md", md_doc("固定", "CANARY-PIN-V2", "固定专项"))},
                         headers=self.h(ADMIN))
        r = self.client.get(url, params={"version_id": cite["version_id"]}, headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 404)                     # the old citation no longer resolves …
        self.assertNotIn("CANARY-PIN-V2", r.text)                # … and never to the new text
        r = self.client.get(url, headers=self.h("eng_alice"))    # unpinned lookups (P4) still show the current text
        self.assertTrue(r.status_code == 404 or "CANARY-PIN-V1" not in r.text)


class TestMigratedDbIntegrity(unittest.TestCase):
    def test_integrity_and_foreign_keys_after_upgrade(self):
        import sqlite3
        from kb_platform import migrate
        from kb_platform.db import make_engine
        from kb_platform.tests.test_migrations import legacy_db, p2_rows, p3_rows
        f = legacy_db("p3_01cfd9a.sql", lambda c: (p2_rows(c, p3_schema=True), p3_rows(c)))
        e = make_engine(f"sqlite:///{f}")
        migrate.upgrade(e)
        e.dispose()
        con = sqlite3.connect(f)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(con.execute("PRAGMA foreign_key_check").fetchall(), [])
        con.close()


if __name__ == "__main__":
    unittest.main()
