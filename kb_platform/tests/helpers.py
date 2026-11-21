"""Test harness: in-memory SQLite, the synthetic ACL corpus (built with the unchanged pipeline scripts), a
numpy-free retriever, a recording mock LLM, and the seeded synthetic users. No model, no Ollama, no private data."""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
import unittest
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*httpx2.*")

from fastapi.testclient import TestClient  # noqa: E402

from kb_platform import security  # noqa: E402
from kb_platform.app import create_app  # noqa: E402
from kb_platform.cli import seed_example  # noqa: E402
from kb_platform.models import User  # noqa: E402
from kb_platform.rag_backend import AnswerCache, RagBackend  # noqa: E402
from kb_platform.settings import Settings  # noqa: E402
from ragas_eval.tests.acl_fixtures import CANARIES, EXPECTED, AclCorpus, RecordingLLM  # noqa: E402

ADMIN = "platform_admin"
_CORPUS = None


def corpus() -> AclCorpus:
    global _CORPUS
    if _CORPUS is None:
        _CORPUS = AclCorpus()
    return _CORPUS


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))


class ApiCase(unittest.TestCase):
    """Fresh app + database per test class; seeded with the 8 synthetic users and 6 synthetic documents."""

    settings_overrides: dict = {}

    @classmethod
    def setUpClass(cls):
        """P2 harness: external (offline-built) index = the synthetic ACL corpus through a fake base retriever."""
        cls.llm = RecordingLLM()
        cls.base = None

        def rf():
            cls.base = corpus().retriever()
            return cls.base
        st = Settings(database_url="sqlite://", **cls.settings_overrides)
        cls.setUpClass_with(st, RagBackend(retriever_factory=rf, llm_factory=lambda: cls.llm,
                                           cache=AnswerCache(st.answer_cache_size, st.answer_cache_ttl_s)))

    @classmethod
    def setUpClass_with(cls, st, backend):
        cls.tmp = Path(tempfile.mkdtemp(prefix="kbapi_"))
        cls.backend = backend
        cls.app = create_app(st, cls.backend)
        cls.client = TestClient(cls.app)
        cls.factory = cls.app.state.sessionmaker
        out = seed_example(cls.factory, cls.tmp / "creds.json")
        cls.kb_id = out["kb_id"]
        cls.creds = json.loads((cls.tmp / "creds.json").read_text(encoding="utf-8"))
        cls.creds[ADMIN] = "Admin-pass-2026x"
        with cls.factory() as db:
            db.add(User(username=ADMIN, password_hash=security.hash_password(cls.creds[ADMIN]), is_admin=True))
            db.commit()
        cls.log = LogCapture()
        logging.getLogger().addHandler(cls.log)
        logging.getLogger().setLevel(logging.DEBUG)
        cls._tokens = {}

    @classmethod
    def tearDownClass(cls):
        logging.getLogger().removeHandler(cls.log)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # helpers ---------------------------------------------------------------------------------------------------------
    def login(self, user, password=None):
        return self.client.post("/api/v1/auth/login", json={"username": user, "password": password or self.creds[user]})

    def token(self, user, fresh=False):
        if fresh or user not in self._tokens:
            r = self.login(user)
            self.assertEqual(r.status_code, 200, r.text)
            self._tokens[user] = r.json()["access_token"]
        return self._tokens[user]

    def h(self, user, fresh=False):
        return {"Authorization": f"Bearer {self.token(user, fresh)}"}

    def new_session(self, user):
        r = self.client.post("/api/v1/sessions", json={"kb_id": self.kb_id}, headers=self.h(user))
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def ask(self, user, question, sid=None, **kw):
        sid = sid or self.new_session(user)
        return self.client.post(f"/api/v1/sessions/{sid}/ask", json={"question": question}, headers=self.h(user), **kw)

    def stored_digest(self, raw: str) -> str:
        """What auth_tokens.token_sha256 holds for this token: sha256(token) for opaque, sha256(jti) for JWT."""
        from kb_platform.security import decode_jwt, token_digest
        if raw.count(".") == 2:
            return token_digest(decode_jwt(raw, self.app.state.jwt)["jti"])
        return token_digest(raw)

    def doc_ids(self):
        r = self.client.get(f"/api/v1/knowledge-bases/{self.kb_id}/documents", headers=self.h(ADMIN))
        return {d["doc_key"]: d["id"] for d in r.json()}

    def forbidden(self, user):
        docs = set(CANARIES) - EXPECTED[user]
        return docs, {c for d in docs for c in corpus().by_doc.get(d, [])}


__all__ = ["ApiCase", "ADMIN", "CANARIES", "EXPECTED", "corpus"]
