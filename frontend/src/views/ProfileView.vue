<template>
  <div class="kb-page">
    <div class="kb-page-header"><div><h1 class="kb-page-title">个人账户</h1><p class="kb-page-desc">身份信息来自服务端，不能在前端修改。</p></div></div>
    <div class="kb-grid kb-grid-2">
      <div class="kb-card">
        <h3 class="kb-card-title">身份</h3>
        <el-descriptions :column="1" border>
          <el-descriptions-item label="用户名">{{ auth.me?.username }}</el-descriptions-item>
          <el-descriptions-item label="显示名">{{ auth.me?.display_name || '—' }}</el-descriptions-item>
          <el-descriptions-item label="部门"><el-tag v-for="d in auth.me?.departments" :key="d" class="tag">{{ d }}</el-tag><span v-if="!auth.me?.departments.length">—</span></el-descriptions-item>
          <el-descriptions-item label="角色"><el-tag v-for="r in auth.me?.roles" :key="r" type="info" class="tag">{{ r }}</el-tag><span v-if="!auth.me?.roles.length">—</span></el-descriptions-item>
          <el-descriptions-item label="密级">{{ auth.me?.clearance }}</el-descriptions-item>
          <el-descriptions-item label="平台管理员">{{ auth.me?.is_admin ? '是' : '否' }}</el-descriptions-item>
        </el-descriptions>
      </div>
      <div class="kb-card">
        <h3 class="kb-card-title">修改密码</h3>
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
          <el-form-item label="当前密码" prop="current"><el-input v-model="form.current" type="password" show-password data-testid="pw-current" /></el-form-item>
          <el-form-item label="新密码（至少 10 位，含字母和数字）" prop="next"><el-input v-model="form.next" type="password" show-password data-testid="pw-new" /></el-form-item>
          <el-form-item label="确认新密码" prop="confirm"><el-input v-model="form.confirm" type="password" show-password data-testid="pw-confirm" /></el-form-item>
          <el-button type="primary" :loading="saving" @click="submit" data-testid="pw-submit">修改密码</el-button>
          <p class="kb-muted">修改后所有登录会话都会失效，需要重新登录。</p>
        </el-form>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { authApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const formRef = ref<FormInstance>()
const form = reactive({ current: '', next: '', confirm: '' })
const saving = ref(false)
const rules: FormRules = {
  current: [{ required: true, message: '请输入当前密码' }],
  next: [{ required: true, message: '请输入新密码' }, { min: 10, message: '至少 10 位' },
    { pattern: /(?=.*[A-Za-z])(?=.*\d)/, message: '必须同时包含字母和数字' }],
  confirm: [{ validator: (_r, v, cb) => (v === form.next ? cb() : cb(new Error('两次输入不一致'))) }],
}

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    await authApi.changePassword(form.current, form.next)
    ElMessage.success('密码已修改，请重新登录')
    auth.clearLocal()
    router.replace('/login')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '修改失败')
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>.tag { margin-right: var(--kb-space-1); }</style>
