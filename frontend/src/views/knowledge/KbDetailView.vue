<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <el-button link :icon="ArrowLeft" @click="$router.push('/knowledge')">知识库列表</el-button>
        <h1 class="kb-page-title">{{ kb?.name || '知识库' }}</h1>
        <p class="kb-page-desc">{{ kb?.description }}</p>
      </div>
      <div class="actions">
        <el-tag v-if="indexInfo" :type="indexInfo.dense ? 'success' : 'warning'" effect="plain" data-testid="index-badge">
          索引 {{ indexInfo.generation ? indexInfo.generation.slice(0, 8) : '未构建' }}{{ indexInfo.generation ? (indexInfo.dense ? ' · BM25 + Dense' : ' · 仅 BM25') : '' }}
        </el-tag>
        <el-button :icon="Refresh" @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" :icon="Upload" @click="uploadOpen = true" data-testid="open-upload">导入文档</el-button>
      </div>
    </div>

    <div class="kb-card">
      <div class="kb-toolbar">
        <el-input v-model="filter" placeholder="按文件名或标题筛选" clearable class="filter" :prefix-icon="Search" />
        <el-segmented v-model="statusFilter" :options="statusOptions" />
        <span v-if="activeJobs" class="kb-muted"><el-icon class="spin"><Loading /></el-icon> {{ activeJobs }} 个后台任务进行中</span>
      </div>
      <el-table v-loading="loading" :data="rows" row-key="id" data-testid="doc-table" empty-text="暂无文档">
        <el-table-column label="文档" min-width="240">
          <template #default="{ row }">
            <div class="doc-name" :data-testid="`doc-${row.doc.original_filename || row.doc.doc_key}`">{{ row.doc.title || row.doc.original_filename || row.doc.doc_key }}</div>
            <div class="kb-muted kb-mono">{{ row.doc.original_filename || row.doc.doc_key }}</div>
          </template>
        </el-table-column>
        <el-table-column label="格式" width="90"><template #default="{ row }">{{ row.status?.current_version?.file_format || row.status?.latest_version?.file_format || '—' }}</template></el-table-column>
        <el-table-column label="密级" width="80"><template #default="{ row }">{{ CLASSIFICATION[row.doc.classification] || row.doc.classification }}</template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="DOC_STATUS[row.doc.status]?.type" size="small">{{ DOC_STATUS[row.doc.status]?.label || row.doc.status }}</el-tag></template></el-table-column>
        <el-table-column label="解析 / 索引" min-width="180">
          <template #default="{ row }">
            <template v-if="row.status?.latest_version">
              <el-tag :type="VERSION_STATUS[row.status.latest_version.status]?.type || 'info'" size="small" :data-testid="`vstatus-${row.doc.original_filename}`">{{ VERSION_STATUS[row.status.latest_version.status]?.label || row.status.latest_version.status }}</el-tag>
              <el-tag v-if="row.status.retrievable" type="success" size="small" effect="plain" class="ml">可检索</el-tag>
              <div v-if="row.status.latest_version.error_message" class="err">{{ row.status.latest_version.error_message }}</div>
            </template>
            <span v-else class="kb-muted">外部索引</span>
          </template>
        </el-table-column>
        <el-table-column label="版本" width="70"><template #default="{ row }">{{ row.status?.current_version ? 'v' + row.status.current_version.version_no : '—' }}</template></el-table-column>
        <el-table-column label="Chunk" width="76"><template #default="{ row }">{{ row.status?.current_version?.chunk_count ?? '—' }}</template></el-table-column>
        <el-table-column label="更新时间" width="160"><template #default="{ row }">{{ fmtTime(row.doc.updated_at) }}</template></el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openChunks(row)" :data-testid="`chunks-${row.doc.original_filename}`">Chunk</el-button>
            <template v-if="canManage">
              <el-button v-if="row.doc.source === 'upload'" link type="primary" @click="openVersions(row)" :data-testid="`versions-${row.doc.original_filename}`">版本</el-button>
              <el-button link type="primary" @click="openAcl(row)" :data-testid="`acl-${row.doc.original_filename || row.doc.doc_key}`">权限</el-button>
              <el-button v-if="failed(row)" link type="warning" @click="retry(row)">重试</el-button>
              <el-button v-if="row.doc.status === 'active'" link type="warning" @click="toggle(row, false)" :data-testid="`disable-${row.doc.original_filename}`">停用</el-button>
              <el-button v-else-if="row.doc.status !== 'deleted'" link type="success" @click="toggle(row, true)">启用</el-button>
              <el-button link type="danger" @click="remove(row)" :data-testid="`delete-${row.doc.original_filename}`">删除</el-button>
            </template>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <div v-if="auth.isAdmin" class="kb-card">
      <h3 class="kb-card-title">知识库管理者 <span class="kb-muted">可上传、编辑、删除文档和配置权限；管理权限不包含阅读权限</span></h3>
      <div class="kb-toolbar">
        <el-tag v-for="m in managers" :key="m" closable @close="removeManager(m)">{{ m }}</el-tag>
        <el-select v-model="newManager" filterable placeholder="添加管理者" class="mgr" data-testid="kb-manager-add" @change="addManager">
          <el-option v-for="u in userOptions" :key="u" :label="u" :value="u" />
        </el-select>
      </div>
    </div>

    <UploadDialog v-model="uploadOpen" :kb-id="kbId" @done="load" />
    <DocumentAclDialog v-if="current" v-model="aclOpen" :doc-id="current.doc.id" :title="current.doc.title || current.doc.doc_key" @saved="load" />
    <ChunkDrawer v-if="current" v-model="chunkOpen" :doc-id="current.doc.id" :title="current.doc.title || current.doc.doc_key" />
    <VersionDrawer v-if="current" v-model="versionOpen" :doc-id="current.doc.id" :title="current.doc.title || current.doc.doc_key" :current-id="current.doc.current_version_id" @changed="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { ArrowLeft, Loading, Refresh, Search, Upload } from '@element-plus/icons-vue'
