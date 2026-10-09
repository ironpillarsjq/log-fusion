import { http } from './http'
import type {
  ClientDetail,
  ClientLogEntry,
  ClientPage,
  ClientQuery,
  MonitorSummary,
  TrendPoint,
} from '@/types/monitor'

/** 去掉空值，避免把 keyword= 这种空参数发给后端 */
function cleanParams(query: ClientQuery): Record<string, string | number> {
  const params: Record<string, string | number> = {
    page: query.page,
    size: query.size,
  }
  const keyword = query.keyword?.trim()
  if (keyword) params.keyword = keyword
  if (query.heartbeatStatus) params.heartbeatStatus = query.heartbeatStatus
  if (query.osType) params.osType = query.osType
  return params
}

/**
 * 最近日志接口的返回结构在后端实现中可能有三种形态：
 * 裸数组、{ items }、{ logs }。这里统一归一化成数组，前端只处理一种结构。
 */
function toLogList(payload: unknown): ClientLogEntry[] {
  if (Array.isArray(payload)) return payload as ClientLogEntry[]
  if (payload && typeof payload === 'object') {
    const record = payload as Record<string, unknown>
    for (const key of ['items', 'logs', 'records', 'list']) {
      const value = record[key]
      if (Array.isArray(value)) return value as ClientLogEntry[]
    }
  }
  return []
}

export const monitorApi = {
  /** 概览卡片和状态分布 */
  getSummary: () => http.get<never, MonitorSummary>('/v1/monitor/summary'),

  /** 分页、搜索和筛选客户端 */
  getClients: (query: ClientQuery) =>
    http.get<never, ClientPage>('/v1/monitor/clients', { params: cleanParams(query) }),

  /** 在线趋势，默认最近 24 小时 */
  getTrend: (hours = 24) => http.get<never, TrendPoint[]>('/v1/monitor/trend', { params: { hours } }),

  /** 客户端详情 */
  getDetail: (clientId: string) =>
    http.get<never, ClientDetail>(`/v1/monitor/clients/${encodeURIComponent(clientId)}`),

  /** 该客户端最近日志 */
  getLogs: (clientId: string, limit = 20) =>
    http
      .get<never, unknown>(`/v1/monitor/clients/${encodeURIComponent(clientId)}/logs`, {
        params: { limit },
      })
      .then(toLogList),
}
