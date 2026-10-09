<script setup lang="ts">
import { computed } from 'vue'
import { heartbeatMeta, logStatusMeta } from '@/constants/status'
import type { HeartbeatStatus, LogStatus } from '@/types/monitor'

/**
 * 统一的状态标签：
 * - kind="heartbeat"：在线 / 延迟 / 离线（带圆点）
 * - kind="log"：正常 / 无日志 / 积压 / 中断（带底色）
 * 中文文案与颜色只在这里映射，接口只返回英文枚举。
 */
const props = withDefaults(
  defineProps<{
    kind: 'heartbeat' | 'log'
    status: HeartbeatStatus | LogStatus | string | null | undefined
    /** 展示「在线」这类短文案，还是「2 秒前」这类延迟文案 */
    suffix?: string
  }>(),
  { suffix: '' },
)

const heartbeat = computed(() => heartbeatMeta(props.status))

const logMeta = computed(() => {
  if (!props.status) return { text: '未知', className: 'log-status silent' }
  return logStatusMeta[props.status as LogStatus] ?? { text: '未知', className: 'log-status silent' }
})
</script>

<template>
  <span v-if="kind === 'heartbeat'" :class="heartbeat.className">
    <span :class="heartbeat.dotClass" />
    {{ heartbeat.text }}<template v-if="suffix">{{ suffix }}</template>
  </span>
  <span v-else :class="logMeta.className">{{ logMeta.text }}</span>
</template>
