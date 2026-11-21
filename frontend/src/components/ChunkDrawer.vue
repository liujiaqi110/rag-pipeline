<template>
  <el-drawer :model-value="modelValue" :title="`Chunk 详情 · ${title}`" size="52%" @update:model-value="emit('update:modelValue', $event)">
    <div v-loading="loading">
      <el-alert v-if="data && !data.text_visible" type="warning" :closable="false" show-icon class="mb"
        title="你可以管理该文档，但没有阅读权限：只显示 Chunk 元数据，不显示正文。" />
      <div v-if="data" class="kb-muted mb">版本 v{{ data.version_no }} · 共 {{ data.total }} 个 Chunk</div>
      <el-empty v-if="data && !data.chunks.length" description="该版本没有 Chunk" />
      <div v-for="c in data?.chunks || []" :key="c.chunk_id" class="chunk" data-testid="chunk-item">
        <div class="chunk-meta">
          <span class="kb-mono">#{{ c.ordinal }} · {{ c.chunk_id }}</span>
          <el-tag v-if="c.page" size="small" effect="plain">第 {{ c.page }} 页</el-tag>
          <el-tag v-if="c.paragraph" size="small" effect="plain" type="info">第 {{ c.paragraph }} 段</el-tag>
          <span class="kb-muted">{{ c.char_count }} 字</span>
        </div>
        <div v-if="c.section_heading" class="heading">{{ c.section_heading }}</div>
        <pre v-if="c.text" class="text">{{ c.text }}</pre>
      </div>
      <el-pagination v-if="data && data.total > pageSize" layout="prev, pager, next" :total="data.total" :page-size="pageSize" @current-change="load" />
    </div>
  </el-drawer>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { docApi } from '@/api/endpoints'
import type { ChunkListOut } from '@/api/types'

const props = defineProps<{ modelValue: boolean; docId: string; title: string; versionId?: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void }>()
const data = ref<ChunkListOut | null>(null)
const loading = ref(false)
const pageSize = 30

async function load(page: number) {
  loading.value = true
  try { data.value = await docApi.chunks(props.docId, { version_id: props.versionId, limit: pageSize, offset: (page - 1) * pageSize }) }
  catch { data.value = null }
  finally { loading.value = false }
}
// load whenever it becomes visible (also when it is mounted already open: no @open event then)
watch(() => [props.modelValue, props.docId], ([open]) => { if (open) load(1) }, { immediate: true })
</script>

<style scoped>
.mb { margin-bottom: var(--kb-space-3); }
.chunk { border: 1px solid var(--kb-border); border-radius: var(--kb-radius-md); padding: var(--kb-space-3); margin-bottom: var(--kb-space-3); background: var(--kb-bg-card); }
.chunk-meta { display: flex; gap: var(--kb-space-2); align-items: center; flex-wrap: wrap; }
.heading { font-weight: 600; color: var(--kb-text-primary); margin-top: var(--kb-space-2); }
.text { white-space: pre-wrap; font-family: var(--kb-font-family); font-size: var(--kb-font-sm); margin: var(--kb-space-2) 0 0; color: var(--kb-text-regular); }
</style>
