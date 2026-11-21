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
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | archived
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
