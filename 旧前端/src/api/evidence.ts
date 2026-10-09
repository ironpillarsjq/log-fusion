import { http } from './http'
import type {
  EvidenceCategory,
  EvidenceDetail,
  EvidencePage,
  EvidenceQuery,
  EvidenceSummary,
  VerifyPageResponse,
  VerifyResult,
} from '@/types/evidence'

/** 存证 ID 的格式由后端决定（EV-… 或 ULID），这里只做长度与字符集兜底校验 */
const EVIDENCE_ID_PATTERN = /^[A-Za-z0-9-]{10,40}$/

/** 前端先校验一次，后端仍必须再校验（指南 10.8） */
export const RAW_HASH_PATTERN = /^[0-9a-fA-F]{64}$/

function clean(params: Record<string, unknown>): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') result[key] = value
  }
  return result
}

export function isValidRawHash(value: string): boolean {
  return RAW_HASH_PATTERN.test(value.trim())
}

export function isValidEvidenceId(value: string): boolean {
  return EVIDENCE_ID_PATTERN.test(value)
}

/** 导出走浏览器直接跳转，由后端流式写出（指南 10.10） */
export function evidenceExportUrl(query: EvidenceQuery): string {
  const base = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')
  const params = new URLSearchParams()
  const cleaned = clean({
    host: query.host,
    category: query.category,
    status: query.status,
    startTime: query.startTime,
    rawHash: query.rawHash,
  })
  for (const [key, value] of Object.entries(cleaned)) params.set(key, String(value))
  const suffix = params.toString()
  return `${base}/v1/evidence/export${suffix ? `?${suffix}` : ''}`
}

export const evidenceApi = {
  /** 顶部四项统计 */
  summary: (signal?: AbortSignal) => http.get<never, EvidenceSummary>('/v1/evidence/summary', { signal }),

  /** 分页查询单条存证 */
  list: (params: EvidenceQuery, signal?: AbortSignal) =>
    http.get<never, EvidencePage>('/v1/evidence', { params: clean({ ...params }), signal }),

  /** 存证详情（含原始日志正文） */
  detail: (evidenceId: string, signal?: AbortSignal) =>
    http.get<never, EvidenceDetail>(`/v1/evidence/${encodeURIComponent(evidenceId)}`, { signal }),

  /** 立即重新校验一条 */
  verify: (evidenceId: string) =>
    http.post<never, VerifyResult>(`/v1/evidence/${encodeURIComponent(evidenceId)}/verify`),

  /** 同步重新校验当前页（最多 100 条） */
  verifyPage: (evidenceIds: string[]) =>
    http.post<never, VerifyPageResponse>('/v1/evidence/verify-page', { evidenceIds }),

  /** 供视图层判断类别是否合法 */
  categoryKeys: (): EvidenceCategory[] => [
    'authentication_session',
    'account_security_change',
    'process_command_execution',
    'file_object_access',
    'network_ipc_communication',
    'system_service_audit_lifecycle',
    'security_policy_config_change',
  ],
}
