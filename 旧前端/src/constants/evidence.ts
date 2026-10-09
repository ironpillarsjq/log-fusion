import type { EvidenceCategory, VerifyStatus } from '@/types/evidence'

/** 校验状态的展示映射（接口只返回英文枚举，中文与颜色在这里集中定义） */
export interface VerifyStatusMeta {
  text: string
  /** 用于 .evidence-state 的颜色类 */
  className: 'valid' | 'invalid' | 'pending'
  dotClass: 'green' | 'red' | 'orange'
}

export const VERIFY_STATUS_META: Record<VerifyStatus, VerifyStatusMeta> = {
  VALID: { text: '校验正常', className: 'valid', dotClass: 'green' },
  HASH_MISMATCH: { text: '哈希不一致', className: 'invalid', dotClass: 'red' },
  RAW_MISSING: { text: '原始日志缺失', className: 'invalid', dotClass: 'red' },
  PENDING: { text: '待校验', className: 'pending', dotClass: 'orange' },
}

export const VERIFY_STATUS_OPTIONS: Array<{ value: VerifyStatus; label: string }> = [
  { value: 'VALID', label: '校验正常' },
  { value: 'HASH_MISMATCH', label: '哈希不一致' },
  { value: 'RAW_MISSING', label: '原始日志缺失' },
  { value: 'PENDING', label: '待校验' },
]

/**
 * 类别键与中文名的展示映射。
 * 类别的**权威定义在后端枚举**（`EvidenceCategory` / 融合模块的 `LogCategoryDefinition`），
 * 这里的键必须与之完全一致；后端新增类别时这里要同步补一行。
 */
export const CATEGORY_LABELS: Record<EvidenceCategory, string> = {
  authentication_session: '认证与会话',
  account_security_change: '账号与安全属性变更',
  process_command_execution: '进程与命令执行',
  file_object_access: '文件与对象访问',
  network_ipc_communication: '网络与 IPC 通信',
  system_service_audit_lifecycle: '系统、服务与审计生命周期',
  security_policy_config_change: '安全策略与配置变更',
}

export const CATEGORY_OPTIONS: Array<{ value: EvidenceCategory; label: string }> = (
  Object.keys(CATEGORY_LABELS) as EvidenceCategory[]
).map((value) => ({ value, label: CATEGORY_LABELS[value] }))

/** 表格列：与指南 10.7 一致（列顺序不变），表头用 `key（label）` 形式 */
export interface EvidenceColumn {
  key: string
  label: string
  width: number
}

export const EVIDENCE_COLUMNS: EvidenceColumn[] = [
  { key: 'receivedAt', label: 'received_at（接收时间）', width: 150 },
  { key: 'evidenceId', label: 'id（存证 ID）', width: 152 },
  { key: 'hostname', label: 'hostname（主机）', width: 118 },
  { key: 'sourceIp', label: 'ip（来源 IP）', width: 112 },
  { key: 'categoryLabel', label: 'category（融合类别）', width: 148 },
  { key: 'type', label: 'type（日志类型）', width: 112 },
  { key: 'rawHash', label: 'hash（SHA-256）', width: 180 },
  { key: 'verifyStatus', label: 'status（校验状态）', width: 108 },
  { key: 'lastVerifiedAt', label: 'verified_at（最近校验时间）', width: 145 },
]

export function verifyStatusMeta(status: VerifyStatus | string | null | undefined): VerifyStatusMeta {
  if (!status) return VERIFY_STATUS_META.PENDING
  return VERIFY_STATUS_META[status as VerifyStatus] ?? VERIFY_STATUS_META.PENDING
}

export function categoryLabelOf(category: string | null | undefined): string {
  if (!category) return '未识别'
  return CATEGORY_LABELS[category as EvidenceCategory] ?? category
}

/** 表格里只显示哈希的首尾，复制 / tooltip / 详情用完整 64 位值 */
export function truncateHash(hash: string | null | undefined): string {
  if (!hash) return '-'
  if (hash.length <= 26) return hash
  return `${hash.slice(0, 14)}…${hash.slice(-8)}`
}
