<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Server } from 'lucide-vue-next'
import BaseModal from '@/components/common/BaseModal.vue'
import StatusTag from '@/components/common/StatusTag.vue'
import { monitorApi } from '@/api/monitor'
import type { ClientDetail, ClientRow } from '@/types/monitor'
import { formatDateTime, formatDelaySeconds, formatNumber, formatPercent } from '@/utils/format'

const props = defineProps<{
  open: boolean
  /** 列表中被点击的那一行，先渲染它，再用详情接口补齐 */
  client: ClientRow | null
}>()

const emit = defineEmits<{ (e: 'close'): void }>()

const detail = ref<ClientDetail | null>(null)
const loading = ref(false)
const error = ref('')

watch(
  () => [props.open, props.client?.clientId] as const,
  async ([open, clientId]) => {
    if (!open || !clientId) return
    detail.value = null
    error.value = ''
    loading.value = true
    try {
      detail.value = await monitorApi.getDetail(clientId)
    } catch (e) {
      error.value = e instanceof Error ? e.message : '详情加载失败'
    } finally {
      loading.value = false
    }
  },
  { immediate: true },
)

/** 详情接口失败时退回列表行数据，弹窗不至于空白 */
const display = computed(() => {
  const base = detail.value ?? props.client
  if (!base) return null
  return {
    ...base,
    bufferedChunks: detail.value?.bufferedChunks ?? null,
    outputHealthy: detail.value?.outputHealthy ?? null,
    createdAt: detail.value?.createdAt ?? null,
    updatedAt: detail.value?.updatedAt ?? null,
    cpuUsagePercent: detail.value?.cpuUsagePercent ?? null,
    memoryUsageMb: detail.value?.memoryUsageMb ?? null,
  }
})

const outputHealthyText = computed(() => {
  const value = display.value?.outputHealthy
  if (value === null || value === undefined) return '-'
  return value ? '正常' : '异常'
})
</script>

<template>
  <BaseModal :open="open" :icon="Server" wide @close="emit('close')">
    <template #title>{{ display ? display.serverName : '' }} 客户端详情</template>

    <div v-if="loading && !display" class="dialog-state">详情加载中…</div>
    <div v-else-if="!display" class="dialog-state">未选择客户端</div>
    <template v-else>
      <div v-if="error" class="inline-error" role="alert" style="margin-bottom: 12px">
        <span class="message">详情接口失败：{{ error }}<span class="hint">（以下为列表中最新的数据）</span></span>
      </div>
      <div class="detail-grid">
        <div class="label">客户端 ID</div>
        <div>{{ display.clientId }}</div>
        <div class="label">IP 地址</div>
        <div>{{ display.ipAddress }}</div>

        <div class="label">系统类型</div>
        <div>{{ display.osType }}</div>
        <div class="label">系统版本</div>
        <div>{{ display.osVersion }}</div>

        <div class="label">Fluent Bit</div>
        <div>{{ display.fluentBitVersion || '-' }}</div>
        <div class="label">心跳周期</div>
        <div>{{ display.heartbeatIntervalSec }} 秒</div>

        <div class="label">心跳状态</div>
        <div><StatusTag kind="heartbeat" :status="display.heartbeatStatus" /></div>
        <div class="label">心跳延迟</div>
        <div>{{ formatDelaySeconds(display.heartbeatDelaySec) }}</div>

        <div class="label">日志状态</div>
        <div><StatusTag kind="log" :status="display.logStatus" /></div>
        <div class="label">最后日志</div>
        <div>{{ formatDateTime(display.lastLogAt) }}</div>

        <div class="label">近 5 分钟日志量</div>
        <div>{{ formatNumber(display.logsLast5m) }} 条</div>
        <div class="label">缓冲区积压</div>
        <div>{{ display.bufferedChunks === null ? '-' : `${display.bufferedChunks} 块` }}</div>

        <div class="label">CPU 使用率</div>
        <div>{{ formatPercent(display.cpuUsagePercent) }}</div>
        <div class="label">内存占用</div>
        <div>{{ display.memoryUsageMb === null ? '-' : `${formatNumber(display.memoryUsageMb)} MB` }}</div>

        <div class="label">输出通道</div>
        <div>{{ outputHealthyText }}</div>
        <div class="label">最近心跳</div>
        <div>{{ formatDateTime(display.lastHeartbeatAt) }}</div>

        <div class="label">记录更新</div>
        <div>{{ formatDateTime(display.updatedAt) }}</div>
        <div class="label">记录创建</div>
        <div>{{ formatDateTime(display.createdAt) }}</div>
      </div>
    </template>
  </BaseModal>
</template>
