"""v1.0.3 safety layer: exact citation IDs, refusal cleanup, source scope (MockLLM; no Ollama) + generated outputs."""

import csv
import json
import unittest
from pathlib import Path

from ragas_eval import local_llm as ll
from ragas_eval import rag_pipeline as rp
from ragas_eval import rag_safety as rs

CTX = ["19c9c0248b15", "c769902edde2", "8278f930c81e"]


class TestStrictCitations(unittest.TestCase):
    def test_truncated_id_blocked_not_completed(self):
        r = rs.strict_citations("是 Embedding 模型 [c769902edde2] [19c9c0248b1] [8278f930c81e]。\n引用：19c9c0248b1, 8278f930c81e", CTX)
        self.assertEqual(r["valid"], ["c769902edde2", "8278f930c81e"])
        self.assertEqual(r["invalid"], {"19c9c0248b1": "truncated_prefix_of_context_id"})
        self.assertNotIn("19c9c0248b1", r["text"])
        self.assertNotIn("19c9c0248b15", r["text"])          # never auto-completed
        self.assertEqual(r["remaining_invalid_after"], [])

    def test_extended_and_substring_ids_rejected(self):
        r = rs.strict_citations("见 [a19c9c0248b15] 和 [19c9c0248b155]", CTX)
        self.assertEqual(r["valid"], [])
        self.assertEqual(set(r["invalid"]), {"a19c9c0248b15", "19c9c0248b155"})
        self.assertTrue(all(v == "extended_context_id" for v in r["invalid"].values()))

    def test_real_chunk_not_in_context_is_invalid(self):
        r = rs.strict_citations("见 [7041b269117c]", CTX, corpus_ids={"7041b269117c", *CTX})
        self.assertEqual(r["invalid"], {"7041b269117c": "real_chunk_not_in_context"})

    def test_valid_ids_untouched_and_length_alone_not_enough(self):
        text = "答案 [c769902edde2]。\n引用：c769902edde2"
        r = rs.strict_citations(text, CTX)
        self.assertEqual((r["valid"], r["invalid"], r["text"]), (["c769902edde2"], {}, text))
        r = rs.strict_citations("答案 [abcdef123456]", CTX)   # 12 hex chars but not in this context
        self.assertEqual(r["valid"], [])

    def test_citation_line_checked(self):
        r = rs.strict_citations("答案 [c769902edde2]。\n引用：c769902edde2, deadbeef0001", CTX)
        self.assertIn("deadbeef0001", r["invalid"])
        self.assertNotIn("deadbeef0001", r["text"])


class TestRefusalCleanup(unittest.TestCase):
    def test_full_refusal_without_fixed_phrase(self):
        t = "根据提供的资料，没有具体说明应选择哪个镜像。因此无法直接回答该问题。\n\n资料未说明：……\n引用：016235a86ee2, defdd7898084"
        self.assertEqual(rs.classify_refusal(t)["kind"], "full_refusal")
        cleaned = rs.clean_full_refusal(t)
        self.assertNotIn("016235a86ee2", cleaned)
        self.assertTrue(cleaned.endswith("引用：无"))

    def test_partial_keeps_citations(self):
        t = "根据提供的资料无法回答该问题。资料中提到物流信息.pdf 包含仓储信息 [7972c6eb91bb]。"
        self.assertEqual(rs.classify_refusal(t)["kind"], "partial")

    def test_citation_after_full_stop_belongs_to_sentence(self):
        t = "它是 Embedding 模型。[c769902edde2]\n\n资料未说明：训练数据。[c769902edde2]"
        self.assertEqual(rs.classify_refusal(t)["kind"], "partial")

    def test_ambiguous_is_uncertain_not_deleted(self):
        t = "资料中没有文字说明第 5 步。主要流程包括上传、训练和预测。\n引用：016235a86ee2"
        self.assertEqual(rs.classify_refusal(t)["kind"], "uncertain")

    def test_normal_answer(self):
        self.assertEqual(rs.classify_refusal("约 9.46 秒 [40ef12f9430b]。")["kind"], "answer")


DOCS = ["3 [example.com]_notes(6).md", "3 [example.com]_notes(9).md", "3_notes(1).md", "33lesson_demo_project_guide.md"]  # fictional names


class TestSourceScope(unittest.TestCase):
    def test_explicit_source_detected(self):
        self.assertEqual(rs.detect_source_scope("3 [example.com]_notes(6) 中讲解了哪些编程知识点？", DOCS)["doc"], DOCS[0])
        self.assertEqual(rs.detect_source_scope("根据notes(9)，驱动怎么装？", DOCS)["doc"], DOCS[1])
        self.assertEqual(rs.detect_source_scope("示例系统（3_notes(1)）如何增强元数据？", DOCS)["doc"], DOCS[2])

    def test_mentions_are_not_scope(self):
        for q in ("33lesson和另一课分别创建了什么环境？", "33lesson需要准备哪两个模型？", "某工具与33lesson的代码实现是什么关系？"):
            self.assertIsNone(rs.detect_source_scope(q, DOCS), q)

    def test_ambiguous_scope_not_guessed(self):
        self.assertIsNone(rs.detect_source_scope("notes(6) 中和notes(9) 中各讲了什么？", DOCS))


