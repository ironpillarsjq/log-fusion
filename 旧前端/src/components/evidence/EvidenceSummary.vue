<script setup lang="ts">
import { computed } from 'vue'
import type { EvidenceSummary } from '@/types/evidence'
import { formatNumber, formatPercent } from '@/utils/format'

/** 顶部四项统计（指南 7.2） */
const props = defineProps<{ summary: EvidenceSummary | null }>()

const hashedRate = computed(() => {
  const data = props.summary
  if (!data || !data.todayRawTotal) return null
  return (data.hashedTotal / data.todayRawTotal) * 100
})

const validRate = computed(() => {
  const data = props.summary
  if (!data || !data.hashedTotal) return null
  return (data.validTotal / data.hashedTotal) * 100
})
</script>

<template>
  <section class="evidence-summary" aria-label="存证统计">
    <div class="evidence-metric">
      <div>
        <div class="evidence-metric-label">今日原始日志</div>
        <div class="evidence-metric-value">{{ summary ? formatNumber(summary.todayRawTotal) : '—' }}</div>
      </div>
      <div class="evidence-metric-note">
        <strong>{{ hashedRate === null ? '—' : formatPercent(hashedRate) }}</strong>已计算哈希
      </div>
    </div>

    <div class="evidence-metric">
      <div>
        <div class="evidence-metric-label">已完成哈希</div>
        <div class="evidence-metric-value">{{ summary ? formatNumber(summary.hashedTotal) : '—' }}</div>
      </div>
      <div class="evidence-metric-note">
        <strong>{{ summary?.hashAlgorithm || 'SHA-256' }}</strong>
        {{ summary ? `${formatNumber(summary.pendingTotal)} 条待校验` : '—' }}
      </div>
    </div>

    <div class="evidence-metric">
      <div>
        <div class="evidence-metric-label">校验正常</div>
        <div class="evidence-metric-value">{{ summary ? formatNumber(summary.validTotal) : '—' }}</div>
      </div>
      <div class="evidence-metric-note">
        <strong>{{ validRate === null ? '—' : formatPercent(validRate) }}</strong>最近校验通过率
      </div>
    </div>

    <div class="evidence-metric">
      <div>
        <div class="evidence-metric-label">校验异常</div>
        <div class="evidence-metric-value">{{ summary ? formatNumber(summary.invalidTotal) : '—' }}</div>
      </div>
      <div class="evidence-metric-note">
        <strong class="bad">需要复核</strong>
        {{ summary ? `含哈希不一致与原始日志缺失，另有 ${formatNumber(summary.pendingTotal)} 条待校验` : '—' }}
      </div>
    </div>
  </section>
</template>
