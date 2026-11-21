import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import FaqEditorDrawer from '@/components/FaqEditorDrawer.vue'
import { EVIDENCE_REASON, FAQ_STATUS, GAP_STATUS, GAP_TYPE, TASK_STATUS } from '@/api/ops'

const base = {
  id: 'f1', kb_id: 'kb', question: '出差住宿标准是多少', answer: '每晚六百元以内 [aaaaaa111111]。',
  citations: [{ chunk_id: 'aaaaaa111111', source: 'abcd1234_差旅.md', version_id: 'v1' }],
  evidence: [{ document_id: 'd1', version_id: 'v1', version_no: 1, title: '差旅.md', source: 'abcd1234_差旅.md' }],
  frequency: 3, distinct_users: 2, similarity_mean: 0.95, confidence: 0.86, answer_source: 'generated', embedding_model: 'BAAI/bge-m3',
  llm_model: 'qwen2.5:7b', llm_digest: 'sha256:x', index_generation: 'g1', params: {}, revision: 1, hit_count: 0,
  created_at: '2026-11-21T00:00:00', updated_at: '2026-11-21T00:00:00', reviewed_by: null, reviewed_at: null, review_note: null,
  published_at: null, members: [{ question: '出差住宿标准', similarity: 0.97, asked_at: '2026-11-21T00:00:00' }], history: [],
}
function mockFaq(over: Record<string, unknown>) {
  vi.spyOn(globalThis, 'fetch').mockImplementation(() => Promise.resolve(new Response(JSON.stringify({ ...base, ...over }), { status: 200 })))
}
async function mountDrawer() {
  const w = mount(FaqEditorDrawer, { props: { modelValue: true, faqId: 'f1' }, global: { plugins: [ElementPlus] }, attachTo: document.body })
  for (let i = 0; i < 4; i++) { await flushPromises(); await new Promise((r) => setTimeout(r, 0)) }
  return w
}
beforeEach(() => { setActivePinia(createPinia()); document.body.innerHTML = '' })
afterEach(() => vi.restoreAllMocks())

describe('FAQ editor drawer', () => {
  it('blocks publishing when the evidence is no longer valid', async () => {
    mockFaq({ status: 'candidate', evidence_valid: false, evidence_reason: 'document_updated' })
    const w = await mountDrawer()
    const btn = document.querySelector('[data-testid="faq-publish"]') as HTMLButtonElement
    expect(btn).not.toBeNull()
    expect(btn.disabled).toBe(true)
    expect(document.body.textContent).toContain('证据文档已更新')
    w.unmount()
  })
  it('published entries are read-only and can only be retired', async () => {
    mockFaq({ status: 'published', evidence_valid: true, evidence_reason: 'ok' })
    const w = await mountDrawer()
    expect(document.querySelector('[data-testid="faq-publish"]')).toBeNull()
    expect(document.querySelector('[data-testid="faq-retire"]')).not.toBeNull()
    expect((document.querySelector('[data-testid="faq-answer"]') as HTMLTextAreaElement).disabled).toBe(true)
    expect(document.body.textContent).not.toContain('user_id')
    w.unmount()
  })
})

describe('operation labels cover every backend state', () => {
  it('maps all enums', () => {
    for (const s of ['candidate', 'published', 'rejected', 'retired']) expect(FAQ_STATUS[s]).toBeTruthy()
    for (const s of ['open', 'in_progress', 'resolved', 'dismissed']) expect(GAP_STATUS[s]).toBeTruthy()
    for (const s of ['no_evidence', 'low_relevance', 'insufficient_evidence', 'stale_knowledge']) expect(GAP_TYPE[s]).toBeTruthy()
    for (const s of ['todo', 'in_progress', 'done', 'verified', 'reopened']) expect(TASK_STATUS[s]).toBeTruthy()
    for (const s of ['ok', 'document_updated', 'document_disabled', 'document_deleted', 'no_evidence']) expect(EVIDENCE_REASON[s]).toBeTruthy()
  })
})
