"""ACL × BM25 / Dense / RRF: permission filtering happens at the CANDIDATE stage, not on the final top-5."""

import unittest

from ragas_eval import acl
from ragas_eval.acl_retrieval import AclRetriever
from ragas_eval.tests.acl_fixtures import FakeBaseRetriever, embed

SECRET_TEXT = "年度 预算 发布 检查 清单：预算 审批 发布 检查 流程 与 预算 复核（第{}节）"
PUBLIC_TEXT = "员工 手册：请假 流程 与 发布 说明（第{}条）"


def corpus(n_secret=30, n_public=8):
    """30 secret chunks match the probe query far better than the 8 public ones and fill every 25-slot pool."""
    ch = [{"chunk_id": f"5{i:011x}", "source_file": "secret.md", "text": SECRET_TEXT.format(i)} for i in range(n_secret)]
    ch += [{"chunk_id": f"7{i:011x}", "source_file": "public.md", "text": PUBLIC_TEXT.format(i)} for i in range(n_public)]
    return ch


POLICY = acl.AclPolicy({"policy_version": "t1",
                        "users": {"staff": {"roles": ["employee"], "clearance": 1},
                                  "boss": {"roles": ["executive"], "clearance": 3}},
                        "documents": {"public.md": {"allow": {"all_authenticated": True}},
                                      "secret.md": {"classification": "restricted", "allow": {"roles": ["executive"]}}}})
Q = "预算 发布 检查 流程"


def ids(rs):
    return [r["chunk"]["chunk_id"] for r in rs]


class TestCandidateStageFiltering(unittest.TestCase):
    def setUp(self):
        self.base = FakeBaseRetriever(corpus())
        self.src = {c["chunk_id"]: c["source_file"] for c in self.base.bm25.documents}
        self.scope = acl.build_scope(POLICY, "staff", self.src)
        self.r = AclRetriever(self.base, self.scope)

    def test_without_acl_secret_chunks_dominate_every_pool(self):
        top5 = ids(self.base.rrf(Q))[:5]
        self.assertTrue(all(self.src[c] == "secret.md" for c in top5))
        # naive "filter the final top-5" would leave the authorised user with NO context at all
        self.assertEqual([c for c in top5 if self.scope.allows_chunk(c)], [])
        # and post-filtering the 25-slot pools would also leave nothing: secrets take every slot
        bm_pool = [r for r in self.base.bm25_full(Q) if r["bm25_score"] > 0][:25]
        self.assertTrue(all(self.src[c] == "secret.md" for c in ids(bm_pool)))
        self.assertTrue(all(self.src[c] == "secret.md" for c in ids(self.base.dense(Q))))

    def test_acl_pools_are_filled_with_authorised_candidates(self):
        bm, de, fused = self.r.bm25_full(Q), self.r.dense(Q), self.r.rrf(Q)
        for lst in (bm, de, fused):
            self.assertTrue(lst)
            self.assertTrue(all(self.scope.allows_chunk(c) for c in ids(lst)))
        self.assertEqual(len(de), 8)                         # all 8 public chunks, none displaced by secrets
        self.assertEqual(len([r for r in bm if r["bm25_score"] > 0]), 8)
        self.assertEqual(len(fused[:5]), 5)                  # full top-5 context for the authorised user

    def test_secret_text_is_never_visible_to_the_pipeline(self):
        self.assertEqual({d["source_file"] for d in self.r.bm25.documents}, {"public.md"})
        self.assertEqual(self.r.bm25.N, 8)

    def test_authorised_order_and_scores_are_unchanged(self):
        want_bm = [(r["chunk"]["chunk_id"], r["bm25_score"]) for r in self.base.bm25_full(Q) if self.scope.allows_chunk(r["chunk"]["chunk_id"])]
        self.assertEqual([(r["chunk"]["chunk_id"], r["bm25_score"]) for r in self.r.bm25_full(Q)], want_bm)
        want_de = [(r["chunk"]["chunk_id"], r["dense_score"]) for r in self.base.dense(Q, k=10 ** 6) if self.scope.allows_chunk(r["chunk"]["chunk_id"])][:25]
        self.assertEqual([(r["chunk"]["chunk_id"], r["dense_score"]) for r in self.r.dense(Q)], want_de)

    def test_full_access_is_identical_to_the_unfiltered_retriever(self):
        full = AclRetriever(self.base, acl.build_scope(POLICY, "boss", self.src))
        for q in (Q, "请假 流程", "手册", "复核"):
            self.assertEqual(full.rrf(q), self.base.rrf(q), q)
            self.assertEqual(full.bm25_full(q), self.base.bm25_full(q), q)
            self.assertEqual(full.dense(q), self.base.dense(q), q)

    def test_source_scope_still_works_inside_acl(self):
        self.assertEqual(self.r.rrf(Q, scope="secret.md"), [])
        self.assertTrue(all(self.src[c] == "public.md" for c in ids(self.r.rrf(Q, scope="public.md"))))

    def test_empty_scope_returns_nothing(self):
        r = AclRetriever(self.base, acl.build_scope(POLICY, "nobody", self.src))
        self.assertEqual((r.rrf(Q), r.bm25_full(Q), r.dense(Q)), ([], [], []))

    def test_rejects_base_without_candidate_stage_support(self):
        class Legacy(FakeBaseRetriever):
            def dense(self, text, scope=None, k=25):  # old signature: could only be post-filtered
                return super().dense(text, scope, k)
        with self.assertRaises(acl.AclViolation):
            AclRetriever(Legacy(corpus(2, 2)), self.scope)

    def test_buggy_base_is_caught_by_the_stage_gate(self):
        class Leaky(FakeBaseRetriever):
            def dense(self, text, scope=None, k=25, allowed=None):
                return super().dense(text, scope, k, allowed=None)  # ignores the ACL
        r = AclRetriever(Leaky(corpus()), self.scope)
        with self.assertRaises(acl.AclViolation):
            r.dense(Q)
        with self.assertRaises(acl.AclViolation):
            r.rrf(Q)


try:
    import numpy as np  # noqa: F401
    HAVE_NUMPY = True
except ImportError:  # CI installs only pyyaml
    HAVE_NUMPY = False


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class TestRealRetrieverDense(unittest.TestCase):
    """The production Retriever.dense with ``allowed`` (no model needed: embeddings injected)."""

    def setUp(self):
        import numpy as np
        from ragas_eval.rag_retriever import Retriever
        ch = corpus()
        r = Retriever.__new__(Retriever)
        r.E = np.asarray([embed(c["text"]) for c in ch])
        r.ids, r.src, r.N = [c["chunk_id"] for c in ch], [c["source_file"] for c in ch], len(ch)
        r.reset()
        r.encode = lambda text: np.asarray(embed(text))
        self.r, self.ch = r, ch

    def test_none_is_the_unchanged_behaviour(self):
        self.assertEqual(self.r.dense(Q), self.r.dense(Q, allowed=None))
        self.assertEqual(len(self.r.dense(Q)), 25)

    def test_allowed_fills_the_pool_before_the_cut(self):
        allowed = {c["chunk_id"] for c in self.ch if c["source_file"] == "public.md"}
        out = self.r.dense(Q, allowed=allowed)
        self.assertEqual({r["chunk"]["chunk_id"] for r in out}, allowed)
        self.assertEqual([r for r in self.r.dense(Q, k=10 ** 6) if r["chunk"]["chunk_id"] in allowed][:25], out)


if __name__ == "__main__":
    unittest.main()
