<template>
  <el-dialog :model-value="modelValue" title="导入文档" width="720px" @update:model-value="emit('update:modelValue', $event)" @closed="reset">
    <div
      class="drop" :class="{ over }" data-testid="drop-zone"
      @dragover.prevent="over = true" @dragleave.prevent="over = false" @drop.prevent="onDrop"
    >
      <el-icon class="drop-icon"><UploadFilled /></el-icon>
      <div>将文件或<b>文件夹</b>拖到这里，或
        <el-button link type="primary" @click="fileInput?.click()">选择文件</el-button> /
        <el-button link type="primary" @click="dirInput?.click()">选择文件夹</el-button>
      </div>
      <div class="kb-muted">支持 .md / .txt / .pdf / .docx，单个文件不超过 20 MB；新文档默认无人可读，需要再配置权限</div>
      <input ref="fileInput" type="file" multiple accept=".md,.markdown,.txt,.pdf,.docx" hidden data-testid="file-input" @change="onPick" />
      <input ref="dirInput" type="file" webkitdirectory hidden @change="onPick" />
    </div>
    <el-alert v-if="skipped.length" type="warning" :closable="false" show-icon class="mt"
      :title="`已忽略 ${skipped.length} 个不支持的文件：${skipped.slice(0, 5).join('、')}${skipped.length > 5 ? '…' : ''}`" />
    <el-table v-if="items.length" :data="items" size="small" class="mt" max-height="320" data-testid="upload-table">
      <el-table-column prop="name" label="文件" min-width="220" show-overflow-tooltip />
      <el-table-column label="大小" width="90"><template #default="{ row }">{{ fmtSize(row.size) }}</template></el-table-column>
      <el-table-column label="状态" min-width="200">
        <template #default="{ row }">
          <div class="row-status">
            <el-tag :type="row.tagType" size="small">{{ row.label }}</el-tag>
            <el-progress v-if="row.progress !== null" :percentage="row.progress" :stroke-width="6" class="prog" :status="row.tagType === 'danger' ? 'exception' : row.progress === 100 ? 'success' : undefined" />
          </div>
          <div v-if="row.message" class="kb-muted err">{{ row.message }}</div>
        </template>
      </el-table-column>
      <el-table-column v-if="!busy && !started" width="60"><template #default="{ $index }"><el-button link type="danger" @click="items.splice($index, 1)">移除</el-button></template></el-table-column>
    </el-table>
    <div v-if="started" class="mt">
      <div class="kb-muted">上传进度</div>
      <el-progress :percentage="uploadPct" :stroke-width="8" data-testid="upload-progress" />
      <div v-if="indexJob" class="kb-muted mt">索引：{{ indexLabel }}</div>
    </div>
    <template #footer>
      <el-radio-group v-model="onDuplicate" size="small" class="dup" :disabled="started">
        <el-radio-button value="reject">同名时拒绝</el-radio-button>
        <el-radio-button value="new_version">同名时作为新版本</el-radio-button>
        <el-radio-button value="skip">同名时跳过</el-radio-button>
      </el-radio-group>
      <el-button @click="emit('update:modelValue', false)">{{ started ? '关闭' : '取消' }}</el-button>
      <el-button type="primary" :disabled="!items.length || started" :loading="busy" @click="start" data-testid="upload-start">开始导入</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { UploadFilled } from '@element-plus/icons-vue'
import { jobApi, kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { JobOut } from '@/api/types'
import { filesFromDrop, filterFiles } from '@/utils/files'
import { fmtSize, JOB_STATUS, STAGE_LABEL, VERSION_STATUS } from '@/utils/format'

const props = defineProps<{ modelValue: boolean; kbId: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'done'): void }>()

interface Item { file: File; name: string; size: number; label: string; tagType: 'info' | 'primary' | 'success' | 'danger' | 'warning'; progress: number | null; message: string; jobId: string | null }
const items = ref<Item[]>([])
const skipped = ref<string[]>([])
const over = ref(false)
const busy = ref(false)
const started = ref(false)
const uploadPct = ref(0)
const onDuplicate = ref('reject')
const indexJob = ref<JobOut | null>(null)
const fileInput = ref<HTMLInputElement>()
const dirInput = ref<HTMLInputElement>()
let timer: ReturnType<typeof setInterval> | undefined

const indexLabel = computed(() => indexJob.value ? `${JOB_STATUS[indexJob.value.status]?.label}（${STAGE_LABEL[indexJob.value.stage] || indexJob.value.stage}）` : '')

