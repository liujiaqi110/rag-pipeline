"""ACL phase 1: identity model, document policies, default deny, deny-overrides, clearance, policy validation, scopes."""

import copy
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ragas_eval import acl
from ragas_eval.kb_service import KnowledgeBaseService
from ragas_eval.tests.acl_fixtures import ACL_POLICY, CANARIES, EXPECTED, load_policy_dict, bm25_from_chunks, retrieval_module

DOCS = sorted(CANARIES)


class TestAccessMatrix(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = acl.AclPolicy.load(ACL_POLICY)

    def test_full_matrix_matches_expectation(self):
        for user, want in EXPECTED.items():
            got = {d for d in DOCS if self.p.decide(user, d).allowed}
            self.assertEqual(got, want, user)

    def test_default_deny_document_without_policy(self):
        self.assertIsNone(self.p.document("legal_unlabeled_draft.md"))
        for user in EXPECTED:
            self.assertFalse(self.p.decide(user, "legal_unlabeled_draft.md").allowed, user)
        self.assertEqual(self.p.decide("ceo_frank", "legal_unlabeled_draft.md").reason, "no_policy")

    def test_default_deny_unknown_and_inactive_user(self):
        self.assertEqual(self.p.decide("nobody_unknown", "company_handbook.md").reason, "unknown_user")
        self.assertEqual(self.p.decide("former_gina", "eng_deploy_runbook.md").reason, "inactive_user")
        self.assertIsNone(self.p.principal(None))
        self.assertIsNone(self.p.principal(["eng_alice"]))

    def test_deny_overrides_allow(self):
        d = self.p.decide("hr_intern_carol", "hr_salary_bands.md")
        self.assertEqual((d.allowed, d.reason), (False, "explicit_deny"))
        self.assertTrue(self.p.decide("hr_bob", "hr_salary_bands.md").allowed)

    def test_clearance_is_required_in_addition_to_a_grant(self):
        d = self.p.decide("contractor_hank", "company_handbook.md")   # all_authenticated, but internal > clearance 0
        self.assertEqual((d.allowed, d.reason), (False, "insufficient_clearance"))
        data = load_policy_dict()
        data["users"]["eng_low"] = {"roles": ["executive"], "departments": [], "clearance": 2}
        d = acl.AclPolicy(data).decide("eng_low", "exec_strategy_memo.md")            # role grant, clearance too low
        self.assertEqual((d.allowed, d.reason), (False, "insufficient_clearance"))

    def test_grant_kinds(self):
        self.assertEqual(self.p.decide("auditor_erin", "finance_budget_plan.md").reason, "grant_role")
        self.assertEqual(self.p.decide("fin_dave", "finance_budget_plan.md").reason, "grant_department")
        self.assertEqual(self.p.decide("eng_alice", "company_handbook.md").reason, "grant_all_authenticated")
        data = load_policy_dict()
        data["documents"]["hr_salary_bands.md"]["allow"]["users"] = ["eng_alice"]
        data["users"]["eng_alice"]["clearance"] = 2
        self.assertEqual(acl.AclPolicy(data).decide("eng_alice", "hr_salary_bands.md").reason, "grant_user")

    def test_no_implicit_superuser(self):
        for d in ("hr_salary_bands.md", "finance_budget_plan.md", "eng_deploy_runbook.md", "legal_unlabeled_draft.md"):
            self.assertFalse(self.p.decide("ceo_frank", d).allowed, d)

    def test_policy_with_no_grant_denies(self):
        data = load_policy_dict()
        data["documents"]["eng_deploy_runbook.md"]["allow"] = {}
        self.assertEqual(acl.AclPolicy(data).decide("eng_alice", "eng_deploy_runbook.md").reason, "no_matching_grant")


class TestPolicyValidationFailsClosed(unittest.TestCase):
    def bad(self, mutate):
        data = copy.deepcopy(load_policy_dict())
        mutate(data)
        with self.assertRaises(acl.PolicyError):
            acl.AclPolicy(data)

    def test_wildcards_rejected(self):
        self.bad(lambda d: d["documents"]["hr_salary_bands.md"]["allow"].update(roles=["*"]))
        self.bad(lambda d: d["documents"]["hr_salary_bands.md"]["allow"].update(departments=["hr*"]))

    def test_unknown_keys_rejected(self):
        self.bad(lambda d: d["documents"]["hr_salary_bands.md"].update(allow_all=True))
        self.bad(lambda d: d["documents"]["hr_salary_bands.md"]["allow"].update(everyone=True))
        self.bad(lambda d: d["users"]["hr_bob"].update(is_admin=True))
        self.bad(lambda d: d.update(superusers=["ceo_frank"]))

    def test_bad_values_rejected(self):
        self.bad(lambda d: d["documents"]["hr_salary_bands.md"].update(classification="secret"))
        self.bad(lambda d: d["users"]["hr_bob"].update(clearance=True))
        self.bad(lambda d: d["users"]["hr_bob"].update(clearance=9))
        self.bad(lambda d: d["users"]["hr_bob"].update(active="yes"))
        self.bad(lambda d: d["users"]["hr_bob"].update(roles="employee"))
        self.bad(lambda d: d["documents"]["company_handbook.md"]["allow"].update(all_authenticated="true"))
        self.bad(lambda d: d.pop("policy_version"))
        self.bad(lambda d: d.update(users=[]))

    def test_unreadable_or_missing_policy(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "p.json"
            p.write_text("{not json", encoding="utf-8")
            with self.assertRaises(acl.PolicyError):
                acl.AclPolicy.load(p)
            with self.assertRaises(acl.PolicyError):
                acl.AclPolicy.load(Path(t) / "missing.json")
        with mock.patch.dict(os.environ, {acl.POLICY_ENV: ""}):
            with self.assertRaises(acl.PolicyError):
                acl.AclPolicy.from_env()
        with mock.patch.dict(os.environ, {acl.POLICY_ENV: str(ACL_POLICY)}):
            self.assertEqual(acl.AclPolicy.from_env().version, "acl-example-2026-11-21.1")

    def test_service_requires_a_policy(self):
        with self.assertRaises(TypeError):
            KnowledgeBaseService(None, retriever=object(), llm=object())

    def test_identity_is_not_taken_from_the_request(self):
        params = set(inspect.signature(KnowledgeBaseService.answer).parameters)
        self.assertEqual(params, {"self", "question", "user_id", "safety"})


class TestScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = acl.AclPolicy.load(ACL_POLICY)
        cls.src = {f"{i:012x}": d for i, d in enumerate(DOCS)}

    def test_scope_contains_only_authorised_chunks(self):
        for user, want in EXPECTED.items():
            s = acl.build_scope(self.p, user, self.src)
            self.assertEqual(set(s.allowed_docs), want, user)
            self.assertEqual({self.src[c] for c in s.allowed_chunks}, want, user)

    def test_dense_source_mismatch_is_excluded(self):
        cid = next(c for c, d in self.src.items() if d == "eng_deploy_runbook.md")
        s = acl.build_scope(self.p, "eng_alice", self.src, dense_sources={cid: "hr_salary_bands.md"})
        self.assertNotIn(cid, s.allowed_chunks)
        self.assertEqual(s.excluded_inconsistent, 1)

    def test_cache_key_differs_by_principal_and_policy(self):
        a = acl.build_scope(self.p, "eng_alice", self.src)
        b = acl.build_scope(self.p, "hr_bob", self.src)
        data = load_policy_dict()
        data["policy_version"] = "v2"
        a2 = acl.build_scope(acl.AclPolicy(data), "eng_alice", self.src)
        self.assertEqual(len({a.cache_key, b.cache_key, a2.cache_key}), 3)

    def test_summary_has_no_document_names(self):
        s = json.dumps(acl.build_scope(self.p, "eng_alice", self.src).summary())
        for d in DOCS:
            self.assertNotIn(d, s)

    def test_assert_within_scope_does_not_name_the_chunk(self):
        s = acl.build_scope(self.p, "eng_alice", self.src)
        foreign = next(c for c, d in self.src.items() if d == "hr_salary_bands.md")
        with self.assertRaises(acl.AclViolation) as cm:
            acl.assert_within_scope([foreign], s, "x")
        self.assertNotIn(foreign, str(cm.exception))
        acl.assert_within_scope([], s, "x")


class TestFilteredBM25View(unittest.TestCase):
    def test_original_scoring_over_authorised_chunks_only(self):
        chunks = [{"chunk_id": f"{i:012x}", "source_file": "a.md" if i % 2 else "b.md", "text": t}
                  for i, t in enumerate(["发布 检查 预算", "发布 窗口", "预算 调整 审批", "检查 清单", "请假 流程"])]
        bm = bm25_from_chunks(chunks)
        p = acl.AclPolicy({"policy_version": "t", "users": {"u": {"departments": ["d"], "clearance": 1}},
                           "documents": {"a.md": {"allow": {"departments": ["d"]}}}})
        scope = acl.build_scope(p, "u", {c["chunk_id"]: c["source_file"] for c in chunks})
        view = acl.FilteredBM25View(bm, scope)
        m = retrieval_module()
        full = m.search_bm25("发布 预算 检查", bm, top_k=99)
        got = m.search_bm25("发布 预算 检查", view, top_k=99)
        self.assertEqual(view.N, 2)
        self.assertTrue(all(r["chunk"]["source_file"] == "a.md" for r in got))
        self.assertEqual([(r["chunk"]["chunk_id"], r["bm25_score"]) for r in got],
                         [(r["chunk"]["chunk_id"], r["bm25_score"]) for r in full if r["chunk"]["source_file"] == "a.md"])


if __name__ == "__main__":
    unittest.main()
