<template>
  <div class="chat">
    <aside class="sessions">
      <div class="sessions-head">
        <el-button type="primary" :icon="Plus" class="full" @click="newOpen = true" data-testid="chat-new">新建对话</el-button>
      </div>
      <el-scrollbar>
        <div v-for="s in sessions" :key="s.id" class="session" :class="{ active: s.id === sessionId }" data-testid="session-item" @click="open(s.id)">
          <div class="s-title">{{ s.title || '未命名对话' }}</div>
          <div class="kb-muted s-meta">{{ kbName(s.kb_id) }} · {{ fmtTime(s.created_at) }}</div>
          <el-button link :icon="Delete" class="s-del" @click.stop="remove(s.id)" />
        </div>
        <div v-if="!sessions.length" class="kb-empty">还没有对话</div>
      </el-scrollbar>
    </aside>

    <section class="conv">
      <template v-if="detail">
        <div class="conv-head">
          <div><b>{{ detail.title || '未命名对话' }}</b><span class="kb-muted"> · {{ kbName(detail.kb_id) }}</span></div>
          <span class="kb-muted">每个问题都会按你当前的权限重新检索；回答只引用你有权阅读的资料</span>
        </div>
        <el-scrollbar ref="scroller" class="msgs">
          <div class="msgs-inner">
            <div v-for="m in messages" :key="m.key" class="turn" data-testid="turn">
              <div class="q"><div class="bubble-q">{{ m.question }}</div></div>
              <div class="a">
                <el-avatar :size="30" class="bot">AI</el-avatar>
                <div class="bubble-a">
                  <el-alert v-if="m.hidden" type="info" :closable="false" show-icon title="该回答生成后权限已变更，内容已隐藏" />
                  <el-alert v-else-if="m.revoked" type="warning" :closable="false" show-icon title="权限或登录状态已变更，回答已停止并隐藏" data-testid="answer-revoked" />
                  <el-alert v-else-if="m.error" type="error" :closable="false" show-icon :title="m.error" data-testid="answer-error" />
                  <template v-else>
                    <div v-if="!m.answer && m.streaming" class="kb-muted thinking"><el-icon class="spin"><Loading /></el-icon> 正在检索有权访问的资料…</div>
                    <AnswerView v-else :answer="m.answer" :citations="m.citations" :streaming="m.streaming" @open="openSource" />
                    <el-alert v-if="statusNotice(m)" :type="statusNotice(m)!.type" :closable="false" show-icon :title="statusNotice(m)!.text" class="notice" data-testid="answer-notice" />
                  </template>
                </div>
              </div>
            </div>
            <div v-if="!messages.length" class="kb-empty">向知识库提问吧。示例：“发布前需要做哪些检查？”</div>
          </div>
        </el-scrollbar>
        <div class="composer">
          <el-input v-model="question" type="textarea" :autosize="{ minRows: 2, maxRows: 6 }" maxlength="2000" show-word-limit
            placeholder="输入问题，Enter 发送，Shift+Enter 换行" :disabled="sending" data-testid="chat-input" @keydown.enter.exact.prevent="send" />
          <div class="composer-actions">
            <el-button v-if="sending" @click="stop">停止</el-button>
            <el-button type="primary" :icon="Promotion" :loading="sending" :disabled="!question.trim()" @click="send" data-testid="chat-send">发送</el-button>
          </div>
        </div>
      </template>
      <div v-else class="kb-empty welcome">
        <el-icon class="welcome-icon"><ChatDotRound /></el-icon>
        <h2>AI 问答工作台</h2>
        <p class="kb-muted">选择左侧对话，或新建一个对话开始提问。</p>
      </div>
    </section>

    <el-dialog v-model="newOpen" title="新建对话" width="440px">
      <el-form label-position="top">
        <el-form-item label="知识库">
          <el-select v-model="newKb" placeholder="选择你可访问的知识库" class="full" data-testid="chat-kb-select">
            <el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" />
          </el-select>
          <div v-if="!kbs.length" class="kb-muted">你当前没有任何可访问的知识库</div>
        </el-form-item>
        <el-form-item label="标题（可选）"><el-input v-model="newTitle" maxlength="255" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="newOpen = false">取消</el-button><el-button type="primary" :disabled="!newKb" @click="create" data-testid="chat-create">创建</el-button></template>
    </el-dialog>
    <SourceDrawer v-if="detail && source" v-model="sourceOpen" :kb-id="detail.kb_id" :chunk-id="source" />
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ChatDotRound, Delete, Loading, Plus, Promotion } from '@element-plus/icons-vue'
import { chatApi, kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { streamPost } from '@/api/sse'
import type { Citation, KnowledgeBase, SessionDetail, SessionOut } from '@/api/types'
import { fmtTime } from '@/utils/format'
import { statusNotice as notice } from '@/utils/answer'
import AnswerView from '@/components/AnswerView.vue'
import SourceDrawer from '@/components/SourceDrawer.vue'

interface Msg { key: string; question: string; answer: string; citations: Citation[]; status: string; hidden: boolean; streaming: boolean; revoked: boolean; error: string }
const route = useRoute()
const router = useRouter()
const sessions = ref<SessionOut[]>([])
const kbs = ref<KnowledgeBase[]>([])
const detail = ref<SessionDetail | null>(null)
const messages = ref<Msg[]>([])
const sessionId = ref<string>('')
const question = ref('')
const sending = ref(false)
const newOpen = ref(false), newKb = ref(''), newTitle = ref('')
const sourceOpen = ref(false), source = ref('')
const scroller = ref<{ setScrollTop: (n: number) => void; wrapRef?: HTMLElement }>()
let abort: AbortController | null = null

const kbName = (id: string) => kbs.value.find((k) => k.id === id)?.name || '知识库'
const statusNotice = (m: Msg) => (m.streaming ? null : notice(m.status))

async function loadSessions() { [sessions.value, kbs.value] = await Promise.all([chatApi.sessions(), kbApi.list()]) }
async function open(id: string) {
  if (id !== route.params.sessionId) router.push(`/chat/${id}`)
}
async function loadDetail(id: string) {
  sessionId.value = id
  try {
    detail.value = await chatApi.get(id)
    messages.value = detail.value.messages.map((m) => ({ key: String(m.id), question: m.question, answer: m.answer, citations: m.citations, status: m.status, hidden: m.hidden, streaming: false, revoked: false, error: '' }))
    scrollDown()
  } catch {
    detail.value = null
    messages.value = []
    ElMessage.warning('对话不存在或无权访问')
    router.replace('/chat')
  }
}
async function create() {
  try {
    const s = await chatApi.create(newKb.value, newTitle.value)
    newOpen.value = false; newTitle.value = ''
    await loadSessions()
    router.push(`/chat/${s.id}`)
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '创建失败') }
}
async function remove(id: string) {
  try { await ElMessageBox.confirm('删除该对话及其全部消息？', '删除对话', { type: 'warning' }) } catch { return }
  await chatApi.remove(id)
  if (id === sessionId.value) { detail.value = null; router.replace('/chat') }
  await loadSessions()
}
function scrollDown() { nextTick(() => { const w = scroller.value?.wrapRef; if (w) w.scrollTop = w.scrollHeight }) }

