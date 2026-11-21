<template>
  <div class="kb-page">
    <div class="kb-page-header"><div><h1 class="kb-page-title">审计日志</h1><p class="kb-page-desc">只记录哈希、计数和结果；不包含 Token、密码、问题或回答原文。</p></div></div>
    <div class="kb-card">
      <div class="kb-toolbar">
        <el-select v-model="action" clearable filterable allow-create placeholder="动作" class="w" data-testid="audit-action">
          <el-option v-for="a in actions" :key="a" :label="a" :value="a" />
        </el-select>
        <el-input v-model="actor" clearable placeholder="操作人" class="w" />
        <el-button type="primary" @click="search">查询</el-button>
      </div>
      <el-table v-loading="loading" :data="rows" size="small" data-testid="audit-table">
        <el-table-column label="时间" width="170"><template #default="{ row }">{{ fmtTime(row.ts) }}</template></el-table-column>
        <el-table-column prop="actor" label="操作人" width="140" />
        <el-table-column prop="action" label="动作" width="180" />
        <el-table-column label="结果" width="110"><template #default="{ row }"><el-tag size="small" :type="outcomeType(row.outcome)">{{ row.outcome }}</el-tag></template></el-table-column>
        <el-table-column prop="resource" label="对象" min-width="160" show-overflow-tooltip />
        <el-table-column label="详情" min-width="260"><template #default="{ row }"><span class="kb-mono">{{ JSON.stringify(row.detail) }}</span></template></el-table-column>
      </el-table>
      <div class="pager">
        <el-button :disabled="page === 0" @click="go(-1)">上一页</el-button>
        <span class="kb-muted">第 {{ page + 1 }} 页</span>
        <el-button :disabled="rows.length < size" @click="go(1)">下一页</el-button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { opsApi } from '@/api/endpoints'
import type { AuditOut } from '@/api/types'
import { fmtTime } from '@/utils/format'

const rows = ref<AuditOut[]>([])
const action = ref(''), actor = ref(''), loading = ref(false), page = ref(0)
const size = 50
const actions = ['auth.login', 'auth.logout', 'chat.ask', 'document.upload', 'document.acl.replace', 'document.delete', 'document.disable', 'index.build', 'job.cancel', 'job.retry', 'admin.user.create', 'admin.user.update']
const outcomeType = (o: string) => (['success', 'ok', 'active', 'created'].includes(o) ? 'success' : ['failure', 'rejected', 'error', 'failed', 'revoked'].includes(o) ? 'danger' : 'info')
async function load() {
  loading.value = true
  try { rows.value = await opsApi.audit({ action: action.value || undefined, actor: actor.value || undefined, limit: size, offset: page.value * size }) } finally { loading.value = false }
}
function search() { page.value = 0; load() }
function go(d: number) { page.value += d; load() }
onMounted(load)
</script>

<style scoped>.w { width: 220px; } .pager { display: flex; gap: var(--kb-space-3); align-items: center; justify-content: flex-end; margin-top: var(--kb-space-3); }</style>
