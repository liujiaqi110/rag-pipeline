<template>
  <el-drawer :model-value="modelValue" :title="`版本管理 · ${title}`" size="56%" @update:model-value="emit('update:modelValue', $event)">
    <div class="kb-toolbar">
      <el-upload :auto-upload="false" :show-file-list="false" accept=".md,.markdown,.txt,.pdf,.docx" :on-change="onFile">
        <el-button type="primary" :loading="uploading" data-testid="version-upload">上传新版本</el-button>
      </el-upload>
      <el-progress v-if="uploading" :percentage="pct" class="prog" />
      <span class="kb-muted">新版本建索引成功后才会替换当前版本；失败时旧版本继续服务。</span>
    </div>
    <el-table v-loading="loading" :data="versions" size="small" data-testid="version-table">
      <el-table-column label="版本" width="70"><template #default="{ row }">v{{ row.version_no }}</template></el-table-column>
      <el-table-column prop="original_filename" label="文件" min-width="160" show-overflow-tooltip />
      <el-table-column label="状态" width="150">
        <template #default="{ row }">
          <el-tag :type="VERSION_STATUS[row.status]?.type || 'info'" size="small">{{ VERSION_STATUS[row.status]?.label || row.status }}</el-tag>
          <el-tag v-if="row.id === currentId" size="small" effect="dark" class="ml">当前</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="Chunk" width="70" prop="chunk_count" />
      <el-table-column label="大小" width="90"><template #default="{ row }">{{ fmtSize(row.size_bytes) }}</template></el-table-column>
      <el-table-column label="上传" min-width="150"><template #default="{ row }">{{ row.created_by }} · {{ fmtTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="错误" min-width="140"><template #default="{ row }"><span class="err">{{ row.error_message || '' }}</span></template></el-table-column>
    </el-table>
    <h4 class="sec">后台任务</h4>
    <el-table :data="jobs" size="small" data-testid="job-table">
      <el-table-column label="类型" width="90"><template #default="{ row }">{{ row.kind === 'process' ? '解析切片' : '索引' }}</template></el-table-column>
      <el-table-column label="状态" width="120"><template #default="{ row }"><el-tag :type="JOB_STATUS[row.status].type" size="small">{{ JOB_STATUS[row.status].label }}</el-tag></template></el-table-column>
      <el-table-column label="进度" min-width="160"><template #default="{ row }"><el-progress :percentage="row.progress" :stroke-width="6" /></template></el-table-column>
      <el-table-column label="说明" min-width="160"><template #default="{ row }">{{ row.error_message || STAGE_LABEL[row.stage] || row.stage }}</template></el-table-column>
      <el-table-column label="操作" width="120">
        <template #default="{ row }">
          <el-button v-if="row.status === 'queued' || row.status === 'running'" link type="warning" @click="cancel(row.id)">取消</el-button>
          <el-button v-if="row.status === 'failed' || row.status === 'cancelled'" link type="primary" @click="retry(row.id)" data-testid="job-retry">重试</el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-drawer>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { ElMessage, type UploadFile } from 'element-plus'
import { docApi, jobApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { JobOut, VersionOut } from '@/api/types'
import { fmtSize, fmtTime, JOB_STATUS, STAGE_LABEL, VERSION_STATUS } from '@/utils/format'

const props = defineProps<{ modelValue: boolean; docId: string; title: string; currentId: string | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'changed'): void }>()
const versions = ref<VersionOut[]>([])
const jobs = ref<JobOut[]>([])
const loading = ref(false)
const uploading = ref(false)
const pct = ref(0)
let timer: ReturnType<typeof setInterval> | undefined

async function load() {
  loading.value = true
  try {
    ;[versions.value, jobs.value] = await Promise.all([docApi.versions(props.docId), jobApi.list({ document_id: props.docId, limit: 20 })])
  } finally { loading.value = false }
  clearInterval(timer)
  // keep refreshing while a processing job runs OR a version still waits for the (KB-wide) index job
  const busy = () => jobs.value.some((j) => j.status === 'queued' || j.status === 'running')
    || versions.value.some((v) => TRANSIENT.includes(v.status))
  if (busy()) timer = setInterval(async () => {
    ;[jobs.value, versions.value] = await Promise.all([jobApi.list({ document_id: props.docId, limit: 20 }), docApi.versions(props.docId)])
    if (!busy()) { clearInterval(timer); emit('changed') }
  }, 1200)
}
const TRANSIENT = ['uploaded', 'processing', 'chunked']
async function onFile(f: UploadFile) {
  if (!f.raw) return
  uploading.value = true
  pct.value = 0
  try {
    const { body } = await docApi.newVersion(props.docId, f.raw, (p) => (pct.value = p))
    ElMessage.success(body.outcome === 'unchanged' ? '内容与当前版本相同，未创建新版本' : `已上传 v${body.version?.version_no}，正在后台处理`)
    await load()
    emit('changed')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '上传失败')
  } finally { uploading.value = false }
}
async function cancel(id: string) { await jobApi.cancel(id); await load() }
async function retry(id: string) {
  try { await jobApi.retry(id); ElMessage.success('已重新排队'); await load() } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '重试失败') }
}
onBeforeUnmount(() => clearInterval(timer))
// load whenever it becomes visible (also when it is mounted already open: no @open event then)
watch(() => [props.modelValue, props.docId], ([open]) => { if (open) load() }, { immediate: true })
</script>

<style scoped>
.prog { width: 180px; }
.ml { margin-left: var(--kb-space-1); }
.sec { margin: var(--kb-space-5) 0 var(--kb-space-2); color: var(--kb-text-primary); }
.err { color: var(--kb-color-danger); font-size: var(--kb-font-xs); }
</style>