async function send() {
  const q = question.value.trim()
  if (!q || sending.value || !detail.value) return
  const m = ref<Msg>({ key: 'p' + Date.now(), question: q, answer: '', citations: [], status: '', hidden: false, streaming: true, revoked: false, error: '' })
  messages.value.push(m.value)
  const msg = messages.value[messages.value.length - 1]
  question.value = ''
  sending.value = true
  abort = new AbortController()
  scrollDown()
  try {
    await streamPost(`/sessions/${detail.value.id}/ask/stream`, { question: q }, (ev) => {
      if (ev.event === 'delta') { msg.answer += ev.data.text; scrollDown() }
      else if (ev.event === 'final') { msg.answer = ev.data.answer; msg.citations = ev.data.citations; msg.status = ev.data.status; msg.streaming = false; msg.key = String(ev.data.message_id) }
      else if (ev.event === 'revoked') { msg.answer = ''; msg.citations = []; msg.revoked = true; msg.streaming = false }
      else if (ev.event === 'error') { msg.answer = ''; msg.error = ev.data.message || '回答生成失败'; msg.streaming = false }
    }, abort.signal)
    if (msg.streaming) { msg.streaming = false; if (!msg.answer) msg.error = '连接已中断' }
  } catch (e) {
    msg.streaming = false
    msg.answer = ''
    if ((e as Error).name === 'AbortError') msg.error = '已停止'
    else msg.error = e instanceof ApiError ? e.message : '请求失败'
  } finally {
    sending.value = false
    abort = null
    scrollDown()
  }
}
function stop() { abort?.abort() }
function openSource(c: Citation) { source.value = c.chunk_id; sourceOpen.value = true }

