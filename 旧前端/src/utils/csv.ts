/**
 * CSV 导出工具。
 * 安全要求（指南 12.8）：以 = + - @ 开头的单元格会被 Excel 当公式执行，
 * 导出时必须加前导单引号，避免公式注入。
 */

const FORMULA_PREFIX = /^[=+\-@\t\r]/

export function escapeCsvCell(value: unknown): string {
  let text = value === null || value === undefined ? '' : String(value)
  if (FORMULA_PREFIX.test(text)) text = `'${text}`
  return `"${text.replaceAll('"', '""')}"`
}

/** 带 UTF-8 BOM，Excel 打开中文不乱码 */
export function buildCsv(headers: string[], rows: unknown[][]): string {
  const lines = [headers, ...rows].map((row) => row.map(escapeCsvCell).join(','))
  return `\ufeff${lines.join('\r\n')}`
}

export function downloadBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = fileName
  anchor.click()
  URL.revokeObjectURL(url)
}

export function downloadCsv(content: string, fileName: string): void {
  downloadBlob(new Blob([content], { type: 'text/csv;charset=utf-8' }), fileName)
}

/** 20261007-154530，用于导出文件名 */
export function timestampSuffix(date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, '0')
  return (
    `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}` +
    `-${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
  )
}
