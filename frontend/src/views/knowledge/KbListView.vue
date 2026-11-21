<template>
  <div class="kb-page">
    <div class="kb-page-header">
      <div>
        <h1 class="kb-page-title">知识管理中心</h1>
        <p class="kb-page-desc">只显示你可阅读或可管理的知识库。</p>
      </div>
      <el-button v-if="auth.isAdmin" type="primary" :icon="Plus" @click="dialog = true" data-testid="kb-create-open">新建知识库</el-button>
    </div>
    <div v-loading="loading" class="kb-grid kb-grid-3">
      <div v-for="kb in kbs" :key="kb.id" class="kb-card kb-item" data-testid="kb-card" @click="$router.push(`/knowledge/${kb.id}`)">
        <div class="kb-item-head">
          <el-icon class="kb-icon"><Collection /></el-icon>
          <div class="kb-name">{{ kb.name }}</div>
          <el-tag v-if="auth.canManage(kb.id)" size="small" effect="plain">可管理</el-tag>
        </div>
        <p class="kb-muted desc">{{ kb.description || '暂无描述' }}</p>
        <div class="kb-muted">创建于 {{ fmtTime(kb.created_at) }}</div>
      </div>
      <div v-if="!loading && !kbs.length" class="kb-card kb-empty">暂无可访问的知识库</div>
    </div>

    <el-dialog v-model="dialog" title="新建知识库" width="480px">
      <el-form ref="formRef" :model="form" :rules="rules" label-position="top">
        <el-form-item label="名称" prop="name"><el-input v-model="form.name" maxlength="128" data-testid="kb-name" /></el-form-item>
        <el-form-item label="描述"><el-input v-model="form.description" type="textarea" :rows="3" maxlength="1000" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="create" data-testid="kb-create">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, type FormInstance } from 'element-plus'
import { Collection, Plus } from '@element-plus/icons-vue'
import { kbApi } from '@/api/endpoints'
import { ApiError } from '@/api/http'
import type { KnowledgeBase } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { fmtTime } from '@/utils/format'

const auth = useAuthStore()
const kbs = ref<KnowledgeBase[]>([])
const loading = ref(false)
const dialog = ref(false)
const saving = ref(false)
const formRef = ref<FormInstance>()
const form = reactive({ name: '', description: '' })
const rules = { name: [{ required: true, message: '请输入名称' }] }

async function load() {
  loading.value = true
  try { kbs.value = await kbApi.list() } finally { loading.value = false }
}
async function create() {
  if (!(await formRef.value?.validate().catch(() => false))) return
  saving.value = true
  try {
    await kbApi.create(form.name, form.description)
    await auth.refreshMe()
    ElMessage.success('知识库已创建')
    dialog.value = false
    form.name = ''
    form.description = ''
    await load()
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '创建失败')
  } finally {
    saving.value = false
  }
}
onMounted(load)
</script>

<style scoped>
.kb-item { cursor: pointer; transition: box-shadow .15s, border-color .15s; }
.kb-item:hover { border-color: var(--kb-color-primary); box-shadow: var(--kb-shadow-pop); }
.kb-item-head { display: flex; align-items: center; gap: var(--kb-space-2); }
.kb-icon { color: var(--kb-color-primary); font-size: 20px; }
.kb-name { font-weight: 600; color: var(--kb-text-primary); flex: 1; }
.desc { min-height: 40px; }
</style>
