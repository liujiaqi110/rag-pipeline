"""Shared helpers: question normalisation, compliance filter, embeddings, readable-document checks."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ragas_eval.acl import PolicyError

from ..acl_store import policy_for
from ..ingest.errors import EmbeddingUnavailable
from ..models import Document, KnowledgeBase, User

_PUNCT_END = re.compile(r"[\s?？。.!！,，;；:：]+$")


def normalize(q: str) -> str:
    q = unicodedata.normalize("NFKC", q or "").lower()
    q = re.sub(r"\s+", " ", q).strip()
    return _PUNCT_END.sub("", q)


def qhash(q: str) -> str:
    return hashlib.sha256(normalize(q).encode("utf-8")).hexdigest()


# "合规的候选问题": questions that carry personal data or identifiers are never mined into FAQs
_PII = [
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),                          # CN mobile number
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),                         # e-mail
    re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),                        # CN resident ID
    re.compile(r"(?<!\d)\d{16,19}(?!\d)"),                           # bank card
    re.compile(r"(?<![0-9a-fA-F])[0-9a-f]{12}(?![0-9a-fA-F])"),      # chunk-ID-like tokens
    re.compile(r"(?i)(password|passwd|密码|token|secret)"),
]


def compliance_problem(q: str) -> Optional[str]:
    n = normalize(q)
    if len(n) < 4:
        return "too_short"
    if len(n) > 300:
        return "too_long"
    for p in _PII:
        if p.search(q):
            return "sensitive_content"
    return None


def embed(backend: Any, texts: List[str]):
    """Normalised embeddings with the platform embedding model (bge-m3 in production). Raises EmbeddingUnavailable."""
    import numpy as np
    if backend is None or getattr(backend, "index", None) is None:
        raise EmbeddingUnavailable("外部索引模式没有平台向量模型")
    model = backend.index.embedder()
    if not texts:
        return np.zeros((0, 1), dtype="float32")
    v = np.asarray(model.encode(list(texts), batch_size=16, normalize_embeddings=True, show_progress_bar=False),
                   dtype="float32")
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return v / n


def embedding_model_name(backend: Any) -> str:
    try:
        return backend.index.settings.embedding_model if getattr(backend.index.embedder(), "name", None) is None \
            else getattr(backend.index.embedder(), "name")
    except Exception:  # noqa: BLE001
        return ""


def readable_doc_ids(db: Session, user: User, kb: KnowledgeBase) -> set:
    """Active documents of the KB the user may READ right now (phase-1 decision on the DB policy). Broken policy →
    empty set (fail closed)."""
    try:
        pol = policy_for(db, user, kb)
    except PolicyError:
        return set()
    return {d.id for d in kb.documents if d.status == "active" and pol.decide(user.username, d.doc_key).allowed}


def docs_by_key(db: Session, keys: Iterable[str]) -> dict:
    keys = list({k for k in keys if k})
    if not keys:
        return {}
    return {d.doc_key: d for d in db.scalars(select(Document).where(Document.doc_key.in_(keys)))}
