import type { HeartbeatStatus, LogStatus, OsType } from '@/types/monitor'

/**
 * 心跳阈值只用于页面文案展示，判定一律以后端返回的 heartbeatStatus / heartbeatDelaySec 为准。
 */
export const HEARTBEAT_THRESHOLDS = {
  /** age <= online：ONLINE */
  online: 10,
  /** online < age <= delayed：DELAYED；age > delayed：OFFLINE */
  delayed: 30,
} as const

export interface HeartbeatStatusMeta {
  text: string
  /** 供 table / 弹窗使用的类名（原型样式） */
  className: string
  dotClass: string
  color: string
}

export const heartbeatStatusMeta: Record<HeartbeatStatus, HeartbeatStatusMeta> = {
  ONLINE: { text: '在线', className: 'status online', dotClass: 'dot online', color: '#168a5b' },
  DELAYED: { text: '延迟', className: 'status warning', dotClass: 'dot warning', color: '#c66a13' },
  OFFLINE: { text: '离线', className: 'status offline', dotClass: 'dot offline', color: '#c8322b' },
}

export const logStatusMeta: Record<LogStatus, { text: string; className: string }> = {
  NORMAL: { text: '正常', className: 'log-status normal' },
  NO_LOG: { text: '无日志', className: 'log-status silent' },
  BACKLOG: { text: '积压', className: 'log-status backlog' },
  INTERRUPTED: { text: '中断', className: 'log-status interrupted' },
}

/** 未知取值兜底，避免后端新增枚举时页面出现空白 */
const unknownHeartbeat: HeartbeatStatusMeta = {
  text: '未知',
  className: 'status warning',
  dotClass: 'dot warning',
  color: '#7a8798',
}

export const HEARTBEAT_STATUS_OPTIONS: Array<{ value: HeartbeatStatus; label: string }> = [
  { value: 'ONLINE', label: '在线' },
  { value: 'DELAYED', label: '延迟' },
  { value: 'OFFLINE', label: '离线' },
]

export const OS_TYPE_OPTIONS: Array<{ value: OsType; label: string }> = [
  { value: 'Linux', label: 'Linux' },
  { value: 'Windows', label: 'Windows' },
]

export const PAGE_SIZE_OPTIONS = [10, 20, 50]

export function heartbeatMeta(status: HeartbeatStatus | string | null | undefined): HeartbeatStatusMeta {
  if (!status) return unknownHeartbeat
  return heartbeatStatusMeta[status as HeartbeatStatus] ?? unknownHeartbeat
}

export function heartbeatStatusText(status: HeartbeatStatus | string | null | undefined): string {
  return heartbeatMeta(status).text
}

export function heartbeatDotClass(status: HeartbeatStatus | string | null | undefined): string {
  return heartbeatMeta(status).dotClass
}

export function logStatusText(status: LogStatus | string | null | undefined): string {
  if (!status) return '未知'
  return logStatusMeta[status as LogStatus]?.text ?? '未知'
}

export function logStatusClass(status: LogStatus | string | null | undefined): string {
  if (!status) return 'log-status silent'
  return logStatusMeta[status as LogStatus]?.className ?? 'log-status silent'
}

export function osTypeText(osType: OsType | string | null | undefined): string {
  return osType === 'Windows' ? 'Windows' : osType === 'Linux' ? 'Linux' : '-'
}