function add(files: File[], skip: string[]) {
  const seen = new Set(items.value.map((i) => i.name + i.size))
  for (const f of files) if (!seen.has(f.name + f.size)) items.value.push({ file: f, name: f.name, size: f.size, label: '待上传', tagType: 'info', progress: null, message: '', jobId: null })
  skipped.value.push(...skip)
}
async function onDrop(e: DragEvent) {
  over.value = false
  if (!e.dataTransfer) return
  const r = await filesFromDrop(e.dataTransfer)
  add(r.files, r.skipped)
}
function onPick(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files) { const r = filterFiles(input.files); add(r.files, r.skipped) }
  input.value = ''
}
function reset() {
  clearInterval(timer)
  items.value = []; skipped.value = []; started.value = false; busy.value = false; uploadPct.value = 0; indexJob.value = null
}

async function start() {
  busy.value = true
  started.value = true
  items.value.forEach((i) => { i.label = '上传中'; i.tagType = 'primary' })
  try {
    // batches of at most 20 files (server limit)
    for (let i = 0; i < items.value.length; i += 20) {
      const chunk = items.value.slice(i, i + 20)
      const { body } = await kbApi.batchUpload(props.kbId, chunk.map((x) => x.file), (p) => { uploadPct.value = Math.round(((i + (p / 100) * chunk.length) / items.value.length) * 100) }, onDuplicate.value)
      body.results.forEach((r, k) => {
        const it = chunk[k]
        it.jobId = r.job_id || null
        if (!r.ok) { it.label = '失败'; it.tagType = 'danger'; it.message = r.message || r.error_code || ''; it.progress = null }
        else if (r.outcome === 'skipped' || r.outcome === 'unchanged') { it.label = r.outcome === 'skipped' ? '已跳过' : '内容未变化'; it.tagType = 'info' }
        else { const s = VERSION_STATUS[r.status || 'uploaded']; it.label = s?.label || '处理中'; it.tagType = s?.type || 'primary'; it.progress = r.status === 'indexed' ? 100 : 10 }
      })
      if (body.index.job_id) indexJob.value = await jobApi.get(body.index.job_id)
    }
    uploadPct.value = 100
    poll()
  } catch (e) {
    items.value.forEach((i) => { if (i.label === '上传中') { i.label = '失败'; i.tagType = 'danger'; i.message = e instanceof ApiError ? e.message : '上传失败' } })
    busy.value = false
  }
}

function poll() {
  clearInterval(timer)
  const tick = async () => {
    let active = false
    for (const it of items.value) {
      if (!it.jobId || it.tagType === 'danger' || it.label === '已索引') continue
      try {
        const j = await jobApi.get(it.jobId)
        it.progress = j.status === 'succeeded' ? 60 : j.progress
        if (j.status === 'failed' || j.status === 'cancelled') { it.label = JOB_STATUS[j.status].label; it.tagType = 'danger'; it.message = j.error_message || ''; it.progress = null }
        else if (j.status !== 'succeeded') { it.label = STAGE_LABEL[j.stage] || '处理中'; it.tagType = 'primary'; active = true }
        else { it.label = '待索引'; it.tagType = 'warning' }
      } catch { /* transient */ }
    }
    if (indexJob.value) {
      indexJob.value = await jobApi.get(indexJob.value.id)
      const ij = indexJob.value
      if (ij.status === 'succeeded') items.value.forEach((it) => { if (it.label === '待索引') { it.label = '已索引'; it.tagType = 'success'; it.progress = 100 } })
      else if (ij.status === 'failed' || ij.status === 'cancelled') items.value.forEach((it) => { if (it.label === '待索引') { it.label = '索引失败'; it.tagType = 'danger'; it.message = ij.error_message || '' } })
      else active = true
    }
    if (!active) { clearInterval(timer); busy.value = false; emit('done') }
  }
  timer = setInterval(tick, 1000)
  tick()
}
onBeforeUnmount(() => clearInterval(timer))
</script>

<style scoped>
.drop { border: 1.5px dashed var(--kb-border-strong); border-radius: var(--kb-radius-lg); padding: var(--kb-space-6); text-align: center; background: var(--kb-bg-subtle); transition: border-color .15s, background .15s; }
.drop.over { border-color: var(--kb-color-primary); background: var(--kb-color-primary-soft); }
.drop-icon { font-size: 36px; color: var(--kb-color-primary); }
.mt { margin-top: var(--kb-space-3); }
.row-status { display: flex; align-items: center; gap: var(--kb-space-2); }
.prog { flex: 1; }
.err { color: var(--kb-color-danger); }
.dup { float: left; }
</style>
