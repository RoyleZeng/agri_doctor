/** 就地記錄抽屜的狀態與日誌載入：`createRecordFlow(api)` 可注入，離線可測（`npm run test:record-flow`）。
 *
 * 為什麼抽出來：第四輪最關鍵的一條是「**存檔後不重抓 workspace**」。
 * 那是一個「沒有發生的事」——畫面看起來對、測試也綠，卻可能偷偷多打兩支 API 把捲動位置和
 * 已選作物洗掉（第三輪就是這樣壞的）。唯一測得到的方式是注入假 API 數呼叫次數，
 * 所以這段邏輯不能留在 `.vue` 裡。
 *
 * 型別刻意用結構化的最小形狀、不 import `@/api/planning`：測試用的 tsc 跑不了 `@/` 別名。
 */
import { ref, type Ref } from 'vue'

export interface FlowPlan {
  id: string
  planted_on: string
  end_on: string
  events: { id: string; title: string; log_id?: string | null }[]
}

export interface FlowLog {
  id: string
  done_on: string
  /** 由規劃事項記錄時後端會一併回傳更新後的規劃。 */
  plan?: { id: string } | null
}

export interface LogPageLike<T> {
  total: number
  items: T[]
}

/** 這支流程只會用到這兩個端點；測試注入假的就數得出呼叫次數。 */
export interface RecordFlowApi<T extends FlowLog> {
  logs: (farmerId: string, params: { from: string; to: string; page: number }) => Promise<LogPageLike<T>>
}

export type DrawerState<P, L> =
  | { kind: 'record'; plan: P; eventId: string; title: string }
  | { kind: 'new'; plan: P; title: string }
  | { kind: 'view'; log: L; title: string }

/** 一次最多抓幾頁（後端一頁 50 筆）。超過就只畫最近的，並在圖下方說明，不假裝畫完了。 */
export const MAX_LOG_PAGES = 6

export function mergeLog<T extends { id: string }>(logs: readonly T[], saved: T): T[] {
  const index = logs.findIndex((l) => l.id === saved.id)
  return index < 0 ? [...logs, saved] : logs.map((l) => (l.id === saved.id ? saved : l))
}

export function createRecordFlow<P extends FlowPlan, L extends FlowLog>(api: RecordFlowApi<L>) {
  const logs = ref([]) as Ref<L[]>
  const truncated = ref(false)
  const drawer = ref(null) as Ref<DrawerState<P, L> | null>
  /** 測試用：這支流程自己打過幾次 API。正式程式碼不看它。 */
  let sequence = 0

  async function loadLogs(farmerId: string | null, range: { from: string; to: string } | null) {
    const token = ++sequence
    if (!farmerId || !range) {
      logs.value = []
      truncated.value = false
      return
    }
    try {
      const collected: L[] = []
      let total = 0
      for (let page = 1; page <= MAX_LOG_PAGES; page++) {
        const result = await api.logs(farmerId, { ...range, page })
        if (token !== sequence) return // 期間又變了，這批結果已經過期
        total = result.total
        collected.push(...result.items)
        if (collected.length >= result.total) break
      }
      logs.value = collected
      truncated.value = collected.length < total
    } catch {
      // 日誌抓不到不該讓整張複合圖壞掉：規劃本來就畫得出來，實際作業那條留空。
      if (token === sequence) {
        logs.value = []
        truncated.value = false
      }
    }
  }

  const logFor = (id: string | null | undefined) => (id ? logs.value.find((l) => l.id === id) ?? null : null)

  /** 點規劃事項：已經記過就看紀錄，沒記過才開記錄表單。 */
  function openRecord(plan: P, eventId: string) {
    const event = plan.events.find((e) => e.id === eventId)
    const existing = logFor(event?.log_id)
    drawer.value = existing
      ? { kind: 'view', log: existing, title: '這筆農事紀錄' }
      : { kind: 'record', plan, eventId, title: event ? `記錄「${event.title}」` : '記一筆' }
  }

  const openView = (log: L) => { drawer.value = { kind: 'view', log, title: '這筆農事紀錄' } }
  const openNew = (plan: P) => { drawer.value = { kind: 'new', plan, title: '記一筆規劃外的作業' } }
  const close = () => { drawer.value = null }

  /**
   * 存檔後就地更新，**不重抓**。
   *
   * `WorkLogForm` 已經把 log 存進去並回傳了，畫面需要的東西全在那個回傳值裡；
   * 再打一次 workspace／logs 只會把使用者的捲動位置與已選作物洗掉。
   * 回傳更新後的規劃（若有），由呼叫端塞回 workspace。
   */
  function saved(log: L): { id: string } | null {
    logs.value = mergeLog(logs.value, log)
    drawer.value = null
    return log.plan ?? null
  }

  return { logs, truncated, drawer, loadLogs, openRecord, openView, openNew, close, saved, logFor }
}
