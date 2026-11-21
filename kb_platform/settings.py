"""Runtime settings (environment variables only; no secrets are required: tokens are random and stored hashed)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


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
    # --- P3 ingestion / index ---
    storage_dir: str = field(default_factory=lambda: os.environ.get("KB_STORAGE_DIR", str(PROJECT_ROOT / "build" / "kb_storage")))
    max_upload_bytes: int = field(default_factory=lambda: _int("KB_MAX_UPLOAD_BYTES", 20 * 1024 * 1024))
    max_batch_files: int = field(default_factory=lambda: _int("KB_MAX_BATCH_FILES", 20))
    # dense: auto = use the embedding model if it loads, else build a BM25-only generation and report it;
    #        required = fail the build (old index stays active); off = BM25 only
    dense_mode: str = field(default_factory=lambda: os.environ.get("KB_DENSE", "auto"))
    embedding_model: str = field(default_factory=lambda: os.environ.get("KB_EMBEDDING_MODEL", "BAAI/bge-m3"))
    # rrf = formal service fusion (needs Dense); bm25 = explicit BM25-only answering (never a silent fallback)
    retrieval_mode: str = field(default_factory=lambda: os.environ.get("KB_RETRIEVAL_MODE", "rrf"))
    # --- P4 auth / jobs ---
    # jwt = HS256 JWT whose jti is stored (hashed) server-side -> revocable like opaque tokens; opaque = P2 tokens.
    # Both kinds are always ACCEPTED (existing sessions survive the switch); this only selects what login issues.
    token_format: str = field(default_factory=lambda: os.environ.get("KB_TOKEN_FORMAT", "jwt"))
    jwt_secret: str = field(default_factory=lambda: os.environ.get("KB_JWT_SECRET", ""))  # empty -> key file in storage
    jwt_issuer: str = "kb_platform"
    jwt_audience: str = "kb_platform_api"
    # background jobs: thread = worker thread (production); inline = run right after enqueue (tests / tiny setups)
    jobs_mode: str = field(default_factory=lambda: os.environ.get("KB_JOBS_MODE", "thread"))
