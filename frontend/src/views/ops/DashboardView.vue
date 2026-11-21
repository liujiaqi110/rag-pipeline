<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <h1 class="kb-page-title">知识运营看板</h1>
        <p class="kb-page-desc">全部数据来自服务端数据库与服务日志（qa_events / page_views），口径见页面底部。{{ windowText }}</p>
      </div>
      <div class="actions">
        <el-select v-model="kbId" clearable placeholder="全部知识库" class="w" @change="load"><el-option v-for="k in kbs" :key="k.id" :label="k.name" :value="k.id" /></el-select>
        <el-segmented v-model="days" :options="[7, 14, 30]" @change="load" />
        <el-button :icon="Refresh" @click="load">刷新</el-button>
      </div>
    </div>

    <div v-loading="loading" class="kb-grid kb-grid-4">
      <div v-for="c in cards" :key="c.key" class="kb-card stat" :data-testid="`kpi-${c.key}`">
        <div class="kb-muted">{{ c.title }}</div>
        <div class="stat-value">{{ c.value }}</div>
        <div class="kb-muted small">{{ c.sub }}</div>
      </div>
    </div>

    <div class="kb-grid kb-grid-2 mt">
      <div class="kb-card"><h3 class="kb-card-title">问答量与模型调用</h3><div ref="qaChart" class="chart" /></div>
      <div class="kb-card"><h3 class="kb-card-title">Token 消耗趋势</h3><div ref="tokenChart" class="chart" /></div>
      <div class="kb-card"><h3 class="kb-card-title">模型响应时间（P50 / P95，毫秒）</h3><div ref="latChart" class="chart" /></div>
      <div class="kb-card"><h3 class="kb-card-title">提问结果分布</h3><div ref="outChart" class="chart" /></div>
    </div>

    <div class="kb-grid kb-grid-2 mt">
      <div class="kb-card">
        <h3 class="kb-card-title">高频问题 TOP</h3>
        <el-table :data="m?.top_questions || []" size="small" data-testid="top-questions">
          <el-table-column type="index" width="50" />
          <el-table-column prop="question" label="问题（最近一次的原文）" min-width="240" show-overflow-tooltip />
          <el-table-column prop="count" label="次数" width="70" />
          <el-table-column prop="faq_hits" label="FAQ 命中" width="90" />
          <el-table-column prop="unanswered" label="未答" width="70" />
        </el-table>
      </div>
      <div class="kb-card">
        <h3 class="kb-card-title">热门引用知识 TOP</h3>
        <el-table :data="m?.top_cited || []" size="small" data-testid="top-cited">
          <el-table-column type="index" width="50" />
          <el-table-column prop="title" label="文档" min-width="240" show-overflow-tooltip />
          <el-table-column prop="citations" label="被引用的回答数" width="140" />
        </el-table>
      </div>
    </div>

    <div class="kb-card mt">
      <el-collapse>
        <el-collapse-item title="指标口径说明" name="defs">
          <el-descriptions :column="1" border size="small">
            <el-descriptions-item v-for="(v, k) in m?.definitions || {}" :key="k" :label="String(k)">{{ v }}</el-descriptions-item>
          </el-descriptions>
        </el-collapse-item>
      </el-collapse>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { opsApi2, type Metrics } from '@/api/ops'
import type { KnowledgeBase } from '@/api/types'
import { fmtTime } from '@/utils/format'

echarts.use([BarChart, LineChart, PieChart, GridComponent, LegendComponent, TooltipComponent, CanvasRenderer])
const PALETTE = ['#2f6bff', '#1f9d55', '#d48806', '#d4380d', '#64748b', '#7c3aed']
const OUTCOME: Record<string, string> = { answered: '已回答', knowledge_gap: '知识缺口', permission: '权限受限', generation_failure: '模型失败', infrastructure: '基础设施故障' }

const m = ref<Metrics | null>(null)
const kbs = ref<KnowledgeBase[]>([])
const kbId = ref<string>('')
const days = ref(14)
const loading = ref(false)
const qaChart = ref<HTMLDivElement>(), tokenChart = ref<HTMLDivElement>(), latChart = ref<HTMLDivElement>(), outChart = ref<HTMLDivElement>()
const charts: echarts.ECharts[] = []
const fmt = (n: number | null | undefined) => (n === null || n === undefined ? '—' : n.toLocaleString('zh-CN'))
const pct = (n: number) => `${(n * 100).toFixed(1)}%`
const windowText = computed(() => m.value ? `统计窗口：${fmtTime(m.value.window.start)} – ${fmtTime(m.value.window.end)}` : '')

