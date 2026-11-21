<template>
  <el-drawer :model-value="modelValue" title="FAQ 审核与编辑" size="62%" @update:model-value="emit('update:modelValue', $event)">
    <div v-loading="loading" v-if="faq" data-testid="faq-editor">
      <div class="head">
        <el-tag :type="FAQ_STATUS[faq.status].type">{{ FAQ_STATUS[faq.status].label }}</el-tag>
        <el-tag :type="faq.evidence_valid ? 'success' : 'danger'" effect="plain">{{ EVIDENCE_REASON[faq.evidence_reason] || faq.evidence_reason }}</el-tag>
        <span class="kb-muted">频次 {{ faq.frequency }} · {{ faq.distinct_users }} 位用户 · 相似度 {{ faq.similarity_mean }} · 置信度 {{ faq.confidence }} · 修订 {{ faq.revision }}</span>
      </div>
      <el-alert v-if="faq.status === 'candidate'" type="info" :closable="false" show-icon class="mb"
        title="候选答案由模型基于证据文档生成，未经审核不会被任何用户看到；发布后，只有能阅读全部证据文档的用户才会命中。" />

      <el-form label-position="top">
        <el-form-item label="标准问题"><el-input v-model="question" :disabled="!editable" data-testid="faq-question" /></el-form-item>
        <el-form-item label="标准答案（Markdown；只能引用下方证据中的 Chunk）">
          <el-input v-model="answer" type="textarea" :rows="7" :disabled="!editable" data-testid="faq-answer" />
        </el-form-item>
        <div class="preview kb-card"><div class="kb-muted">预览</div><AnswerView :answer="answer" :citations="faq.citations" /></div>
      </el-form>

      <el-tabs class="mt">
        <el-tab-pane :label="`证据文档（${faq.evidence.length}）`">
          <el-table :data="faq.evidence" size="small">
            <el-table-column prop="title" label="文档" min-width="200" />
            <el-table-column label="版本" width="80"><template #default="{ row }">v{{ row.version_no }}</template></el-table-column>
            <el-table-column label="引用 Chunk" min-width="200"><template #default="{ row }"><span class="kb-mono">{{ faq.citations.filter((c) => c.source === row.source).map((c) => c.chunk_id).join(', ') }}</span></template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane :label="`同义问题（${faq.members?.length || 0}）`">
          <el-table :data="faq.members || []" size="small" data-testid="faq-members">
            <el-table-column prop="question" label="用户原始问题（不显示提问人）" min-width="260" />
            <el-table-column prop="similarity" label="与簇中心相似度" width="140" />
            <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.asked_at) }}</template></el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="生成信息">
          <el-descriptions :column="2" border size="small">
            <el-descriptions-item label="答案来源">{{ faq.answer_source }}</el-descriptions-item>
            <el-descriptions-item label="大模型">{{ faq.llm_model || '—' }} {{ faq.llm_digest ? '(' + faq.llm_digest.slice(0, 19) + ')' : '' }}</el-descriptions-item>
            <el-descriptions-item label="向量模型">{{ faq.embedding_model }}</el-descriptions-item>
            <el-descriptions-item label="索引代际">{{ faq.index_generation.slice(0, 12) }}</el-descriptions-item>
            <el-descriptions-item label="挖掘参数">{{ JSON.stringify(faq.params) }}</el-descriptions-item>
            <el-descriptions-item label="命中次数">{{ faq.hit_count }}</el-descriptions-item>
          </el-descriptions>
        </el-tab-pane>
        <el-tab-pane label="审核历史">
          <el-timeline>
            <el-timeline-item v-for="(h, i) in faq.history || []" :key="i" :timestamp="fmtTime(h.ts)">{{ h.actor }} · {{ ACTION[h.action] || h.action }}<span v-if="h.note" class="kb-muted"> · {{ h.note }}</span></el-timeline-item>
          </el-timeline>
        </el-tab-pane>
      </el-tabs>
    </div>
    <template #footer>
      <el-input v-model="note" placeholder="审核意见（可选）" class="note" />
      <el-button v-if="editable" :loading="busy" @click="save" data-testid="faq-save">保存修改</el-button>
      <el-button v-if="faq?.status === 'candidate'" type="danger" plain :loading="busy" @click="act('reject')" data-testid="faq-reject">驳回</el-button>
      <el-button v-if="faq?.status === 'published'" type="warning" :loading="busy" @click="act('retire')" data-testid="faq-retire">下线</el-button>
      <el-button v-if="faq?.status === 'candidate' || faq?.status === 'retired'" type="primary" :loading="busy" :disabled="!faq.evidence_valid" @click="act('publish')" data-testid="faq-publish">审核通过并发布</el-button>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/api/http'
import { EVIDENCE_REASON, FAQ_STATUS, faqApi, type FaqItem } from '@/api/ops'
import { fmtTime } from '@/utils/format'
import AnswerView from './AnswerView.vue'

const props = defineProps<{ modelValue: boolean; faqId: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'changed'): void }>()
const ACTION: Record<string, string> = { mined: '挖掘生成候选', remined: '再次挖掘合并', edit: '编辑', publish: '发布', reject: '驳回', retire: '下线' }
const faq = ref<FaqItem | null>(null)
const question = ref(''), answer = ref(''), note = ref('')
const loading = ref(false), busy = ref(false)
const editable = computed(() => faq.value?.status === 'candidate' || faq.value?.status === 'retired')

async function load() {
  loading.value = true
  try { faq.value = await faqApi.get(props.faqId); question.value = faq.value.question; answer.value = faq.value.answer }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '加载失败'); emit('update:modelValue', false) }
  finally { loading.value = false }
}
async function save() {
  busy.value = true
  try { faq.value = await faqApi.edit(props.faqId, { question: question.value, answer: answer.value }); answer.value = faq.value.answer; ElMessage.success('已保存'); emit('changed') }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '保存失败') } finally { busy.value = false }
}
async function act(a: 'publish' | 'reject' | 'retire') {
  busy.value = true
  try {
    if (editable.value && (question.value !== faq.value?.question || answer.value !== faq.value?.answer)) await faqApi.edit(props.faqId, { question: question.value, answer: answer.value })
    faq.value = await faqApi[a](props.faqId, note.value)
    ElMessage.success({ publish: '已发布：立即生效', reject: '已驳回', retire: '已下线：立即停止命中' }[a])
    emit('changed')
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败') } finally { busy.value = false }
}
// load whenever it becomes visible (also when mounted already open)
watch(() => [props.modelValue, props.faqId], ([open]) => { if (open) load() }, { immediate: true })
</script>

<style scoped>
.head { display: flex; gap: var(--kb-space-2); align-items: center; margin-bottom: var(--kb-space-3); flex-wrap: wrap; }
.mb { margin-bottom: var(--kb-space-3); }
.mt { margin-top: var(--kb-space-4); }
.preview { padding: var(--kb-space-3); }
.note { width: 260px; margin-right: var(--kb-space-2); }
</style>
