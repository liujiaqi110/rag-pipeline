export const VERSION_STATUS: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' | 'primary' }> = {
  uploaded: { label: '已上传', type: 'info' },
  processing: { label: '解析中', type: 'primary' },
  chunked: { label: '待索引', type: 'warning' },
  indexed: { label: '已索引', type: 'success' },
  failed: { label: '解析失败', type: 'danger' },
  index_failed: { label: '索引失败', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
  superseded: { label: '已被新版本替换', type: 'info' },
  purged: { label: '已清除', type: 'info' },
}
export const DOC_STATUS: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' }> = {
  active: { label: '启用', type: 'success' }, disabled: { label: '已停用', type: 'warning' },
  archived: { label: '已归档', type: 'info' }, deleted: { label: '已删除', type: 'danger' },
}
export const JOB_STATUS: Record<string, { label: string; type: 'success' | 'warning' | 'danger' | 'info' | 'primary' }> = {
  queued: { label: '排队中', type: 'info' }, running: { label: '运行中', type: 'primary' },
  succeeded: { label: '成功', type: 'success' }, failed: { label: '失败', type: 'danger' }, cancelled: { label: '已取消', type: 'info' },
}
export const STAGE_LABEL: Record<string, string> = {
  queued: '排队', starting: '准备', parsing: '解析与切片', chunked: '切片完成', indexing: '构建索引', done: '完成', failed: '失败', cancelled: '已取消',
}
export const CLASSIFICATION: Record<string, string> = { public: '公开', internal: '内部', confidential: '机密', restricted: '绝密' }

export function fmtTime(s?: string | null): string {
  if (!s) return '—'
  const d = new Date(s.endsWith('Z') || /[+-]\d\d:\d\d$/.test(s) ? s : s + 'Z')
  return d.toLocaleString('zh-CN', { hour12: false })
}
export function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}
export const ALLOWED_EXT = ['.md', '.markdown', '.txt', '.pdf', '.docx']
export function allowedFile(name: string): boolean {
  const n = name.toLowerCase()
  return ALLOWED_EXT.some((e) => n.endsWith(e))
}
