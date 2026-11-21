"""Runtime settings (environment variables only; no secrets are required: tokens are random and stored hashed)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: os.environ.get("KB_DATABASE_URL", "sqlite:///./build/kb_platform.db"))
    token_ttl_minutes: int = field(default_factory=lambda: _int("KB_TOKEN_TTL_MINUTES", 480))
    max_failed_logins: int = field(default_factory=lambda: _int("KB_MAX_FAILED_LOGINS", 5))
    lockout_minutes: int = field(default_factory=lambda: _int("KB_LOCKOUT_MINUTES", 15))
    answer_cache_ttl_s: int = field(default_factory=lambda: _int("KB_ANSWER_CACHE_TTL_S", 300))
    answer_cache_size: int = field(default_factory=lambda: _int("KB_ANSWER_CACHE_SIZE", 256))
    max_question_chars: int = field(default_factory=lambda: _int("KB_MAX_QUESTION_CHARS", 2000))
