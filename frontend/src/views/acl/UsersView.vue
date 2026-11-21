<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div><h1 class="kb-page-title">用户管理</h1><p class="kb-page-desc">角色、部门、密级或启用状态变更后，该用户的旧登录立即失效。平台管理员只能通过命令行创建。</p></div>
      <el-button type="primary" :icon="Plus" @click="openCreate" data-testid="user-create-open">新建用户</el-button>
    </div>
    <div class="kb-card">
      <div class="kb-toolbar"><el-input v-model="q" placeholder="搜索用户名 / 部门 / 角色" clearable class="search" :prefix-icon="Search" /></div>
      <el-table v-loading="loading" :data="filtered" data-testid="user-table">
        <el-table-column prop="username" label="用户名" min-width="140" />
        <el-table-column prop="display_name" label="显示名" min-width="120" />
        <el-table-column label="部门" min-width="160"><template #default="{ row }"><el-tag v-for="d in row.departments" :key="d" size="small" class="tag">{{ d }}</el-tag></template></el-table-column>
        <el-table-column label="角色" min-width="160"><template #default="{ row }"><el-tag v-for="r in row.roles" :key="r" size="small" type="info" class="tag">{{ r }}</el-tag></template></el-table-column>
        <el-table-column label="密级" width="70" prop="clearance" />
        <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.active ? 'success' : 'info'" size="small">{{ row.active ? '启用' : '停用' }}</el-tag></template></el-table-column>
        <el-table-column label="管理员" width="80"><template #default="{ row }">{{ row.is_admin ? '是' : '' }}</template></el-table-column>
        <el-table-column label="操作" width="160" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
            <el-button link :type="row.active ? 'warning' : 'success'" :disabled="row.username === auth.me?.username" @click="toggle(row)">{{ row.active ? '停用' : '启用' }}</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialog" :title="editing ? `编辑用户 · ${form.username}` : '新建用户'" width="560px">
      <el-form ref="formRef" :model="form" :rules="rules" label-width="96px">
        <el-form-item label="用户名" prop="username"><el-input v-model="form.username" :disabled="!!editing" data-testid="user-username" /></el-form-item>
        <el-form-item label="显示名"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item :label="editing ? '重置密码' : '初始密码'" prop="password"><el-input v-model="form.password" type="password" show-password :placeholder="editing ? '留空则不修改' : '至少 10 位，含字母和数字'" data-testid="user-password" /></el-form-item>
        <el-form-item label="部门"><el-select v-model="form.departments" multiple filterable class="full" data-testid="user-departments"><el-option v-for="d in depts" :key="d" :label="d" :value="d" /></el-select></el-form-item>
        <el-form-item label="角色"><el-select v-model="form.roles" multiple filterable class="full" data-testid="user-roles"><el-option v-for="r in roles" :key="r" :label="r" :value="r" /></el-select></el-form-item>
        <el-form-item label="密级"><el-radio-group v-model="form.clearance"><el-radio-button v-for="(l, i) in levels" :key="i" :value="i">{{ l }}</el-radio-button></el-radio-group></el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save" data-testid="user-save">保存</el-button></template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import { adminApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { UserOut } from '@/api/types'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const users = ref<UserOut[]>([]), roles = ref<string[]>([]), depts = ref<string[]>([])
const loading = ref(false), saving = ref(false), dialog = ref(false)
const editing = ref<UserOut | null>(null)
const q = ref('')
const levels = ['0 公开', '1 内部', '2 机密', '3 绝密']
const formRef = ref<FormInstance>()
const form = reactive({ username: '', display_name: '', password: '', departments: [] as string[], roles: [] as string[], clearance: 1 })
const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名' }, { pattern: /^[A-Za-z0-9_.-]{1,64}$/, message: '只能包含字母、数字、_ . -' }],
  password: [{ validator: (_r, v, cb) => (!v && editing.value ? cb() : v && v.length >= 10 && /[A-Za-z]/.test(v) && /\d/.test(v) ? cb() : cb(new Error('至少 10 位，且同时包含字母和数字'))) }],
}
const filtered = computed(() => users.value.filter((u) => !q.value || [u.username, u.display_name, ...u.departments, ...u.roles].join(' ').toLowerCase().includes(q.value.toLowerCase())))

async function load() {
  loading.value = true
  try {
    const [u, r, d] = await Promise.all([adminApi.users(), adminApi.roles(), adminApi.departments()])
    users.value = u; roles.value = r.map((x) => x.name); depts.value = d.map((x) => x.name)
  } finally { loading.value = false }
}
function openCreate() { editing.value = null; Object.assign(form, { username: '', display_name: '', password: '', departments: [], roles: [], clearance: 1 }); dialog.value = true }
function openEdit(u: UserOut) { editing.value = u; Object.assign(form, { username: u.username, display_name: u.display_name, password: '', departments: [...u.departments], roles: [...u.roles], clearance: u.clearance }); dialog.value = true }
async function save() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    if (editing.value) {
      await adminApi.updateUser(form.username, { display_name: form.display_name, departments: form.departments, roles: form.roles, clearance: form.clearance, ...(form.password ? { password: form.password } : {}) })
    } else {
      await adminApi.createUser({ username: form.username, password: form.password, display_name: form.display_name, departments: form.departments, roles: form.roles, clearance: form.clearance })
    }
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '保存失败') } finally { saving.value = false }
}
async function toggle(u: UserOut) {
  try { await adminApi.updateUser(u.username, { active: !u.active }); await load() } catch (e) { ElMessage.error(e instanceof ApiError ? e.message : '操作失败') }
}
onMounted(load)
</script>

<style scoped>.search { width: 280px; } .tag { margin: 0 4px 2px 0; } .full { width: 100%; }</style>
