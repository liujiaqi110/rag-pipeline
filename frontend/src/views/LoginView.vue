<template>
  <div class="login-page">
    <div class="panel">
      <div class="intro">
        <div class="logo">KB</div>
        <h1>企业知识库工作台</h1>
        <p>按部门、角色与用户授权的知识检索与问答。未授权的资料不会进入模型上下文、引用或界面。</p>
      </div>
      <el-card class="form-card" shadow="never">
        <h2>登录</h2>
        <el-alert v-if="auth.expiredNotice" type="warning" :closable="false" show-icon title="登录已失效，请重新登录" class="mb" />
        <el-alert v-if="error" type="error" :closable="false" show-icon :title="error" class="mb" data-testid="login-error" />
        <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
          <el-form-item label="用户名" prop="username">
            <el-input v-model="form.username" autocomplete="username" data-testid="login-username" />
          </el-form-item>
          <el-form-item label="密码" prop="password">
            <el-input v-model="form.password" type="password" show-password autocomplete="current-password" data-testid="login-password" @keyup.enter="submit" />
          </el-form-item>
          <el-button type="primary" :loading="loading" class="full" native-type="submit" data-testid="login-submit">登录</el-button>
        </el-form>
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { ApiError } from '@/api/http'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const formRef = ref<FormInstance>()
const form = reactive({ username: '', password: '' })
const rules: FormRules = { username: [{ required: true, message: '请输入用户名' }], password: [{ required: true, message: '请输入密码' }] }
const loading = ref(false)
const error = ref('')

async function submit() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  loading.value = true
  error.value = ''
  try {
    await auth.login(form.username, form.password)
    form.password = ''
    const redirect = typeof route.query.redirect === 'string' && route.query.redirect.startsWith('/') ? route.query.redirect : '/chat'
    router.replace(redirect)
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : '登录失败'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page { min-height: 100vh; display: flex; align-items: center; justify-content: center; background: linear-gradient(160deg, #eef3ff 0%, var(--kb-bg-page) 60%); }
.panel { display: grid; grid-template-columns: 1.1fr 1fr; gap: var(--kb-space-8); width: min(960px, 92vw); align-items: center; }
.intro h1 { font-size: 28px; color: var(--kb-text-primary); margin: var(--kb-space-4) 0 var(--kb-space-2); }
.intro p { color: var(--kb-text-secondary); max-width: 420px; }
.logo { width: 48px; height: 48px; border-radius: var(--kb-radius-lg); background: var(--kb-color-primary); color: #fff; font-weight: 700; display: flex; align-items: center; justify-content: center; }
.form-card { border-radius: var(--kb-radius-lg); border: 1px solid var(--kb-border); box-shadow: var(--kb-shadow-pop); padding: var(--kb-space-2) var(--kb-space-4); }
.form-card h2 { margin: 0 0 var(--kb-space-4); font-size: var(--kb-font-xl); color: var(--kb-text-primary); }
.full { width: 100%; margin-top: var(--kb-space-2); }
.mb { margin-bottom: var(--kb-space-4); }
@media (max-width: 900px) { .panel { grid-template-columns: 1fr; } .intro { display: none; } }
</style>
