<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <h1 class="kb-page-title">已发布 FAQ 管理</h1>
        <p class="kb-page-desc">已发布的 FAQ 优先于模型回答；证据文档被停用、删除、更新或用户无权阅读时，不会命中。下线立即生效。</p>
      </div>
      <div class="actions">
        <el-select v-model="kbId" placeholder="选择知识库" class="w" @change="load"><el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" /></el-select>
        <el-segmented v-model="status" :options="statusOptions" @change="load" />
      </div>
    </div>
    <div class="kb-card">
      <el-table v-loading="loading" :data="items" data-testid="faq-published" empty-text="暂无">
        <el-table-column prop="question" label="标准问题" min-width="240" show-overflow-tooltip />
        <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="FAQ_STATUS[row.status].type" size="small">{{ FAQ_STATUS[row.status].label }}</el-tag></template></el-table-column>
        <el-table-column label="证据" width="140"><template #default="{ row }"><el-tag :type="row.evidence_valid ? 'success' : 'danger'" size="small" effect="plain">{{ EVIDENCE_REASON[row.evidence_reason] }}</el-tag></template></el-table-column>
        <el-table-column prop="hit_count" label="命中次数" width="100" sortable />
        <el-table-column prop="reviewed_by" label="审核人" width="120" />
        <el-table-column label="发布时间" width="160"><template #default="{ row }">{{ fmtTime(row.published_at) }}</template></el-table-column>
        <el-table-column label="操作" width="160" fixed="right"><template #default="{ row }">
          <el-button link type="primary" @click="open(row.id)">查看</el-button>
          <el-button v-if="row.status === 'published'" link type="warning" @click="retire(row.id)" data-testid="faq-retire-row">下线</el-button>
        </template></el-table-column>
      </el-table>
      <p v-if="hidden" class="kb-muted">另有 {{ hidden }} 条的证据文档你无权阅读，已隐藏。</p>
    </div>
    <FaqEditorDrawer v-if="current" v-model="drawer" :faq-id="current" @changed="load" />
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { EVIDENCE_REASON, FAQ_STATUS, faqApi, type FaqItem } from '@/api/ops'
import type { KnowledgeBase } from '@/api/types'
import { fmtTime } from '@/utils/format'
import FaqEditorDrawer from '@/components/FaqEditorDrawer.vue'

const statusOptions = [{ label: '已发布', value: 'published' }, { label: '已下线', value: 'retired' }, { label: '已驳回', value: 'rejected' }]
const kbs = ref<KnowledgeBase[]>([]), kbId = ref(''), status = ref('published')
const items = ref<FaqItem[]>([]), hidden = ref(0), loading = ref(false), drawer = ref(false), current = ref('')
async function load() {
  if (!kbId.value) return
  loading.value = true
  try { const r = await faqApi.list({ kb_id: kbId.value, status: status.value }); items.value = r.items; hidden.value = r.hidden_without_evidence_access }
  finally { loading.value = false }
}
async function retire(id: string) {
  try { await ElMessageBox.confirm('下线后该 FAQ 立即停止命中，可在“已下线”中重新审核发布。', '下线 FAQ', { type: 'warning' }) } catch { return }
  try { await faqApi.retire(id); ElMessage.success('已下线'); await load() } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败') }
}
function open(id: string) { current.value = id; drawer.value = true }
onMounted(async () => { kbs.value = await kbApi.list(); if (kbs.value.length) { kbId.value = kbs.value[0].id; await load() } })
</script>

<style scoped>.actions { display: flex; gap: var(--kb-space-2); } .w { width: 220px; }</style>
