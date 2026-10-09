/**
 * 融合日志管理的前端类型，与《融合日志管理 Vue 与 Spring Boot 实现指南》第 5、8.3 节一致。
 *
 * 约定：
 * - 类别与字段元数据**全部来自后端** `/api/v1/logs/categories`，前端不另维护一套字段；
 * - 只有总览的 8 个展示字段是前端固定的（指南 8.5）；
 * - 时间统一是带时区的 ISO 8601 字符串，显示时转本地时区。
 */

export type FieldType = 'STRING' | 'TEXT' | 'LONG' | 'INTEGER' | 'DATETIME'

export interface LogField {
  /** 接口字段名，也是表头展示的英文名 */
  key: string
  label: string
  type: FieldType
  searchable: boolean
  /**
   * 仅前端总览使用：总览接口返回 camelCase 字段（eventTime/osType/sourceIp），
   * 列头仍按 `key（label）` 显示英文列名，取值时读 prop。
   * 分类页由后端元数据驱动，没有 prop，直接用 key 取值。
   */
  prop?: string
}

export interface LogCategory {
  key: string
  label: string
  /** 可选的标签页短名，后端不提供时前端用内置短名兜底 */
  shortLabel?: string
  fields: LogField[]
  /** 可选：后端给了就在标签页上显示条数 */
  count?: number
}

/** 总览行（camelCase，见指南 5.3） */
export interface OverviewLog {
  id: number
  categoryRowId: number
  eventTime: string
  hostname: string | null
  osType: 'Linux' | 'Windows' | null
  category: string
  categoryLabel: string
  type: string
  summary: string | null
  result: string | null
  sourceIp: string | null
  ingestedAt: string
}

/** 分类页的动态行：键是数据库列名 */
export type DynamicLogRecord = Record<string, string | number | null>

export interface PageResponse<T> {
  items: T[]
  page: number
  size: number
  total: number
  serverTime?: string
}

export interface CategoryPageResponse {
  category: string
  fields: LogField[]
  records: DynamicLogRecord[]
  page: number
  size: number
  total: number
}

export interface LogSummary {
  todayTotal: number
  todayGrowthRate: number
  last5Minutes: number
  currentRatePerSecond: number
  activeHosts: number
  managedHosts: number
  pipelineStatus: string
  lastReceivedAt: string | null
}

export type SearchOperator = 'EQ' | 'CONTAINS' | 'STARTS_WITH' | 'GT' | 'GTE' | 'LT' | 'LTE'

export interface OverviewQuery {
  host?: string
  osType?: 'Linux' | 'Windows'
  startTime?: string
  endTime?: string
  page: number
  size: number
}

export interface CategoryQuery {
  field?: string
  operator?: SearchOperator
  value?: string
  startTime?: string
  endTime?: string
  page: number
  size: number
}

/** 详情：完整字段 + 字段元数据，前端详情弹窗不依赖列表里被截断的内容 */
export interface LogDetail {
  category: string
  fields: LogField[]
  record: Record<string, unknown>
}

export interface ExportRequest {
  category: string
  field?: string
  operator?: SearchOperator
  value?: string
  host?: string
  osType?: string
  startTime?: string
  endTime?: string
}

export interface ExportTask {
  taskId: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
  rowCount?: number
  downloadUrl?: string
  message?: string
}

export type StreamState = 'connecting' | 'open' | 'closed'
