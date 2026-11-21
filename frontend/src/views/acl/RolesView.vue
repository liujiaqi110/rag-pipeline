<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div><h1 class="kb-page-title">角色管理</h1><p class="kb-page-desc">角色可用于文档授权（role 维度）。</p></div>
    </div>
    <div class="kb-card">
      <div class="kb-toolbar">
        <el-input v-model="name" placeholder="角色名（字母、数字、_ . -）" class="w" data-testid="role-name" />
        <el-input v-model="desc" placeholder="描述" class="w" />
        <el-button type="primary" :loading="saving" @click="create" data-testid="role-create">新建角色</el-button>
      </div>
      <el-table v-loading="loading" :data="roles">
        <el-table-column prop="name" label="角色" min-width="160" />
        <el-table-column prop="description" label="描述" min-width="240" />
        <el-table-column label="成员数" width="100"><template #default="{ row }">{{ members(row.name) }}</template></el-table-column>
      </el-table>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { NamedOut, UserOut } from '@/api/types'

const roles = ref<NamedOut[]>([]), users = ref<UserOut[]>([])
const name = ref(''), desc = ref(''), loading = ref(false), saving = ref(false)
const members = (r: string) => users.value.filter((u) => u.roles.includes(r)).length
async function load() {
  loading.value = true
  try { [roles.value, users.value] = await Promise.all([adminApi.roles(), adminApi.users()]) } finally { loading.value = false }
}
async function create() {
  if (!/^[A-Za-z0-9_.-]{1,64}$/.test(name.value)) return ElMessage.warning('角色名只能包含字母、数字、_ . -')
  saving.value = true
  try { await adminApi.createRole(name.value, desc.value); name.value = ''; desc.value = ''; await load() }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '创建失败') } finally { saving.value = false }
}
onMounted(load)
</script>

<style scoped>.w { width: 240px; }</style>
