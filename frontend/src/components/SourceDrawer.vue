<template>
  <el-drawer :model-value="modelValue" title="来源文档" size="44%" @update:model-value="emit('update:modelValue', $event)">
    <div v-loading="loading" data-testid="source-drawer">
      <el-result v-if="error" icon="warning" title="来源不可用" :sub-title="error" />
      <template v-else-if="detail">
        <el-descriptions :column="1" border size="small">
          <el-descriptions-item label="文档">{{ detail.document_title }}</el-descriptions-item>
          <el-descriptions-item label="版本">v{{ detail.version_no }}</el-descriptions-item>
          <el-descriptions-item label="位置">
            <span v-if="detail.page">第 {{ detail.page }} 页 </span><span v-if="detail.paragraph">第 {{ detail.paragraph }} 段 </span>
            <span v-if="detail.section_heading">· {{ detail.section_heading }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="Chunk"><span class="kb-mono">{{ detail.chunk_id }}</span></el-descriptions-item>
        </el-descriptions>
        <pre class="text" data-testid="source-text">{{ detail.text }}</pre>
      </template>
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { kbApi } from '@/api/endpoints'
import type { CitationDetail } from '@/api/types'

const props = defineProps<{ modelValue: boolean; kbId: string; chunkId: string; versionId?: string | null }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()
const detail = ref<CitationDetail | null>(null)
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  detail.value = null
  try { detail.value = await kbApi.citation(props.kbId, props.chunkId, props.versionId) }   // pinned to the answered version
  catch { error.value = '该来源已不可访问：可能权限已变更、文档已停用，或文档已更新（回答基于旧版本）。' }
  finally { loading.value = false }
}
// load whenever it becomes visible (also when it is mounted already open: no @open event then)
watch(() => [props.modelValue, props.chunkId], ([open]) => { if (open) load() }, { immediate: true })
</script>

<style scoped>
.text { white-space: pre-wrap; font-family: var(--kb-font-family); background: var(--kb-bg-subtle); border: 1px solid var(--kb-border); border-left: 3px solid var(--kb-color-primary); border-radius: var(--kb-radius-md); padding: var(--kb-space-4); margin-top: var(--kb-space-4); line-height: 1.8; }
</style>
