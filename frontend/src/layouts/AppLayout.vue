<template>
  <el-container class="layout">
    <el-aside class="sidebar" width="var(--kb-sidebar-width)">
      <div class="brand">
        <div class="logo">KB</div>
        <div>
          <div class="brand-name">企业知识库</div>
          <div class="brand-sub">ACL · RAG 工作台</div>
        </div>
      </div>
      <el-menu :default-active="active" router class="menu">
        <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon><span>AI 问答</span></el-menu-item>
        <el-menu-item index="/knowledge"><el-icon><Files /></el-icon><span>知识管理</span></el-menu-item>
        <el-sub-menu v-if="auth.managesAny" index="ops">
          <template #title><el-icon><DataAnalysis /></el-icon><span>知识运营</span></template>
          <el-menu-item index="/ops/dashboard" data-testid="menu-ops-dashboard">运营看板</el-menu-item>
          <el-menu-item index="/ops/faq" data-testid="menu-faq">FAQ 候选审核</el-menu-item>
          <el-menu-item index="/ops/faq-published">已发布 FAQ</el-menu-item>
          <el-menu-item index="/ops/gaps" data-testid="menu-gaps">知识缺口</el-menu-item>
          <el-menu-item index="/ops/tasks">知识补充任务</el-menu-item>
        </el-sub-menu>
        <el-sub-menu v-if="auth.isAdmin" index="acl">
          <template #title><el-icon><Lock /></el-icon><span>权限管理</span></template>
          <el-menu-item index="/acl/users">用户</el-menu-item>
          <el-menu-item index="/acl/roles">角色</el-menu-item>
          <el-menu-item index="/acl/departments">部门</el-menu-item>
        </el-sub-menu>
        <el-sub-menu v-if="auth.isAdmin" index="platform">
          <template #title><el-icon><Monitor /></el-icon><span>平台管理</span></template>
          <el-menu-item index="/admin/system">系统状态</el-menu-item>
          <el-menu-item index="/admin/audit">审计日志</el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <div class="crumb">{{ title }}</div>
        <el-dropdown trigger="click" @command="onCommand">
          <span class="user" data-testid="user-menu">
            <el-avatar :size="28" class="avatar">{{ initials }}</el-avatar>
            <span>{{ auth.me?.display_name || auth.me?.username }}</span>
            <el-tag v-if="auth.isAdmin" size="small" type="warning" effect="plain">管理员</el-tag>
            <el-icon><ArrowDown /></el-icon>
          </span>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="profile">个人账户</el-dropdown-item>
              <el-dropdown-item command="logout" divided data-testid="logout">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowDown, ChatDotRound, DataAnalysis, Files, Lock, Monitor } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const active = computed(() => '/' + (route.path.split('/')[1] || 'chat') + (['/acl', '/admin', '/ops'].some((p) => route.path.startsWith(p)) ? '/' + route.path.split('/')[2] : ''))
const title = computed(() => route.meta.title || '')
const initials = computed(() => (auth.me?.display_name || auth.me?.username || '?').slice(0, 1).toUpperCase())

async function onCommand(cmd: string) {
  if (cmd === 'profile') router.push('/profile')
  if (cmd === 'logout') {
    await auth.logout()
    router.replace('/login')
  }
}
</script>

<style scoped>
.layout { height: 100vh; }
.sidebar { background: var(--kb-bg-sidebar); border-right: 1px solid var(--kb-border); display: flex; flex-direction: column; }
.brand { height: var(--kb-header-height); display: flex; align-items: center; gap: var(--kb-space-3); padding: 0 var(--kb-space-5); border-bottom: 1px solid var(--kb-border); }
.logo { width: 32px; height: 32px; border-radius: var(--kb-radius-md); background: var(--kb-color-primary); color: #fff; font-weight: 700; display: flex; align-items: center; justify-content: center; font-size: 13px; }
.brand-name { font-weight: 600; color: var(--kb-text-primary); }
.brand-sub { font-size: var(--kb-font-xs); color: var(--kb-text-secondary); }
.menu { border-right: none; padding: var(--kb-space-2); }
.header { height: var(--kb-header-height); background: var(--kb-bg-card); border-bottom: 1px solid var(--kb-border); display: flex; align-items: center; justify-content: space-between; padding: 0 var(--kb-space-6); }
.crumb { font-weight: 600; color: var(--kb-text-primary); }
.user { display: inline-flex; align-items: center; gap: var(--kb-space-2); cursor: pointer; color: var(--kb-text-regular); }
.avatar { background: var(--kb-color-primary-soft); color: var(--kb-color-primary); font-weight: 600; }
.main { padding: 0; overflow: auto; }
</style>
