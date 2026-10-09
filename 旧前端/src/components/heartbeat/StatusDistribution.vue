<script setup lang="ts">
import { computed } from 'vue'
import type { MonitorSummary } from '@/types/monitor'
import { formatPercent, formatTime } from '@/utils/format'
import { REFRESH_INTERVAL_MS } from '@/stores/monitor'

const props = defineProps<{
  summary: MonitorSummary | null
  /** 最后一次成功刷新的时间，而不是浏览器当前时间 */
  lastSuccessAt: Date | null
}>()

const total = computed(() => props.summary?.total ?? 0)

/** 环形图按在线 / 延迟 / 离线占比着色，与原型一致 */
const donutStyle = computed(() => {
  const data = props.summary
  if (!data || !data.total) {
    return { background: 'conic-gradient(#dde4ec 0 100%)' }
  }
  const online = clamp((data.online / data.total) * 100)
  const delayedEnd = clamp(online + (data.delayed / data.total) * 100)
  return {
    background: `conic-gradient(var(--green) 0 ${online}%, var(--orange) ${online}% ${delayedEnd}%, var(--red) ${delayedEnd}% 100%)`,
  }
})

function clamp(value: number): number {
  return Math.min(100, Math.max(0, Number.isFinite(value) ? value : 0))
}

const intervalSeconds = REFRESH_INTERVAL_MS / 1000
</script>

<template>
  <article class="panel">
    <div class="panel-header">
      <div class="section-title">运行状态分布</div>
      <span style="color: var(--muted); font-size: 11px">实时</span>
    </div>
    <div class="distribution">
      <div class="donut" :style="donutStyle">
        <div class="donut-label">
          <strong>{{ summary ? formatPercent(summary.onlineRate) : '—' }}</strong>
          <span>在线率</span>
        </div>
      </div>
      <div class="state-list">
        <div class="state-row">
          <span class="dot online" />
          <span>在线</span>
          <strong>{{ summary ? `${summary.online} 台` : '—' }}</strong>
        </div>
        <div class="state-row">
          <span class="dot warning" />
          <span>延迟</span>
          <strong>{{ summary ? `${summary.delayed} 台` : '—' }}</strong>
        </div>
        <div class="state-row">
          <span class="dot offline" />
          <span>离线</span>
          <strong>{{ summary ? `${summary.offline} 台` : '—' }}</strong>
        </div>
        <div class="state-divider" />
        <div class="last-refresh">
          <span>每 {{ intervalSeconds }} 秒确认 · 最近刷新</span>
          <span>{{ lastSuccessAt ? formatTime(lastSuccessAt) : '--:--:--' }}</span>
        </div>
        <div class="last-refresh">
          <span>纳管客户端</span>
          <span>{{ total }} 台</span>
        </div>
      </div>
    </div>
  </article>
</template>
