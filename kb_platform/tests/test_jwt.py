"""P4 JWT: compatible with standard JWT tooling, but every token is still revocable server-side."""

import base64
import json
import time
import unittest

import jwt as pyjwt

from kb_platform.tests.helpers import ADMIN, ApiCase


def b64(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


class TestJwt(ApiCase):
    def claims(self, raw):
        return pyjwt.decode(raw, options={"verify_signature": False})

    def me(self, raw):
        return self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {raw}"}).status_code

    def test_login_issues_standard_hs256_jwt_without_privileges(self):
        r = self.login("eng_alice").json()
        self.assertEqual(r["token_format"], "jwt")
        raw = r["access_token"]
        self.assertEqual(pyjwt.get_unverified_header(raw)["alg"], "HS256")
        c = self.claims(raw)
        self.assertEqual((c["sub"], c["typ"], c["iss"], c["aud"]), ("eng_alice", "access", "kb_platform", "kb_platform_api"))
        for k in ("jti", "exp", "iat", "nbf", "uid"):
            self.assertIn(k, c)
        for k in ("roles", "departments", "clearance", "is_admin"):
            self.assertNotIn(k, c)                       # privileges are always read from the database
        self.assertEqual(self.me(raw), 200)

    def test_tampering_and_algorithm_attacks(self):
        raw = self.login("eng_alice").json()["access_token"]
        h, p, s = raw.split(".")
        c = self.claims(raw)
        forged = {**c, "sub": "ceo_frank", "uid": c["uid"] + 5}
        self.assertEqual(self.me(f"{h}.{b64(forged)}.{s}"), 401)                       # payload changed, old signature
        self.assertEqual(self.me(f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64(forged)}."), 401)  # alg=none
        self.assertEqual(self.me(pyjwt.encode(c, "attacker-key-" + "x" * 40, algorithm="HS256")), 401)  # wrong key
        self.assertEqual(self.me(raw[:-3] + ("AAA" if not raw.endswith("AAA") else "BBB")), 401)
        hs512 = pyjwt.encode(c, self.app.state.jwt.secret, algorithm="HS512")
        self.assertEqual(self.me(hs512), 401)                                          # only HS256 accepted

    def test_valid_signature_is_not_enough(self):
        """Even with the real key, a JWT whose jti has no server record (or belongs to someone else) is rejected."""
        key = self.app.state.jwt.secret
        c = self.claims(self.login("eng_alice").json()["access_token"])
        now = int(time.time())
        unknown = {**c, "jti": "never-issued-jti", "iat": now, "nbf": now, "exp": now + 600}
        self.assertEqual(self.me(pyjwt.encode(unknown, key, algorithm="HS256")), 401)
        bob = self.claims(self.login("hr_bob").json()["access_token"])
        swap = {**bob, "sub": "eng_alice", "uid": c["uid"]}                          # bob's jti, alice's identity
        self.assertEqual(self.me(pyjwt.encode(swap, key, algorithm="HS256")), 401)
        for missing in ("jti", "exp", "aud", "iss"):
            bad = {k: v for k, v in c.items() if k != missing}
            self.assertEqual(self.me(pyjwt.encode(bad, key, algorithm="HS256")), 401, missing)
        wrong_aud = {**c, "aud": "other-service"}
        self.assertEqual(self.me(pyjwt.encode(wrong_aud, key, algorithm="HS256")), 401)

    def test_expired_and_revoked(self):
        key = self.app.state.jwt.secret
        raw = self.login("fin_dave").json()["access_token"]
        c = self.claims(raw)
        self.assertEqual(self.me(pyjwt.encode({**c, "exp": int(time.time()) - 5}, key, algorithm="HS256")), 401)
        self.assertEqual(self.client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {raw}"}).status_code, 204)
        self.assertEqual(self.me(raw), 401)                                            # signature still valid, jti revoked
        raw = self.login("fin_dave").json()["access_token"]
        self.client.patch("/api/v1/admin/users/fin_dave", headers=self.h(ADMIN), json={"clearance": 1})
        self.assertEqual(self.me(raw), 401)                                            # permission change revokes
        self.client.patch("/api/v1/admin/users/fin_dave", headers=self.h(ADMIN), json={"clearance": 2})

    def test_opaque_tokens_keep_working_and_can_be_issued(self):
        from kb_platform import security
        with self.factory() as db:
            from sqlalchemy import select
            from kb_platform.models import User
            u = db.scalar(select(User).where(User.username == "hr_bob"))
            raw, _ = security.issue_token(db, u, 10, "opaque")
        self.assertEqual(raw.count("."), 0)
        self.assertEqual(self.me(raw), 200)
        self.client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {raw}"})
        self.assertEqual(self.me(raw), 401)


class TestOpaqueMode(ApiCase):
    settings_overrides = {"token_format": "opaque"}

    def test_opaque_login(self):
        r = self.login("eng_alice").json()
        self.assertEqual((r["token_format"], r["access_token"].count(".")), ("opaque", 0))
        self.assertEqual(self.client.get("/api/v1/auth/me", headers=self.h("eng_alice")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
