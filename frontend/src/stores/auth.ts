import { defineStore } from 'pinia'
import { authApi } from '@/api/endpoints'
import { onUnauthorized, tokenStore } from '@/api/http'
import type { Me } from '@/api/types'

let expiryTimer: ReturnType<typeof setTimeout> | undefined

export const useAuthStore = defineStore('auth', {
  state: () => ({
    me: null as Me | null,
    restoring: false,
    expiredNotice: false,   // shown on the login page after an expiry / revocation
  }),
  getters: {
    loggedIn: (s) => !!s.me,
    isAdmin: (s) => !!s.me?.is_admin,
    /** UI hint only — the backend authorises every operation again. */
    canManage: (s) => (kbId?: string) => !!s.me && (s.me.is_admin || (!!kbId && s.me.managed_kb_ids.includes(kbId))),
    managesAny: (s) => !!s.me && (s.me.is_admin || s.me.managed_kb_ids.length > 0),
  },
  actions: {
    scheduleExpiry() {
      clearTimeout(expiryTimer)
      const exp = tokenStore.expiresAt()
      if (exp) expiryTimer = setTimeout(() => this.expire(), Math.max(0, exp - Date.now()))
    },
    async login(username: string, password: string) {
      const t = await authApi.login(username, password)
      tokenStore.set(t.access_token, t.expires_at)
      this.expiredNotice = false
      this.me = await authApi.me()
      this.scheduleExpiry()
    },
    /** Restore the session after a page reload (token still in this tab and accepted by the server). */
    async restore(): Promise<boolean> {
      if (this.me) return true
      if (!tokenStore.get()) return false
      this.restoring = true
      try {
        this.me = await authApi.me()
        this.scheduleExpiry()
        return true
      } catch {
        tokenStore.clear()
        return false
      } finally {
        this.restoring = false
      }
    },
    async refreshMe() { if (tokenStore.get()) this.me = await authApi.me() },
    async logout() {
      try { if (tokenStore.get()) await authApi.logout() } catch { /* already invalid */ }
      this.clearLocal()
    },
    expire() {
      this.clearLocal()
      this.expiredNotice = true
      window.dispatchEvent(new CustomEvent('kb:session-ended'))
    },
    clearLocal() {
      clearTimeout(expiryTimer)
      tokenStore.clear()
      this.me = null
    },
  },
})

export function installUnauthorizedHandler() {
  onUnauthorized(() => {
    const s = useAuthStore()
    if (s.me) s.expire()
  })
}
