import { createRouter, createWebHistory } from 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
  }
}

/**
 * 两个页面：客户端心跳监控、融合日志管理。
 * 侧栏用 RouterLink 跳转，不再使用静态原型的 index.html / logs.html。
 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/clients/heartbeat' },
    {
      path: '/clients/heartbeat',
      name: 'client-heartbeat',
      component: () => import('@/views/ClientHeartbeatView.vue'),
      meta: { title: '心跳监控' },
    },
    {
      path: '/logs',
      name: 'audit-logs',
      component: () => import('@/views/AuditLogManagementView.vue'),
      meta: { title: '融合日志管理' },
    },
    {
      path: '/integrity',
      name: 'audit-evidence',
      component: () => import('@/views/AuditEvidenceView.vue'),
      meta: { title: '日志完整性存证' },
    },
    // 未匹配的地址回到默认页，避免刷新后白屏
    { path: '/:pathMatch(.*)*', redirect: '/clients/heartbeat' },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} - 日志汇聚与审计分析平台` : '日志汇聚与审计分析平台'
})