const cards = computed(() => {
  const x = m.value
  if (!x) return []
  return [
    { key: 'pv', title: 'PV / UV', value: x.traffic.pv === null ? '—' : `${fmt(x.traffic.pv)} / ${fmt(x.traffic.uv)}`, sub: x.traffic.note || '页面访问次数 / 独立访问用户' },
    { key: 'totals', title: '知识库 / 文档 / Chunk', value: `${x.totals.knowledge_bases} / ${x.totals.documents} / ${fmt(x.totals.chunks)}`, sub: 'Chunk 为当前活动索引中的子 Chunk' },
    { key: 'questions', title: '用户提问（今日 / 本周）', value: `${fmt(x.questions.today)} / ${fmt(x.questions.this_week)}`, sub: `窗口内共 ${fmt(x.questions.window)} 次` },
    { key: 'llm', title: '模型调用次数', value: fmt(x.llm.calls), sub: `答案缓存命中 ${x.faq.answer_cache_hits} · 失败 ${x.failures.failed}` },
    { key: 'faq', title: 'FAQ 命中率', value: pct(x.faq.hit_rate), sub: `命中 ${x.faq.hits} 次 · 节省模型调用 ${x.faq.saved_llm_calls} 次` },
    { key: 'latency', title: '模型响应 P50 / P95', value: `${fmt(x.llm.generation_p50_ms)} / ${fmt(x.llm.generation_p95_ms)} ms`, sub: `端到端 P95 ${fmt(x.llm.end_to_end_p95_ms)} ms` },
    { key: 'tokens', title: 'Token（输入 / 输出）', value: `${fmt(x.llm.prompt_tokens)} / ${fmt(x.llm.output_tokens)}`, sub: Object.keys(x.llm.models).join('、') || '—' },
    { key: 'gaps', title: '知识缺口（待处理 / 已解决）', value: `${(x.gaps.by_status.open || 0) + (x.gaps.by_status.in_progress || 0)} / ${x.gaps.by_status.resolved || 0}`, sub: `解决率 ${pct(x.gaps.resolution_rate)} · 权限受限 ${x.failures.permission_denied} 次（不计入缺口）` },
  ]
})

async function load() {
  loading.value = true
  try {
    m.value = await opsApi2.metrics(days.value, kbId.value || undefined)
    await nextTick()
    draw()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '加载失败') } finally { loading.value = false }
}
function chart(el: HTMLDivElement | undefined, i: number) {
  if (!el) return null
  charts[i] = charts[i] || echarts.init(el)
  return charts[i]
}
function draw() {
  const x = m.value
  if (!x) return
  const dates = x.daily.map((d) => d.date.slice(5))
  const grid = { left: 40, right: 16, top: 30, bottom: 30 }
  chart(qaChart.value, 0)?.setOption({ color: PALETTE, tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid, xAxis: { type: 'category', data: dates }, yAxis: { type: 'value', minInterval: 1 },
    series: [{ name: '用户提问', type: 'bar', data: x.daily.map((d) => d.questions) }, { name: '模型调用', type: 'line', data: x.daily.map((d) => d.llm_calls) },
      { name: 'FAQ 命中', type: 'line', data: x.daily.map((d) => d.faq_hits) }, { name: '失败', type: 'line', data: x.daily.map((d) => d.failed) }] })
  chart(tokenChart.value, 1)?.setOption({ color: PALETTE, tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid, xAxis: { type: 'category', data: dates }, yAxis: { type: 'value' },
    series: [{ name: '输入 Token', type: 'bar', stack: 't', data: x.daily.map((d) => d.prompt_tokens) }, { name: '输出 Token', type: 'bar', stack: 't', data: x.daily.map((d) => d.output_tokens) }] })
  chart(latChart.value, 2)?.setOption({ color: PALETTE, tooltip: { trigger: 'axis' }, legend: { top: 0 }, grid, xAxis: { type: 'category', data: dates }, yAxis: { type: 'value' },
    series: [{ name: 'P50', type: 'line', connectNulls: true, data: x.daily.map((d) => d.p50_ms) }, { name: 'P95', type: 'line', connectNulls: true, data: x.daily.map((d) => d.p95_ms) }] })
  const outs = Object.entries(x.questions.by_outcome).map(([k, v]) => ({ name: OUTCOME[k] || k, value: v }))
  chart(outChart.value, 3)?.setOption({ color: PALETTE, tooltip: { trigger: 'item' }, legend: { bottom: 0 },
    series: [{ type: 'pie', radius: ['40%', '68%'], data: outs.length ? outs : [{ name: '暂无数据', value: 0 }], label: { formatter: '{b}: {c}' } }] })
}
const onResize = () => charts.forEach((c) => c?.resize())
onMounted(async () => { kbs.value = await kbApi.list(); await load(); window.addEventListener('resize', onResize) })
onBeforeUnmount(() => { window.removeEventListener('resize', onResize); charts.forEach((c) => c?.dispose()) })
</script>

<style scoped>
.actions { display: flex; gap: var(--kb-space-2); align-items: center; }
.w { width: 200px; }
.stat-value { font-size: var(--kb-font-xl); font-weight: 600; color: var(--kb-text-primary); margin: var(--kb-space-2) 0; }
.small { font-size: var(--kb-font-xs); }
.chart { height: 280px; }
.mt { margin-top: var(--kb-space-4); }
</style>
