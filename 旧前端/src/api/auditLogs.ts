import { http } from './http'
import type {
  CategoryPageResponse,
  CategoryQuery,
  ExportRequest,
  ExportTask,
  LogCategory,
  LogDetail,
  LogField,
  LogSummary,
  OverviewLog,
  OverviewQuery,
  PageResponse,
} from '@/types/auditLogs'

/** 去掉空值，避免把 host=&osType= 这类空参数发给后端 */
function clean(params: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') result[key] = value
  }
  return result
}

/**
 * 详情接口的返回结构没有强约束（指南 5.5 只说"完整字段和技术元数据"），
 * 这里把 `{ category, fields, record }` 和"直接返回一条记录"两种形态都归一化。
 */
function toDetail(payload: unknown, category: string, fallbackFields: LogField[]): LogDetail {
  if (payload && typeof payload === 'object') {
    const bag = payload as Record<string, unknown>
    const record = bag.record && typeof bag.record === 'object' ? (bag.record as Record<string, unknown>) : bag
    const fields = Array.isArray(bag.fields) ? (bag.fields as LogField[]) : fallbackFields
    return { category, fields, record }
  }
  return { category, fields: fallbackFields, record: {} }
}

export const auditLogApi = {
  /** 七类字段与中文标签 */
  getCategories: (signal?: AbortSignal) =>
    http.get<never, LogCategory[]>('/v1/logs/categories', { signal }),

  /** 顶部统计 */
  getSummary: (signal?: AbortSignal) => http.get<never, LogSummary>('/v1/logs/summary', { signal }),

  /** 总览列表 */
  getOverview: (params: OverviewQuery, signal?: AbortSignal) =>
    http.get<never, PageResponse<OverviewLog>>('/v1/logs/overview', {
      params: clean({ ...params }),
      signal,
    }),

  /** 指定类别的动态字段查询 */
  getCategory: (category: string, params: CategoryQuery, signal?: AbortSignal) =>
    http.get<never, CategoryPageResponse>(`/v1/logs/categories/${encodeURIComponent(category)}`, {
      params: clean({ ...params }),
      signal,
    }),

  /** 一条完整日志 */
  getDetail: async (category: string, id: number, fields: LogField[] = [], signal?: AbortSignal) => {
    const payload = await http.get<never, unknown>(
      `/v1/logs/categories/${encodeURIComponent(category)}/${id}`,
      { signal },
    )
    return toDetail(payload, category, fields)
  },

  /** 创建大结果集导出任务 */
  createExport: (payload: ExportRequest) => http.post<never, ExportTask>('/v1/logs/exports', payload),

  /** 查询导出任务状态 */
  getExportTask: (taskId: string, signal?: AbortSignal) =>
    http.get<never, ExportTask>(`/v1/logs/exports/${encodeURIComponent(taskId)}`, { signal }),

  /** 下载导出文件（走统一的 http 实例，避免把后端地址写死） */
  downloadExport: (downloadUrl: string) =>
    http.get<never, Blob>(downloadUrl, { responseType: 'blob', timeout: 120000 }),
}
