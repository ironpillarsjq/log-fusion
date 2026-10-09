import axios from 'axios'
import type { AxiosError, AxiosResponse } from 'axios'
import type { ApiEnvelope } from '@/types/monitor'

/**
 * 全站唯一的 Axios 实例。
 *
 * - baseURL 固定为 `/api`：开发环境由 Vite 代理转发，生产环境由 Nginx 转发，
 *   因此代码里不会出现后端 IP、数据库账号或令牌。
 * - 响应拦截器把统一结构 `{ code, message, data }` 拆出 data，
 *   业务代码拿到的就是 data 本身；配合 `http.get<never, T>()` 获得类型。
 */
export const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 8000,
  headers: { Accept: 'application/json' },
})

/** 把各种失败情况收敛成一句可以直接展示给运维的中文提示 */
function toFriendlyMessage(error: AxiosError<ApiEnvelope<unknown>>): string {
  if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
    return '请求超时，请检查后端服务与网络'
  }
  if (!error.response) {
    return '无法连接后端服务，请确认 Spring Boot 已启动'
  }
  const payload = error.response.data
  const message = payload && typeof payload === 'object' ? payload.message : ''
  if (message) return message

  const { status } = error.response
  if (status === 401 || status === 403) return '没有访问权限，请检查登录状态或令牌'
  if (status === 404) return '接口不存在（404），请确认后端版本'
  if (status >= 500) return `后端服务异常（HTTP ${status}）`
  return `请求失败（HTTP ${status}）`
}

/** 主动取消（切换类别、打断上一次查询）不是业务错误，调用方应当忽略 */
export function isRequestCanceled(error: unknown): boolean {
  if (axios.isCancel(error)) return true
  return (error as { code?: string } | null | undefined)?.code === 'ERR_CANCELED'
}

http.interceptors.response.use(
  (response: AxiosResponse) => {
    const payload = response.data as ApiEnvelope<unknown> | undefined
    // 命中统一响应结构时，先校验业务状态码，再只把 data 交给调用方
    if (payload && typeof payload === 'object' && 'code' in payload) {
      if (payload.code !== 0) {
        return Promise.reject(new Error(payload.message || `接口返回错误码 ${payload.code}`))
      }
      return payload.data
    }
    return response.data
  },
  (error: AxiosError<ApiEnvelope<unknown>>) => {
    // 取消请求原样抛出，交给调用方判断，避免被翻译成"无法连接后端"
    if (isRequestCanceled(error)) return Promise.reject(error)
    return Promise.reject(new Error(toFriendlyMessage(error)))
  },
)
