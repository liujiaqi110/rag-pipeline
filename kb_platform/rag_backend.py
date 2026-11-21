"""Bridge to the phase-1 ACL RAG service, plus a per-scope answer cache.

The ONLY question-answering implementation reachable from the API is ``ragas_eval.kb_service.KnowledgeBaseService``
(ACL at candidate stage, forced safety layer, prompt/output gates). ``ragas_eval.rag_service`` is not imported here.

Cache isolation: the key is ``(scope.cache_key, kb_id@index_generation, sha256(question))`` where ``scope.cache_key`` = principal
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
    """Holds the LLM, the answer cache and the retrieval source:

    * platform mode (P3 default): ``index`` is an :class:`~kb_platform.ingest.manager.IndexManager`; the caller passes
      the retriever of the ACTIVE index generation, whose id is part of every cache key;
    * external mode (P2): ``retriever_factory`` builds one shared retriever over an index built offline
      (``RAG_DATA_DIR``); tests inject fakes this way.
    """

    def __init__(self, retriever_factory: Optional[Callable[[], Any]] = None,
                 llm_factory: Optional[Callable[[], Any]] = None, cache: Optional[AnswerCache] = None,
                 index: Any = None, mode: str = "rrf"):
        if mode not in ("rrf", "bm25"):
            raise ValueError("retrieval mode must be rrf or bm25")
        self._rf, self._lf = retriever_factory, llm_factory
        self.index = index if retriever_factory is None else None
        self.mode = mode
        self._retriever = self._llm = None
        self._init_lock = threading.Lock()
        self.request_lock = threading.Lock()
        self.cache = cache or AnswerCache(256, 300)

    @property
    def external(self) -> bool:
        return self.index is None

    def _ensure(self, need_retriever: bool):
        with self._init_lock:
            if need_retriever and self._retriever is None:
                if self._rf is None:
                    from ragas_eval.rag_retriever import Retriever
                    self._rf = Retriever
                self._retriever = self._rf()
            if self._llm is None:
                if self._lf is None:
                    from ragas_eval.local_llm import OllamaClient
                    self._lf = OllamaClient
                self._llm = self._lf()

    def service(self, policy: AclPolicy, retriever: Any = None) -> KnowledgeBaseService:
        self._ensure(need_retriever=retriever is None)
        return KnowledgeBaseService(policy, retriever=retriever if retriever is not None else self._retriever,
                                    llm=self._llm, mode=self.mode, lock=self.request_lock)

    def ask(self, policy: AclPolicy, username: str, kb_id: str, question: str, retriever: Any = None,
            generation: str = "external") -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
        """Returns (user_response, audit_summary, cached). The phase-1 audit block is reduced to counts here."""
        svc = self.service(policy, retriever)
        scope = svc.scope_for(username)
        key = AnswerCache.key(scope.cache_key, f"{kb_id}@{generation}", question)
        hit = self.cache.get(key)
        if hit is not None:
            return hit["user"], hit["summary"], True
        out = svc.answer(question, username)
        user, aud = out["user_response"], out["audit"]
        summary = {"status": user.get("status"), "llm_calls": aud.get("llm_calls", 0),
                   "authorized_documents": (aud.get("acl") or {}).get("authorized_documents", 0),
                   "authorized_chunks": (aud.get("acl") or {}).get("authorized_chunks", 0),
                   "policy_version": policy.version, "error": aud.get("error_kind"),
                   "scope_key": scope.cache_key, "generation": generation, "mode": self.mode}
        if user.get("status") in CACHEABLE:
            self.cache.put(key, {"user": user, "summary": summary})
        return user, summary, False
