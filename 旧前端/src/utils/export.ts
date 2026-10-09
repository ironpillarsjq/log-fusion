import type { ClientRow } from '@/types/monitor'
import { heartbeatStatusText, logStatusText } from '@/constants/status'
import { formatDateTime, formatHeartbeatAge } from './format'

const CSV_HEADER = [
  '客户端ID',
  'IP地址',
  '服务器名称',
  '系统类型',
  '系统版本',
  'Fluent Bit版本',
  '心跳周期(秒)',
  '最近心跳',
  '心跳延迟(秒)',
  '最后日志时间',
  '近5分钟日志量',
  '心跳状态',
  '日志状态',
]

function escapeCell(value: unknown): string {
  return `"${String(value ?? '').replaceAll('"', '""')}"`
}

/**
 * 把当前列表导出成 CSV。
 * 带 UTF-8 BOM，Excel 打开中文不乱码；导出的是「本次查询结果」而不是整库数据。
 */
export function exportClientsCsv(rows: ClientRow[], fileName = 'fluent-bit-clients.csv'): number {
  const lines = rows.map((row) =>
    [
      row.clientId,
      row.ipAddress,
      row.serverName,
      row.osType,
      row.osVersion,
      row.fluentBitVersion ?? '',
      row.heartbeatIntervalSec,
      formatHeartbeatAge(row.heartbeatDelaySec),
      row.heartbeatDelaySec,
      formatDateTime(row.lastLogAt),
      row.logsLast5m,
      heartbeatStatusText(row.heartbeatStatus),
      logStatusText(row.logStatus),
    ]
      .map(escapeCell)
      .join(','),
  )

  const csv = `\ufeff${[CSV_HEADER.map(escapeCell).join(','), ...lines].join('\r\n')}`
  const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.click()
  URL.revokeObjectURL(url)
  return rows.length
}
