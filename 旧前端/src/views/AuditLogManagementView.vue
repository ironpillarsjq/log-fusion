<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Download, Pause, Play } from 'lucide-vue-next'
import PageHeader from '@/components/common/PageHeader.vue'
import InlineError from '@/components/common/InlineError.vue'
import LogSummaryStrip from '@/components/logs/LogSummaryStrip.vue'
import LogCategoryTabs from '@/components/logs/LogCategoryTabs.vue'
import OverviewFilter from '@/components/logs/OverviewFilter.vue'
import CategoryFieldFilter from '@/components/logs/CategoryFieldFilter.vue'
import DynamicLogTable from '@/components/logs/DynamicLogTable.vue'
import LogDetailDialog from '@/components/logs/LogDetailDialog.vue'
import NewLogNotice from '@/components/logs/NewLogNotice.vue'
import { auditLogApi } from '@/api/auditLogs'
import { useLogStream } from '@/composables/useLogStream'
import { EXPORT_INLINE_LIMIT, OVERVIEW_KEY } from '@/constants/auditLogs'
import { useAuditLogStore } from '@/stores/auditLogs'
import { useToastStore } from '@/stores/toast'
import type { CategoryQuery, DynamicLogRecord, LogField, OverviewQuery } from '@/types/auditLogs'
import { buildCsv, downloadBlob, downloadCsv, timestampSuffix } from '@/utils/csv'
import { formatDateTime, formatNumber, formatTime } from '@/utils/format'

/**
 * 融合日志管理页面。
 * 页面只负责组合组件：数据在 Pinia store，接口路径在 api 目录，
 * 类别与字段元数据全部来自后端，本文件不维护第二套字段。
 */
const store = useAuditLogStore()
const toast = useToastStore()
const route = useRoute()
const router = useRouter()

const detailOpen = ref(false)
const detailCategory = ref('')
const detailLabel = ref('')
const detailRecordId = ref<number | null>(null)
const exporting = ref(false)

// 实时日志流：连接状态反映到摘要条，重连成功后补一次当前页
useLogStream({
  onLog: (log) => store.handleRealtimeLog(log),
  onStateChange: (state) => store.setStreamState(state),
  onReconnect: () => void store.loadCurrent(),
})

onMounted(async () => {
  await store.initialize()
  // 支持 /logs?category=xxx 深链，刷新或分享后仍停留在同一类别
  const requested = typeof route.query.category === 'string' ? route.query.category : ''
  if (requested && store.categories.some((item) => item.key === requested)) {
    await store.switchCategory(requested)
  }
  void store.startSummaryPolling()
})

onUnmounted(() => {
  store.stopSummaryPolling()
})

const errorHint = computed(() =>
  store.lastLoadedAt
    ? `页面保留的是上一次成功查询的数据（${formatDateTime(store.lastLoadedAt)}）`
    : '请确认后端服务与数据库可用',
)

const tableTitle = computed(() => store.activeLabel)

const detailFields = computed<LogField[]>(() =>
  store.isOverview
    ? store.fields
    : (store.activeCategoryMeta?.fields ?? store.fields),
)

function onSwitchCategory(key: string): void {
  // 把当前类别写进地址栏，便于分享与刷新
  void router.replace({ query: key === OVERVIEW_KEY ? {} : { category: key } })
  void store.switchCategory(key)
}

function onOverviewQuery(next: OverviewQuery): void {
  void store.applyOverviewQuery(next)
}

function onCategoryQuery(next: CategoryQuery): void {
  void store.applyCategoryQuery(next)
}

function openDetail(row: DynamicLogRecord): void {
  if (store.isOverview) {
    const rowId = Number(row.categoryRowId)
    if (!Number.isFinite(rowId)) return
    detailCategory.value = String(row.category ?? '')
    detailLabel.value = String(row.categoryLabel ?? '')
    detailRecordId.value = rowId
  } else {
    const rowId = Number(row.id)
    if (!Number.isFinite(rowId)) return
    detailCategory.value = store.activeCategory
    detailLabel.value = store.activeCategoryMeta?.label ?? ''
    detailRecordId.value = rowId
  }
  detailOpen.value = true
}

// ---------- 导出 ----------

function cellText(field: LogField, row: Record<string, unknown>): string {
  const raw = row[field.prop ?? field.key]
  if (raw === null || raw === undefined) return ''
  if (field.key === 'event_time' || field.type === 'DATETIME') return formatDateTime(String(raw))
  return String(raw)
}

