import { computed, reactive, ref } from 'vue'
import { defineStore } from 'pinia'
import { evidenceApi } from '@/api/evidence'
import { isRequestCanceled } from '@/api/http'
import type {
  EvidenceListItem,
  EvidenceQuery,
  EvidenceSummary,
  VerifyPageResponse,
  VerifyResult,
} from '@/types/evidence'

const SUMMARY_REFRESH_MS = 5000
export const DEFAULT_PAGE_SIZE = 50
export const MAX_PAGE_SIZE = 100
export const MAX_VERIFY_PAGE = 100

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}

/**
 * 存证页的数据全部走这个 store（指南第 12 节）：
 * - 顶部统计每 5 秒刷新；
 * - 列表**不自动刷新**，只由"查询 / 翻页 / 校验完成"触发，避免用户看详情时内容跳动；
 * - 校验失败只恢复该行按钮，不清空列表。
 */
export const useEvidenceStore = defineStore('evidence', () => {
  const summary = ref<EvidenceSummary | null>(null)
  const items = ref<EvidenceListItem[]>([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref('')
  const failureCount = ref(0)
  const lastLoadedAt = ref<Date | null>(null)
  const verifyingIds = ref<string[]>([])
  const verifyingPage = ref(false)

  const query = reactive<EvidenceQuery>({ page: 1, size: DEFAULT_PAGE_SIZE })

  let controller: AbortController | null = null
  let summaryTimer: number | undefined
  let summaryBusy = false
  let summaryRunning = false

  const degraded = computed(() => failureCount.value >= 2)
  const pageCount = computed(() => Math.max(1, Math.ceil(total.value / Math.max(1, query.size))))
  const hasItems = computed(() => items.value.length > 0)
  const isVerifyingAll = computed(() => verifyingPage.value)

  function isVerifying(evidenceId: string): boolean {
    return verifyingIds.value.includes(evidenceId)
  }

  // ---------- 顶部统计 ----------

  async function loadSummary(): Promise<void> {
    if (summaryBusy) return
    summaryBusy = true
    try {
      summary.value = await evidenceApi.summary()
    } catch {
      // 统计失败保留上一份数据，不打断列表
    } finally {
      summaryBusy = false
    }
  }

  function scheduleSummary(): void {
    window.clearTimeout(summaryTimer)
    summaryTimer = window.setTimeout(async () => {
      if (!document.hidden) await loadSummary()
      if (summaryRunning) scheduleSummary()
    }, SUMMARY_REFRESH_MS)
  }

  async function startSummaryPolling(): Promise<void> {
    if (summaryRunning) return
    summaryRunning = true
    await loadSummary()
    scheduleSummary()
  }

  function stopSummaryPolling(): void {
    summaryRunning = false
    window.clearTimeout(summaryTimer)
    summaryTimer = undefined
  }

  // ---------- 列表查询 ----------

  async function loadPage(): Promise<void> {
    controller?.abort()
    const current = new AbortController()
    controller = current
    loading.value = true
    try {
      const result = await evidenceApi.list(query, current.signal)
      items.value = result?.items ?? []
      total.value = result?.total ?? 0
      error.value = ''
      failureCount.value = 0
      lastLoadedAt.value = new Date()
    } catch (e) {
      if (!isRequestCanceled(e)) {
        error.value = messageOf(e, '存证查询失败')
        failureCount.value += 1
      }
    } finally {
      if (controller === current) {
        controller = null
        loading.value = false
      }
    }
  }

  /** 查询条件变化：页码重置为 1 再查 */
  async function applyQuery(next: Partial<EvidenceQuery>): Promise<void> {
    Object.assign(query, next, { page: 1 })
    await loadPage()
  }

  async function resetQuery(): Promise<void> {
    Object.assign(query, {
      host: undefined,
      category: undefined,
      status: undefined,
      startTime: undefined,
      rawHash: undefined,
      page: 1,
    })
    await loadPage()
  }

  async function setPage(next: number): Promise<void> {
    if (next < 1 || next > pageCount.value || next === query.page) return
    query.page = next
    await loadPage()
  }

  async function setSize(next: number): Promise<void> {
    const safe = Math.min(MAX_PAGE_SIZE, Math.max(1, next))
    if (safe === query.size) return
    Object.assign(query, { size: safe, page: 1 })
    await loadPage()
  }

  // ---------- 校验 ----------

  /** 单条：只更新该行，不清空列表（指南 15） */
  async function verifyOne(evidenceId: string): Promise<VerifyResult | null> {
    if (isVerifying(evidenceId)) return null
    verifyingIds.value = [...verifyingIds.value, evidenceId]
    try {
      const result = await evidenceApi.verify(evidenceId)
      const row = items.value.find((item) => item.evidenceId === evidenceId)
      if (row) {
        row.verifyStatus = result.verifyStatus
        row.lastVerifiedAt = result.lastVerifiedAt
      }
      await loadSummary()
      return result
    } catch (e) {
      error.value = messageOf(e, `校验失败：${evidenceId}`)
      return null
    } finally {
      verifyingIds.value = verifyingIds.value.filter((id) => id !== evidenceId)
    }
  }

  /** 当前页：同步校验，最多 100 条，只发当前页实际显示的 ID */
  async function verifyCurrentPage(): Promise<VerifyPageResponse | null> {
    const ids = items.value.map((item) => item.evidenceId).slice(0, MAX_VERIFY_PAGE)
    if (!ids.length) return null
    verifyingPage.value = true
    try {
      const result = await evidenceApi.verifyPage(ids)
      await Promise.all([loadPage(), loadSummary()])
      return result
    } catch (e) {
      error.value = messageOf(e, '当前页校验失败')
      return null
    } finally {
      verifyingPage.value = false
    }
  }

  async function initialize(): Promise<void> {
    await Promise.all([loadSummary(), loadPage()])
  }

  return {
    // 状态
    summary,
    items,
    total,
    loading,
    error,
    failureCount,
    lastLoadedAt,
    verifyingIds,
    verifyingPage,
    query,
    // 派生
    degraded,
    pageCount,
    hasItems,
    isVerifyingAll,
    // 动作
    isVerifying,
    initialize,
    loadSummary,
    startSummaryPolling,
    stopSummaryPolling,
    loadPage,
    applyQuery,
    resetQuery,
    setPage,
    setSize,
    verifyOne,
    verifyCurrentPage,
  }
})
