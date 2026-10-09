import { ref } from 'vue'
import { defineStore } from 'pinia'

export type ToastKind = 'success' | 'error' | 'info'

export interface ToastItem {
  id: number
  text: string
  kind: ToastKind
}

/** 轻量提示。统一在这里排队和自动消失，避免各组件各写一套 setTimeout。 */
export const useToastStore = defineStore('toast', () => {
  const items = ref<ToastItem[]>([])
  let seq = 0

  function dismiss(id: number): void {
    items.value = items.value.filter((item) => item.id !== id)
  }

  function push(text: string, kind: ToastKind = 'info', duration = 2600): number {
    const id = (seq += 1)
    items.value = [...items.value, { id, text, kind }]
    window.setTimeout(() => dismiss(id), duration)
    return id
  }

  return {
    items,
    dismiss,
    push,
    success: (text: string) => push(text, 'success'),
    error: (text: string) => push(text, 'error', 4000),
    info: (text: string) => push(text, 'info'),
  }
})
