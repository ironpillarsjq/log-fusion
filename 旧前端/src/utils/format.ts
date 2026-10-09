/**
 * 时间与数字的展示规则。
 *
 * 接口统一返回 ISO 8601 UTC 时间（例如 2026-10-06T13:41:17Z），
 * 页面负责转换为浏览器本地时间显示。
 */

const DATE_TIME_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  // 用 h23 而不是 hour12:false，避免零点被格式化成 24 时
  hourCycle: 'h23',
})

const TIME_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

const HOUR_MINUTE_FORMATTER = new Intl.DateTimeFormat('zh-CN', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

const NUMBER_FORMATTER = new Intl.NumberFormat('zh-CN')

function isValidDate(date: Date): boolean {
  return !Number.isNaN(date.getTime())
}

export function parseDate(value: string | null | undefined): Date | null {
  if (!value) return null
  const date = new Date(value)
  return isValidDate(date) ? date : null
}

/** 2026-10-06 21:41:17（本机时区），可传 ISO 字符串或 Date */
export function formatDateTime(value: string | Date | null | undefined): string {
  const date = value instanceof Date ? value : parseDate(value)
  if (!date || !isValidDate(date)) return '-'
  const parts = DATE_TIME_FORMATTER.formatToParts(date)
  const pick = (type: Intl.DateTimeFormatPartTypes) =>
    parts.find((part) => part.type === type)?.value ?? ''
  const year = pick('year')
  const month = pick('month')
  const day = pick('day')
  const time = `${pick('hour')}:${pick('minute')}:${pick('second')}`
  return `${year}-${month}-${day} ${time}`
}

/** 21:41:17，用于顶栏时钟与「最近刷新」 */
export function formatTime(value: Date | string | null | undefined): string {
  const date = typeof value === 'string' ? parseDate(value) : value
  if (!date || !isValidDate(date)) return '--:--:--'
  return TIME_FORMATTER.format(date)
}

/** 21:41，用于趋势图坐标轴 */
export function formatHourMinute(value: Date | string | null | undefined): string {
  const date = typeof value === 'string' ? parseDate(value) : value
  if (!date || !isValidDate(date)) return '-'
  return HOUR_MINUTE_FORMATTER.format(date)
}

/** 2 秒前 / 5 分钟前 / 3 小时前 */
export function formatHeartbeatAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '-'
  const value = Math.max(0, Math.floor(seconds))
  if (value < 60) return `${value} 秒前`
  const minutes = Math.floor(value / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  return `${Math.floor(hours / 24)} 天前`
}

/** 12840 -> 12,840 */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  return NUMBER_FORMATTER.format(value)
}

/** 80 -> 80% ；80.5 -> 80.5% */
export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '-'
  const rounded = Math.round(value * 10) / 10
  return `${Number.isInteger(rounded) ? rounded.toFixed(0) : rounded.toFixed(1)}%`
}

/** 心跳延迟秒数，例如 2 秒 */
export function formatDelaySeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '-'
  return `${Math.max(0, Math.floor(seconds))} 秒`
}
