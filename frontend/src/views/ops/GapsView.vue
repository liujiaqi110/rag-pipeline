<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <h1 class="kb-page-title">知识缺口</h1>
        <p class="kb-page-desc">只收录真正的知识问题（无证据、相关性不足、证据不足、知识过期）；权限受限、模型失败、基础设施故障单独统计，不在此列。</p>
      </div>
    </div>
    <div class="kb-card">
      <div class="kb-toolbar">
        <el-select v-model="f.kb_id" clearable placeholder="知识库" class="w"><el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" /></el-select>
        <el-select v-model="f.gap_type" clearable placeholder="缺口类型" class="w"><el-option v-for="(v, k) in GAP_TYPE" :key="k" :label="v" :value="k" /></el-select>
        <el-select v-model="f.status" clearable placeholder="状态" class="w"><el-option v-for="(v, k) in GAP_STATUS" :key="k" :label="v.label" :value="k" /></el-select>
        <el-date-picker v-model="range" type="daterange" start-placeholder="开始" end-placeholder="结束" value-format="YYYY-MM-DDTHH:mm:ss" />
        <el-checkbox v-model="f.repeated">只看反复出现</el-checkbox>
        <el-button type="primary" @click="load">查询</el-button>
      </div>
      <el-table v-loading="loading" :data="gaps" data-testid="gap-table" empty-text="暂无知识缺口">
        <el-table-column prop="question" label="问题（代表）" min-width="240" show-overflow-tooltip />
        <el-table-column label="类型" width="130"><template #default="{ row }">{{ GAP_TYPE[row.gap_type] || row.gap_type }}</template></el-table-column>
        <el-table-column label="频次" width="110"><template #default="{ row }">{{ row.occurrences }} 次 / {{ row.distinct_users }} 人<el-tag v-if="row.repeated" size="small" type="danger" class="ml">反复</el-tag></template></el-table-column>
        <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="GAP_STATUS[row.status].type" size="small" :data-testid="`gap-status-${row.id}`">{{ GAP_STATUS[row.status].label }}</el-tag></template></el-table-column>
        <el-table-column label="最近出现" width="160"><template #default="{ row }">{{ fmtTime(row.last_seen) }}</template></el-table-column>
        <el-table-column label="操作" width="260" fixed="right"><template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row)">详情</el-button>
          <el-button v-if="row.status !== 'resolved'" link type="primary" @click="openTask(row)" data-testid="gap-task">转为补充任务</el-button>
          <el-button link type="success" @click="verify(row)" data-testid="gap-verify">验证</el-button>
          <el-button v-if="row.status === 'open' || row.status === 'in_progress'" link type="info" @click="setStatus(row, 'dismissed')">忽略</el-button>
          <el-button v-if="row.status === 'dismissed'" link @click="setStatus(row, 'open')">重新打开</el-button>
        </template></el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="taskOpen" title="转为知识补充任务" width="520px">
      <el-form label-width="80px">
        <el-form-item label="缺口"><span>{{ sel?.question }}</span></el-form-item>
        <el-form-item label="任务标题"><el-input v-model="task.title" data-testid="task-title" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="task.description" type="textarea" :rows="3" /></el-form-item>
        <el-form-item label="负责人"><el-input v-model="task.assignee" placeholder="用户名（可选）" /></el-form-item>
      </el-form>
      <template #footer><el-button @click="taskOpen = false">取消</el-button><el-button type="primary" @click="createTask" data-testid="task-create">创建任务</el-button></template>
    </el-dialog>

    <el-drawer v-model="detailOpen" title="缺口详情" size="48%">
      <template v-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="问题">{{ detail.question }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ GAP_TYPE[detail.gap_type] }}</el-descriptions-item>
          <el-descriptions-item label="首次 / 最近">{{ fmtTime(detail.first_seen) }} / {{ fmtTime(detail.last_seen) }}</el-descriptions-item>
          <el-descriptions-item label="说明">{{ detail.note || '—' }}</el-descriptions-item>
        </el-descriptions>
        <h4>最近一次验证</h4>
        <div v-if="detail.verification?.at" data-testid="gap-verification">
          <el-tag :type="detail.verification.resolved ? 'success' : 'warning'">{{ detail.verification.resolved ? '已解决' : '尚未解决' }}</el-tag>
          <span class="kb-muted"> {{ fmtTime(detail.verification.at) }}</span>
          <el-table :data="detail.verification.results || []" size="small" class="mt">
            <el-table-column prop="user" label="提问用户（匿名）" width="140" />
            <el-table-column prop="status" label="结果" width="140" />
            <el-table-column label="能否回答" width="100"><template #default="{ row }">{{ row.answered ? '能' : '否' }}</template></el-table-column>
            <el-table-column prop="citations" label="引用数" />
          </el-table>
        </div>
        <p v-else class="kb-muted">尚未验证</p>
        <h4>出现记录（不显示提问人）</h4>
        <el-table :data="detail.occurrence_list || []" size="small">
          <el-table-column prop="question" label="问题" min-width="240" />
          <el-table-column label="类型" width="130"><template #default="{ row }">{{ GAP_TYPE[row.gap_type] }}</template></el-table-column>
          <el-table-column label="时间" width="160"><template #default="{ row }">{{ fmtTime(row.ts) }}</template></el-table-column>
        </el-table>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { jobApi, kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { GAP_STATUS, GAP_TYPE, gapApi, type GapItem } from '@/api/ops'
import type { KnowledgeBase } from '@/api/types'
import { fmtTime } from '@/utils/format'

const kbs = ref<KnowledgeBase[]>([])
const gaps = ref<GapItem[]>([])
const f = reactive({ kb_id: '', gap_type: '', status: '', repeated: false })
const range = ref<[string, string] | null>(null)
const loading = ref(false), taskOpen = ref(false), detailOpen = ref(false)
const sel = ref<GapItem | null>(null), detail = ref<GapItem | null>(null)
const task = reactive({ title: '', description: '', assignee: '' })

async function load() {
  loading.value = true
  try {
    gaps.value = await gapApi.list({ kb_id: f.kb_id || undefined, gap_type: f.gap_type || undefined, status: f.status || undefined,
      repeated: f.repeated || undefined, since: range.value?.[0], until: range.value?.[1] })
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '加载失败') } finally { loading.value = false }
}
function openTask(g: GapItem) { sel.value = g; task.title = `补充知识：${g.question.slice(0, 40)}`; task.description = ''; task.assignee = ''; taskOpen.value = true }
async function createTask() {
  try { await gapApi.createTask(sel.value!.id, { title: task.title, description: task.description, assignee: task.assignee || undefined }); ElMessage.success('已创建补充任务'); taskOpen.value = false; await load() }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '创建失败') }
}
async function verify(g: GapItem) {
  try {
    let j = (await gapApi.verify(g.id))[0]
    while (j.status === 'queued' || j.status === 'running') { await new Promise((r) => setTimeout(r, 1000)); j = await jobApi.get(j.id) }
    const r = j.result as Record<string, any>
    ElMessage[r.resolved ? 'success' : 'warning'](r.resolved ? '验证通过：缺口已解决' : '验证未通过：仍无法回答')
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '验证失败') }
}
async function setStatus(g: GapItem, s: string) { await gapApi.update(g.id, s); await load() }
async function openDetail(g: GapItem) { detail.value = await gapApi.get(g.id); detailOpen.value = true }
onMounted(async () => { kbs.value = await kbApi.list(); await load() })
</script>

<style scoped>.w { width: 160px; } .ml { margin-left: 4px; } .mt { margin-top: var(--kb-space-2); }</style>
