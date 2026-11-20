"""Formal QA entry (rag_service): safety switch resolution, chain wiring, user-facing response hygiene (MockLLM)."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ragas_eval import local_llm as ll
from ragas_eval import rag_service as svc
from ragas_eval.tests.test_rag_safety_v103 import DOCS


class FakeBM25:
    documents = [{"chunk_id": "c769902edde2", "text": "[s] 加载知识库 相似度检索", "source_file": DOCS[3]},
                 {"chunk_id": "19c9c0248b15", "text": "[s] 模型列表", "source_file": DOCS[3]},
                 {"chunk_id": "aaaaaaaaaaaa", "text": "[s] 笔记九", "source_file": DOCS[1]},
                 {"chunk_id": "bbbbbbbbbbbb", "text": "[s] 笔记六", "source_file": DOCS[0]}]


class FakeR:
    bm25 = FakeBM25()

    def __init__(self, ids=("c769902edde2", "19c9c0248b15")):
        self.ids = list(ids)
        self.resets = 0

    def reset(self):
        self.resets += 1

    def rrf(self, q):
        return [{"chunk": {"chunk_id": c}} for c in self.ids]

    dense = rrf


BAD = "根据资料 [c769902edde2] 回答。另见 [19c9c0248b1] 与 [a19c9c0248b15] 和 [7041b269117c]。\n引用：c769902edde2, 19c9c0248b1, a19c9c0248b15"


class TestSwitch(unittest.TestCase):
    def test_resolution_order(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "s.json"
            self.assertEqual(svc.resolve_safety(None, cfg)["enabled"], False)               # default off
            cfg.write_text('{"enabled": true}', encoding="utf-8")
            with mock.patch.dict(os.environ, {"RAG_SAFETY_LAYER": ""}):
                self.assertTrue(svc.resolve_safety(None, cfg)["enabled"])                     # config
            with mock.patch.dict(os.environ, {"RAG_SAFETY_LAYER": "off"}):
                self.assertFalse(svc.resolve_safety(None, cfg)["enabled"])                    # env beats config
                self.assertTrue(svc.resolve_safety(True, cfg)["enabled"])                     # argument beats env
            cfg.write_text("not json", encoding="utf-8")
            with mock.patch.dict(os.environ, {"RAG_SAFETY_LAYER": ""}):
                self.assertFalse(svc.resolve_safety(None, cfg)["enabled"])                    # unreadable -> off


class TestChain(unittest.TestCase):
    def ask(self, text, safety, ids=("c769902edde2", "19c9c0248b15"), q="示例流程包含哪些步骤？"):
        s = svc.RagService(FakeR(ids), ll.MockLLM(lambda m: text), corpus_ids={"c769902edde2", "19c9c0248b15", "7041b269117c"})
        return s.answer(q, safety=safety)

    def test_safety_on_user_response_has_no_invalid_ids(self):
        out = self.ask(BAD, True)
        u = json.dumps(out["user_response"], ensure_ascii=False)
        for bad in ("19c9c0248b1]", "a19c9c0248b15", "7041b269117c"):
            self.assertNotIn(bad, u)
        self.assertNotIn("19c9c0248b1,", u)
        self.assertEqual([c["chunk_id"] for c in out["user_response"]["citations"]], ["c769902edde2"])
        self.assertTrue(out["audit"]["safety_layer"]["enabled"])
        self.assertIn("19c9c0248b1", out["audit"]["safety"]["citation"]["invalid"])     # kept only in the audit

    def test_safety_off_is_the_old_behaviour(self):
        out = self.ask(BAD, False)
        self.assertIsNone(out["audit"]["safety"])
        self.assertIn("19c9c0248b1", out["user_response"]["answer"])                     # what the layer fixes

    def test_full_refusal_and_partial_through_service(self):
        out = self.ask("资料中没有提到该参数。因此无法回答该问题 [c769902edde2]。", True)
        self.assertEqual((out["user_response"]["status"], out["user_response"]["citations"]), ("refused", []))
        out = self.ask("根据提供的资料无法回答该问题。资料提到相似度检索 [c769902edde2]。\n引用：无", True)
        self.assertEqual(out["user_response"]["status"], "partial_answer")
        self.assertEqual(len(out["user_response"]["citations"]), 1)

    def test_source_scope_through_service(self):
        out = self.ask("笔记九的内容 [aaaaaaaaaaaa]", True, ids=("aaaaaaaaaaaa",), q="3 [example.com]_notes(6) 中讲了什么？")
        self.assertEqual(out["user_response"]["status"], "insufficient_scoped_evidence")
        self.assertIn("notice", out["user_response"])
        self.assertEqual(out["user_response"]["citations"], [])

    def test_cache_reset_per_question(self):
        r = FakeR()
        s = svc.RagService(r, ll.MockLLM(lambda m: "答 [c769902edde2]"), corpus_ids=set())
        s.answer("问题一", safety=True)
        s.answer("问题二", safety=False)
        self.assertEqual(r.resets, 2)


OUT = Path(__file__).resolve().parent / "_no_research_outputs_in_public_release"


@unittest.skipUnless((OUT / "acceptance.json").exists(), "integration not run")
class TestIntegrationOutputs(unittest.TestCase):
    def test_acceptance_and_switch_consistent(self):
        res = json.loads((OUT / "acceptance.json").read_text(encoding="utf-8"))
        self.assertTrue(res["all_passed_without_test_suite"], res["acceptance_without_test_suite"])
        cfg = json.loads(svc.SAFETY_CONFIG.read_text(encoding="utf-8"))
        if cfg.get("enabled"):
            self.assertTrue(all(res.get("final_acceptance", {}).values()) and res.get("final_acceptance"))
        self.assertFalse(cfg.get("retrieval_default_changed", False))

    def test_smoke_records(self):
        recs = [json.loads(l) for l in (OUT / "smoke_results.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        by = {r["item"]: r for r in recs}
        self.assertEqual(by["q011"]["status"], "insufficient_scoped_evidence")
        self.assertTrue(by["q011"]["retrieval_failure"])
        self.assertEqual(by["c036"]["citations"], [])
        self.assertEqual(by["c038"]["status"], "partial_answer")
        for r in recs:
            self.assertEqual(r["human_approved"], False)

    def test_no_sensitive_values(self):
        import re
        from ragas_eval import gt_migration_v08 as gm
        from ragas_eval import index_rebuild as ir
        from ragas_eval import sensitive_scan as ss
        c = gm.Corpus(gm.NEW_CORPUS)
        vals = {v for k in c.kids for v in re.findall(r"inviteCode=([0-9a-zA-Z]{16,})", k["text"])}
        probes = ir.sensitive_probes()
        for p in OUT.iterdir():
            if p.is_file():
                b = p.read_bytes()
                self.assertEqual(ss.probe_total(b, probes), 0, p.name)
                self.assertFalse(any(v[:10].encode() in b for v in vals), p.name)


if __name__ == "__main__":
    unittest.main()
