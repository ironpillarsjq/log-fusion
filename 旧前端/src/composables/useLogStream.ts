import { onBeforeUnmount, onMounted } from 'vue'
import type { OverviewLog, StreamState } from '@/types/auditLogs'

interface UseLogStreamOptions {
  onLog: (log: OverviewLog) => void
  onStateChange?: (state: StreamState) => void
  /** 断线重连成功后回调，用于重新查询第一页补齐断线期间的数据（指南 13） */
  onReconnect?: () => void
}

function streamUrl(): string {
  const base = import.meta.env.VITE_API_BASE_URL || '/api'
  return `${base.replace(/\/$/, '')}/v1/logs/stream`
}

/**
 * SSE 实时日志流。
 * - 使用原生 EventSource，断线由浏览器自动重连；
 * - 这里只负责更新连接状态、分发日志、以及重连成功后的补数据回调；
 * - 组件卸载时关闭连接。
 */
export function useLogStream(options: UseLogStreamOptions): { connect: () => void; disconnect: () => void } {
  let source: EventSource | undefined
  let hadError = false

  function connect(): void {
    if (source) return
    options.onStateChange?.('connecting')

    source = new EventSource(streamUrl(), { withCredentials: true })

    source.addEventListener('log', (event) => {
      try {
        options.onLog(JSON.parse((event as MessageEvent).data) as OverviewLog)
      } catch {
        // 单条坏数据不影响整体连接
      }
    })

    // 服务端约定的控制事件，收到即可，不需要额外处理
    source.addEventListener('connected', () => undefined)
    source.addEventListener('heartbeat', () => undefined)

    source.onopen = () => {
      options.onStateChange?.('open')
      if (hadError) {
        hadError = false
        options.onReconnect?.()
      }
    }

    source.onerror = () => {
      // EventSource 会自动重连，这里只反映到页面状态
      hadError = true
      options.onStateChange?.('closed')
    }
  }

  function disconnect(): void {
    source?.close()
    source = undefined
  }

  onMounted(connect)
  onBeforeUnmount(disconnect)

  return { connect, disconnect }
}
