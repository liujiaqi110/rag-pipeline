// Answer rendering: Markdown (no raw HTML) → sanitised HTML, code highlighting, citation markers.
import MarkdownIt from 'markdown-it'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import type { Citation } from '@/api/types'

const md: MarkdownIt = new MarkdownIt({
  html: false,           // model output is untrusted: never render raw HTML
  linkify: false,
  breaks: true,
  highlight(code: string, lang: string): string {
    try {
      const out = lang && hljs.getLanguage(lang) ? hljs.highlight(code, { language: lang }).value : hljs.highlightAuto(code).value
      return `<pre class="hljs"><code>${out}</code></pre>`
    } catch {
      return ''
    }
  },
})

const HEX12 = /\[?\b([0-9a-f]{12})\b\]?/g

/** Strip the model's trailing "引用：…" line and turn [chunk_id] into numbered markers for VALIDATED citations only.
 *  Any other 12-hex token is removed (the backend already blocked/redacted it; this is defence in depth). */
export function prepareAnswer(answer: string, citations: Citation[]): string {
  const order = new Map(citations.map((c, i) => [c.chunk_id, i + 1]))
  let text = answer.replace(/\n*引用[：:][^\n]*\s*$/u, '').trimEnd()
  text = text.replace(HEX12, (_m, id: string) => (order.has(id) ? ` @@CITE${order.get(id)}@@ ` : ''))
  return text
}

export function renderAnswer(answer: string, citations: Citation[] = []): string {
  const html = md.render(prepareAnswer(answer, citations))
    .replace(/\s?@@CITE(\d+)@@\s?/g, (_m, n) => `<sup class="kb-cite" data-cite="${n}">${n}</sup>`)
  return DOMPurify.sanitize(html, { ALLOWED_ATTR: ['class', 'data-cite'], FORBID_TAGS: ['style', 'img', 'iframe', 'form', 'input'] })
}

export function renderMarkdown(text: string): string {
  return DOMPurify.sanitize(md.render(text), { ALLOWED_ATTR: ['class'] })
}
