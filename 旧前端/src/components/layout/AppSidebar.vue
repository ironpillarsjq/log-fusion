<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  Activity,
  BadgeCheck,
  ChevronDown,
  Database,
  Fingerprint,
  LayoutDashboard,
  ListFilter,
  ScanSearch,
  ServerCog,
  Settings,
  TriangleAlert,
} from 'lucide-vue-next'
import { useToastStore } from '@/stores/toast'

interface NavItem {
  key: string
  label: string
  icon: Component
  /** 有 to 的走路由，没有的是原型里的占位入口 */
  to?: string
  parent?: boolean
  child?: boolean
}

const navItems: NavItem[] = [
  { key: 'overview', label: '运行概览', icon: LayoutDashboard },
  { key: 'clients', label: '客户端管理', icon: ServerCog, parent: true },
  { key: 'heartbeat', label: '心跳监控', icon: Activity, to: '/clients/heartbeat', child: true },
  { key: 'logs', label: '融合日志管理', icon: Database, to: '/logs' },
  { key: 'log-query', label: '日志查询', icon: ListFilter, to: '/logs', child: true },
  { key: 'evidence', label: '日志完整性存证', icon: Fingerprint, to: '/integrity' },
  { key: 'evidence-verify', label: '存证校验', icon: BadgeCheck, to: '/integrity', child: true },
  { key: 'anomaly', label: '异常检测', icon: ScanSearch },
  { key: 'alerts', label: '告警中心', icon: TriangleAlert },
  { key: 'settings', label: '系统配置', icon: Settings },
]

const emit = defineEmits<{ (e: 'navigate'): void }>()
const toast = useToastStore()
const route = useRoute()

function isActive(item: NavItem): boolean {
  return Boolean(item.to) && route.path === item.to
}

/** 底部状态随页面切换：存证页显示存证服务状态 */
const footer = computed(() =>
  route.path === '/integrity'
    ? { label: '存证服务状态', text: '哈希存证服务运行正常' }
    : { label: '数据接入状态', text: '融合管道运行正常' },
)

function onPlaceholder(item: NavItem): void {
  emit('navigate')
  toast.info(`「${item.label}」尚未实现，当前提供心跳监控、融合日志管理与日志存证三个页面`)
}
</script>

<template>
  <aside class="sidebar">
    <nav class="sidebar-nav" aria-label="主导航">
      <div class="nav-label">平台导航</div>
      <template v-for="item in navItems" :key="item.key">
        <RouterLink
          v-if="item.to"
          class="nav-item"
          :class="{ child: item.child, active: isActive(item) }"
          :to="item.to"
          @click="emit('navigate')"
        >
          <component :is="item.icon" />
          <span>{{ item.label }}</span>
        </RouterLink>
        <button
          v-else
          type="button"
          class="nav-item"
          :class="{ parent: item.parent }"
          @click="onPlaceholder(item)"
        >
          <component :is="item.icon" />
          <span>{{ item.label }}</span>
          <ChevronDown v-if="item.parent" style="width: 17px; height: 17px; margin-left: auto" />
        </button>
      </template>
    </nav>
    <div class="sidebar-footer">
      <div class="nav-label" style="padding: 0 0 10px">{{ footer.label }}</div>
      <div class="collector-status">
        <span class="dot online pulse" />
        <span>{{ footer.text }}</span>
      </div>
    </div>
  </aside>
</template>