/** Session ended elsewhere (logout / expiry / revocation): wipe visible answers immediately. */
function onSessionEnded() { abort?.abort(); messages.value = []; detail.value = null }
window.addEventListener('kb:session-ended', onSessionEnded)
onBeforeUnmount(() => { window.removeEventListener('kb:session-ended', onSessionEnded); abort?.abort() })

watch(() => route.params.sessionId, (id) => { if (typeof id === 'string' && id) loadDetail(id); else { detail.value = null; sessionId.value = '' } })
onMounted(async () => {
  await loadSessions()
  const id = route.params.sessionId
  if (typeof id === 'string' && id) await loadDetail(id)
})
</script>

<style scoped>
.chat { display: grid; grid-template-columns: 280px 1fr; height: calc(100vh - var(--kb-header-height)); }
.sessions { border-right: 1px solid var(--kb-border); background: var(--kb-bg-card); display: flex; flex-direction: column; min-height: 0; }
.sessions-head { padding: var(--kb-space-4); border-bottom: 1px solid var(--kb-border); }
.session { position: relative; padding: var(--kb-space-3) var(--kb-space-4); cursor: pointer; border-bottom: 1px solid var(--kb-bg-subtle); }
.session:hover { background: var(--kb-bg-subtle); }
.session.active { background: var(--kb-color-primary-soft); }
.s-title { font-weight: 500; color: var(--kb-text-primary); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; padding-right: 24px; }
.s-meta { font-size: var(--kb-font-xs); }
.s-del { position: absolute; right: 8px; top: 12px; opacity: 0; }
.session:hover .s-del { opacity: 1; }
.conv { display: flex; flex-direction: column; min-height: 0; background: var(--kb-bg-page); }
.conv-head { display: flex; justify-content: space-between; align-items: center; padding: var(--kb-space-3) var(--kb-space-6); background: var(--kb-bg-card); border-bottom: 1px solid var(--kb-border); }
.msgs { flex: 1; min-height: 0; }
.msgs-inner { max-width: 960px; margin: 0 auto; padding: var(--kb-space-6); }
.turn { margin-bottom: var(--kb-space-6); }
.q { display: flex; justify-content: flex-end; margin-bottom: var(--kb-space-3); }
.bubble-q { background: var(--kb-color-primary); color: #fff; padding: var(--kb-space-2) var(--kb-space-4); border-radius: var(--kb-radius-lg) var(--kb-radius-lg) var(--kb-radius-sm) var(--kb-radius-lg); max-width: 75%; white-space: pre-wrap; }
.a { display: flex; gap: var(--kb-space-3); align-items: flex-start; }
.bot { background: var(--kb-color-primary-soft); color: var(--kb-color-primary); font-weight: 700; flex-shrink: 0; }
.bubble-a { flex: 1; background: var(--kb-bg-card); border: 1px solid var(--kb-border); border-radius: var(--kb-radius-lg); padding: var(--kb-space-4); box-shadow: var(--kb-shadow-card); min-width: 0; }
.notice { margin-top: var(--kb-space-3); }
.thinking { display: flex; align-items: center; gap: var(--kb-space-2); }
.composer { background: var(--kb-bg-card); border-top: 1px solid var(--kb-border); padding: var(--kb-space-4) var(--kb-space-6); display: flex; gap: var(--kb-space-3); align-items: flex-end; }
.composer > :first-child { flex: 1; }
.composer-actions { display: flex; gap: var(--kb-space-2); }
.welcome { margin: auto; }
.welcome-icon { font-size: 48px; color: var(--kb-color-primary); }
.full { width: 100%; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
