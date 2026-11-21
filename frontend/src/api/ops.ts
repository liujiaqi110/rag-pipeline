// P5 knowledge-operations API (metrics, FAQ, gaps, tasks). Types mirror kb_platform/routers/ops.py.
import { http } from './http'
import type { Citation, JobOut } from './types'

export interface FaqEvidence { document_id: string; version_id: string; version_no: number | null; title: string; source: string }
export interface FaqItem {
  id: string; kb_id: string; status: 'candidate' | 'published' | 'rejected' | 'retired'; question: string; answer: string
  citations: (Citation & { document_id?: string })[]; evidence: FaqEvidence[]; evidence_valid: boolean; evidence_reason: string
  frequency: number; distinct_users: number; similarity_mean: number; confidence: number; answer_source: string
  embedding_model: string; llm_model: string; llm_digest: string; index_generation: string; params: Record<string, unknown>
  revision: number; hit_count: number; created_at: string; updated_at: string; reviewed_by: string | null
  reviewed_at: string | null; review_note: string | null; published_at: string | null
  members?: { question: string; similarity: number; asked_at: string }[]
  history?: { ts: string; actor: string; action: string; from: string | null; to: string | null; note: string | null }[]
}
export interface FaqList { items: FaqItem[]; hidden_without_evidence_access: number }
export interface GapItem {
  id: string; kb_id: string; gap_type: string; question: string; occurrences: number; distinct_users: number; repeated: boolean
  status: 'open' | 'in_progress' | 'resolved' | 'dismissed'; first_seen: string; last_seen: string; resolved_at: string | null
  verification: { at?: string; resolved?: boolean; results?: { user: string; status: string; answered: boolean; citations?: number; documents?: string[] }[] }
  note: string | null; tasks: { id: string; title: string; status: string }[]
  occurrence_list?: { question: string; gap_type: string; ts: string }[]
}
export interface TaskItem {
  id: string; gap_id: string; kb_id: string; title: string; description: string; assignee: string | null; status: string
  document_id: string | null; document_title: string | null; created_by: string; created_at: string; updated_at: string
  verified_at: string | null; verification: GapItem['verification']; gap_question: string | null; gap_status: string | null
  verify_job_id?: string | null
}
export interface Metrics {
  window: { days: number; start: string; end: string }
  definitions: Record<string, string>
  traffic: { pv: number | null; uv: number | null; note?: string }
  totals: { knowledge_bases: number; documents: number; chunks: number }
  questions: { window: number; today: number; this_week: number; by_source: Record<string, number>; by_outcome: Record<string, number> }
  llm: { calls: number; prompt_tokens: number; output_tokens: number; generation_p50_ms: number | null; generation_p95_ms: number | null
    end_to_end_p50_ms: number | null; end_to_end_p95_ms: number | null; models: Record<string, number> }
  faq: { hits: number; hit_rate: number; saved_llm_calls: number; answer_cache_hits: number; by_status: Record<string, number> }
  failures: { failed: number; generation_failure: number; infrastructure: number; permission_denied: number }
  gaps: { total: number; by_status: Record<string, number>; by_type: Record<string, number>; tasks: Record<string, number>; resolution_rate: number }
  daily: { date: string; questions: number; llm_calls: number; prompt_tokens: number; output_tokens: number; faq_hits: number; failed: number; p50_ms: number | null; p95_ms: number | null }[]
  top_questions: { question: string; count: number; faq_hits: number; unanswered: number }[]
  top_cited: { document_id: string | null; title: string; citations: number }[]
}

export const opsApi2 = {
  metrics: (days: number, kb_id?: string) => http.get<Metrics>('/ops/metrics', { days, kb_id }),
  pageview: (route: string) => http.post<void>('/ops/pageview', { route }),
}
export const faqApi = {
  list: (q: { kb_id?: string; status?: string }) => http.get<FaqList>('/faq', q),
  get: (id: string) => http.get<FaqItem>(`/faq/${id}`),
  mine: (b: { kb_id: string; min_frequency?: number; similarity_threshold?: number; days?: number }) => http.post<JobOut[]>('/faq/mine', b),
  edit: (id: string, b: { question?: string; answer?: string }) => http.patch<FaqItem>(`/faq/${id}`, b),
  publish: (id: string, note = '') => http.post<FaqItem>(`/faq/${id}/publish`, { note }),
  reject: (id: string, note = '') => http.post<FaqItem>(`/faq/${id}/reject`, { note }),
  retire: (id: string, note = '') => http.post<FaqItem>(`/faq/${id}/retire`, { note }),
}
export const gapApi = {
  list: (q: { kb_id?: string; gap_type?: string; status?: string; since?: string; until?: string; repeated?: boolean }) => http.get<GapItem[]>('/gaps', q),
  get: (id: string) => http.get<GapItem>(`/gaps/${id}`),
  update: (id: string, status: string, note = '') => http.patch<GapItem>(`/gaps/${id}`, { status, note }),
  createTask: (id: string, b: { title: string; description?: string; assignee?: string }) => http.post<TaskItem>(`/gaps/${id}/tasks`, b),
  verify: (id: string) => http.post<JobOut[]>(`/gaps/${id}/verify`),
}
export const taskApi = {
  list: (q: { kb_id?: string; status?: string } = {}) => http.get<TaskItem[]>('/tasks', q),
  update: (id: string, b: { status?: string; assignee?: string; document_id?: string }) => http.patch<TaskItem>(`/tasks/${id}`, b),
}

export const GAP_TYPE: Record<string, string> = {
  no_evidence: '无可用证据', low_relevance: '检索相关性不足', insufficient_evidence: '证据不足以回答', stale_knowledge: '知识过期',
}
export const GAP_STATUS: Record<string, { label: string; type: 'danger' | 'warning' | 'success' | 'info' }> = {
  open: { label: '待处理', type: 'danger' }, in_progress: { label: '处理中', type: 'warning' },
  resolved: { label: '已解决', type: 'success' }, dismissed: { label: '已忽略', type: 'info' },
}
export const FAQ_STATUS: Record<string, { label: string; type: 'warning' | 'success' | 'danger' | 'info' }> = {
  candidate: { label: '待审核', type: 'warning' }, published: { label: '已发布', type: 'success' },
  rejected: { label: '已驳回', type: 'danger' }, retired: { label: '已下线', type: 'info' },
}
export const TASK_STATUS: Record<string, { label: string; type: 'info' | 'warning' | 'success' | 'danger' | 'primary' }> = {
  todo: { label: '待办', type: 'info' }, in_progress: { label: '进行中', type: 'primary' }, done: { label: '已完成，验证中', type: 'warning' },
  verified: { label: '已验证解决', type: 'success' }, reopened: { label: '验证未通过', type: 'danger' },
}
export const EVIDENCE_REASON: Record<string, string> = {
  ok: '证据有效', document_updated: '证据文档已更新', document_disabled: '证据文档已停用', document_deleted: '证据文档已删除', no_evidence: '无证据',
}
