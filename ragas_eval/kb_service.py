"""Enterprise knowledge-base entry point with ACL (knowledge platform, phase 1).

    $env:RAG_DATA_DIR="build/acl_example_data"; $env:RAG_ACL_POLICY="examples/acl_corpus/acl_policy.json"
    python -m ragas_eval.kb_service --user eng_alice "发布前要做哪些检查？"
    python -m ragas_eval.kb_service --user eng_alice "发布前要做哪些检查？" --audit

Chain (per request, nothing shared between users):
    user_id → directory lookup (policy file; unknown / inactive → access_denied, same message for both)
    → AccessScope (default deny, deny-overrides, clearance AND grant)
    → AclRetriever: BM25 / Dense / RRF restricted at the CANDIDATE stage (acl_retrieval)
    → SafeRagPipeline (citation safety layer, ALWAYS on — `safety=False`, RAG_SAFETY_LAYER=off and
      config/safety_layer.json are ignored here and the override attempt is recorded in the audit)
    → PromptGuardLLM: every <资料 编号=…> block in the prompt must be authorised, else no generation
    → output gate: context / retrieved / cited IDs re-checked; any chunk ID outside the scope is redacted from the
      whole response, including the audit block.

The legacy single-tenant ``rag_service`` is unchanged and has no ACL; the platform must use this module.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import rag_pipeline as rp
from . import rag_safety as rs
from .acl import AccessScope, AclPolicy, AclViolation, assert_within_scope, build_scope
from .acl_retrieval import AclRetriever

DEFAULT_MODE = "rrf"
DENIED_TEXT = "无法处理该请求：用户不存在、已停用或没有任何知识库访问权限。"
BLOCKED_TEXT = "该请求因权限校验未通过而被终止。"
CTX_BLOCK = re.compile(r"<资料\s")
CTX_ID = re.compile(r"<资料\s+编号=\"([^\"]*)\"")
HEX12 = re.compile(r"(?<![0-9A-Za-z])[0-9a-f]{12}(?![0-9A-Za-z])")
REDACTED = "[已隐藏]"


class PromptGuardLLM:
    """Last gate before the model: refuses to generate if any context block is not an authorised chunk."""

    def __init__(self, llm: Any, scope: AccessScope):
        self.llm, self.scope = llm, scope
        self.calls = 0

    def chat(self, messages: List[Dict[str, str]], options: Dict[str, Any], fmt: Optional[str] = None):
        for m in messages:
            content = m.get("content", "")
            ids = CTX_ID.findall(content)
            if len(ids) != len(CTX_BLOCK.findall(content)):
                raise AclViolation("acl gate failed at stage 'prompt' (unlabelled context block)")
            assert_within_scope(ids, self.scope, "prompt")
        self.calls += 1
        return self.llm.chat(messages, options, fmt) if fmt is not None else self.llm.chat(messages, options)

    def model_info(self):
        return self.llm.model_info() if hasattr(self.llm, "model_info") else {}


def _redact(obj: Any, foreign: set) -> Any:
    if isinstance(obj, str):
        return HEX12.sub(lambda m: REDACTED if m.group(0) in foreign else m.group(0), obj)
    if isinstance(obj, list):
        return [_redact(x, foreign) for x in obj]
    if isinstance(obj, dict):  # keys too: e.g. the safety report's {"invalid": {<id>: reason}}
        return {_redact(k, foreign) if isinstance(k, str) else k: _redact(v, foreign) for k, v in obj.items()}
    return obj


class KnowledgeBaseService:
    def __init__(self, policy: AclPolicy, retriever: Any = None, llm: Any = None, mode: str = DEFAULT_MODE,
                 lock: Optional[Any] = None):
        """``lock``: pass one shared lock when several service instances (e.g. one per request/policy) use the same
        base retriever, so its caches are never used by two requests at once."""
        if not isinstance(policy, AclPolicy):
            raise TypeError("an AclPolicy is required (the knowledge platform never runs without ACL)")
        if retriever is None:
            from .rag_retriever import Retriever
            retriever = Retriever()
        if llm is None:
            from .local_llm import OllamaClient
            llm = OllamaClient()
        self.policy, self.base, self.llm, self.mode = policy, retriever, llm, mode
        self.chunk_sources = {d["chunk_id"]: d["source_file"] for d in retriever.bm25.documents}
        ids, src = getattr(retriever, "ids", None), getattr(retriever, "src", None)
        self.dense_sources = dict(zip(ids, src)) if ids is not None and src is not None else None
        self.all_ids = set(self.chunk_sources) | set(self.dense_sources or ())
        self._lock = lock or threading.Lock()  # base retriever caches are shared objects: one request at a time

    def scope_for(self, user_id: str) -> AccessScope:
        return build_scope(self.policy, user_id, self.chunk_sources, self.dense_sources)

    def _safety_record(self, requested: Optional[bool]) -> Dict[str, Any]:
        env = os.environ.get("RAG_SAFETY_LAYER", "").strip().lower()
        attempts = []
        if requested is False:
            attempts.append("argument")
        if env in ("off", "0", "false"):
            attempts.append("env RAG_SAFETY_LAYER")
        return {"enabled": True, "source": "acl_enforced", "override_attempts_ignored": attempts}

    def answer(self, question: str, user_id: str, safety: Optional[bool] = None) -> Dict[str, Any]:
        sw = self._safety_record(safety)
        pr = self.policy.principal(user_id)
        if pr is None or not pr.active:
            return self._finish(question, "access_denied", DENIED_TEXT, None, sw, None)
        scope = self.scope_for(user_id)
        if not scope.allowed_chunks:
            return self._finish(question, "access_denied", DENIED_TEXT, None, sw, scope)
        with self._lock:
            retr = AclRetriever(self.base, scope)
            retr.reset()
            guard = PromptGuardLLM(self.llm, scope)
            cfg = rp.PipelineConfig(mode=self.mode)
            try:
                out = rs.SafeRagPipeline(retr, guard, cfg, set(scope.allowed_chunks)).answer(question)
            except AclViolation:
                return self._finish(question, "blocked", BLOCKED_TEXT, None, sw, scope)
        resp, rep = out["response"], out["safety"]
        if resp.get("error_kind") == "AclViolation":
            return self._finish(question, "blocked", BLOCKED_TEXT, None, sw, scope)
        try:
            for key in ("retrieved_ids", "context_ids", "valid_citations"):
                assert_within_scope(resp.get(key) or [], scope, f"output.{key}")
        except AclViolation:
            return self._finish(question, "blocked", BLOCKED_TEXT, None, sw, scope)
        return self._finish(question, resp.get("status"), resp.get("answer", ""), resp, sw, scope, rep, guard.calls)

    def _finish(self, question, status, answer, resp, sw, scope: Optional[AccessScope], rep=None, llm_calls=0):
        resp = resp or {}
        citations = [{"chunk_id": c, "source": self.chunk_sources.get(c, "")} for c in resp.get("valid_citations") or []]
        user = {"question": question, "answer": answer, "citations": citations, "status": status}
        if status == "insufficient_scoped_evidence":
            user["notice"] = "检索上下文中没有所指定文档的内容（记录为检索失败）"
        audit = {"safety_layer": sw, "safety": rep, "mode": self.mode,
                 "acl": scope.summary() if scope else {"user_id": "<denied>", "policy_version": self.policy.version},
                 "llm_calls": llm_calls, "prompt_version": resp.get("prompt_version"), "prompt_sha256": rp.prompt_hash(),
                 "retrieved_ids": resp.get("retrieved_ids"), "context_ids": resp.get("context_ids"),
                 "raw_answer": resp.get("raw_answer"), "blocked_citations": resp.get("blocked_citations"),
                 "timings_ms": resp.get("timings_ms"), "tokens": resp.get("tokens"), "model": resp.get("model"),
                 "error_kind": resp.get("error_kind")}
        foreign = self.all_ids - (set(scope.allowed_chunks) if scope else set())
        return _redact({"user_response": user, "audit": audit}, foreign)


def main(argv=None) -> int:
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="python -m ragas_eval.kb_service")
    ap.add_argument("question")
    ap.add_argument("--user", required=True, help="user_id from the policy directory")
    ap.add_argument("--policy", help="ACL policy JSON (default: env RAG_ACL_POLICY; there is no built-in policy)")
    ap.add_argument("--audit", action="store_true", help="also print the audit block (contains authorised IDs only)")
    a = ap.parse_args(argv)
    policy = AclPolicy.load(Path(a.policy)) if a.policy else AclPolicy.from_env()
    out = KnowledgeBaseService(policy).answer(a.question, a.user)
    print(json.dumps(out if a.audit else out["user_response"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
