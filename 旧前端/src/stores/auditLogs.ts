import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { auditLogApi } from '@/api/auditLogs'
import { isRequestCanceled } from '@/api/http'
import {
  DEFAULT_PAGE_SIZE,
  EXPORT_INLINE_LIMIT,
  MAX_PAGE_SIZE,
  OVERVIEW_FIELDS,
  OVERVIEW_KEY,
} from '@/constants/auditLogs'
import type {
  CategoryQuery,
  DynamicLogRecord,
  LogCategory,
  LogField,
  LogSummary,
  OverviewLog,
  OverviewQuery,
  StreamState,
} from '@/types/auditLogs'

const SUMMARY_REFRESH_MS = 5000

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

/**
 * 融合日志管理的全部数据都走这个 store：
 * - 类别与字段元数据来自后端，切换类别只换 fields 与数据，不复制七套表格；
 * - 切换类别时取消上一个未完成的请求（指南 13）；
 * - 查询失败保留上一次成功数据，只提示错误；
 * - 实时日志按指南 8.10 的规则决定"直接插入"还是"进入待刷新计数"。
 */
export const useAuditLogStore = defineStore('auditLogs', () => {
  const categories = ref<LogCategory[]>([])
  const activeCategory = ref<string>(OVERVIEW_KEY)
  const summary = ref<LogSummary | null>(null)
  const records = ref<DynamicLogRecord[]>([])
  const fields = ref<LogField[]>([...OVERVIEW_FIELDS])
  const total = ref(0)
  const loading = ref(false)
  const error = ref('')
  const summaryFailed = ref(false)
  const failureCount = ref(0)
  const lastLoadedAt = ref<Date | null>(null)
  const pendingRealtime = ref<OverviewLog[]>([])
  const realtimePaused = ref(false)
  const streamState = ref<StreamState>('closed')
  const newIds = ref<number[]>([])

  const overviewQuery = reactive<OverviewQuery>({ page: 1, size: DEFAULT_PAGE_SIZE })
  const categoryQuery = reactive<CategoryQuery>({ page: 1, size: DEFAULT_PAGE_SIZE })

  /** 当前进行中的查询，切换类别或重新查询时取消它 */
  let controller: AbortController | null = null
  let summaryTimer: number | undefined
  let summaryBusy = false
  let summaryRunning = false

  const isOverview = computed(() => activeCategory.value === OVERVIEW_KEY)
  const activeCategoryMeta = computed(
    () => categories.value.find((item) => item.key === activeCategory.value) ?? null,
  )
  const searchableFields = computed(() => fields.value.filter((field) => field.searchable))
  const page = computed(() => (isOverview.value ? overviewQuery.page : categoryQuery.page))
  const size = computed(() => (isOverview.value ? overviewQuery.size : categoryQuery.size))
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / Math.max(1, size.value))))
  const degraded = computed(() => failureCount.value >= 2)
  const activeLabel = computed(() =>
    isOverview.value ? '实时融合日志总览' : `${activeCategoryMeta.value?.label ?? ''}日志`,
  )

  function markNew(id: number): void {
    newIds.value = [...newIds.value, id]
    window.setTimeout(() => {
      newIds.value = newIds.value.filter((item) => item !== id)
    }, 1800)
  }

  function abortCurrent(): void {
    controller?.abort()
    controller = null
  }

  // ---------- 汇总统计 ----------

  async function refreshSummary(): Promise<void> {
    if (summaryBusy) return
    summaryBusy = true
    try {
      summary.value = await auditLogApi.getSummary()
      summaryFailed.value = false
    } catch (e) {
      // 统计失败不影响列表，保留上一份数据
      if (!isRequestCanceled(e)) summaryFailed.value = true
    } finally {
      summaryBusy = false
    }
  }

  function scheduleSummary(): void {
    window.clearTimeout(summaryTimer)
    summaryTimer = window.setTimeout(async () => {
      // 页面隐藏时不请求；这里不叠加执行锁之外的并发
      if (!document.hidden) await refreshSummary()
      if (summaryRunning) scheduleSummary()
    }, SUMMARY_REFRESH_MS)
  }

  /** 顶部统计每 5 秒刷新一次（后端按分钟聚合，前端读取很轻） */
  async function startSummaryPolling(): Promise<void> {
    if (summaryRunning) return
    summaryRunning = true
    await refreshSummary()
    scheduleSummary()
  }

  function stopSummaryPolling(): void {
    summaryRunning = false
    window.clearTimeout(summaryTimer)
    summaryTimer = undefined
  }

  // ---------- 类别元数据 ----------

  async function loadCategories(): Promise<void> {
    if (categories.value.length) return
    try {
      const result = await auditLogApi.getCategories()
      categories.value = Array.isArray(result) ? result : []
    } catch (e) {
      if (!isRequestCanceled(e)) {
        error.value = messageOf(e, '日志类别元数据加载失败')
        failureCount.value += 1
      }
    }
  }

  // ---------- 列表查询 ----------

  async function loadOverview(): Promise<void> {
    abortCurrent()
    const current = new AbortController()
    controller = current
    loading.value = true
    try {
      const result = await auditLogApi.getOverview(overviewQuery, current.signal)
      records.value = (result?.items ?? []) as unknown as DynamicLogRecord[]
      total.value = result?.total ?? 0
      fields.value = [...OVERVIEW_FIELDS]
      error.value = ''
      failureCount.value = 0
      lastLoadedAt.value = new Date()
    } catch (e) {
      if (!isRequestCanceled(e)) {
        error.value = messageOf(e, '总览查询失败')
        failureCount.value += 1
      }
    } finally {
      if (controller === current) {
        controller = null
        loading.value = false
      }
    }
  }

  async function loadCategory(): Promise<void> {
    if (isOverview.value) return loadOverview()

    abortCurrent()
    const current = new AbortController()
    controller = current
    const category = activeCategory.value
    loading.value = true
    try {
      const result = await auditLogApi.getCategory(category, categoryQuery, current.signal)
      records.value = result?.records ?? []
      total.value = result?.total ?? 0
      if (result?.fields?.length) fields.value = result.fields
      error.value = ''
      failureCount.value = 0
      lastLoadedAt.value = new Date()
    } catch (e) {
      if (!isRequestCanceled(e)) {
        error.value = messageOf(e, '分类查询失败')
        failureCount.value += 1
      }
    } finally {
      if (controller === current) {
        controller = null
        loading.value = false
      }
    }
  }

  async function loadCurrent(): Promise<void> {
    if (isOverview.value) await loadOverview()
    else await loadCategory()
  }

  async function switchCategory(category: string): Promise<void> {
    if (category === activeCategory.value) return
    activeCategory.value = category
    error.value = ''
    categoryQuery.page = 1
    if (category === OVERVIEW_KEY) {
      fields.value = [...OVERVIEW_FIELDS]
      await loadOverview()
      return
    }
    // 先拿到字段元数据再查询，避免表格列闪一下
    fields.value = categories.value.find((item) => item.key === category)?.fields ?? []
    await loadCategory()
  }

  // ---------- 筛选与分页 ----------

  async function applyOverviewQuery(next: Partial<OverviewQuery>): Promise<void> {
    Object.assign(overviewQuery, next, { page: 1 })
    await loadOverview()
  }

  async function resetOverviewQuery(): Promise<void> {
    Object.assign(overviewQuery, { host: undefined, osType: undefined, startTime: undefined, endTime: undefined, page: 1 })
    await loadOverview()
  }

  async function applyCategoryQuery(next: Partial<CategoryQuery>): Promise<void> {
    Object.assign(categoryQuery, next, { page: 1 })
    await loadCategory()
  }

  async function resetCategoryQuery(): Promise<void> {
    Object.assign(categoryQuery, { field: undefined, operator: undefined, value: undefined, page: 1 })
    await loadCategory()
  }

  async function setPage(next: number): Promise<void> {
    if (next < 1 || next > pageCount.value || next === page.value) return
    if (isOverview.value) overviewQuery.page = next
    else categoryQuery.page = next
    await loadCurrent()
  }

  async function setSize(next: number): Promise<void> {
    const safe = Math.min(MAX_PAGE_SIZE, Math.max(1, next))
    if (safe === size.value) return
    if (isOverview.value) Object.assign(overviewQuery, { size: safe, page: 1 })
    else Object.assign(categoryQuery, { size: safe, page: 1 })
    await loadCurrent()
  }

  // ---------- 实时 ----------

  function hasOverviewFilter(): boolean {
    return Boolean(
      overviewQuery.host || overviewQuery.osType || overviewQuery.startTime || overviewQuery.endTime,
    )
  }

  /**
   * 指南 8.10：
   * - 顶栏统计始终自增；
   * - 不在总览页时只更新统计，不动列表；
   * - 暂停实时、或存在筛选条件时，新日志进入"待刷新"计数，由用户点按钮后重新查询；
   * - 只有第 1 页且没有筛选时才直接插到列表顶部。
   */
  function handleRealtimeLog(log: OverviewLog): void {
    if (summary.value) {
      summary.value = {
        ...summary.value,
        last5Minutes: summary.value.last5Minutes + 1,
        lastReceivedAt: log.ingestedAt,
      }
    }

    if (!isOverview.value) return

    if (realtimePaused.value || hasOverviewFilter()) {
      pendingRealtime.value = [log, ...pendingRealtime.value].slice(0, 500)
      return
    }

    if (overviewQuery.page === 1) {
      records.value = [log as unknown as DynamicLogRecord, ...records.value].slice(0, overviewQuery.size)
      total.value += 1
      markNew(log.id)
    } else {
      pendingRealtime.value = [log, ...pendingRealtime.value].slice(0, 500)
    }
  }

  /** 用户点"收到 N 条新日志"或恢复实时：回到第 1 页重新查询，而不是批量插入缓存 */
  async function applyPendingRealtime(): Promise<void> {
    pendingRealtime.value = []
    if (isOverview.value) overviewQuery.page = 1
    else categoryQuery.page = 1
    await loadCurrent()
  }

  async function toggleRealtime(): Promise<void> {
    realtimePaused.value = !realtimePaused.value
    if (!realtimePaused.value) await applyPendingRealtime()
  }

  function setStreamState(state: StreamState): void {
    streamState.value = state
  }

  // ---------- 导出 ----------

  /** 小结果集：按当前条件把所有页拉齐，交给浏览器生成 CSV */
  async function fetchAllForExport(limit = EXPORT_INLINE_LIMIT): Promise<DynamicLogRecord[]> {
    const pageSize = MAX_PAGE_SIZE
    const collected: DynamicLogRecord[] = []
    const maxPages = Math.ceil(limit / pageSize)
    for (let index = 0; index < maxPages; index += 1) {
      if (isOverview.value) {
        const result = await auditLogApi.getOverview({ ...overviewQuery, page: index + 1, size: pageSize })
        const items = (result?.items ?? []) as unknown as DynamicLogRecord[]
        collected.push(...items)
        if (items.length < pageSize) break
      } else {
        const result = await auditLogApi.getCategory(activeCategory.value, {
          ...categoryQuery,
          page: index + 1,
          size: pageSize,
        })
        const items = result?.records ?? []
        collected.push(...items)
        if (items.length < pageSize) break
      }
    }
    return collected.slice(0, limit)
  }

  // ---------- 生命周期 ----------

  async function initialize(): Promise<void> {
    await Promise.all([loadCategories(), refreshSummary()])
    await loadCurrent()
  }

  return {
    // 状态
    categories,
    activeCategory,
    summary,
    records,
    fields,
    total,
    loading,
    error,
    summaryFailed,
    failureCount,
    lastLoadedAt,
    pendingRealtime,
    realtimePaused,
    streamState,
    newIds,
    overviewQuery,
    categoryQuery,
    // 派生
    isOverview,
    activeCategoryMeta,
    searchableFields,
    page,
    size,
    pageCount,
    degraded,
    activeLabel,
    // 动作
    initialize,
    refreshSummary,
    startSummaryPolling,
    stopSummaryPolling,
    loadCurrent,
    loadOverview,
    loadCategory,
    switchCategory,
    applyOverviewQuery,
    resetOverviewQuery,
    applyCategoryQuery,
    resetCategoryQuery,
    setPage,
    setSize,
    handleRealtimeLog,
    applyPendingRealtime,
    toggleRealtime,
    setStreamState,
    fetchAllForExport,
  }
})
