<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import {
  FileSearch,
  LogIn,
  Network,
  Radio,
  ServerCog,
  ShieldAlert,
  SquareTerminal,
  UserCog,
} from 'lucide-vue-next'
import type { LogCategory } from '@/types/auditLogs'
import { OVERVIEW_KEY } from '@/constants/auditLogs'
import { formatNumber } from '@/utils/format'

const props = defineProps<{
  categories: LogCategory[]
  active: string
  /** 总览当前条数 */
  total?: number
}>()

const emit = defineEmits<{ (e: 'change', key: string): void }>()

const ICONS: Record<string, Component> = {
  [OVERVIEW_KEY]: Radio,
  authentication_session: LogIn,
  account_security_change: UserCog,
  process_command_execution: SquareTerminal,
  file_object_access: FileSearch,
  network_ipc_communication: Network,
  system_service_audit_lifecycle: ServerCog,
  security_policy_config_change: ShieldAlert,
}

/** 标签页上的短名只是展示用；后端元数据给了 shortLabel 就用后端的 */
const SHORT_LABELS: Record<string, string> = {
  authentication_session: '认证会话',
  account_security_change: '账号变更',
  process_command_execution: '进程命令',
  file_object_access: '文件对象',
  network_ipc_communication: '网络 IPC',
  system_service_audit_lifecycle: '系统服务',
  security_policy_config_change: '安全策略',
}

interface TabItem {
  key: string
  short: string
  full: string
  count?: number
  icon: Component
}

const tabs = computed<TabItem[]>(() => [
  {
    key: OVERVIEW_KEY,
    short: '总览',
    full: '实时融合日志总览',
    count: props.total,
    icon: ICONS[OVERVIEW_KEY],
  },
  ...props.categories.map((category) => ({
    key: category.key,
    short: category.shortLabel ?? SHORT_LABELS[category.key] ?? category.label,
    full: category.label,
    count: category.count,
    icon: ICONS[category.key] ?? Radio,
  })),
])
</script>

<template>
  <div class="view-tabs" role="tablist" aria-label="日志类别">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      type="button"
      class="view-tab"
      :class="{ active: tab.key === active }"
      role="tab"
      :aria-selected="tab.key === active"
      :title="tab.full"
      @click="emit('change', tab.key)"
    >
      <component :is="tab.icon" />
      {{ tab.short }}
      <span v-if="typeof tab.count === 'number'" class="tab-count">{{ formatNumber(tab.count) }}</span>
    </button>
  </div>
</template>
