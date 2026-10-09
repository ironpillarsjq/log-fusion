/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 接口前缀，默认 /api */
  readonly VITE_API_BASE_URL?: string
  /** 仅开发环境使用：Vite 代理的后端地址 */
  readonly VITE_PROXY_TARGET?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
