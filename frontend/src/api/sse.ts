// POST + text/event-stream reader (EventSource cannot send a body or an Authorization header).
import { API_BASE, authHeaders, toApiError } from './http'

export interface SseEvent { event: string; data: any }

/** Parse a chunk of SSE text; returns complete events and the unconsumed remainder. */
export function parseSse(buffer: string): { events: SseEvent[]; rest: string } {
  const events: SseEvent[] = []
  const blocks = buffer.split(/\r?\n\r?\n/)
  const rest = blocks.pop() ?? ''
  for (const block of blocks) {
    let event = 'message'
    const data: string[] = []
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    if (!data.length) continue
    try { events.push({ event, data: JSON.parse(data.join('\n')) }) } catch { events.push({ event, data: data.join('\n') }) }
  }
  return { events, rest }
}

export async function streamPost(path: string, body: unknown, onEvent: (e: SseEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await fetch(API_BASE + path, {
    method: 'POST', signal,
    headers: { ...authHeaders(), 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
  })
  if (!res.ok || !res.body) throw await toApiError(res)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const { events, rest } = parseSse(buf)
    buf = rest
    events.forEach(onEvent)
  }
  if (buf.trim()) parseSse(buf + '\n\n').events.forEach(onEvent)
}
