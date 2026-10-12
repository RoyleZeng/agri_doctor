import { reactive } from 'vue'

/** 極簡 toast，取代 Element Plus 的 ElMessage。
 *
 * 設計稿是完全自訂的視覺語言，為了幾個提示訊息載入整套 UI 套件不划算。
 */
export type ToastKind = 'info' | 'ok' | 'warn' | 'crit'

export interface Toast {
  id: number
  kind: ToastKind
  text: string
}

const state = reactive<{ items: Toast[] }>({ items: [] })
let seq = 0

function push(kind: ToastKind, text: string, ms = 4000): void {
  const id = ++seq
  state.items.push({ id, kind, text })
  setTimeout(() => dismiss(id), ms)
}

export function dismiss(id: number): void {
  const i = state.items.findIndex((t) => t.id === id)
  if (i >= 0) state.items.splice(i, 1)
}

export const toasts = state

export const toast = {
  info: (t: string) => push('info', t),
  ok: (t: string) => push('ok', t),
  warn: (t: string) => push('warn', t),
  error: (t: string) => push('crit', t, 6000),
}
