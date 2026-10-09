/**
 * 原始日志单条存证的类型定义，与《原始日志单条存证 Vue 与 Spring Boot 实现指南》
 * 第 10.3 节一致。
 *
 * 重要约定：
 * - 哈希针对"保存的原始字节"计算，算法固定 SHA-256、版本固定 EVIDENCE-V1；
 * - 时间统一带时区的 ISO 8601，显示时转本地时区；
 * - 第一阶段只有单条存证，没有批次 / Merkle / 签名 / 校验任务。
 */

export type EvidenceCategory =
  | 'authentication_session'
  | 'account_security_change'
  | 'process_command_execution'
  | 'file_object_access'
  | 'network_ipc_communication'
  | 'system_service_audit_lifecycle'
  | 'security_policy_config_change'

/** 校验状态（指南第 6 节） */
export type VerifyStatus = 'PENDING' | 'VALID' | 'HASH_MISMATCH' | 'RAW_MISSING'

export interface EvidenceListItem {
  evidenceId: string
  receivedAt: string
  sourceClientId: string
  hostname: string | null
  sourceIp: string | null
  category: EvidenceCategory | null
  categoryLabel: string | null
  type: string
  hashAlgorithm: string
  /** 完整 64 位小写十六进制 */
  rawHash: string
  verifyStatus: VerifyStatus
  lastVerifiedAt: string | null
}

export interface EvidenceDetail extends EvidenceListItem {
  rawStoragePath: string
  rawOffset: number
  rawLength: number
  algorithmVersion: string
  /** 后端按 path + offset + length 现读的原始正文；读不到时为 null */
  rawLog: string | null
  normalizedTable: string | null
  normalizedRowId: number | null
}

export interface EvidenceSummary {
  todayRawTotal: number
  hashedTotal: number
  validTotal: number
  /** = hashMismatch + rawMissing */
  invalidTotal: number
  pendingTotal: number
  hashAlgorithm: string
  serverTime: string
}

export interface EvidenceQuery {
  host?: string
  category?: EvidenceCategory
  status?: VerifyStatus
  startTime?: string
  /** 只支持完整 64 位十六进制精确匹配 */
  rawHash?: string
  page: number
  size: number
}

export interface EvidencePage {
  items: EvidenceListItem[]
  page: number
  size: number
  total: number
  serverTime: string
}

export interface VerifyResult {
  evidenceId: string
  verifyStatus: VerifyStatus
  lastVerifiedAt: string | null
}

export interface VerifyPageResponse {
  checked: number
  valid: number
  hashMismatch: number
  rawMissing: number
  results: VerifyResult[]
}