function exportFileName(): string {
  const name = store.isOverview ? OVERVIEW_KEY : store.activeCategory
  return `融合日志-${name}-${timestampSuffix()}.csv`
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

/**
 * 导出文件地址归一化：
 * 后端可能返回完整 URL，也可能返回相对路径（带或不带 /api 前缀）。
 * 这里统一成"相对 API 前缀"的形式，交给统一的 http 实例下载，避免把后端地址写死。
 */
function resolveDownloadUrl(raw: string | undefined, taskId: string): string {
  if (!raw) return `/v1/logs/exports/${encodeURIComponent(taskId)}/download`
  if (/^https?:\/\//i.test(raw)) return raw
  return raw.startsWith('/api/') ? raw.slice(4) : raw
}

/** 大结果集：创建异步导出任务并轮询（指南 10.2） */
async function exportViaTask(): Promise<void> {
  const task = await auditLogApi.createExport({
    category: store.isOverview ? OVERVIEW_KEY : store.activeCategory,
    host: store.isOverview ? store.overviewQuery.host : undefined,
    osType: store.isOverview ? store.overviewQuery.osType : undefined,
    field: store.isOverview ? undefined : store.categoryQuery.field,
    operator: store.isOverview ? undefined : store.categoryQuery.operator,
    value: store.isOverview ? undefined : store.categoryQuery.value,
    startTime: store.isOverview ? store.overviewQuery.startTime : store.categoryQuery.startTime,
    endTime: store.isOverview ? store.overviewQuery.endTime : store.categoryQuery.endTime,
  })

  toast.info('结果集较大，已创建导出任务，正在生成文件…')

  for (let attempt = 0; attempt < 80; attempt += 1) {
    await delay(1500)
    const status = await auditLogApi.getExportTask(task.taskId)
    if (status.status === 'COMPLETED') {
      const blob = await auditLogApi.downloadExport(resolveDownloadUrl(status.downloadUrl, task.taskId))
      downloadBlob(blob, exportFileName())
      toast.success(`导出完成，共 ${formatNumber(status.rowCount ?? 0)} 条`)
      return
    }
    if (status.status === 'FAILED') {
      throw new Error(status.message || '导出任务失败')
    }
  }
  throw new Error('导出任务超时，请稍后重试')
}

async function exportCurrent(): Promise<void> {
  if (!store.total) {
    toast.info('当前没有可导出的数据')
    return
  }
  exporting.value = true
  try {
    if (store.total <= EXPORT_INLINE_LIMIT) {
      const rows = await store.fetchAllForExport()
      const headers = store.fields.map((field) => `${field.key}（${field.label}）`)
      const csv = buildCsv(
        headers,
        rows.map((row) => store.fields.map((field) => cellText(field, row))),
      )
      downloadCsv(csv, exportFileName())
      toast.success(`已导出 ${formatNumber(rows.length)} 条融合日志`)
      return
    }
    await exportViaTask()
  } catch (e) {
    toast.error(e instanceof Error ? e.message : '导出失败')
  } finally {
    exporting.value = false
  }
}
</script>

<template>
  <div class="audit-logs-page">
    <PageHeader
      title="融合日志管理"
      subtitle="统一检索七大类审计日志，实时查看融合结果"
      parent="融合日志管理"
      current="日志查询"
    >
      <button class="btn" type="button" @click="store.toggleRealtime()">
        <component :is="store.realtimePaused ? Play : Pause" />
        {{ store.realtimePaused ? '继续实时' : '暂停实时' }}
      </button>
      <button class="btn primary" type="button" :disabled="exporting" @click="exportCurrent">
        <Download />{{ exporting ? '导出中…' : '导出当前结果' }}
      </button>
    </PageHeader>

    <LogSummaryStrip
      :summary="store.summary"
      :stream-state="store.streamState"
      :paused="store.realtimePaused"
      :failed="store.summaryFailed"
    />

    <InlineError
      v-if="store.error"
      :message="store.degraded ? `已连续 ${store.failureCount} 次查询失败：${store.error}` : `查询失败：${store.error}`"
      :hint="errorHint"
      :retrying="store.loading"
      @retry="store.loadCurrent()"
    />

    <section class="panel workspace">
      <LogCategoryTabs
        :categories="store.categories"
        :active="store.activeCategory"
        :total="store.isOverview ? store.total : undefined"
        @change="onSwitchCategory"
      />

      <OverviewFilter
        v-if="store.isOverview"
        :model-value="store.overviewQuery"
        :loading="store.loading"
        @query="onOverviewQuery"
      />
      <CategoryFieldFilter
        v-else
        :model-value="store.categoryQuery"
        :fields="store.searchableFields"
        :loading="store.loading"
        @query="onCategoryQuery"
      />

      <div class="table-head">
        <div class="section-title">
          {{ tableTitle }}
          <span class="table-meta">共 {{ formatNumber(store.total) }} 条</span>
        </div>
        <div class="table-tools">
          <NewLogNotice :count="store.pendingRealtime.length" @refresh="store.applyPendingRealtime()" />
          <span class="data-time">
            数据更新时间：<b>{{ store.lastLoadedAt ? formatTime(store.lastLoadedAt) : '--:--:--' }}</b>
          </span>
        </div>
      </div>

      <DynamicLogTable
        :fields="store.fields"
        :records="store.records"
        :total="store.total"
        :page="store.page"
        :size="store.size"
        :loading="store.loading"
        :new-ids="store.newIds"
        @detail="openDetail"
        @update:page="store.setPage($event)"
        @update:size="store.setSize($event)"
      />
    </section>

    <LogDetailDialog
      :open="detailOpen"
      :category="detailCategory"
      :category-label="detailLabel"
      :record-id="detailRecordId"
      :fields="detailFields"
      @close="detailOpen = false"
    />
  </div>
</template>
