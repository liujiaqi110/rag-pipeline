"""Authentication, token lifecycle, and identity cannot be chosen by the caller."""

import unittest
from datetime import timedelta

from sqlalchemy import select, text

from kb_platform import security
from kb_platform.models import AuthToken, User, utcnow
from kb_platform.tests.helpers import ADMIN, ApiCase


class TestLogin(ApiCase):
    def test_login_and_me(self):
        r = self.client.get("/api/v1/auth/me", headers=self.h("eng_alice"))
        self.assertEqual(r.status_code, 200)
        me = r.json()
        self.assertEqual((me["username"], me["departments"], me["clearance"], me["is_admin"]),
                         ("eng_alice", ["engineering"], 1, False))

    def test_failures_are_indistinguishable(self):
        bodies = {self.login("eng_alice", "wrong-password-1").text.split('"request_id"')[0],
                  self.login("no_such_user", "whatever-123").text.split('"request_id"')[0],
                  self.login("former_gina").text.split('"request_id"')[0]}           # inactive, correct password
        self.assertEqual(len(bodies), 1)
        self.assertIn("invalid_credentials", bodies.pop())

    def test_unauthenticated_and_bad_tokens(self):
        for hdr in ({}, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"},
                    {"Authorization": "Bearer " + "x" * 500}):
            r = self.client.get("/api/v1/auth/me", headers=hdr)
            self.assertEqual(r.status_code, 401, hdr)
            self.assertEqual(r.json()["error"], "unauthenticated")

    def test_lockout_after_repeated_failures(self):
        r = self.client.post("/api/v1/admin/users", headers=self.h(ADMIN),
                             json={"username": "lock_me", "password": "Lockme-pass-01", "clearance": 1})
        self.assertEqual(r.status_code, 201, r.text)
        for _ in range(5):
            self.assertEqual(self.login("lock_me", "bad-pass-0000").status_code, 401)
        self.assertEqual(self.login("lock_me", "Lockme-pass-01").status_code, 401)   # locked even with right pw
        with self.factory() as db:
            u = db.scalar(select(User).where(User.username == "lock_me"))
            u.locked_until = utcnow() - timedelta(minutes=1)
            db.commit()
        self.assertEqual(self.login("lock_me", "Lockme-pass-01").status_code, 200)

    def test_logout_revokes_token(self):
        h = self.h("hr_bob", fresh=True)
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers=h).status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=h).status_code, 401)

    def test_expired_token(self):
        h = self.h("fin_dave", fresh=True)
        with self.factory() as db:
            tok = db.scalar(select(AuthToken).where(AuthToken.token_sha256 == security.token_digest(h["Authorization"][7:])))
            tok.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=h).status_code, 401)

    def test_deactivation_and_permission_change_kill_existing_tokens(self):
        self.client.post("/api/v1/admin/users", headers=self.h(ADMIN),
                         json={"username": "temp_user", "password": "Temp-pass-001", "departments": ["engineering"],
                               "clearance": 1})
        h = {"Authorization": "Bearer " + self.login("temp_user", "Temp-pass-001").json()["access_token"]}
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=h).status_code, 200)
        r = self.client.patch("/api/v1/admin/users/temp_user", headers=self.h(ADMIN), json={"departments": ["hr"]})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=h).status_code, 401)
        h = {"Authorization": "Bearer " + self.login("temp_user", "Temp-pass-001").json()["access_token"]}
        self.client.patch("/api/v1/admin/users/temp_user", headers=self.h(ADMIN), json={"active": False})
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=h).status_code, 401)
        self.assertEqual(self.login("temp_user", "Temp-pass-001").status_code, 401)

    def test_password_change(self):
        self.client.post("/api/v1/admin/users", headers=self.h(ADMIN),
                         json={"username": "pw_user", "password": "First-pass-001"})
        h = {"Authorization": "Bearer " + self.login("pw_user", "First-pass-001").json()["access_token"]}
        r = self.client.post("/api/v1/auth/password", headers=h, json={"current_password": "First-pass-001",
                                                                        "new_password": "short"})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/v1/auth/password", headers=h, json={"current_password": "First-pass-001",
                                                                        "new_password": "Second-pass-002"})
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=h).status_code, 401)
        self.assertEqual(self.login("pw_user", "Second-pass-002").status_code, 200)

    def test_database_stores_only_hashes(self):
        raw = self.token("eng_alice", fresh=True)
        with self.factory() as db:
            dump = " ".join(str(v) for row in db.execute(text("SELECT * FROM auth_tokens")) for v in row)
            dump += " ".join(str(v) for row in db.execute(text("SELECT * FROM users")) for v in row)
        self.assertNotIn(raw, dump)
        self.assertIn(security.token_digest(raw), dump)
        self.assertNotIn(self.creds["eng_alice"], dump)
        self.assertIn("scrypt$", dump)