import { adminApi, docApi, jobApi, kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { DocumentOut, DocumentStatusOut, KnowledgeBase } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { CLASSIFICATION, DOC_STATUS, fmtTime, VERSION_STATUS } from '@/utils/format'
import UploadDialog from '@/components/UploadDialog.vue'
import DocumentAclDialog from '@/components/DocumentAclDialog.vue'
import ChunkDrawer from '@/components/ChunkDrawer.vue'
import VersionDrawer from '@/components/VersionDrawer.vue'

interface Row { id: string; doc: DocumentOut; status: DocumentStatusOut | null }
const route = useRoute()
const auth = useAuthStore()
const kbId = computed(() => String(route.params.kbId))
const canManage = computed(() => auth.canManage(kbId.value))
const kb = ref<KnowledgeBase | null>(null)
const all = ref<Row[]>([])
const loading = ref(false)
const filter = ref('')
const statusFilter = ref('全部')
const statusOptions = ['全部', '启用', '已停用', '失败']
const current = ref<Row | null>(null)
const uploadOpen = ref(false), aclOpen = ref(false), chunkOpen = ref(false), versionOpen = ref(false)
const managers = ref<string[]>([]), userOptions = ref<string[]>([]), newManager = ref('')
const activeJobs = ref(0)
const indexInfo = ref<{ generation: string | null; dense: boolean | null } | null>(null)
let timer: ReturnType<typeof setInterval> | undefined

const failed = (r: Row) => ['failed', 'index_failed', 'cancelled'].includes(r.status?.latest_version?.status || '')
const rows = computed(() => all.value.filter((r) => {
  const f = filter.value.trim().toLowerCase()
  const name = `${r.doc.title} ${r.doc.original_filename || ''} ${r.doc.doc_key}`.toLowerCase()
  if (f && !name.includes(f)) return false
  if (statusFilter.value === '启用') return r.doc.status === 'active'
  if (statusFilter.value === '已停用') return r.doc.status === 'disabled'
  if (statusFilter.value === '失败') return failed(r)
  return r.doc.status !== 'deleted'
}))

async function load() {
  loading.value = all.value.length === 0
  try {
    kb.value = await kbApi.get(kbId.value)
    const docs = await kbApi.documents(kbId.value)
    const statuses = await Promise.all(docs.map((d) => docApi.status(d.id).catch(() => null)))
    all.value = docs.map((d, i) => ({ id: d.id, doc: d, status: statuses[i] }))
    const st = statuses.find((s) => s)
    indexInfo.value = st ? { generation: st.active_generation_id, dense: st.dense } : null
    if (canManage.value) {
      const jobs = await jobApi.list({ kb_id: kbId.value, limit: 50 })
      activeJobs.value = jobs.filter((j) => j.status === 'queued' || j.status === 'running').length
      schedule()
    }
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '加载失败')
  } finally { loading.value = false }
}
function schedule() {
  clearInterval(timer)
  if (activeJobs.value) timer = setInterval(load, 2000)
}
async function loadManagers() {
  if (!auth.isAdmin) return
  managers.value = await kbApi.managers(kbId.value)
  userOptions.value = (await adminApi.users()).map((u) => u.username).filter((u) => !managers.value.includes(u))
}
async function addManager(u: string) { await kbApi.addManager(kbId.value, u); newManager.value = ''; await loadManagers() }
async function removeManager(u: string) { await kbApi.removeManager(kbId.value, u); await loadManagers() }

function openAcl(r: Row) { current.value = r; aclOpen.value = true }
function openChunks(r: Row) { current.value = r; chunkOpen.value = true }
function openVersions(r: Row) { current.value = r; versionOpen.value = true }

async function toggle(r: Row, enable: boolean) {
  try {
    await (enable ? docApi.enable(r.id) : docApi.disable(r.id))
    ElMessage.success(enable ? '已启用' : '已停用：立即不再参与问答')
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败') }
}
async function remove(r: Row) {
  try {
    await ElMessageBox.confirm(`删除「${r.doc.title || r.doc.doc_key}」后立即失效，索引重建后其 Chunk 与文件会被清除，无法恢复。确定删除？`, '删除文档', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' })
  } catch { return }
  try { await docApi.remove(r.id); ElMessage.success('已删除'); await load() } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '删除失败') }
}
async function retry(r: Row) {
  const jobs = await jobApi.list({ document_id: r.id, limit: 5 })
  const j = jobs.find((x) => x.status === 'failed' || x.status === 'cancelled')
  try {
    if (j) await jobApi.retry(j.id)
    else if (auth.isAdmin) await (await import('@/api/endpoints')).opsApi.rebuild()
    ElMessage.success('已重新排队')
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '重试失败') }
}
onMounted(() => { load(); loadManagers() })
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.actions { display: flex; gap: var(--kb-space-2); align-items: center; }
.filter { width: 260px; }
.doc-name { font-weight: 500; color: var(--kb-text-primary); }
.ml { margin-left: var(--kb-space-1); }
.err { color: var(--kb-color-danger); font-size: var(--kb-font-xs); }
.mgr { width: 220px; }
.spin { animation: spin 1s linear infinite; vertical-align: middle; }
@keyframes spin { to { transform: rotate(360deg); } }
</style>
