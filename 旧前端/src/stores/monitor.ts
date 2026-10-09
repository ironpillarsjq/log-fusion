import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { monitorApi } from '@/api/monitor'
import type { ClientQuery, ClientRow, MonitorSummary, TrendPoint } from '@/types/monitor'

/** 心跳周期 5 秒，页面按同一节奏查询 */
export const REFRESH_INTERVAL_MS = 5000
/** 趋势数据变化慢，没必要每 5 秒拉一次 */
export const TREND_REFRESH_INTERVAL_MS = 60_000

const DEFAULT_QUERY: ClientQuery = { page: 1, size: 20 }

/**
 * 页面数据全部走这个 store：
 * - 页面与组件不各自请求同一个汇总接口；
 * - 5 秒刷新使用递归 setTimeout + 执行锁，接口变慢时不会叠加并发请求；
 * - 刷新失败保留上一份成功数据，只提示错误，不清空页面。
 */
export const useMonitorStore = defineStore('monitor', () => {
  const summary = ref<MonitorSummary | null>(null)
  const clients = ref<ClientRow[]>([])
  const total = ref(0)
  const trend = ref<TrendPoint[]>([])
  const trendHours = ref(24)
  const query = reactive<ClientQuery>({ ...DEFAULT_QUERY })

  /** 首次加载（用于骨架屏） */
  const loading = ref(false)
  /** 任意一次刷新进行中（用于刷新按钮转圈） */
  const refreshing = ref(false)
  const error = ref('')
  const failureCount = ref(0)
  const lastSuccessAt = ref<Date | null>(null)
  const loaded = ref(false)

  let timer: number | undefined
  /** 执行锁：慢请求不会产生并发刷新 */
  let busy = false
  let running = false
  let lastTrendAt = 0

  /** 连续失败 2 次以上，页面给出更明显的降级提示 */
  const degraded = computed(() => failureCount.value >= 2)
  const hasData = computed(() => loaded.value)

  async function refresh(forceTrend = false): Promise<void> {
    if (busy) return
    busy = true
    refreshing.value = true

    const needTrend =
      forceTrend || trend.value.length === 0 || Date.now() - lastTrendAt >= TREND_REFRESH_INTERVAL_MS

    try {
      const [summaryResult, clientResult, trendResult] = await Promise.all([
        monitorApi.getSummary(),
        monitorApi.getClients(query),
        needTrend ? monitorApi.getTrend(trendHours.value) : Promise.resolve(null),
      ])

      summary.value = summaryResult ?? null
      clients.value = Array.isArray(clientResult?.items) ? clientResult.items : []
      total.value = clientResult?.total ?? clients.value.length
      if (trendResult) {
        trend.value = trendResult
        lastTrendAt = Date.now()
      }

      error.value = ''
      failureCount.value = 0
      lastSuccessAt.value = new Date()
      loaded.value = true
    } catch (e) {
      failureCount.value += 1
      error.value = e instanceof Error ? e.message : '刷新失败'
      // 保留上一份成功数据，页面继续可用
    } finally {
      busy = false
      refreshing.value = false
    }
  }

  function scheduleNext(): void {
    window.clearTimeout(timer)
    timer = window.setTimeout(async () => {
      // 标签页不可见时跳过这一轮，避免无意义的请求
      if (!document.hidden) await refresh()
      if (running) scheduleNext()
    }, REFRESH_INTERVAL_MS)
  }

  /** 首屏立即加载，随后每 5 秒刷新一次 */
  async function start(): Promise<void> {
    if (running) return
    running = true
    loading.value = !loaded.value
    try {
      await refresh()
    } finally {
      loading.value = false
    }
    scheduleNext()
  }

  /** 组件卸载后必须停止定时器 */
  function stop(): void {
    running = false
    window.clearTimeout(timer)
    timer = undefined
  }

  /** 应用筛选条件（页码回到第 1 页）并立即刷新 */
  async function applyQuery(next: Partial<ClientQuery>): Promise<void> {
    Object.assign(query, { ...next, page: next.page ?? 1 })
    await refresh()
  }

  async function resetQuery(): Promise<void> {
    Object.assign(query, DEFAULT_QUERY)
    await refresh()
  }

  async function setPage(page: number): Promise<void> {
    if (page < 1 || page === query.page) return
    query.page = page
    await refresh()
  }

  async function setSize(size: number): Promise<void> {
    if (size === query.size) return
    query.size = size
    query.page = 1
    await refresh()
  }

  /** 标签页重新可见时立即刷新一次 */
  async function refreshIfVisible(): Promise<void> {
    if (!document.hidden) await refresh()
  }

  /** 切换趋势时间范围并立即重新拉取趋势数据 */
  async function setTrendHours(hours: number): Promise<void> {
    if (hours === trendHours.value) return
    trendHours.value = hours
    lastTrendAt = 0
    await refresh(true)
  }

  return {
    // 状态
    summary,
    clients,
    total,
    trend,
    trendHours,
    query,
    loading,
    refreshing,
    error,
    failureCount,
    lastSuccessAt,
    loaded,
    // 派生
    degraded,
    hasData,
    // 动作
    refresh,
    start,
    stop,
    applyQuery,
    resetQuery,
    setPage,
    setSize,
    setTrendHours,
    refreshIfVisible,
  }
})
