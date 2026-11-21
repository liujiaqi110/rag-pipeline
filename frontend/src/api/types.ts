// Types mirroring the FastAPI schemas (docs/openapi.json).
export interface Me {
  username: string; display_name: string; roles: string[]; departments: string[]
  clearance: number; is_admin: boolean; managed_kb_ids: string[]
}
export interface TokenOut { access_token: string; token_type: 'bearer'; expires_at: string; token_format: 'jwt' | 'opaque' }
export interface ApiErrorBody { error: string; message: string; request_id: string }

export interface UserOut { id: number; username: string; display_name: string; roles: string[]; departments: string[]; clearance: number; active: boolean; is_admin: boolean }
export interface NamedOut { id: number; name: string; description: string }
export interface DepartmentOut { id: number; name: string; description: string; parent: string | null }

export interface KnowledgeBase { id: string; name: string; description: string; created_at: string }
export type DocStatus = 'active' | 'disabled' | 'archived' | 'deleted'
export interface DocumentOut {
  id: string; kb_id: string; doc_key: string; title: string; classification: string; owner_department: string | null
  status: DocStatus; created_at: string; updated_at: string; source: string; original_filename: string | null; current_version_id: string | null
}
export interface VersionOut {
  id: string; document_id: string; version_no: number; original_filename: string; file_format: string; size_bytes: number
  sha256: string; status: string; error_code: string | null; error_message: string | null; chunk_count: number
  page_count: number | null; created_by: string; created_at: string; indexed_at: string | null; generation_id: string | null
}
export interface IndexInfo { status: 'active' | 'failed' | 'skipped' | 'pending'; job_id?: string | null; generation_id?: string | null; dense?: boolean | null; dense_error?: string | null; error_code?: string | null; message?: string | null }
export interface UploadOut { outcome: string; document: DocumentOut; version: VersionOut | null; index: IndexInfo; process_job_id: string | null; index_job_id: string | null }
export interface BatchItem { filename: string; ok: boolean; outcome?: string | null; document_id?: string | null; version_id?: string | null; status?: string | null; error_code?: string | null; message?: string | null; job_id?: string | null }
export interface BatchOut { results: BatchItem[]; succeeded: number; failed: number; index: IndexInfo }
export interface DocumentStatusOut { document_id: string; status: string; source: string; current_version: VersionOut | null; latest_version: VersionOut | null; retrievable: boolean; active_generation_id: string | null; dense: boolean | null }
export interface ChunkOut { chunk_id: string; ordinal: number; is_parent: boolean; parent_id: string | null; section_heading: string | null; page: number | null; paragraph: number | null; char_count: number; text: string | null }
export interface ChunkListOut { document_id: string; version_id: string; version_no: number; total: number; text_visible: boolean; chunks: ChunkOut[] }
export interface Subjects { users: string[]; roles: string[]; departments: string[] }
export interface DocumentAcl { document_id: string; allow: Subjects & { all_authenticated: boolean }; deny: Subjects; acl_version: number }
export interface JobOut {
  id: string; kind: 'process' | 'index'; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'; stage: string; progress: number
  kb_id: string | null; document_id: string | null; version_id: string | null; created_by: string; cancel_requested: boolean
  attempts: number; error_code: string | null; error_message: string | null; result: Record<string, unknown>
  created_at: string; started_at: string | null; finished_at: string | null
}
export interface Citation { chunk_id: string; source: string; version_id?: string | null }
export interface SessionOut { id: string; kb_id: string; title: string; created_at: string }
export interface MessageOut { id: number; question: string; answer: string; citations: Citation[]; status: string; created_at: string; hidden: boolean }
export interface SessionDetail extends SessionOut { messages: MessageOut[] }
export interface AskOut { session_id: string; message_id: number; answer: string; citations: Citation[]; status: string; notice?: string | null }
export interface CitationDetail { chunk_id: string; document_id: string; document_title: string; source: string; version_no: number; page: number | null; paragraph: number | null; section_heading: string | null; text: string }
export interface AuditOut { id: number; ts: string; request_id: string; actor: string; action: string; resource: string; outcome: string; detail: Record<string, unknown> }
export interface GenerationOut { id: string; status: string; created_at: string; activated_at: string | null; chunk_count: number; document_count: number; dense: boolean; dense_model: string | null; dense_error: string | null; error_code: string | null; built_by: string }
export interface IndexStatusOut { retrieval_mode: string; dense_mode: string; embedder: Record<string, unknown>; active: GenerationOut | null; recent: GenerationOut[]; pending_versions: number; failed_versions: number }
export interface SystemStatus { version: string; database: Record<string, unknown>; jobs: Record<string, number>; index: Record<string, unknown>; embedder: Record<string, unknown>; llm: Record<string, unknown>; settings: Record<string, string> }
