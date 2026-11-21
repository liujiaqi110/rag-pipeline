<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <h1 class="kb-page-title">FAQ 候选审核</h1>
        <p class="kb-page-desc">由真实问答记录语义聚类得到；模型答案未经人工审核不会发布。你只能看到证据文档你都有权阅读的候选。</p>
      </div>
      <div class="actions">
        <el-select v-model="kbId" placeholder="选择知识库" class="w" data-testid="faq-kb" @change="load"><el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" /></el-select>
        <el-button type="primary" :disabled="!kbId" @click="mineOpen = true" data-testid="faq-mine-open">挖掘候选 FAQ</el-button>
      </div>
    </div>
    <div class="kb-card">
      <el-alert v-if="hidden" type="info" :closable="false" show-icon class="mb" :title="`另有 ${hidden} 个候选的证据文档你无权阅读，已隐藏（不显示其问题与答案）`" />
      <el-alert v-if="lastJob" :type="lastJob.status === 'succeeded' ? 'success' : lastJob.status === 'failed' ? 'error' : 'info'" :closable="false" show-icon class="mb" data-testid="mine-result"
        :title="mineText" />
      <el-table v-loading="loading" :data="items" data-testid="faq-candidates" empty-text="暂无候选（可先挖掘）">
        <el-table-column prop="question" label="标准问题" min-width="240" show-overflow-tooltip />
        <el-table-column prop="frequency" label="频次" width="70" sortable />
        <el-table-column prop="distinct_users" label="用户数" width="80" />
        <el-table-column label="置信度" width="110"><template #default="{ row }"><el-progress :percentage="Math.round(row.confidence * 100)" :stroke-width="6" /></template></el-table-column>
        <el-table-column label="证据" min-width="180"><template #default="{ row }">
          <el-tag v-for="e in row.evidence" :key="e.document_id" size="small" effect="plain" class="tag">{{ e.title }} v{{ e.version_no }}</el-tag>
          <el-tag v-if="!row.evidence_valid" size="small" type="danger">{{ EVIDENCE_REASON[row.evidence_reason] }}</el-tag>
        </template></el-table-column>
        <el-table-column label="生成时间" width="160"><template #default="{ row }">{{ fmtTime(row.created_at) }}</template></el-table-column>
        <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }"><el-button link type="primary" @click="open(row.id)" data-testid="faq-review">审核</el-button></template></el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="mineOpen" title="挖掘候选 FAQ" width="460px">
      <el-form label-width="120px">
        <el-form-item label="频次阈值"><el-input-number v-model="minFreq" :min="2" :max="100" data-testid="mine-min-frequency" /></el-form-item>
        <el-form-item label="聚类相似度"><el-slider v-model="sim" :min="0.6" :max="0.98" :step="0.01" show-input /></el-form-item>
        <el-form-item label="统计天数"><el-input-number v-model="mineDays" :min="1" :max="365" /></el-form-item>
      </el-form>
      <p class="kb-muted">在后台任务中运行：向量化问题 → 语义聚类 → 按证据文档分组 → 生成候选答案。</p>
      <template #footer><el-button @click="mineOpen = false">取消</el-button><el-button type="primary" :loading="mining" @click="mine" data-testid="mine-start">开始挖掘</el-button></template>
    </el-dialog>
    <FaqEditorDrawer v-if="current" v-model="drawer" :faq-id="current" @changed="load" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { jobApi, kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { EVIDENCE_REASON, faqApi, type FaqItem } from '@/api/ops'
import type { JobOut, KnowledgeBase } from '@/api/types'
import { fmtTime } from '@/utils/format'
import FaqEditorDrawer from '@/components/FaqEditorDrawer.vue'

const kbs = ref<KnowledgeBase[]>([])
const kbId = ref('')
const items = ref<FaqItem[]>([])
const hidden = ref(0)
const loading = ref(false), mineOpen = ref(false), mining = ref(false), drawer = ref(false)
const current = ref('')
const minFreq = ref(3), sim = ref(0.8), mineDays = ref(30)
const lastJob = ref<JobOut | null>(null)
const mineText = computed(() => {
  const j = lastJob.value
  if (!j) return ''
  if (j.status !== 'succeeded') return `挖掘任务：${j.status}${j.error_message ? '，' + j.error_message : ''}`
  const r = j.result as Record<string, any>
  return `挖掘完成：新增候选 ${r.candidates_created}，更新 ${r.candidates_updated}，语义簇 ${r.clusters}；可用问题 ${r.stats?.eligible ?? 0}，因不合规跳过 ${r.stats?.skipped_non_compliant ?? 0}`
})

async function load() {
  if (!kbId.value) return
  loading.value = true
  try { const r = await faqApi.list({ kb_id: kbId.value, status: 'candidate' }); items.value = r.items; hidden.value = r.hidden_without_evidence_access }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '加载失败') } finally { loading.value = false }
}
async function mine() {
  mining.value = true
  try {
    let j = (await faqApi.mine({ kb_id: kbId.value, min_frequency: minFreq.value, similarity_threshold: sim.value, days: mineDays.value }))[0]
    while (j.status === 'queued' || j.status === 'running') { await new Promise((r) => setTimeout(r, 1000)); j = await jobApi.get(j.id) }
    lastJob.value = j
    mineOpen.value = false
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '挖掘失败') } finally { mining.value = false }
}
function open(id: string) { current.value = id; drawer.value = true }
onMounted(async () => { kbs.value = (await kbApi.list()); if (kbs.value.length) { kbId.value = kbs.value[0].id; await load() } })
</script>

<style scoped>
.actions { display: flex; gap: var(--kb-space-2); }
.w { width: 220px; }
.mb { margin-bottom: var(--kb-space-3); }
.tag { margin: 0 4px 2px 0; }
</style>
