<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { Download, RefreshCw } from 'lucide-vue-next'
import PageHeader from '@/components/common/PageHeader.vue'
import InlineError from '@/components/common/InlineError.vue'
import SummaryMetrics from '@/components/heartbeat/SummaryMetrics.vue'
import OnlineTrendChart from '@/components/heartbeat/OnlineTrendChart.vue'
import StatusDistribution from '@/components/heartbeat/StatusDistribution.vue'
import ClientFilterBar from '@/components/heartbeat/ClientFilterBar.vue'
import ClientTable from '@/components/heartbeat/ClientTable.vue'
import ClientDetailDialog from '@/components/heartbeat/ClientDetailDialog.vue'
import ClientLogsDialog from '@/components/heartbeat/ClientLogsDialog.vue'
import { useMonitorStore } from '@/stores/monitor'
import { useToastStore } from '@/stores/toast'
import { exportClientsCsv } from '@/utils/export'
import type { ClientQuery, ClientRow } from '@/types/monitor'

/**
 * 页面只负责组合组件：数据在 Pinia store，接口路径在 api 目录，
 * 本文件不包含任何模拟数组与状态判定逻辑。
 */
const store = useMonitorStore()
const toast = useToastStore()

const detailOpen = ref(false)
const detailClient = ref<ClientRow | null>(null)
const logsOpen = ref(false)
const logsClient = ref<ClientRow | null>(null)

function openDetail(row: ClientRow): void {
  detailClient.value = row
  detailOpen.value = true
}

function openLogs(row: ClientRow): void {
  logsClient.value = row
  logsOpen.value = true
}

async function onRefresh(): Promise<void> {
  await store.refresh()
  if (store.error) {
    toast.error(`刷新失败：${store.error}`)
  } else {
    toast.success('已刷新监控数据')
  }
}

async function onQuery(query: ClientQuery): Promise<void> {
  await store.applyQuery(query)
}

function onExport(): void {
  if (!store.clients.length) {
    toast.info('当前没有可导出的客户端数据')
    return
  }
  const count = exportClientsCsv(store.clients)
  toast.success(`已导出 ${count} 条客户端记录`)
}

/** 标签页重新可见时立即刷新一次，隐藏期间不发请求 */
function handleVisibilityChange(): void {
  if (!document.hidden) void store.refresh()
}

onMounted(() => {
  void store.start()
  document.addEventListener('visibilitychange', handleVisibilityChange)
})

onUnmounted(() => {
  store.stop()
  document.removeEventListener('visibilitychange', handleVisibilityChange)
})

/** 已有成功数据时提示用户当前看到的是旧数据 */
const errorHint = computed(() => (store.hasData ? '页面保留的是最后一次成功获取的数据' : ''))

const trendHoursOptions = [6, 12, 24]
</script>

<template>
  <div class="heartbeat-page">
    <PageHeader title="客户端心跳监控" subtitle="汇聚端实时监测 Fluent Bit 客户端，心跳周期 5 秒">
      <button class="btn" type="button" :disabled="store.refreshing" @click="onRefresh">
        <RefreshCw :class="{ spin: store.refreshing }" />刷新
      </button>
      <button class="btn primary" type="button" @click="onExport">
        <Download />导出
      </button>
    </PageHeader>

    <InlineError
      v-if="store.error"
      :message="
        store.degraded
          ? `已连续 ${store.failureCount} 次刷新失败：${store.error}`
          : `数据刷新失败：${store.error}`
      "
      :hint="errorHint"
      :retrying="store.refreshing"
      @retry="onRefresh"
    />

    <SummaryMetrics :summary="store.summary" :loading="store.loading" />

    <section class="charts">
      <OnlineTrendChart
        :points="store.trend"
        :hours="store.trendHours"
        :hours-options="trendHoursOptions"
        @update:hours="store.setTrendHours($event)"
      />
      <StatusDistribution :summary="store.summary" :last-success-at="store.lastSuccessAt" />
    </section>

    <section class="panel table-panel">
      <ClientFilterBar
        :model-value="store.query"
        :total="store.total"
        @query="onQuery"
      />
      <ClientTable
        :rows="store.clients"
        :total="store.total"
        :page="store.query.page"
        :size="store.query.size"
        :loading="store.loading"
        @detail="openDetail"
        @logs="openLogs"
        @update:page="store.setPage($event)"
        @update:size="store.setSize($event)"
      />
    </section>

    <ClientDetailDialog :open="detailOpen" :client="detailClient" @close="detailOpen = false" />
    <ClientLogsDialog :open="logsOpen" :client="logsClient" @close="logsOpen = false" />
  </div>
</template>
