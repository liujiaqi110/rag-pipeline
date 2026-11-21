import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import ElementPlus from 'element-plus'
import AnswerView from '@/components/AnswerView.vue'
import LoginView from '@/views/LoginView.vue'
import { useAuthStore } from '@/stores/auth'
import { tokenStore } from '@/api/http'

function json(status: number, body: unknown) {
  return Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }))
}
const ME = { username: 'eng_alice', display_name: 'Alice', roles: ['employee'], departments: ['engineering'], clearance: 1, is_admin: false, managed_kb_ids: [] }

beforeEach(() => { setActivePinia(createPinia()); sessionStorage.clear() })
afterEach(() => { vi.restoreAllMocks() })

describe('AnswerView', () => {
  const cites = [{ chunk_id: 'aaaaaa111111', source: '1a2b3c4d_发布手册.md' }]
  it('shows citation cards with the file name and emits open', async () => {
    const w = mount(AnswerView, { props: { answer: '先测试 [aaaaaa111111]。', citations: cites }, global: { plugins: [ElementPlus] } })
    const cards = w.findAll('[data-testid="citation-card"]')
    expect(cards).toHaveLength(1)
    expect(cards[0].text()).toContain('发布手册.md')
    expect(cards[0].text()).not.toContain('1a2b3c4d_')
    await cards[0].trigger('click')
    expect(w.emitted('open')![0][0]).toEqual(cites[0])
    await w.find('sup.kb-cite').trigger('click')
    expect(w.emitted('open')).toHaveLength(2)
  })
  it('does not render citation markers while streaming', () => {
    const w = mount(AnswerView, { props: { answer: '流式 [aaaaaa111111]', streaming: true }, global: { plugins: [ElementPlus] } })
    expect(w.find('sup.kb-cite').exists()).toBe(false)
    expect(w.find('[data-testid="citations"]').exists()).toBe(false)
  })
})

describe('auth store', () => {
  it('logs in, stores the token per tab and clears it on logout', async () => {
    const f = vi.spyOn(globalThis, 'fetch')
      .mockImplementationOnce(() => json(200, { access_token: 'h.p.s', token_type: 'bearer', expires_at: new Date(Date.now() + 3600e3).toISOString(), token_format: 'jwt' }))
      .mockImplementationOnce(() => json(200, ME))
      .mockImplementationOnce(() => Promise.resolve(new Response(null, { status: 204 })))
    const s = useAuthStore()
    await s.login('eng_alice', 'pw')
    expect(s.me?.username).toBe('eng_alice')
    expect(tokenStore.get()).toBe('h.p.s')
    expect((f.mock.calls[1][1] as RequestInit).headers).toMatchObject({ Authorization: 'Bearer h.p.s' })
    await s.logout()
    expect(tokenStore.get()).toBeNull()
    expect(s.me).toBeNull()
  })
  it('expires the session on any 401 and on token expiry time', async () => {
    tokenStore.set('t', new Date(Date.now() + 3600e3).toISOString())
    vi.spyOn(globalThis, 'fetch').mockImplementationOnce(() => json(200, ME)).mockImplementationOnce(() => json(401, { error: 'unauthenticated', message: '登录已失效', request_id: '' }))
    const { installUnauthorizedHandler } = await import('@/stores/auth')
    installUnauthorizedHandler()
    const s = useAuthStore()
    expect(await s.restore()).toBe(true)
    const { http } = await import('@/api/http')
    await expect(http.get('/sessions')).rejects.toMatchObject({ status: 401 })
    expect(s.me).toBeNull()
    expect(s.expiredNotice).toBe(true)
    tokenStore.set('old', new Date(Date.now() - 1000).toISOString())
    expect(tokenStore.get()).toBeNull()                                    // expired tokens are never sent
  })
  it('canManage is only a UI hint derived from /auth/me', () => {
    const s = useAuthStore()
    s.me = { ...ME, managed_kb_ids: ['kb1'] }
    expect(s.canManage('kb1')).toBe(true)
    expect(s.canManage('kb2')).toBe(false)
  })
})

describe('LoginView', () => {
  it('shows the server error message on failure', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(() => json(401, { error: 'invalid_credentials', message: '用户名或密码错误', request_id: 'r' }))
    const { createRouter, createMemoryHistory } = await import('vue-router')
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/login', component: LoginView }, { path: '/chat', component: { template: '<div />' } }] })
    router.push('/login')
    await router.isReady()
    const w = mount(LoginView, { global: { plugins: [ElementPlus, router] }, attachTo: document.body })
    await w.find('[data-testid="login-username"]').setValue('x')
    await w.find('[data-testid="login-password"]').setValue('y')
    await w.find('form').trigger('submit')
    for (let i = 0; i < 5; i++) { await flushPromises(); await new Promise((r) => setTimeout(r, 0)) }
    expect(w.find('[data-testid="login-error"]').text()).toContain('用户名或密码错误')
    expect(router.currentRoute.value.path).toBe('/login')
    w.unmount()
  })
})
