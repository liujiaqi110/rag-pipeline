"""Local generation adapter (Ollama HTTP API on 127.0.0.1 only) + a mock for software tests. 0 paid / remote calls.

* Never pulls or downloads models and never changes Ollama configuration: the model must already be listed by /api/tags.
* Only loopback hosts are accepted, so a misconfigured URL cannot send data to a remote service.
* Returns measured token counts / durations from Ollama; nothing is estimated.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


class GenerationError(RuntimeError):
    def __init__(self, kind: str, msg: str):
        super().__init__(msg)
        self.kind = kind  # unavailable | model_missing | timeout | http_error | empty_response | bad_response


@dataclass
class GenResult:
    text: str
    model: str
    prompt_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_ms: Optional[float] = None          # wall clock measured here
    ollama_total_ms: Optional[float] = None   # reported by Ollama
    load_ms: Optional[float] = None
    prompt_eval_ms: Optional[float] = None
    eval_ms: Optional[float] = None
    done_reason: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


class OllamaClient:
    def __init__(self, model: str = "qwen2.5:7b", base_url: str = "http://127.0.0.1:11434", timeout_s: float = 300.0):
        host = urlparse(base_url).hostname
        if host not in LOOPBACK:
            raise ValueError("only a local (loopback) Ollama endpoint is allowed")
        self.model, self.base, self.timeout = model, base_url.rstrip("/"), timeout_s

    def _req(self, path: str, payload: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": "application/json"},
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise GenerationError("http_error", f"HTTP {e.code}") from None
        except (urllib.error.URLError, ConnectionError) as e:
            raise GenerationError("unavailable", type(e).__name__) from None
        except TimeoutError:
            raise GenerationError("timeout", "timeout") from None

    def model_info(self) -> Dict[str, Any]:
        """Version + digest of the installed model; raises model_missing instead of pulling."""
        ver = self._req("/api/version", timeout=10).get("version")
        tags = self._req("/api/tags", timeout=10).get("models", [])
        m = next((x for x in tags if x.get("name") == self.model or x.get("model") == self.model), None)
        if m is None:
            raise GenerationError("model_missing", f"{self.model} not installed (no automatic download)")
        d = m.get("details", {})
        return {"ollama_version": ver, "model": m.get("name"), "digest": m.get("digest"), "size": m.get("size"),
                "parameter_size": d.get("parameter_size"), "quantization": d.get("quantization_level"), "family": d.get("family")}

    def chat(self, messages: List[Dict[str, str]], options: Dict[str, Any], fmt: Optional[str] = None) -> GenResult:
        payload = {"model": self.model, "messages": messages, "stream": False, "options": options}
        if fmt:
            payload["format"] = fmt
        t0 = time.perf_counter()
        r = self._req("/api/chat", payload)
        wall = (time.perf_counter() - t0) * 1000
        text = ((r.get("message") or {}).get("content") or "").strip()
        if not text:
            raise GenerationError("empty_response", f"done_reason={r.get('done_reason')}")
        ns = lambda k: round(r[k] / 1e6, 2) if isinstance(r.get(k), (int, float)) else None
        return GenResult(text=text, model=r.get("model", self.model), prompt_tokens=r.get("prompt_eval_count"),
                         output_tokens=r.get("eval_count"), total_ms=round(wall, 2), ollama_total_ms=ns("total_duration"),
                         load_ms=ns("load_duration"), prompt_eval_ms=ns("prompt_eval_duration"), eval_ms=ns("eval_duration"),
                         done_reason=r.get("done_reason"))


    def chat_stream(self, messages: List[Dict[str, str]], options: Dict[str, Any],
                    on_delta: Callable[[str], None]) -> GenResult:
        """Same as ``chat`` but with ``stream: true``; ``on_delta`` receives each text fragment as it arrives.
        Returns the complete result (identical shape to ``chat``) so callers can validate the full answer."""
        payload = {"model": self.model, "messages": messages, "stream": True, "options": options}
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        t0, parts, last = time.perf_counter(), [], {}
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                for line in r:
                    if not line.strip():
                        continue
                    obj = json.loads(line.decode("utf-8"))
                    piece = (obj.get("message") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        on_delta(piece)
                    if obj.get("done"):
                        last = obj
                        break
        except urllib.error.HTTPError as e:
            raise GenerationError("http_error", f"HTTP {e.code}") from None
        except (urllib.error.URLError, ConnectionError) as e:
            raise GenerationError("unavailable", type(e).__name__) from None
        except TimeoutError:
            raise GenerationError("timeout", "timeout") from None
        text = "".join(parts).strip()
        if not text:
            raise GenerationError("empty_response", f"done_reason={last.get('done_reason')}")
        ns = lambda k: round(last[k] / 1e6, 2) if isinstance(last.get(k), (int, float)) else None  # noqa: E731
        return GenResult(text=text, model=last.get("model", self.model), prompt_tokens=last.get("prompt_eval_count"),
                         output_tokens=last.get("eval_count"), total_ms=round((time.perf_counter() - t0) * 1000, 2),
                         ollama_total_ms=ns("total_duration"), load_ms=ns("load_duration"),
                         prompt_eval_ms=ns("prompt_eval_duration"), eval_ms=ns("eval_duration"),
                         done_reason=last.get("done_reason"))


class MockLLM:
    """Deterministic stand-in for tests: ``responder(messages) -> text``. Token counts are reported as unavailable."""

    def __init__(self, responder: Callable[[List[Dict[str, str]]], str], model: str = "mock"):
        self.responder, self.model, self.calls = responder, model, 0

    def model_info(self) -> Dict[str, Any]:
        return {"ollama_version": None, "model": self.model, "digest": "mock"}

    def chat(self, messages, options, fmt=None) -> GenResult:
        self.calls += 1
        t0 = time.perf_counter()
        text = self.responder(messages)
        if isinstance(text, Exception):
            raise text
        return GenResult(text=text, model=self.model, total_ms=round((time.perf_counter() - t0) * 1000, 3))


def gpu_memory_used_mib() -> Optional[int]:
    """Measured GPU memory in use (all processes) via nvidia-smi; None when unavailable."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0]) if out.returncode == 0 and out.stdout.strip() else None
    except Exception:
        return None


def gpu_name_total() -> Optional[Dict[str, Any]]:
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        n, t = out.stdout.strip().splitlines()[0].split(",")
        return {"name": n.strip(), "memory_total_mib": int(t)}
    except Exception:
        return None
