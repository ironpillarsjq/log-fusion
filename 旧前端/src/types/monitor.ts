/**
 * 与后端约定的枚举与数据结构。
 *
 * 约定：接口只返回固定英文枚举，中文展示值集中在前端 `constants/status.ts` 中映射，
 * 避免数据库里同时出现「在线」「ONLINE」等多种取值。
 */

/** 心跳状态，由后端依据 received_at（汇聚端接收时间）计算，前端只负责展示 */
export type HeartbeatStatus = 'ONLINE' | 'DELAYED' | 'OFFLINE'

/** 日志状态，与心跳状态独立判定，不能互相推导 */
export type LogStatus = 'NORMAL' | 'NO_LOG' | 'BACKLOG' | 'INTERRUPTED'

export type OsType = 'Linux' | 'Windows'

/** 客户端列表行，对应 GET /api/v1/monitor/clients 的 items 元素 */
export interface ClientRow {
  clientId: string
  ipAddress: string
  serverName: string
  osType: OsType
  osVersion: string
  fluentBitVersion: string | null
  heartbeatIntervalSec: number
  /** ISO 8601 UTC，例如 2026-10-06T13:41:17Z */
  lastHeartbeatAt: string
  /** 后端计算好的心跳延迟秒数，前端不要用本机时间重新计算 */
  heartbeatDelaySec: number
  heartbeatStatus: HeartbeatStatus
  lastLogAt: string | null
  logsLast5m: number
  logStatus: LogStatus
}

/** 客户端详情，比列表行多出 Fluent Bit 运行信息与记录时间 */
export interface ClientDetail extends ClientRow {
  bufferedChunks: number
  outputHealthy: boolean
  createdAt: string | null
  updatedAt: string | null
  /**
   * 以下两项为可选扩展字段：心跳上报里带上就展示，没带则详情弹窗显示「-」。
   * 需要采集端在心跳请求中一并上报（见文档 5.1 心跳请求）。
   */
  cpuUsagePercent?: number | null
  memoryUsageMb?: number | null
}

/** 概览卡片与状态分布，对应 GET /api/v1/monitor/summary */
export interface MonitorSummary {
  total: number
  online: number
  delayed: number
  offline: number
  onlineRate: number
  linuxCount: number
  windowsCount: number
  logsLast5m: number
  normalLogClients: number
  /** 后端服务器时间，ISO 8601 UTC */
  serverTime: string
}

/** 在线趋势点，对应 GET /api/v1/monitor/trend */
export interface TrendPoint {
  snapshotTime: string
  totalCount: number
  onlineCount: number
  delayedCount: number
  offlineCount: number
}

/** 列表查询参数，分页从 1 开始（后端 Controller 内部再减 1） */
export interface ClientQuery {
  page: number
  size: number
  keyword?: string
  heartbeatStatus?: HeartbeatStatus
  osType?: OsType
}

/** 分页结果 */
export interface ClientPage {
  items: ClientRow[]
  page: number
  size: number
  total: number
}

/**
 * 客户端最近日志。
 * 后端字段可能不完全固定，`api/monitor.ts` 做了归一化，这里用宽松结构兜底。
 */
export interface ClientLogEntry {
  logTime?: string | null
  level?: string | null
  category?: string | null
  source?: string | null
  message?: string | null
  [key: string]: unknown
}

/** 统一响应结构 { code, message, data } */
export interface ApiEnvelope<T> {
  code: number
  message: string
  data: T
}
