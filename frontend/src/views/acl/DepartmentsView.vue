<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div><h1 class="kb-page-title">部门管理</h1><p class="kb-page-desc">部门树只用于组织管理；文档授权<b>不会</b>沿部门树继承，授予上级部门的文档下级部门看不到。</p></div>
    </div>
    <div class="kb-grid kb-grid-2">
      <div class="kb-card">
        <h3 class="kb-card-title">组织结构</h3>
        <el-tree v-loading="loading" :data="tree" node-key="name" default-expand-all :props="{ label: 'label', children: 'children' }" data-testid="dept-tree">
          <template #default="{ data }">
            <span class="node"><span>{{ data.name }}</span><span class="kb-muted">{{ data.description }}</span><el-tag size="small" effect="plain">{{ data.count }} 人</el-tag></span>
          </template>
        </el-tree>
        <el-empty v-if="!loading && !tree.length" description="暂无部门" />
      </div>
      <div class="kb-card">
        <h3 class="kb-card-title">新建 / 调整部门</h3>
        <el-form label-width="88px">
          <el-form-item label="部门名"><el-input v-model="form.name" placeholder="字母、数字、_ . -" data-testid="dept-name" /></el-form-item>
          <el-form-item label="描述"><el-input v-model="form.description" data-testid="dept-desc" /></el-form-item>
          <el-form-item label="上级部门"><el-select v-model="form.parent" clearable filterable class="full" data-testid="dept-parent"><el-option v-for="d in depts" :key="d.name" :label="d.name" :value="d.name" /></el-select></el-form-item>
          <el-button type="primary" :loading="saving" @click="create" data-testid="dept-create">新建部门</el-button>
          <el-button :disabled="!exists" :loading="saving" @click="move">修改上级 / 描述</el-button>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { DepartmentOut, UserOut } from '@/api/types'
import { buildDeptTree, type DeptNode } from '@/utils/tree'

const depts = ref<DepartmentOut[]>([]), users = ref<UserOut[]>([])
const loading = ref(false), saving = ref(false)
const form = reactive({ name: '', description: '', parent: '' as string | '' })
const exists = computed(() => depts.value.some((d) => d.name === form.name))
const tree = computed<DeptNode[]>(() => buildDeptTree(depts.value, users.value))

async function load() {
  loading.value = true
  try { [depts.value, users.value] = await Promise.all([adminApi.departments(), adminApi.users()]) } finally { loading.value = false }
}
async function create() {
  saving.value = true
  try { await adminApi.createDepartment(form.name, form.description, form.parent || null); ElMessage.success('已创建'); Object.assign(form, { name: '', description: '', parent: '' }); await load() }
  catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '创建失败') } finally { saving.value = false }
}
async function move() {
  saving.value = true
  try {
    await adminApi.updateDepartment(form.name, { description: form.description || undefined, ...(form.parent ? { parent: form.parent } : { clear_parent: true }) })
    ElMessage.success('已修改'); await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '修改失败') } finally { saving.value = false }
}
onMounted(load)
</script>

<style scoped>
.node { display: inline-flex; gap: var(--kb-space-2); align-items: center; }
.full { width: 100%; }
</style>
