"""v1.2 P0: legacy metric / legacy hybrid are labelled and reproducible, the formal service uses RRF k=60 pools 25/25,
and the documented configuration matches the code. No model, no index, no network."""

import re
import unittest
from pathlib import Path
from unittest import mock

from ragas_eval import hybrid_rrf as hr
from ragas_eval import rag_pipeline as rp
from ragas_eval import rag_service as svc
from ragas_eval.config import PROJECT_ROOT, RETRIEVAL_SCRIPT_PATH
from ragas_eval.dataset_adapter import _load_module

LEGACY = _load_module(RETRIEVAL_SCRIPT_PATH, "legacy_eval_for_tests")


def bm(ids, start=30.0):
    return [{"chunk": {"chunk_id": c, "source_file": "a.md"}, "bm25_score": start - i} for i, c in enumerate(ids)]


def de(ids, start=0.9):
    return [{"chunk": {"chunk_id": c, "source_file": "a.md"}, "bm25_score": 0.0, "dense_score": round(start - i * 0.01, 4)}
            for i, c in enumerate(ids)]


class TestLegacySourceHit(unittest.TestCase):
    def test_renamed_and_not_a_recall(self):
        self.assertFalse(hasattr(LEGACY, "compute_recall"))          # the misleading name is gone
        res = [{"chunk": {"chunk_id": "wrong", "source_file": "guide.md"}}]
        # a WRONG chunk from the expected file counts as a hit: file-level, not chunk-level recall
        self.assertTrue(LEGACY.compute_source_hit(res, "guide", 5))
        self.assertFalse(LEGACY.compute_source_hit(res, "", 5))

    def test_report_labels(self):
        t = RETRIEVAL_SCRIPT_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Hybrid (RRF", t)
        self.assertNotIn("RRF fusion", t)
        self.assertIsNone(re.search(r"_recall(5|10)\b", t))
        self.assertIn("SourceHit@5", t)
        self.assertIn("NOT RRF", LEGACY.LEGACY_HYBRID_LABEL)


class TestLegacyUnionSafeDefects(unittest.TestCase):
    """Reproduces the v0.9 diagnosis of the legacy union_safe hybrid on a synthetic ranking."""

    def run_legacy(self, bm_ids, de_ids, top_k=10):
        with mock.patch.object(LEGACY, "search_bm25", lambda *a, **k: bm(bm_ids)), \
                mock.patch.object(LEGACY, "search_dense", lambda *a, **k: de(de_ids)):
            return [r["chunk"]["chunk_id"] for r in LEGACY.search_hybrid_legacy_union_safe("q", None, {"x": 1}, top_k=top_k)]

    def setUp(self):
        self.bm_ids = [f"b{i:02d}" for i in range(1, 26)]                       # BM25 top-25
        self.bm_ids[5] = "both"                                                   # 'both' = BM25 rank 6
        self.de_ids = ["both", "donly", "d3", "d4", "d5"] + [f"x{i}" for i in range(20)]  # Dense rank 1 / 2

    def test_alias_is_legacy(self):
        self.assertIs(LEGACY.search_hybrid, LEGACY.search_hybrid_legacy_union_safe)

    def test_lost_bm25_rank_pushes_shared_candidate_down(self):
        top = self.run_legacy(self.bm_ids, self.de_ids, top_k=40)
        # 'both' is BM25 #6 AND Dense #1, but it entered via the Dense loop, lost its BM25 rank and is sorted after
        # every BM25 top-25 candidate.
        self.assertGreater(top.index("both"), top.index("b25"))
        self.assertNotIn("both", self.run_legacy(self.bm_ids, self.de_ids, top_k=10))

    def test_dense_only_candidate_truncated(self):
        self.assertNotIn("donly", self.run_legacy(self.bm_ids, self.de_ids, top_k=10))

    def test_bm25_rank_dominates(self):
        top = self.run_legacy(self.bm_ids, self.de_ids, top_k=10)
        self.assertEqual(top, [c for c in self.bm_ids if c != "both"][:10])

    def test_rrf_fixes_the_same_case(self):
        fused = [r["chunk"]["chunk_id"] for r in hr.rrf_fuse(bm(self.bm_ids), de(self.de_ids), hr.RRFParams())]
        self.assertEqual(fused[0], "both")                                       # found by both lists → first
        self.assertIn("donly", fused[:10])


class TestRRFGolden(unittest.TestCase):
    def test_scores_order_and_zero_filter(self):
        b = bm(["a", "b", "c"]) + [{"chunk": {"chunk_id": "z"}, "bm25_score": 0.0}]
        d = de(["c", "d", "a"])
        out = hr.rrf_fuse(b, d, hr.RRFParams())
        self.assertEqual([r["chunk"]["chunk_id"] for r in out], ["a", "c", "b", "d"])
        self.assertAlmostEqual(out[0]["rrf_score"], round(1 / 61 + 1 / 63, 8))
        self.assertNotIn("z", [r["chunk"]["chunk_id"] for r in out])          # zero-score BM25 dropped
        self.assertEqual(out[0]["retrieval_origin"], "both")

    def test_pools_truncate_inputs(self):
        out = hr.rrf_fuse(bm([f"b{i}" for i in range(40)]), de([f"d{i}" for i in range(40)]), hr.RRFParams())
        self.assertEqual(len(out), 50)                                           # 25 + 25 disjoint


class TestServiceConfiguration(unittest.TestCase):
    def test_service_uses_rrf_k60_pools_25(self):
        import ast
        src = (PROJECT_ROOT / "ragas_eval" / "rag_retriever.py").read_text(encoding="utf-8")
        self.assertIn("SERVICE_RRF = RRFParams(k=60, bm25_pool=25, dense_pool=25)", src)
        self.assertIn("rrf_params=SERVICE_RRF", src)
        ast.parse(src)
        self.assertEqual((hr.RRFParams().k, hr.RRFParams().bm25_pool, hr.RRFParams().dense_pool), (60, 25, 25))
        self.assertEqual(svc.DEFAULT_MODE, "rrf")
        cfg = rp.PipelineConfig(mode="rrf")
        self.assertEqual((cfg.top_k, cfg.context_char_budget), (5, 6000))

    def test_yaml_documents_the_code(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("pyyaml not installed")
        y = yaml.safe_load((PROJECT_ROOT / "config" / "rag_config.yaml").read_text(encoding="utf-8"))
        s = y["service_retrieval"]
        self.assertEqual(s, {"fusion": "rrf", "rrf_k": 60, "bm25_pool": 25, "dense_pool": 25, "context_top_k": 5,
                             "context_char_budget": 6000})
        self.assertEqual(y["retrieval"]["hybrid_fusion_method"], "union_safe")  # kept as documented legacy value

    def test_legacy_yaml_fields_are_not_read_by_code(self):
        code = "".join(p.read_text(encoding="utf-8") for d in ("ragas_eval", "scripts")
                       for p in (PROJECT_ROOT / d).glob("*.py"))
        for key in ("hybrid_fusion_method", "bm25_weight", "dense_weight", "union_keep_bm25_top", "hybrid_safe_mode"):
            self.assertNotIn(key, code, key)


if __name__ == "__main__":
    unittest.main()
