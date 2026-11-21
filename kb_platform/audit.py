"""Audit trail + log hygiene.

* ``audit()`` writes one AuditLog row. ``detail`` is filtered through :func:`safe_detail`: only whitelisted keys with
  scalar values survive; free text (questions, answers, passwords, tokens, chunk text) is never stored — questions are
  represented by SHA-256 + length.
* :class:`RedactingFilter` is attached to the ``kb_platform`` and ``uvicorn`` loggers and scrubs bearer tokens,
  ``password``/``token`` key-values and anything that looks like an issued token from every log record.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from .models import AuditLog

log = logging.getLogger("kb_platform")

SAFE_KEYS = {"status", "llm_calls", "authorized_documents", "authorized_chunks", "policy_version", "question_sha256",
             "question_chars", "citations", "kb_id", "session_id", "document_id", "target_user", "reason", "cached",
             "http_status", "path", "method", "count", "fields", "error"}


def question_fingerprint(q: str) -> Dict[str, Any]:
    return {"question_sha256": hashlib.sha256(q.encode("utf-8")).hexdigest()[:16], "question_chars": len(q)}


def safe_detail(detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in (detail or {}).items():
        if k not in SAFE_KEYS:
            continue
        if isinstance(v, (bool, int, float)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:128]
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[k] = [x[:64] for x in v[:20]]
    return out


def audit(db: Session, *, action: str, outcome: str, actor: str = "", resource: str = "", request_id: str = "",
          detail: Optional[Dict[str, Any]] = None, commit: bool = True) -> None:
    db.add(AuditLog(action=action, outcome=outcome, actor=actor[:64], resource=resource[:128],
                    request_id=request_id[:32], detail=safe_detail(detail)))
    if commit:
        db.commit()


_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\"?(?:password|new_password|access_token|token|authorization)\"?\s*[:=]\s*\"?)[^\"\s,}]+"),
     r"\1[REDACTED]"),
    (re.compile(r"[A-Za-z0-9_-]{43}"), "[REDACTED]"),  # shape of secrets.token_urlsafe(32)
]


def scrub(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        record.msg, record.args = scrub(msg), ()
        return True


def install_log_filter() -> None:
    f = RedactingFilter()
    for name in ("kb_platform", "uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        lg = logging.getLogger(name)
        if not any(isinstance(x, RedactingFilter) for x in lg.filters):
            lg.addFilter(f)
