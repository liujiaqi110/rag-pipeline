import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

declare module 'vue-router' {
  interface RouteMeta { public?: boolean; admin?: boolean; manager?: boolean; title?: string }
}

const routes: RouteRecordRaw[] = [
  { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { public: true, title: '登录' } },
  {
    path: '/', component: () => import('@/layouts/AppLayout.vue'),
    children: [
      { path: '', redirect: '/chat' },
      { path: 'chat/:sessionId?', name: 'chat', component: () => import('@/views/chat/ChatView.vue'), meta: { title: 'AI 问答' } },
      { path: 'knowledge', name: 'kb-list', component: () => import('@/views/knowledge/KbListView.vue'), meta: { title: '知识管理' } },
      { path: 'knowledge/:kbId', name: 'kb-detail', component: () => import('@/views/knowledge/KbDetailView.vue'), meta: { title: '知识库详情' } },
      { path: 'acl/users', name: 'users', component: () => import('@/views/acl/UsersView.vue'), meta: { admin: true, title: '用户管理' } },
      { path: 'acl/roles', name: 'roles', component: () => import('@/views/acl/RolesView.vue'), meta: { admin: true, title: '角色管理' } },
      { path: 'acl/departments', name: 'departments', component: () => import('@/views/acl/DepartmentsView.vue'), meta: { admin: true, title: '部门管理' } },
      { path: 'admin/system', name: 'system', component: () => import('@/views/admin/SystemView.vue'), meta: { admin: true, title: '系统状态' } },
      { path: 'admin/audit', name: 'audit', component: () => import('@/views/admin/AuditView.vue'), meta: { admin: true, title: '审计日志' } },
      { path: 'profile', name: 'profile', component: () => import('@/views/ProfileView.vue'), meta: { title: '个人账户' } },
      { path: 'forbidden', name: 'forbidden', component: () => import('@/views/ForbiddenView.vue'), meta: { title: '权限不足' } },
      { path: ':pathMatch(.*)*', name: 'not-found', component: () => import('@/views/NotFoundView.vue'), meta: { title: '页面不存在' } },
    ],
  },
]

export const router = createRouter({ history: createWebHistory(), routes })

/** Route guard: login required everywhere except /login; admin pages need is_admin (UI only — the API re-checks). */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (to.meta.public) return true
  if (!(await auth.restore())) return { name: 'login', query: { redirect: to.fullPath } }
  if (to.meta.admin && !auth.isAdmin) return { name: 'forbidden' }
  return true
})

router.afterEach((to) => { document.title = `${to.meta.title ? to.meta.title + ' · ' : ''}企业知识库工作台` })
