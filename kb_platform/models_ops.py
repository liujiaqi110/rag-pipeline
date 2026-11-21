"""P5 knowledge-operations tables (registered on the same declarative Base as kb_platform.models)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import new_id, utcnow


class PageView(Base):
    """One SPA route view (route NAME only — no query string, no ids). PV = rows, UV = distinct user_id."""
    __tablename__ = "page_views"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    route: Mapped[str] = mapped_column(String(48), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class QaEvent(Base):
    """Service log of every user question (one row per question, whatever the outcome).

    source:  faq (published FAQ answered, no model call) | answer_cache | rag (pipeline ran) | none (rejected first)
    outcome: answered | knowledge_gap | permission | generation_failure | infrastructure
    llm_calls counts REAL model invocations of this request (0 for FAQ and answer-cache hits)."""
    __tablename__ = "qa_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kb_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    message_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    question_hash: Mapped[str] = mapped_column(String(64), default="")
    source: Mapped[str] = mapped_column(String(16), default="rag")
    status: Mapped[str] = mapped_column(String(48), default="")
    outcome: Mapped[str] = mapped_column(String(24), default="answered", index=True)
    gap_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    generation_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    faq_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    generation: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    top_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    retrieved: Mapped[int] = mapped_column(Integer, default=0)
    citations: Mapped[list] = mapped_column(JSON, default=list)


class FaqEntry(Base):
    """FAQ candidate / published entry. Mining ONLY creates candidates; publishing needs a human reviewer.
    status: candidate -> published | rejected;  published -> retired;  retired -> published (re-review)."""
    __tablename__ = "faq_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)        # [{chunk_id, source, document_id, version_id}]
    evidence: Mapped[list] = mapped_column(JSON, default=list)         # [{document_id, version_id, version_no, title}]
    evidence_key: Mapped[str] = mapped_column(String(64), default="")  # hash of the evidence document set
    embeddings: Mapped[list] = mapped_column(JSON, default=list)       # question + member embeddings (normalised)
    frequency: Mapped[int] = mapped_column(Integer, default=0)
    distinct_users: Mapped[int] = mapped_column(Integer, default=0)
    similarity_mean: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    answer_source: Mapped[str] = mapped_column(String(24), default="generated")  # generated | member_answer | edited
    embedding_model: Mapped[str] = mapped_column(String(128), default="")
    llm_model: Mapped[str] = mapped_column(String(128), default="")
    llm_digest: Mapped[str] = mapped_column(String(128), default="")
    index_generation: Mapped[str] = mapped_column(String(40), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)


class FaqMember(Base):
    __tablename__ = "faq_members"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    faq_id: Mapped[str] = mapped_column(ForeignKey("faq_entries.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)       # never returned by the API
    question: Mapped[str] = mapped_column(Text, nullable=False)
    similarity: Mapped[float] = mapped_column(Float, default=0.0)
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FaqEvent(Base):
    __tablename__ = "faq_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    faq_id: Mapped[str] = mapped_column(ForeignKey("faq_entries.id", ondelete="CASCADE"), nullable=False, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    from_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class KnowledgeGap(Base):
    """A real KNOWLEDGE gap (no / insufficient / low-relevance evidence, stale knowledge). Permission denials, model
    failures and infrastructure failures are NOT gaps (they are qa_events outcomes of their own)."""
    __tablename__ = "knowledge_gaps"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    gap_type: Mapped[str] = mapped_column(String(32), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    occurrences: Mapped[int] = mapped_column(Integer, default=0)
    distinct_users: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open|in_progress|resolved|dismissed
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)


class GapOccurrence(Base):
    __tablename__ = "gap_occurrences"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gap_id: Mapped[str] = mapped_column(ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False, index=True)
    qa_event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)       # never returned by the API
    question: Mapped[str] = mapped_column(Text, nullable=False)
    gap_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeTask(Base):
    """Knowledge supplement task created from a gap. status: todo -> in_progress -> done -> verified | reopened."""
    __tablename__ = "knowledge_tasks"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    gap_id: Mapped[str] = mapped_column(ForeignKey("knowledge_gaps.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    assignee: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="todo")
    document_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
