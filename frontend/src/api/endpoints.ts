// One function per backend endpoint used by the workbench (all under /api/v1).
import { http, uploadWithProgress } from './http'
import type * as T from './types'

export const authApi = {
  login: (username: string, password: string) => http.post<T.TokenOut>('/auth/login', { username, password }),
  logout: () => http.post<void>('/auth/logout'),
  me: () => http.get<T.Me>('/auth/me'),
  changePassword: (current_password: string, new_password: string) => http.post<void>('/auth/password', { current_password, new_password }),
}

export const adminApi = {
  users: () => http.get<T.UserOut[]>('/admin/users', { limit: 500 }),
  createUser: (b: { username: string; password: string; display_name?: string; roles: string[]; departments: string[]; clearance: number }) => http.post<T.UserOut>('/admin/users', b),
  updateUser: (username: string, b: Partial<{ display_name: string; roles: string[]; departments: string[]; clearance: number; active: boolean; password: string }>) =>
    http.patch<T.UserOut>(`/admin/users/${encodeURIComponent(username)}`, b),
  roles: () => http.get<T.NamedOut[]>('/admin/roles'),
  createRole: (name: string, description: string) => http.post<T.NamedOut>('/admin/roles', { name, description }),
  departments: () => http.get<T.DepartmentOut[]>('/admin/departments'),
  createDepartment: (name: string, description: string, parent: string | null) => http.post<T.DepartmentOut>('/admin/departments', { name, description, parent: parent || undefined }),
  updateDepartment: (name: string, b: { description?: string; parent?: string; clear_parent?: boolean }) => http.patch<T.DepartmentOut>(`/admin/departments/${encodeURIComponent(name)}`, b),
}

export const kbApi = {
  list: () => http.get<T.KnowledgeBase[]>('/knowledge-bases'),
  get: (id: string) => http.get<T.KnowledgeBase>(`/knowledge-bases/${id}`),
  create: (name: string, description: string) => http.post<T.KnowledgeBase>('/knowledge-bases', { name, description }),
  documents: (id: string) => http.get<T.DocumentOut[]>(`/knowledge-bases/${id}/documents`),
  managers: (id: string) => http.get<string[]>(`/knowledge-bases/${id}/managers`),
  addManager: (id: string, username: string) => http.post<void>(`/knowledge-bases/${id}/managers`, { username }),
  removeManager: (id: string, username: string) => http.del<void>(`/knowledge-bases/${id}/managers/${encodeURIComponent(username)}`),
  citation: (kbId: string, chunkId: string) => http.get<T.CitationDetail>(`/knowledge-bases/${kbId}/chunks/${chunkId}`),
  batchUpload: (kbId: string, files: File[], onProgress: (p: number) => void, onDuplicate = 'reject') => {
    const f = new FormData()
    files.forEach((x) => f.append('files', x, x.name))
    return uploadWithProgress<T.BatchOut>(`/knowledge-bases/${kbId}/documents/batch-upload`, f, onProgress, { on_duplicate: onDuplicate })
  },
}

export const docApi = {
  status: (id: string) => http.get<T.DocumentStatusOut>(`/documents/${id}/status`),
  versions: (id: string) => http.get<T.VersionOut[]>(`/documents/${id}/versions`),
  newVersion: (id: string, file: File, onProgress: (p: number) => void) => {
    const f = new FormData()
    f.append('file', file, file.name)
    return uploadWithProgress<T.UploadOut>(`/documents/${id}/versions`, f, onProgress)
  },
  chunks: (id: string, q: { version_id?: string; limit?: number; offset?: number } = {}) => http.get<T.ChunkListOut>(`/documents/${id}/chunks`, q),
  disable: (id: string) => http.post<T.DocumentStatusOut>(`/documents/${id}/disable`),
  enable: (id: string) => http.post<T.DocumentStatusOut>(`/documents/${id}/enable`),
  remove: (id: string) => http.del<T.IndexInfo>(`/documents/${id}`),
  acl: (id: string) => http.get<T.DocumentAcl>(`/documents/${id}/acl`),
  putAcl: (id: string, allow: T.Subjects & { all_authenticated: boolean }, deny: T.Subjects) => http.put<T.DocumentAcl>(`/documents/${id}/acl`, { allow, deny }),
}

export const jobApi = {
  list: (q: { kb_id?: string; status?: string; document_id?: string; limit?: number } = {}) => http.get<T.JobOut[]>('/jobs', q),
  get: (id: string) => http.get<T.JobOut>(`/jobs/${id}`),
  cancel: (id: string) => http.post<T.JobOut>(`/jobs/${id}/cancel`),
  retry: (id: string) => http.post<T.JobOut[]>(`/jobs/${id}/retry`),
}

export const chatApi = {
  sessions: () => http.get<T.SessionOut[]>('/sessions'),
  create: (kb_id: string, title: string) => http.post<T.SessionOut>('/sessions', { kb_id, title }),
  get: (id: string) => http.get<T.SessionDetail>(`/sessions/${id}`),
  remove: (id: string) => http.del<void>(`/sessions/${id}`),
}

export const opsApi = {
  health: () => http.get<{ status: string; version: string }>('/health'),
  system: () => http.get<T.SystemStatus>('/system/status'),
  index: () => http.get<T.IndexStatusOut>('/index/status'),
  rebuild: () => http.post<T.IndexInfo>('/index/rebuild'),
  audit: (q: { action?: string; actor?: string; limit?: number; offset?: number }) => http.get<T.AuditOut[]>('/audit', q),
}
