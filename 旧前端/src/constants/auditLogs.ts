import type { LogField } from '@/types/auditLogs'

/** 总览在标签页里的固定 key */
export const OVERVIEW_KEY = 'overview'

export const DEFAULT_PAGE_SIZE = 50
export const PAGE_SIZE_OPTIONS = [20, 50, 100]
/** 后端上限，见指南 6.2 */
export const MAX_PAGE_SIZE = 100
/** 小于等于这个条数时在浏览器里直接生成 CSV，超过则走异步导出任务（指南 10） */
export const EXPORT_INLINE_LIMIT = 5000

/**
 * 总览固定展示的 8 个字段（指南 3.1 / 8.5）。
 * 列头显示 key，取值读 prop —— 因为总览接口返回的是 camelCase 字段。
 */
export const OVERVIEW_FIELDS: LogField[] = [
  { key: 'event_time', label: '事件时间', type: 'DATETIME', searchable: false, prop: 'eventTime' },
  { key: 'hostname', label: '主机', type: 'STRING', searchable: false },
  { key: 'os_type', label: '系统', type: 'STRING', searchable: false, prop: 'osType' },
  { key: 'category', label: '日志类别', type: 'STRING', searchable: false },
  { key: 'type', label: '日志类型', type: 'STRING', searchable: false },
  { key: 'summary', label: '融合摘要', type: 'TEXT', searchable: false },
  { key: 'result', label: '结果', type: 'STRING', searchable: false },
  { key: 'source_ip', label: '来源 IP', type: 'STRING', searchable: false, prop: 'sourceIp' },
]

/** 详情弹窗里额外展示的技术字段 */
export const TECHNICAL_FIELD_LABELS: Record<string, string> = {
  id: '行 ID',
  category_row_id: '分类表行 ID',
  source_client_id: '客户端 ID',
  source_ip: '来源 IP',
  ingested_at: '入库时间',
}

/** 结果列的展示映射（与心跳页的状态映射同一思路：接口只给英文枚举） */
export const RESULT_META: Record<string, { text: string; className: string }> = {
  success: { text: '成功', className: 'success' },
  failed: { text: '失败', className: 'failed' },
  denied: { text: '拒绝', className: 'denied' },
}

export const PIPELINE_STATUS_TEXT: Record<string, string> = {
  RUNNING: '运行正常',
  DEGRADED: '管道降级',
  STOPPED: '管道已停止',
}

export function resultMetaOf(value: unknown): { text: string; className: string } {
  const key = value === null || value === undefined ? '' : String(value).toLowerCase()
  return RESULT_META[key] ?? { text: value ? String(value) : '-', className: 'denied' }
}
