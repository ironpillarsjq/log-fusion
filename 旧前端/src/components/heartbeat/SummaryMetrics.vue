<script setup lang="ts">
import { computed } from 'vue'
import { Database, Server } from 'lucide-vue-next'
import type { MonitorSummary } from '@/types/monitor'
import { HEARTBEAT_THRESHOLDS } from '@/constants/status'
import { formatNumber, formatPercent } from '@/utils/format'

/**
 * 顶部 5 张概览卡片。数据全部来自 GET /api/v1/monitor/summary，
 * 组件内部不再单独请求汇总接口。
 */
const props = defineProps<{
  summary: MonitorSummary | null
  /** 首次加载时显示骨架屏 */
  loading?: boolean
}>()

const summary = computed(() => props.summary)
const showSkeleton = computed(() => props.loading === true && props.summary === null)
</script>

<template>
  <section class="metrics" aria-label="客户端概览">
    <article class="metric" style="--accent: #1769c2">
      <div class="metric-top">
        <span>客户端总数</span>
        <Server style="width: 18px; height: 18px" />
      </div>
      <div class="metric-value">
        <span v-if="showSkeleton" class="skeleton value" />
        <template v-else>{{ summary ? summary.total : '—' }}</template>
      </div>
      <div class="metric-foot">
        <span>已纳管服务器</span>
        <strong v-if="summary">Linux {{ summary.linuxCount }} / Windows {{ summary.windowsCount }}</strong>
        <strong v-else>—</strong>
      </div>
    </article>

    <article class="metric" style="--accent: #168a5b">
      <div class="metric-top">
        <span>在线客户端</span>
        <span class="dot online" />
      </div>
      <div class="metric-value">
        <span v-if="showSkeleton" class="skeleton value" />
        <template v-else>{{ summary ? summary.online : '—' }}</template>
      </div>
      <div class="metric-foot">
        <span>{{ HEARTBEAT_THRESHOLDS.online }} 秒内收到心跳</span>
        <strong v-if="summary">在线率 {{ formatPercent(summary.onlineRate) }}</strong>
        <strong v-else>—</strong>
      </div>
    </article>

    <article class="metric" style="--accent: #c66a13">
      <div class="metric-top">
        <span>延迟客户端</span>
        <span class="dot warning" />
      </div>
      <div class="metric-value">
        <span v-if="showSkeleton" class="skeleton value" />
        <template v-else>{{ summary ? summary.delayed : '—' }}</template>
      </div>
      <div class="metric-foot">
        <span>{{ HEARTBEAT_THRESHOLDS.online }}–{{ HEARTBEAT_THRESHOLDS.delayed }} 秒未收到心跳</span>
        <strong>建议关注</strong>
      </div>
    </article>

    <article class="metric" style="--accent: #c8322b">
      <div class="metric-top">
        <span>离线客户端</span>
        <span class="dot offline" />
      </div>
      <div class="metric-value">
        <span v-if="showSkeleton" class="skeleton value" />
        <template v-else>{{ summary ? summary.offline : '—' }}</template>
      </div>
      <div class="metric-foot">
        <span>超过 {{ HEARTBEAT_THRESHOLDS.delayed }} 秒无心跳</span>
        <strong>需及时排查</strong>
      </div>
    </article>

    <article class="metric" style="--accent: #0d77b7">
      <div class="metric-top">
        <span>近 5 分钟日志量</span>
        <Database style="width: 18px; height: 18px" />
      </div>
      <div class="metric-value">
        <span v-if="showSkeleton" class="skeleton value" />
        <template v-else-if="summary">
          {{ formatNumber(summary.logsLast5m) }}<small>条</small>
        </template>
        <template v-else>—</template>
      </div>
      <div class="metric-foot">
        <span v-if="summary">{{ summary.normalLogClients }} 台日志正常</span>
        <span v-else>—</span>
        <strong>心跳与日志独立判定</strong>
      </div>
    </article>
  </section>
</template>
