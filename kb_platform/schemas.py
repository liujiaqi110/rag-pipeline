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
    token_format: Literal["jwt", "opaque"] = "opaque"


class MeOut(BaseModel):
    username: str
    display_name: str
    roles: List[str]
    departments: List[str]
    clearance: int
    is_admin: bool
    managed_kb_ids: List[str] = []   # UI hint only: every operation is re-authorised by the backend


class DepartmentIn(Req):
    name: str = Field(pattern=NAME)
    description: str = Field(default="", max_length=255)
    parent: Optional[str] = Field(default=None, pattern=NAME)


class DepartmentUpdateIn(Req):
    description: Optional[str] = Field(default=None, max_length=255)
    parent: Optional[str] = Field(default=None, pattern=NAME)
    clear_parent: bool = False


class DepartmentOut(BaseModel):
    id: int
    name: str
    description: str
    parent: Optional[str] = None


class CitationDetailOut(BaseModel):
    chunk_id: str
    document_id: str
    document_title: str
    source: str
    version_no: int
    page: Optional[int] = None
    paragraph: Optional[int] = None
    section_heading: Optional[str] = None
    text: str


class SystemStatusOut(BaseModel):
    version: str
    database: dict
    jobs: dict
    index: dict
    embedder: dict
    llm: dict
    settings: dict


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
    status: Optional[Literal["active", "disabled", "archived"]] = None  # deletion: DELETE /documents/{id}


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
    source: str = "metadata"
    original_filename: Optional[str] = None
    current_version_id: Optional[str] = None


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


class ManagerIn(Req):
    username: str = Field(pattern=NAME)


# --- P3 ingestion -------------------------------------------------------------------------------------------------
class VersionOut(Out):
    id: str
    document_id: str
    version_no: int
    original_filename: str
    file_format: str
    size_bytes: int
    sha256: str
    status: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    chunk_count: int
    page_count: Optional[int] = None
    created_by: str
    created_at: datetime
    indexed_at: Optional[datetime] = None
    generation_id: Optional[str] = None


class IndexInfo(BaseModel):
    status: Literal["active", "failed", "skipped", "pending"]
    job_id: Optional[str] = None
    generation_id: Optional[str] = None
    dense: Optional[bool] = None
    dense_error: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None


class UploadOut(BaseModel):
    outcome: Literal["created", "new_version", "unchanged", "skipped"]
    document: DocumentOut
    version: Optional[VersionOut] = None
    index: IndexInfo
    process_job_id: Optional[str] = None
    index_job_id: Optional[str] = None


class BatchItem(BaseModel):
    filename: str
    ok: bool
    outcome: Optional[str] = None
    document_id: Optional[str] = None
    version_id: Optional[str] = None
    status: Optional[str] = None
    error_code: Optional[str] = None
    message: Optional[str] = None
    job_id: Optional[str] = None


class BatchOut(BaseModel):
    results: List[BatchItem]
    succeeded: int
    failed: int
    index: IndexInfo


class DocumentStatusOut(BaseModel):
    document_id: str
    status: str
    source: str
    current_version: Optional[VersionOut] = None
    latest_version: Optional[VersionOut] = None
    retrievable: bool
    active_generation_id: Optional[str] = None
    dense: Optional[bool] = None


class ChunkOut(BaseModel):
    chunk_id: str
    ordinal: int
    is_parent: bool
    parent_id: Optional[str] = None
    section_heading: Optional[str] = None
    page: Optional[int] = None
    paragraph: Optional[int] = None
    char_count: int
    text: Optional[str] = None   # only for users with READ access; managers without a grant see metadata only


class ChunkListOut(BaseModel):
    document_id: str
    version_id: str
    version_no: int
    total: int
    text_visible: bool
    chunks: List[ChunkOut]


class JobOut(Out):
    id: str
    kind: str
    status: str
    stage: str
    progress: int
    kb_id: Optional[str] = None
    document_id: Optional[str] = None
    version_id: Optional[str] = None
    created_by: str
    cancel_requested: bool
    attempts: int
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    result: dict
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


class GenerationOut(Out):
    id: str
    status: str
    created_at: datetime
    activated_at: Optional[datetime] = None
    chunk_count: int
    document_count: int
    dense: bool
    dense_model: Optional[str] = None
    dense_error: Optional[str] = None
    error_code: Optional[str] = None
    built_by: str


class IndexStatusOut(BaseModel):
    retrieval_mode: str
    dense_mode: str
    embedder: dict
    active: Optional[GenerationOut] = None
    recent: List[GenerationOut]
    pending_versions: int
    failed_versions: int


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
