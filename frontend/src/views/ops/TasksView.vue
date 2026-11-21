<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <h1 class="kb-page-title">知识补充任务</h1>
        <p class="kb-page-desc">由知识缺口生成。补充文档并关联后标记完成，系统会以当初提问用户的当前权限重新提问，验证缺口是否解决。</p>
      </div>
      <el-segmented v-model="status" :options="statusOptions" @change="load" />
    </div>
    <div class="kb-card">
      <el-table v-loading="loading" :data="tasks" data-testid="task-table" empty-text="暂无任务">
        <el-table-column prop="title" label="任务" min-width="220" show-overflow-tooltip />
        <el-table-column prop="gap_question" label="对应缺口问题" min-width="200" show-overflow-tooltip />
        <el-table-column prop="assignee" label="负责人" width="110" />
        <el-table-column label="状态" width="140"><template #default="{ row }"><el-tag :type="TASK_STATUS[row.status]?.type || 'info'" size="small" :data-testid="`task-status-${row.id}`">{{ TASK_STATUS[row.status]?.label || row.status }}</el-tag></template></el-table-column>
        <el-table-column label="关联文档" min-width="170">
          <template #default="{ row }">
            <el-select :model-value="row.document_id || ''" placeholder="选择补充的文档" size="small" filterable :disabled="row.status === 'verified'" @change="(v: string) => link(row, v)" data-testid="task-doc">
              <el-option v-for="d in docsFor(row.kb_id)" :key="d.id" :label="d.title || d.original_filename || d.doc_key" :value="d.id" />
            </el-select>
          </template>
        </el-table-column>
        <el-table-column label="验证" min-width="140"><template #default="{ row }">
          <span v-if="row.verification?.at">{{ row.verification.resolved ? '已解决' : '未解决' }} · {{ fmtTime(row.verification.at) }}</span><span v-else class="kb-muted">—</span>
        </template></el-table-column>
        <el-table-column label="操作" width="200" fixed="right"><template #default="{ row }">
          <el-button v-if="row.status === 'todo'" link type="primary" @click="update(row, 'in_progress')">开始处理</el-button>
          <el-button v-if="row.status !== 'verified'" link type="success" @click="done(row)" data-testid="task-done">完成并验证</el-button>
        </template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { jobApi, kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { TASK_STATUS, taskApi, type TaskItem } from '@/api/ops'
import type { DocumentOut } from '@/api/types'
import { fmtTime } from '@/utils/format'

const statusOptions = [{ label: '全部', value: '' }, { label: '待办', value: 'todo' }, { label: '进行中', value: 'in_progress' }, { label: '验证未通过', value: 'reopened' }, { label: '已验证', value: 'verified' }]
const status = ref('')
const tasks = ref<TaskItem[]>([])
const docs = ref<Record<string, DocumentOut[]>>({})
const loading = ref(false)
const docsFor = (kb: string) => (docs.value[kb] || []).filter((d) => d.status === 'active')

async function load() {
  loading.value = true
  try {
    tasks.value = await taskApi.list({ status: status.value || undefined })
    for (const kb of new Set(tasks.value.map((t) => t.kb_id))) if (!docs.value[kb]) docs.value[kb] = await kbApi.documents(kb)
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '加载失败') } finally { loading.value = false }
}
async function update(t: TaskItem, s: string) { await taskApi.update(t.id, { status: s }); await load() }
async function link(t: TaskItem, docId: string) {
  try { await taskApi.update(t.id, { document_id: docId }); ElMessage.success('已关联文档'); await load() } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '关联失败') }
}
async function done(t: TaskItem) {
  try {
    const r = await taskApi.update(t.id, { status: 'done' })
    if (r.verify_job_id) {
      let j = await jobApi.get(r.verify_job_id)
      while (j.status === 'queued' || j.status === 'running') { await new Promise((res) => setTimeout(res, 1000)); j = await jobApi.get(j.id) }
      const res = j.result as Record<string, any>
      ElMessage[res.resolved ? 'success' : 'warning'](res.resolved ? '验证通过：缺口已解决' : '验证未通过：缺口仍无法回答，任务已重新打开')
    }
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败') }
}
onMounted(load)
</script>
