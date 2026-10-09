<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { Bell, ChevronDown, Menu, ShieldCheck } from 'lucide-vue-next'
import { formatDateTime } from '@/utils/format'
import { useToastStore } from '@/stores/toast'

const props = defineProps<{ menuOpen: boolean }>()
defineEmits<{ (e: 'toggle-menu'): void }>()

const toast = useToastStore()

/** 顶栏时钟：每秒走一次，卸载时清理定时器 */
const now = ref(new Date())
let timer: number | undefined

onMounted(() => {
  timer = window.setInterval(() => {
    now.value = new Date()
  }, 1000)
})

onBeforeUnmount(() => {
  window.clearInterval(timer)
})

const clockText = computed(() => formatDateTime(now.value))
</script>

<template>
  <header class="topbar">
    <div class="brand">
      <button
        class="icon-btn mobile-menu"
        type="button"
        :aria-expanded="props.menuOpen"
        aria-label="打开导航"
        title="打开导航"
        @click="$emit('toggle-menu')"
      >
        <Menu />
      </button>
      <div class="brand-mark"><ShieldCheck /></div>
      <strong>日志汇聚与审计分析平台</strong>
    </div>
    <div class="top-actions">
      <span class="clock">{{ clockText }}</span>
      <button
        class="icon-btn"
        type="button"
        aria-label="告警通知"
        title="告警通知"
        @click="toast.info('告警中心尚未接入，当前仅提供心跳监控页面')"
      >
        <Bell />
      </button>
      <div class="user">
        <span class="avatar">管</span>
        <span>系统管理员</span>
        <ChevronDown style="width: 14px" />
      </div>
    </div>
  </header>
</template>
