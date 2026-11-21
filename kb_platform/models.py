"""Persistent model: identities, roles, departments, knowledge bases, documents, document ACL grants, tokens,
chat sessions/messages, audit log, and a global ACL version counter (bumped on every permission-relevant change)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Table, Text, Column, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


user_roles = Table("user_roles", Base.metadata,
                   Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
                   Column("role_id", ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True))
user_departments = Table("user_departments", Base.metadata,
                         Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
                         Column("department_id", ForeignKey("departments.id", ondelete="CASCADE"), primary_key=True))


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="")
    # P4: organisation tree for management/UI only. ACL does NOT inherit along the tree (no implicit grants).
    parent_id: Mapped[Optional[int]] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL", name="fk_departments_parent_id"), nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    clearance: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # platform administration (manage users / metadata / ACL). Grants NO document read access by itself.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    roles: Mapped[List[Role]] = relationship(secondary=user_roles, lazy="selectin")
    departments: Mapped[List[Department]] = relationship(secondary=user_departments, lazy="selectin")


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # never the raw token
    # P4: "opaque" (sha256 of the random token) or "jwt" (sha256 of the JWT's jti) — both revocable server-side
    kind: Mapped[str] = mapped_column(String(8), default="opaque", server_default="opaque")
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    documents: Mapped[List["Document"]] = relationship(back_populates="kb", lazy="selectin")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("doc_key", name="uq_documents_doc_key"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    doc_key: Mapped[str] = mapped_column(String(255), nullable=False)  # = source_file of the chunks in the index
    title: Mapped[str] = mapped_column(String(255), default="")
    classification: Mapped[str] = mapped_column(String(32), default="internal")
    owner_department: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | disabled | archived | deleted
    # P3: "metadata" = registered without a file (external index, P2); "upload" = managed by the ingestion pipeline
    source: Mapped[str] = mapped_column(String(16), default="metadata")
    original_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    current_version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # version being served
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    kb: Mapped[KnowledgeBase] = relationship(back_populates="documents")
    grants: Mapped[List["DocumentGrant"]] = relationship(back_populates="document", lazy="selectin",
                                                          cascade="all, delete-orphan")


class DocumentGrant(Base):
    """effect allow|deny × subject user|role|department|all_authenticated (subject_value empty for the latter)."""
    __tablename__ = "document_grants"
    __table_args__ = (UniqueConstraint("document_id", "effect", "subject_type", "subject_value", name="uq_grant"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    effect: Mapped[str] = mapped_column(String(8), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_value: Mapped[str] = mapped_column(String(64), default="")
    document: Mapped[Document] = relationship(back_populates="grants")


class AclState(Base):
    """Single row. ``version`` is bumped on every change to users/roles/departments/documents/grants, so policy
    fingerprints (and therefore every answer-cache key) change immediately."""
    __tablename__ = "acl_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    messages: Mapped[List["ChatMessage"]] = relationship(back_populates="session", lazy="selectin",
                                                          cascade="all, delete-orphan", order_by="ChatMessage.id")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, default="")
    citations: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(48), default="")
    scope_key: Mapped[str] = mapped_column(String(64), default="")  # principal + policy fingerprint at answer time
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    session: Mapped[ChatSession] = relationship(back_populates="messages")


class AuditLog(Base):
    """Never stores tokens, passwords, question/answer text or chunk text — only hashes, counts and outcomes."""
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    request_id: Mapped[str] = mapped_column(String(32), default="")
    actor: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource: Mapped[str] = mapped_column(String(128), default="")
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class KbManager(Base):
    """P3: users allowed to upload / edit / delete documents and edit ACLs in ONE knowledge base. Managing a document
    does NOT grant reading it: content access still needs an ACL grant (default: none)."""
    __tablename__ = "kb_managers"
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class DocumentVersion(Base):
    """One uploaded file. status: uploaded → chunked → indexed | failed | index_failed; superseded when a newer
    version is indexed; purged after the document is deleted and the index no longer contains it."""
    __tablename__ = "document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version_no", name="uq_version_no"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    file_format: Mapped[str] = mapped_column(String(16), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    storage_relpath: Mapped[str] = mapped_column(String(255), default="")  # relative to KB_STORAGE_DIR, server-chosen
    status: Mapped[str] = mapped_column(String(16), default="uploaded")
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # fixed user-safe text only
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    generation_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint("version_id", "chunk_id", name="uq_version_chunk"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(32), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    is_parent: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    section_heading: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    paragraph: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # full chunk record from scripts 03/04/05


class IndexGeneration(Base):
    """An immutable on-disk BM25 (+ Dense) index built from the database. Exactly one is ``active``; a failed build
    never replaces the active one."""
    __tablename__ = "index_generations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(16), default="building")  # building | active | retired | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    document_count: Mapped[int] = mapped_column(Integer, default=0)
    dense: Mapped[bool] = mapped_column(Boolean, default=False)
    dense_model: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    dense_error: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    built_by: Mapped[str] = mapped_column(String(64), default="")


class Job(Base):
    """P4 background job (document processing / index build). status: queued → running → succeeded | failed |
    cancelled. ``cancel_requested`` is honoured between stages; a cancelled job never activates an index."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)          # ingest | index_rebuild
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    kb_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
