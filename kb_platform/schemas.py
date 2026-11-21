"""Pydantic request/response schemas. Every request model uses ``extra="forbid"``: a body that tries to smuggle
``user_id`` / ``username`` / ``roles`` / ``is_admin`` into an endpoint that does not define them is rejected (422)."""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

NAME = r"^[A-Za-z0-9_.-]{1,64}$"          # roles / departments / usernames: no wildcards, no spaces
DOC_KEY = r"^[^\\/*?<>|:\"]{1,255}$"       # = source_file in the index; no path separators or wildcards
Classification = Literal["public", "internal", "confidential", "restricted"]


class Req(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- auth ---------------------------------------------------------------------------------------------------------
class LoginIn(Req):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime


class MeOut(BaseModel):
    username: str
    display_name: str
    roles: List[str]
    departments: List[str]
    clearance: int
    is_admin: bool


class ChangePasswordIn(Req):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


# --- directory ----------------------------------------------------------------------------------------------------
class NamedIn(Req):
    name: str = Field(pattern=NAME)
    description: str = Field(default="", max_length=255)


class NamedOut(Out):
    id: int
    name: str
    description: str


class UserCreateIn(Req):
    username: str = Field(pattern=NAME)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=128)
    roles: List[str] = Field(default_factory=list, max_length=50)
    departments: List[str] = Field(default_factory=list, max_length=50)
    clearance: int = Field(default=0, ge=0, le=3)


class UserUpdateIn(Req):
    display_name: Optional[str] = Field(default=None, max_length=128)
    roles: Optional[List[str]] = Field(default=None, max_length=50)
    departments: Optional[List[str]] = Field(default=None, max_length=50)
    clearance: Optional[int] = Field(default=None, ge=0, le=3)
    active: Optional[bool] = None
    password: Optional[str] = Field(default=None, max_length=256)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    roles: List[str]
    departments: List[str]
    clearance: int
    active: bool
    is_admin: bool


# --- knowledge bases / documents ----------------------------------------------------------------------------------
class KnowledgeBaseIn(Req):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=1000)


class KnowledgeBaseOut(Out):
    id: str
    name: str
    description: str
    created_at: datetime


class DocumentIn(Req):
    doc_key: str = Field(pattern=DOC_KEY)
    title: str = Field(default="", max_length=255)
    classification: Classification = "internal"
    owner_department: Optional[str] = Field(default=None, pattern=NAME)


class DocumentUpdateIn(Req):
    title: Optional[str] = Field(default=None, max_length=255)
    classification: Optional[Classification] = None
    owner_department: Optional[str] = Field(default=None, pattern=NAME)
    status: Optional[Literal["active", "archived"]] = None


class DocumentOut(Out):
    id: str
    kb_id: str
    doc_key: str
    title: str
    classification: str
    owner_department: Optional[str]
    status: str
    created_at: datetime
    updated_at: datetime


class Subjects(Req):
    users: List[str] = Field(default_factory=list, max_length=200)
    roles: List[str] = Field(default_factory=list, max_length=200)
    departments: List[str] = Field(default_factory=list, max_length=200)


class AllowSubjects(Subjects):
    all_authenticated: bool = False


class DocumentAclIn(Req):
    allow: AllowSubjects = Field(default_factory=AllowSubjects)
    deny: Subjects = Field(default_factory=Subjects)


class DocumentAclOut(BaseModel):
    document_id: str
    allow: dict
    deny: dict
    acl_version: int


# --- chat ---------------------------------------------------------------------------------------------------------
class SessionIn(Req):
    kb_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="", max_length=255)


class Citation(BaseModel):
    chunk_id: str
    source: str


class MessageOut(BaseModel):
    id: int
    question: str
    answer: str
    citations: List[Citation]
    status: str
    created_at: datetime
    hidden: bool = False


class SessionOut(BaseModel):
    id: str
    kb_id: str
    title: str
    created_at: datetime


class SessionDetailOut(SessionOut):
    messages: List[MessageOut]


class AskIn(Req):
    question: str = Field(min_length=1, max_length=2000)


class AskOut(BaseModel):
    session_id: str
    message_id: int
    answer: str
    citations: List[Citation]
    status: str
    notice: Optional[str] = None


# --- audit / errors -----------------------------------------------------------------------------------------------
class AuditOut(Out):
    id: int
    ts: datetime
    request_id: str
    actor: str
    action: str
    resource: str
    outcome: str
    detail: dict


class ErrorOut(BaseModel):
    error: str
    message: str
    request_id: str
