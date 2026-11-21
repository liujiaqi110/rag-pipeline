// Fetch wrapper for /api/v1. The token lives in sessionStorage (per tab) and is sent as a Bearer header.
// Any 401 clears the local session and notifies the auth store (expired / revoked / logged out elsewhere).
// NOTE: hiding a button is only a UI hint — every request is authorised again by the backend.
import type { ApiErrorBody } from './types'

export const API_BASE = '/api/v1'
const TOKEN_KEY = 'kb.token'
const EXP_KEY = 'kb.expires_at'

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId = '') {
    super(message)
  }
}

type Listener = () => void
const unauthorizedListeners: Listener[] = []
export function onUnauthorized(fn: Listener) { unauthorizedListeners.push(fn) }

export const tokenStore = {
  get(): string | null {
    const t = sessionStorage.getItem(TOKEN_KEY)
    const exp = sessionStorage.getItem(EXP_KEY)
    if (t && exp && Date.parse(exp) <= Date.now()) { this.clear(); return null }
    return t
  },
  expiresAt(): number | null { const e = sessionStorage.getItem(EXP_KEY); return e ? Date.parse(e) : null },
  set(token: string, expiresAt: string) { sessionStorage.setItem(TOKEN_KEY, token); sessionStorage.setItem(EXP_KEY, expiresAt) },
  clear() { sessionStorage.removeItem(TOKEN_KEY); sessionStorage.removeItem(EXP_KEY) },
}

export function authHeaders(): Record<string, string> {
  const t = tokenStore.get()
  return t ? { Authorization: `Bearer ${t}` } : {}
}

export async function toApiError(res: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> = {}
  try { body = await res.json() } catch { /* non-JSON */ }
  if (res.status === 401) {
    tokenStore.clear()
    unauthorizedListeners.forEach((f) => f())
  }
  const fallback = res.status === 403 ? '权限不足' : res.status === 404 ? '资源不存在或无权访问' : `请求失败（${res.status}）`
  return new ApiError(res.status, body.error || 'http_error', body.message || fallback, body.request_id || '')
}

export async function request<T>(method: string, path: string, body?: unknown, query?: Record<string, unknown>): Promise<T> {
  const qs = query
    ? '?' + Object.entries(query).filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`).join('&')
    : ''
  const init: RequestInit = { method, headers: { ...authHeaders() } }
  if (body instanceof FormData) init.body = body
  else if (body !== undefined) {
    init.body = JSON.stringify(body)
    ;(init.headers as Record<string, string>)['Content-Type'] = 'application/json'
  }
  const res = await fetch(API_BASE + path + (qs === '?' ? '' : qs), init)
  if (!res.ok) throw await toApiError(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

export const http = {
  get: <T>(p: string, q?: Record<string, unknown>) => request<T>('GET', p, undefined, q),
  post: <T>(p: string, b?: unknown, q?: Record<string, unknown>) => request<T>('POST', p, b, q),
  put: <T>(p: string, b?: unknown) => request<T>('PUT', p, b),
  patch: <T>(p: string, b?: unknown) => request<T>('PATCH', p, b),
  del: <T>(p: string) => request<T>('DELETE', p),
}

/** Multipart upload with progress (XMLHttpRequest: fetch has no upload progress). */
export function uploadWithProgress<T>(path: string, form: FormData, onProgress: (pct: number) => void,
  query?: Record<string, string>): Promise<{ status: number; body: T }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const qs = query ? '?' + new URLSearchParams(query).toString() : ''
    xhr.open('POST', API_BASE + path + qs)
    const t = tokenStore.get()
    if (t) xhr.setRequestHeader('Authorization', `Bearer ${t}`)
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)) }
    xhr.onload = async () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ status: xhr.status, body: JSON.parse(xhr.responseText) as T })
      } else {
        reject(await toApiError(new Response(xhr.responseText, { status: xhr.status })))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, 'network_error', '网络错误，上传失败'))
    xhr.send(form)
  })
}
