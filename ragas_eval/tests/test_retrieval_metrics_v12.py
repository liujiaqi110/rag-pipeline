"""v1.2: chunk-level metrics, evidence-slot coverage and failure reasons (hand-computed values)."""

import math
import unittest

from ragas_eval import retrieval_metrics as rm


class TestBasicMetrics(unittest.TestCase):
    top = ["x", "a", "y", "b", "z"]
    rel = {"a", "b", "c"}

    def test_hit_recall_mrr(self):
        self.assertEqual(rm.hit_at_k(self.top, self.rel, 1), 0)
        self.assertEqual(rm.hit_at_k(self.top, self.rel, 2), 1)
        self.assertAlmostEqual(rm.recall_at_k(self.top, self.rel, 5), 2 / 3)
        self.assertAlmostEqual(rm.mrr_at_k(self.top, self.rel), 0.5)
        self.assertEqual(rm.mrr_at_k(["q"], self.rel), 0.0)

    def test_ndcg(self):
        dcg = 1 / math.log2(3) + 1 / math.log2(5)
        idcg = 1 + 1 / math.log2(3) + 1 / math.log2(4)
        self.assertAlmostEqual(rm.ndcg_at_k(self.top, self.rel, 5), dcg / idcg)
        self.assertAlmostEqual(rm.ndcg_at_k(["a", "b", "c"], self.rel, 3), 1.0)

    def test_undefined_without_relevant(self):
        with self.assertRaises(ValueError):
            rm.recall_at_k(self.top, set(), 5)


class TestSlots(unittest.TestCase):
    # two slots (AND); slot 2 has two alternative groups (OR); group 'g2b' needs two items (AND)
    slots = [[[{"a1", "a2"}]], [[{"c"}], [{"d"}, {"e"}]]]

    def test_one_hit_is_not_full_coverage(self):
        top = ["a1", "x", "y"]
        self.assertEqual(rm.hit_at_k(top, rm.relevant_chunks(self.slots), 3), 1)
        self.assertEqual(rm.slot_fullcov_at_k(top, self.slots, 3), 0)
        self.assertAlmostEqual(rm.slot_recall_at_k(top, self.slots, 3), 0.5)

    def test_group_needs_all_items_and_alternatives_work(self):
        self.assertEqual(rm.slot_fullcov_at_k(["a2", "d"], self.slots, 2), 0)
        self.assertEqual(rm.slot_fullcov_at_k(["a2", "d", "e"], self.slots, 3), 1)
        self.assertEqual(rm.slot_fullcov_at_k(["c", "a1"], self.slots, 2), 1)
        self.assertEqual(rm.completion_rank(["x", "c", "y", "a1"], self.slots), 4)
        self.assertIsNone(rm.completion_rank(["x", "c"], self.slots))

    def test_failure_reasons(self):
        self.assertEqual(rm.failure_reason(["c", "a1"], [], self.slots, 2)["reason"], "covered")
        self.assertEqual(rm.failure_reason(["x", "y"], ["x", "y", "c", "a1"], self.slots, 2)["reason"], "truncation")
        self.assertEqual(rm.failure_reason(["x", "y"], ["x", "y"], self.slots, 2)["reason"], "recall_failure")
        r = rm.failure_reason(["x"], ["x", "c"], self.slots, 1)
        self.assertEqual((r["reason"], r["slot_kinds"]), ("mixed", ["recall_failure", "truncation"]))

    def test_source_hit_is_not_recall(self):
        # legacy file-level hit is 1 although no relevant chunk was retrieved
        self.assertEqual(rm.source_hit_at_k(["guide.md"], "guide", 1), 1)
        self.assertEqual(rm.recall_at_k(["wrong_chunk"], {"right_chunk"}, 1), 0.0)

    def test_question_metrics_and_aggregate(self):
        m = rm.question_metrics(["a1", "c"], self.slots, (1, 2))
        self.assertEqual((m["hit@1"], m["slot_fullcov@1"], m["slot_fullcov@2"], m["completion_rank"]), (1, 0, 1, 2))
        agg = rm.aggregate([m, m])
        self.assertEqual(agg["n"], 2)
        self.assertNotIn("completion_rank", agg)


if __name__ == "__main__":
    unittest.main()
