"""Bridge to the phase-1 ACL RAG service, plus a per-scope answer cache.

The ONLY question-answering implementation reachable from the API is ``ragas_eval.kb_service.KnowledgeBaseService``
(ACL at candidate stage, forced safety layer, prompt/output gates). ``ragas_eval.rag_service`` is not imported here.

Cache isolation: the key is ``(scope.cache_key, kb_id, sha256(question))`` where ``scope.cache_key`` = principal
fingerprint (user, roles, departments, clearance, active) + policy SHA-256 (which embeds the DB ACL version and the
KB's documents/grants). Any permission change → new key → no stale or foreign hit. Only ``ok`` / ``refused`` answers
are cached; denials and errors are never cached.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, Optional, Tuple

from ragas_eval.acl import AclPolicy
from ragas_eval.kb_service import KnowledgeBaseService

CACHEABLE = {"ok", "refused", "partial_answer"}


class AnswerCache:
    def __init__(self, size: int, ttl_s: int):
        self.size, self.ttl = size, ttl_s
        self._d: "OrderedDict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0

    @staticmethod
    def key(scope_key: str, kb_id: str, question: str) -> Tuple[str, str, str]:
        return scope_key, kb_id, hashlib.sha256(question.encode("utf-8")).hexdigest()

    def get(self, key):
        if self.size <= 0 or self.ttl <= 0:
            return None
        with self._lock:
            v = self._d.get(key)
            if not v:
                return None
            if time.monotonic() - v[0] > self.ttl:
                self._d.pop(key, None)
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return dict(v[1])

    def put(self, key, value: Dict[str, Any]):
        if self.size <= 0 or self.ttl <= 0:
            return
        with self._lock:
            self._d[key] = (time.monotonic(), dict(value))
            self._d.move_to_end(key)
            while len(self._d) > self.size:
                self._d.popitem(last=False)

    def clear(self):
        with self._lock:
            self._d.clear()


class RagBackend:
    """Holds the shared base retriever and LLM. Both are created lazily (real: Retriever over RAG_DATA_DIR +
    local Ollama); tests inject fakes via ``retriever_factory`` / ``llm_factory``."""

    def __init__(self, retriever_factory: Optional[Callable[[], Any]] = None,
                 llm_factory: Optional[Callable[[], Any]] = None, cache: Optional[AnswerCache] = None):
        self._rf, self._lf = retriever_factory, llm_factory
        self._retriever = self._llm = None
        self._init_lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.cache = cache or AnswerCache(256, 300)

    def _ensure(self):
        with self._init_lock:
            if self._retriever is None:
                if self._rf is None:
                    from ragas_eval.rag_retriever import Retriever
                    self._rf = Retriever
                self._retriever = self._rf()
            if self._llm is None:
                if self._lf is None:
                    from ragas_eval.local_llm import OllamaClient
                    self._lf = OllamaClient
                self._llm = self._lf()

    def service(self, policy: AclPolicy) -> KnowledgeBaseService:
        self._ensure()
        return KnowledgeBaseService(policy, retriever=self._retriever, llm=self._llm, lock=self.request_lock)

    def ask(self, policy: AclPolicy, username: str, kb_id: str, question: str) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
        """Returns (user_response, audit_summary, cached). The phase-1 audit block is reduced to counts here."""
        svc = self.service(policy)
        scope = svc.scope_for(username)
        key = AnswerCache.key(scope.cache_key, kb_id, question)
        hit = self.cache.get(key)
        if hit is not None:
            return hit["user"], hit["summary"], True
        out = svc.answer(question, username)
        user, aud = out["user_response"], out["audit"]
        summary = {"status": user.get("status"), "llm_calls": aud.get("llm_calls", 0),
                   "authorized_documents": (aud.get("acl") or {}).get("authorized_documents", 0),
                   "authorized_chunks": (aud.get("acl") or {}).get("authorized_chunks", 0),
                   "policy_version": policy.version, "error": aud.get("error_kind"),
                   "scope_key": scope.cache_key}
        if user.get("status") in CACHEABLE:
            self.cache.put(key, {"user": user, "summary": summary})
        return user, summary, False
