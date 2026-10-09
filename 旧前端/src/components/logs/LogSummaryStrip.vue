<script setup lang="ts">
import { computed } from 'vue'
import type { LogSummary, StreamState } from '@/types/auditLogs'
import { PIPELINE_STATUS_TEXT } from '@/constants/auditLogs'
import { formatNumber, formatTime } from '@/utils/format'

const props = defineProps<{
  summary: LogSummary | null
  streamState: StreamState
  paused: boolean
  /** 统计接口最近一次失败，提示数字可能不是最新 */
  failed?: boolean
}>()

const growth = computed(() => props.summary?.todayGrowthRate ?? 0)
const growthText = computed(() => `${growth.value >= 0 ? '+' : ''}${growth.value.toFixed(1)}%`)
const growthClass = computed(() => (growth.value >= 0 ? 'up' : 'down'))

const rateText = computed(() => `${(props.summary?.currentRatePerSecond ?? 0).toFixed(1)} 条/秒`)

const liveText = computed(() => {
  if (props.paused) return '已暂停'
  if (props.streamState === 'open') return '接收中'
  if (props.streamState === 'connecting') return '连接中'
  return '连接已断开'
})

const liveClass = computed(() => {
  if (props.paused) return 'paused'
  return props.streamState === 'open' ? 'online' : 'offline'
})

const pipelineText = computed(() => {
  const status = props.summary?.pipelineStatus
  if (!status) return '状态未知'
  return PIPELINE_STATUS_TEXT[status] ?? status
})
</script>

<template>
  <section class="summary-strip" aria-label="融合日志概览">
    <div class="summary-item">
      <div>
        <div class="summary-label">今日融合日志</div>
        <div class="summary-value">{{ summary ? formatNumber(summary.todayTotal) : '—' }}</div>
      </div>
      <div class="summary-note">
        <strong :class="growthClass">{{ growthText }}</strong>较昨日同期
      </div>
    </div>

    <div class="summary-item">
      <div>
        <div class="summary-label">近 5 分钟接收</div>
        <div class="summary-value">{{ summary ? formatNumber(summary.last5Minutes) : '—' }}</div>
      </div>
      <div class="summary-note">
        <strong>{{ rateText }}</strong>当前吞吐
      </div>
    </div>

    <div class="summary-item">
      <div>
        <div class="summary-label">接入主机</div>
        <div class="summary-value">{{ summary ? summary.managedHosts : '—' }}</div>
      </div>
      <div class="summary-note">
        <strong>{{ summary ? `${summary.activeHosts} 台有日志` : '—' }}</strong>{{ pipelineText }}
      </div>
    </div>

    <div class="summary-item">
      <div>
        <div class="summary-label">实时接收状态</div>
        <div class="live-state" :class="liveClass">
          <span class="dot" :class="liveClass === 'online' ? 'green pulse' : liveClass === 'paused' ? 'orange' : 'red'" />
          <span>{{ liveText }}</span>
        </div>
      </div>
      <div class="summary-note">
        <strong :class="{ warn: failed }">{{ summary?.lastReceivedAt ? formatTime(summary.lastReceivedAt) : '—' }}</strong>
        {{ failed ? '统计刷新失败' : '每 5 秒刷新统计' }}
      </div>
    </div>
  </section>
</template>
