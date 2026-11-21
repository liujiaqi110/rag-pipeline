"""P4 SSE streaming for the ACL QA chain.

The answer is produced by exactly the same chain as ``POST /sessions/{id}/ask`` (phase-1 KnowledgeBaseService:
candidate-stage ACL, forced safety layer, PromptGuard, output redaction). Streaming only adds a tap on the model:

    PromptGuardLLM ─► StreamingLLM(shared LLM) ─► deltas ─► DeltaMasker ─► SSE "delta"
                                               └► full text ─► safety layer ─► SSE "final" (authoritative)

* The model only ever saw authorised context (PromptGuard runs before the tap), so deltas cannot contain
  unauthorised text. Still, every citation-like hex token in a delta that is not an ID of the request's authorised
  context is masked before it leaves the server (a model may hallucinate IDs), and partial hex runs at the end of a
  fragment are held back until complete.
* The ``final`` event carries the validated answer + citations and REPLACES the streamed text in the client.
* Revocation during streaming: every ~0.5 s the server re-checks that the token is still valid and that the user's
  scope (principal + policy fingerprint) is unchanged; otherwise a ``revoked`` event is sent, streaming stops, and
  the answer is stored hidden. Nothing else is sent after that.
* Events never contain retrieved chunk IDs before the final validated citations, and never contain document names
  other than those of validated citations.
"""

from __future__ import annotations

import json
import queue
import re
import threading
from typing import Any, Callable, Iterable, Optional, Set

HEX_RUN = re.compile(r"[0-9a-fA-F]{6,}")
TRAILING_HEX = re.compile(r"[0-9a-fA-F]+$")
MASK = "[…]"


class DeltaMasker:
    def __init__(self, allowed: Optional[Set[str]] = None):
        self.allowed = set(allowed or ())
        self._buf = ""

    def set_allowed(self, ids: Iterable[str]):
        self.allowed = set(ids)

    def _mask(self, text: str) -> str:
        def rep(m):
            tok = m.group(0)
            if len(tok) == 12 and tok in self.allowed:
                return tok
            if len(tok) == 12:
                # P5 fix: the final validation (rag_pipeline.CITE_RE) blocks EVERY bare 12-hex token, including
                # all-letter ("abcdefabcdef") and all-digit ones; the stream must never show what the final removes
                return MASK
            if len(tok) < 6 or not (re.search(r"\d", tok) and re.search(r"[a-fA-F]", tok)):
                return tok            # plain numbers / words stay
            return MASK
        return HEX_RUN.sub(rep, text)

    def feed(self, piece: str) -> str:
        self._buf += piece
        m = TRAILING_HEX.search(self._buf)
        hold = m.group(0) if m else ""
        if len(hold) > 40:            # absurdly long hex run: release (it will be masked anyway)
            hold = ""
        out, self._buf = self._buf[: len(self._buf) - len(hold)], hold
        return self._mask(out)

    def flush(self) -> str:
        out, self._buf = self._buf, ""
        return self._mask(out)


class StreamingLLM:
    """Per-request wrapper around the shared LLM: forwards fragments to ``on_delta``; returns the full GenResult."""

    CTX_ID = re.compile(r"<资料\s+编号=\"([0-9a-f]{12})\"")

    def __init__(self, inner: Any, on_delta: Callable[[str], None], on_context: Callable[[Set[str]], None]):
        self.inner, self.on_delta, self.on_context = inner, on_delta, on_context

    def chat(self, messages, options, fmt=None):
        self.on_context(set(self.CTX_ID.findall(messages[-1]["content"])))
        if hasattr(self.inner, "chat_stream"):
            return self.inner.chat_stream(messages, options, self.on_delta)
        g = self.inner.chat(messages, options)                 # non-streaming model (tests): emit in fragments
        for i in range(0, len(g.text), 6):
            self.on_delta(g.text[i:i + 6])
        return g

    def model_info(self):
        return self.inner.model_info() if hasattr(self.inner, "model_info") else {}


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class StreamSession:
    """Runs one answer in a worker thread and exposes its events as an iterator of SSE frames."""

    def __init__(self, run: Callable[[Callable[[Any], Any]], Any], still_valid: Callable[[], bool],
                 finish: Callable[[Any, bool], dict], check_every_s: float = 0.5):
        self.q: "queue.Queue" = queue.Queue()
        self.masker = DeltaMasker()
        self.run, self.still_valid, self.finish, self.every = run, still_valid, finish, check_every_s

    def _wrap(self, llm):
        return StreamingLLM(llm, lambda t: self.q.put(("delta", t)), self.masker.set_allowed)

    def _worker(self):
        try:
            self.q.put(("done", self.run(self._wrap)))
        except BaseException as e:  # noqa: BLE001
            self.q.put(("error", e))

    def events(self):
        import time
        threading.Thread(target=self._worker, daemon=True).start()
        yield sse("status", {"stage": "retrieving"})
        last_check, revoked = time.monotonic(), False
        while True:
            try:
                kind, payload = self.q.get(timeout=self.every)
            except queue.Empty:
                kind, payload = "tick", None
            if time.monotonic() - last_check >= self.every or kind in ("done",):
                last_check = time.monotonic()
                if not self.still_valid():
                    revoked = True
            if revoked:
                if kind == "done":
                    self.finish(payload, True)            # store hidden, show nothing
                else:
                    threading.Thread(target=self._drain_and_store, daemon=True).start()
                yield sse("revoked", {"message": "权限或登录状态已变更，回答已停止并隐藏"})
                return
            if kind == "delta":
                txt = self.masker.feed(payload)
                if txt:
                    yield sse("delta", {"text": txt})
            elif kind == "done":
                rest = self.masker.flush()
                if rest:
                    yield sse("delta", {"text": rest})
                yield sse("final", self.finish(payload, False))
                return
            elif kind == "error":
                code, msg = getattr(payload, "error", "stream_error"), getattr(payload, "message", "回答生成失败")
                yield sse("error", {"error": code, "message": msg})
                return

    def _drain_and_store(self):
        while True:
            kind, payload = self.q.get()
            if kind == "done":
                self.finish(payload, True)
                return
            if kind == "error":
                return