class FakeBM25:
    documents = [{"chunk_id": "aaaaaaaaaaaa", "text": "[s] 笔记九的内容", "source_file": DOCS[1]},
                 {"chunk_id": "bbbbbbbbbbbb", "text": "[s] 笔记六的内容", "source_file": DOCS[0]}]


class FakeR:
    bm25 = FakeBM25()

    def __init__(self, ids):
        self.ids = ids

    def rrf(self, q):
        return [{"chunk": {"chunk_id": c}} for c in self.ids]

    dense = rrf


class TestSafePipeline(unittest.TestCase):
    def run_(self, ids, q, text):
        llm = ll.MockLLM(lambda m: text)
        out = rs.SafeRagPipeline(FakeR(ids), llm, rp.PipelineConfig(mode="rrf")).answer(q)
        return out, llm

    def test_scope_without_target_doc_no_generation(self):
        out, llm = self.run_(["aaaaaaaaaaaa"], "3 [example.com]_notes(6) 中讲了什么？", "notes(6) 讲了 X [aaaaaaaaaaaa]")
        self.assertEqual(llm.calls, 0)
        self.assertEqual(out["response"]["status"], "insufficient_scoped_evidence")
        self.assertTrue(out["safety"]["retrieval_failure"])
        self.assertEqual(out["response"]["valid_citations"], [])

    def test_scope_restricts_context(self):
        seen = {}

        def resp(m):
            seen["u"] = m[1]["content"]
            return "笔记六的内容 [bbbbbbbbbbbb]\n引用：bbbbbbbbbbbb"
        llm = ll.MockLLM(resp)
        out = rs.SafeRagPipeline(FakeR(["aaaaaaaaaaaa", "bbbbbbbbbbbb"]), llm, rp.PipelineConfig(mode="rrf")).answer("3 [example.com]_notes(6) 中讲了什么？")
        self.assertNotIn("aaaaaaaaaaaa", seen["u"])            # other document's chunk never shown to the model
        self.assertEqual(out["safety"]["scope_action"], "restricted_context")
        self.assertEqual(out["response"]["valid_citations"], ["bbbbbbbbbbbb"])

    def test_full_refusal_status_and_partial_status(self):
        out, _ = self.run_(["aaaaaaaaaaaa"], "问题？", "资料中没有提到该参数。因此无法回答该问题 [aaaaaaaaaaaa]。")
        self.assertEqual((out["response"]["status"], out["response"]["valid_citations"]), ("refused", []))
        out, _ = self.run_(["aaaaaaaaaaaa"], "问题？", rp.REFUSAL_TEXT + "资料提到笔记九的内容 [aaaaaaaaaaaa]。\n引用：无")
        self.assertEqual(out["response"]["status"], "partial_answer")
        self.assertEqual(out["response"]["valid_citations"], ["aaaaaaaaaaaa"])

    def test_prompt_unchanged(self):
        self.assertEqual(rp.prompt_hash(), "1064b3ba8c26bda782598369b47bd604099609932b78e7fecaa3f68ceccebd5e")


OUT = Path(__file__).resolve().parent / "_no_research_outputs_in_public_release"


@unittest.skipUnless((OUT / "experiment_manifest.json").exists(), "v1.0.3 not reported")
class TestOutputs(unittest.TestCase):
    def test_files_and_acceptance(self):
        for n in ("safety_fix_report.md", "citation_validation_regression.csv", "refusal_cleanup_regression.csv", "source_attribution_tests.csv",
                  "before_after_comparison.csv", "known_remaining_risks.md", "experiment_manifest.json"):
            self.assertTrue((OUT / n).exists(), n)
        man = json.loads((OUT / "experiment_manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(all(man["acceptance"].values()), man["acceptance"])
        self.assertTrue(man["history_model_gt_unchanged"])
        self.assertFalse(man["production_strategy_switched"])

    def test_regression_rows(self):
        rows = list(csv.DictReader(open(OUT / "citation_validation_regression.csv", encoding="utf-8-sig")))
        self.assertEqual(len(rows), 260)
        self.assertTrue(all(not r["remaining_invalid_after"] and not r["legit_citation_lost"] for r in rows))
        ref = list(csv.DictReader(open(OUT / "refusal_cleanup_regression.csv", encoding="utf-8-sig")))
        self.assertFalse(any(r["removed_on_answerable_with_evidence_in_context"] == "True" for r in ref))

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
