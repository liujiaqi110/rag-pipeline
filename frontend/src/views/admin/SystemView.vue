<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div><h1 class="kb-page-title">系统状态</h1><p class="kb-page-desc">数据来自 /api/v1/system/status 与 /api/v1/index/status，每 10 秒刷新。</p></div>
      <div><el-button :icon="Refresh" @click="load">刷新</el-button><el-button type="primary" :loading="rebuilding" @click="rebuild" data-testid="index-rebuild">重建索引</el-button></div>
    </div>
    <div v-loading="loading" class="kb-grid kb-grid-4">
      <div v-for="c in cards" :key="c.title" class="kb-card stat" :data-testid="`stat-${c.key}`">
        <div class="stat-head"><span>{{ c.title }}</span><el-tag :type="c.ok ? 'success' : c.ok === null ? 'info' : 'danger'" size="small">{{ c.ok ? '正常' : c.ok === null ? '未知' : '异常' }}</el-tag></div>
        <div class="stat-value">{{ c.value }}</div>
        <div class="kb-muted">{{ c.sub }}</div>
      </div>
    </div>
    <div class="kb-grid kb-grid-2 mt">
      <div class="kb-card"><h3 class="kb-card-title">后台任务</h3><div ref="jobChart" class="chart" /></div>
      <div class="kb-card">
        <h3 class="kb-card-title">索引代际</h3>
        <el-table :data="index?.recent || []" size="small" max-height="300">
          <el-table-column label="代际" width="100"><template #default="{ row }"><span class="kb-mono">{{ row.id.slice(0, 8) }}</span></template></el-table-column>
          <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag size="small" :type="row.status === 'active' ? 'success' : row.status === 'failed' ? 'danger' : 'info'">{{ row.status }}</el-tag></template></el-table-column>
          <el-table-column label="文档/Chunk" width="110"><template #default="{ row }">{{ row.document_count }} / {{ row.chunk_count }}</template></el-table-column>
          <el-table-column label="Dense" width="120"><template #default="{ row }">{{ row.dense ? row.dense_model : (row.dense_error || '无') }}</template></el-table-column>
          <el-table-column label="时间" min-width="150"><template #default="{ row }">{{ fmtTime(row.activated_at || row.created_at) }}</template></el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { opsApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { IndexStatusOut, SystemStatus } from '@/api/types'
import { fmtTime, JOB_STATUS } from '@/utils/format'

echarts.use([PieChart, LegendComponent, TooltipComponent, CanvasRenderer])
const sys = ref<SystemStatus | null>(null)
const index = ref<IndexStatusOut | null>(null)
const health = ref<string>('')
const loading = ref(false), rebuilding = ref(false)
const jobChart = ref<HTMLDivElement>()
let chart: echarts.ECharts | undefined
let timer: ReturnType<typeof setInterval> | undefined

const cards = computed(() => {
  const s = sys.value
  const db = (s?.database || {}) as Record<string, any>, llm = (s?.llm || {}) as Record<string, any>
  const emb = (s?.embedder || {}) as Record<string, any>, idx = (s?.index || {}) as Record<string, any>
  return [
    { key: 'api', title: 'API 服务', ok: health.value === 'ok', value: health.value === 'ok' ? '运行中' : '不可用', sub: `版本 ${s?.version || '—'}` },
    { key: 'db', title: '数据库', ok: s ? !!db.ok && db.revision === db.head : null, value: db.dialect || '—', sub: `迁移 ${db.revision || '—'}` },
    { key: 'index', title: '当前索引', ok: s ? !!idx.active_generation : null, value: idx.active_generation ? `${idx.documents} 文档 / ${idx.chunks} Chunk` : '未构建', sub: idx.active_generation ? (idx.dense ? 'BM25 + Dense' : `仅 BM25（${idx.dense_error || 'Dense 关闭'}）`) : '' },
    { key: 'model', title: '模型服务', ok: s ? !!llm.available && emb.available !== false : null, value: llm.available ? llm.model : '大模型不可用', sub: `向量模型：${emb.available ? emb.model : (emb.reason || '不可用')}` },
  ]
})

async function load() {
  loading.value = !sys.value
  try {
    const [h, s, i] = await Promise.all([opsApi.health(), opsApi.system(), opsApi.index()])
    health.value = h.status; sys.value = s; index.value = i
    await nextTick()
    draw()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '加载失败') } finally { loading.value = false }
}
function draw() {
  if (!jobChart.value) return
  chart = chart || echarts.init(jobChart.value)
  const data = Object.entries(sys.value?.jobs || {}).map(([k, v]) => ({ name: JOB_STATUS[k]?.label || k, value: v }))
  chart.setOption({
    tooltip: { trigger: 'item' }, legend: { bottom: 0 },
    color: ['#1f9d55', '#2f6bff', '#a0a7b6', '#d4380d', '#d48806'],
    series: [{ type: 'pie', radius: ['45%', '70%'], data: data.length ? data : [{ name: '暂无任务', value: 0 }], label: { formatter: '{b}: {c}' } }],
  })
}
async function rebuild() {
  rebuilding.value = true
  try { const r = await opsApi.rebuild(); ElMessage.success(r.status === 'pending' ? '已排队重建' : r.status === 'active' ? '重建完成' : `重建失败：${r.message || r.error_code}`); await load() }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '重建失败') } finally { rebuilding.value = false }
}
onMounted(() => { load(); timer = setInterval(load, 10000); window.addEventListener('resize', () => chart?.resize()) })
onBeforeUnmount(() => { clearInterval(timer); chart?.dispose() })
</script>

<style scoped>
.stat-head { display: flex; justify-content: space-between; align-items: center; color: var(--kb-text-secondary); }
.stat-value { font-size: var(--kb-font-xl); font-weight: 600; color: var(--kb-text-primary); margin: var(--kb-space-2) 0; }
.chart { height: 300px; }
.mt { margin-top: var(--kb-space-4); }
</style>
