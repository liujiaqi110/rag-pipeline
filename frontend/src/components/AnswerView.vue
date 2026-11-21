<template>
  <div>
    <div class="kb-markdown" :class="{ streaming }" v-html="html" data-testid="answer-text" @click="onClick" />
    <div v-if="citations.length" class="cites" data-testid="citations">
      <div v-for="(c, i) in citations" :key="c.chunk_id" class="cite-card" data-testid="citation-card" @click="emit('open', c)">
        <span class="kb-cite">{{ i + 1 }}</span>
        <div class="cite-body">
          <div class="cite-src">{{ displayName(c.source) }}</div>
          <div class="kb-mono kb-muted">{{ c.chunk_id }}</div>
        </div>
        <el-icon><ArrowRight /></el-icon>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ArrowRight } from '@element-plus/icons-vue'
import type { Citation } from '@/api/types'
import { renderAnswer, renderMarkdown } from '@/utils/markdown'

const props = withDefaults(defineProps<{ answer: string; citations?: Citation[]; streaming?: boolean }>(), { citations: () => [], streaming: false })
const emit = defineEmits<{ (e: 'open', c: Citation): void }>()

// while streaming the text is provisional: render plain markdown; the final event brings validated citations
const html = computed(() => (props.streaming ? renderMarkdown(props.answer) : renderAnswer(props.answer, props.citations)))
/** doc_key is "<8-hex document prefix>_<file name>": show the file name. */
function displayName(src: string) { return src.replace(/^[0-9a-f]{8}_/, '') }
function onClick(e: MouseEvent) {
  const n = (e.target as HTMLElement).dataset?.cite
  if (n) { const c = props.citations[Number(n) - 1]; if (c) emit('open', c) }
}
defineExpose({ displayName })
</script>

<style scoped>
.streaming :deep(p:last-child)::after { content: '▍'; color: var(--kb-color-primary); animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.cites { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: var(--kb-space-2); margin-top: var(--kb-space-3); }
.cite-card { display: flex; align-items: center; gap: var(--kb-space-2); padding: var(--kb-space-2) var(--kb-space-3); border: 1px solid var(--kb-border); border-radius: var(--kb-radius-md); background: var(--kb-bg-subtle); cursor: pointer; }
.cite-card:hover { border-color: var(--kb-color-primary); background: var(--kb-color-primary-soft); }
.cite-body { flex: 1; min-width: 0; }
.cite-src { font-weight: 500; color: var(--kb-text-primary); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