class TestNoIdentitySpoofing(ApiCase):
    def test_body_cannot_carry_identity(self):
        sid = self.new_session("eng_alice")
        for extra in ({"user_id": "hr_bob"}, {"username": "hr_bob"}, {"roles": ["executive"]}, {"clearance": 3}):
            r = self.client.post(f"/api/v1/sessions/{sid}/ask", headers=self.h("eng_alice"),
                                 json={"question": "薪酬等级是什么？", **extra})
            self.assertEqual(r.status_code, 422, extra)
            self.assertEqual(r.json()["error"], "validation_error")

    def test_headers_and_query_cannot_switch_identity(self):
        sid = self.new_session("eng_alice")
        r = self.client.post(f"/api/v1/sessions/{sid}/ask?user_id=hr_bob&as_user=hr_bob",
                             headers={**self.h("eng_alice"), "X-User-Id": "hr_bob", "X-Forwarded-User": "hr_bob",
                                      "X-Remote-User": "hr_bob"}, json={"question": "薪酬等级和调薪流程是什么？"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("ACLCANARY-HR", r.text)
        self.assertTrue(all(c["source"] in {"company_handbook.md", "eng_deploy_runbook.md"} for c in r.json()["citations"]))
        me = self.client.get("/api/v1/auth/me?username=hr_bob", headers={**self.h("eng_alice"), "X-User-Id": "hr_bob"})
        self.assertEqual(me.json()["username"], "eng_alice")

    def test_no_api_path_to_admin_rights(self):
        r = self.client.patch("/api/v1/admin/users/eng_alice", headers=self.h(ADMIN), json={"is_admin": True})
        self.assertEqual(r.status_code, 422)
        r = self.client.post("/api/v1/admin/users", headers=self.h(ADMIN),
                             json={"username": "sneaky", "password": "Sneaky-pass-01", "is_admin": True})
        self.assertEqual(r.status_code, 422)
        for method, path in (("get", "/api/v1/admin/users"), ("post", "/api/v1/admin/roles"), ("get", "/api/v1/audit"),
                             ("post", "/api/v1/knowledge-bases"), ("put", "/api/v1/documents/x/acl")):
            r = self.client.request(method.upper(), path, headers=self.h("ceo_frank"),
                                    json={"name": "x"} if method != "get" else None)
            self.assertEqual(r.status_code, 403, path)

    def test_unknown_subjects_and_wildcards_rejected(self):
        doc = self.doc_ids()["company_handbook.md"]
        for acl in ({"allow": {"roles": ["*"]}}, {"allow": {"users": ["ghost_user"]}},
                    {"allow": {"departments": ["no_such_dept"]}}, {"allow": {"everyone": True}}):
            r = self.client.put(f"/api/v1/documents/{doc}/acl", headers=self.h(ADMIN), json=acl)
            self.assertEqual(r.status_code, 422, acl)
        r = self.client.post("/api/v1/admin/roles", headers=self.h(ADMIN), json={"name": "*"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
