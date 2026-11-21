<template>
  <el-dialog :model-value="modelValue" :title="`文档权限 · ${title}`" width="680px" @update:model-value="emit('update:modelValue', $event)">
    <el-alert type="info" :closable="false" show-icon class="mb"
      title="默认拒绝：未授权的用户看不到文档、检索不到内容；拒绝优先于允许；还需满足文档密级。" />
    <div v-loading="loading">
      <h4 class="sec">允许</h4>
      <el-form label-width="96px">
        <el-form-item label="全员（global）">
          <el-switch v-model="allow.all_authenticated" data-testid="acl-global" />
          <span class="kb-muted hint">所有已登录且密级满足的用户</span>
        </el-form-item>
        <el-form-item label="部门"><SubjectSelect v-model="allow.departments" :options="opts.departments" test-id="acl-allow-departments" /></el-form-item>
        <el-form-item label="角色"><SubjectSelect v-model="allow.roles" :options="opts.roles" test-id="acl-allow-roles" /></el-form-item>
        <el-form-item label="用户"><SubjectSelect v-model="allow.users" :options="opts.users" test-id="acl-allow-users" /></el-form-item>
      </el-form>
      <h4 class="sec">拒绝（优先于允许）</h4>
      <el-form label-width="96px">
        <el-form-item label="部门"><SubjectSelect v-model="deny.departments" :options="opts.departments" /></el-form-item>
        <el-form-item label="角色"><SubjectSelect v-model="deny.roles" :options="opts.roles" /></el-form-item>
        <el-form-item label="用户"><SubjectSelect v-model="deny.users" :options="opts.users" /></el-form-item>
      </el-form>
      <p v-if="!auth.isAdmin" class="kb-muted">知识库管理者无法读取用户目录，请直接输入名称；服务端会校验主体是否存在。</p>
    </div>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save" data-testid="acl-save">保存权限</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { adminApi, docApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'
import SubjectSelect from './SubjectSelect.vue'

const props = defineProps<{ modelValue: boolean; docId: string; title: string }>()
const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'saved'): void }>()
const auth = useAuthStore()
const loading = ref(false)
const saving = ref(false)
const allow = reactive({ all_authenticated: false, users: [] as string[], roles: [] as string[], departments: [] as string[] })
const deny = reactive({ users: [] as string[], roles: [] as string[], departments: [] as string[] })
const opts = reactive({ users: [] as string[], roles: [] as string[], departments: [] as string[] })

async function load() {
  loading.value = true
  try {
    const acl = await docApi.acl(props.docId)
    Object.assign(allow, acl.allow)
    Object.assign(deny, acl.deny)
    if (auth.isAdmin) {
      const [u, r, d] = await Promise.all([adminApi.users(), adminApi.roles(), adminApi.departments()])
      opts.users = u.map((x) => x.username); opts.roles = r.map((x) => x.name); opts.departments = d.map((x) => x.name)
    }
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '读取权限失败')
  } finally {
    loading.value = false
  }
}
async function save() {
  saving.value = true
  try {
    await docApi.putAcl(props.docId, { ...allow }, { ...deny })
    ElMessage.success('权限已保存，立即生效')
    emit('saved')
    emit('update:modelValue', false)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '保存失败')
  } finally {
    saving.value = false
  }
}
// load whenever it becomes visible (also when it is mounted already open: no @open event then)
watch(() => [props.modelValue, props.docId], ([open]) => { if (open) load() }, { immediate: true })
</script>

<style scoped>
.sec { margin: var(--kb-space-3) 0 var(--kb-space-2); color: var(--kb-text-primary); }
.mb { margin-bottom: var(--kb-space-3); }
.hint { margin-left: var(--kb-space-3); }
</style>
