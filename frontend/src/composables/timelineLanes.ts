/** 複合圖 v3 的色帶邏輯：不 import 任何模組，讓 `npm run test:timeline` 直接用 node 跑。
 *
 * 為什麼抽出來：這裡的判斷（日誌該落在哪條色帶、對到哪個規劃事項、早幾天晚幾天、
 * 哪些事項算逾期未記錄）是農民會拿去對產銷履歷的東西，錯一天就是錯的，
 * 不該只靠開瀏覽器看。位置換算（％、px）留在元件裡，那是版面不是規則。
 */

export type LaneKey = 'stage' | 'planned' | 'actual' | 'pest'

/** 固定四條。原本農事管理與肥培管理各一條，合併成「規劃事項」用顏色區分，空出的高度給「實際作業」。 */
export const LANES: {
  key: LaneKey
  name: string
  short: string
  note: string
  icon: 'leaf' | 'check' | 'note' | 'bug'
}[] = [
  { key: 'stage', name: '生育時期', short: '生育', note: '範本估計', icon: 'leaf' },
  { key: 'planned', name: '規劃事項', short: '規劃', note: '農事＋肥培', icon: 'check' },
  { key: 'actual', name: '實際作業', short: '實際', note: '來自農事日誌', icon: 'note' },
  { key: 'pest', name: '病蟲害巡查', short: '巡查', note: '觀察窗口', icon: 'bug' },
]

/** 規劃事項的 `kind` 對應到哪一條色帶；`actual` 不在這裡，它的來源是日誌不是規劃。 */
export const LANE_OF_KIND: Record<string, LaneKey> = {
  stage: 'stage',
  care: 'planned',
  nutrition: 'planned',
  pest: 'pest',
}

/** 日誌點顏色與日誌卡 `.lic` 同一套：同一件事在兩個畫面不該是兩個顏色。 */
export const LOG_COLORS: Record<string, string> = {
  spray: '#b23b2d',
  fertilize: '#c07a16',
  irrigate: '#2f6fb5',
  fieldwork: '#2f8f5b',
  tillage: '#2f8f5b',
  harvest: '#7a5230',
  scout: '#865e20',
  other: '#617066',
}
export const logColor = (kind: string) => LOG_COLORS[kind] ?? LOG_COLORS.other

export interface LaneEvent {
  id: string
  kind: string
  title: string
  start_on: string
  end_on: string
  completed: boolean
  log_id?: string | null
}

export interface LaneLog {
  id: string
  kind: string
  title: string
  done_on: string
  event_id: string | null
  photo_count: number
}

export interface PlacedLog {
  log: LaneLog
  lane: 'actual' | 'pest'
  /** 畫點用的日期；對到規劃事項時是事項的中點（規格要求畫在「正下方」），否則是實際完成日。 */
  anchor: string
  event: LaneEvent | null
  /** 實際完成日與規劃窗口差幾天：負數＝早，正數＝晚，0＝落在窗口內。 */
  offsetDays: number | null
  offsetText: string
  photo: boolean
}

const DAY = 86400000
const dayNumber = (isoDate: string) => Math.floor(Date.parse(isoDate + 'T00:00:00Z') / DAY)
const isoDate = (n: number) => new Date(n * DAY).toISOString().slice(0, 10)

/** 事項窗口的中點，用來把日誌點對齊到規劃事項正下方。 */
export function midpoint(e: { start_on: string; end_on: string }): string {
  const a = dayNumber(e.start_on)
  const b = dayNumber(e.end_on)
  if (!Number.isFinite(a) || !Number.isFinite(b)) return e.start_on
  return isoDate(Math.floor((a + b) / 2))
}

/** 早／晚幾天：落在規劃窗口內回 0，比窗口早回負數，比窗口晚回正數。 */
export function offsetFrom(e: { start_on: string; end_on: string }, doneOn: string): number {
  const done = dayNumber(doneOn)
  const start = dayNumber(e.start_on)
  const end = dayNumber(e.end_on)
  if (!Number.isFinite(done) || !Number.isFinite(start) || !Number.isFinite(end)) return 0
  if (done < start) return done - start
  if (done > end) return done - end
  return 0
}

export const offsetLabel = (days: number) =>
  days === 0 ? '如期' : days < 0 ? `早 ${-days} 天` : `晚 ${days} 天`

/**
 * 逾期未記錄：規劃窗口已經過去、沒有對應日誌，而且沒有被標完成。
 *
 * 只挑 care／nutrition——生育時期是範本估計不是待辦，巡查是觀察窗口，
 * 兩者「沒做」都不是農民的疏漏，標黃框只會讓整張圖都是黃框。
 */
export function isOverdue(e: LaneEvent, today: string): boolean {
  if (LANE_OF_KIND[e.kind] !== 'planned') return false
  return e.end_on < today && !e.completed && !e.log_id
}

/**
 * 把日誌放到色帶上。
 *
 * `kind=scout` 走巡查色帶（規格要求）：巡田本身就是在回應巡查窗口，
 * 放在實際作業裡會跟施藥施肥混在一起，看不出「有沒有去看」。
 */
export function placeLogs(events: LaneEvent[], logs: LaneLog[]): PlacedLog[] {
  const byId = new Map(events.map((e) => [e.id, e]))
  return logs.map((log) => {
    const event = (log.event_id && byId.get(log.event_id)) || null
    const offsetDays = event ? offsetFrom(event, log.done_on) : null
    return {
      log,
      lane: log.kind === 'scout' ? 'pest' : 'actual',
      anchor: event ? midpoint(event) : log.done_on,
      event,
      offsetDays,
      offsetText: offsetDays === null ? '' : offsetLabel(offsetDays),
      photo: log.photo_count > 0,
    }
  })
}

/**
 * 同一天附近的點會疊在一起，往下排第二、第三列。
 *
 * 用 px 而不是日期判斷：點的大小是固定的 16px，兩筆日誌差 3 天在「每週」刻度下
 * 分得開、在「30 天」刻度下會重疊，重不重疊是版面問題不是日期問題。
 */
export function stackRows(xs: number[], minGap: number): number[] {
  const rowLastX: number[] = []
  const order = xs.map((x, i) => ({ x, i })).sort((a, b) => a.x - b.x || a.i - b.i)
  const rows = new Array<number>(xs.length).fill(0)
  for (const { x, i } of order) {
    let row = rowLastX.findIndex((last) => x - last >= minGap)
    if (row < 0) {
      row = rowLastX.length
      rowLastX.push(x)
    } else rowLastX[row] = x
    rows[i] = row
  }
  return rows
}

/** 橫軸要不要往種植日之前延伸，延伸幾天。 */
export const PRE_PLANT_LEAD = 14
export const PRE_PLANT_MAX = 45

/**
 * 複合圖橫軸的起點（以「天」為單位的整數，不是日期字串）。
 *
 * 為什麼不直接用最早的種植日：整地與基肥本來就發生在種植之前，日誌記了卻畫不進圖裡，
 * 農民會以為系統沒收到。所以固定先往前 14 天，若有更早的日誌就跟著往前，但最多 45 天——
 * 再往前多半是上一期的作業，畫進來只會讓這一期的色帶被壓扁。
 *
 * @param plantedDays  各期作物的種植日（天）
 * @param logDays      這些作物相關日誌的完成日（天）；沒有就傳空陣列
 */
export function axisStart(plantedDays: readonly number[], logDays: readonly number[]): number {
  const planted = Math.min(...plantedDays)
  if (!Number.isFinite(planted)) return planted
  const earliestLog = logDays.length ? Math.min(...logDays) : Infinity
  const wanted = Math.min(planted - PRE_PLANT_LEAD, earliestLog)
  return Math.max(wanted, planted - PRE_PLANT_MAX)
}

export interface RegionAlertLike {
  pest_text: string
  metric: string
  value: number
  warn_value: number
  basis: string
}

/**
 * 巡查事項對上區域警戒。
 *
 * 這裡**是**用子字串比對，跟專案「藥劑名不可用子字串比對」的規則相反，理由不同：
 * 藥劑要比的是「這支藥是不是那支藥」，複方名稱包含單方名稱會給出錯誤的用藥結論；
 * 這裡比的是「附近在流行的東西，跟我要巡的是不是同一類」，
 * 「稻熱病」對上「葉稻熱病」、「薊馬」對上「小黃薊馬」正是想要的結果。
 * 而且這個提示只是叫農民多看一眼，不會改變任何用藥或日期。
 *
 * 即使如此仍然只做雙向包含、不做分詞：寧可漏標，也不要把「二化螟」標成「三化螟」那種錯。
 */
export function matchRegionAlert<T extends RegionAlertLike>(
  alerts: readonly T[],
  eventTitle: string,
): T | null {
  return (
    alerts.find(
      (a) => a.pest_text && (eventTitle.includes(a.pest_text) || a.pest_text.includes(eventTitle)),
    ) ?? null
  )
}

/* ---- 「規劃 vs 實際」面板 ---- */

export type PlannedStatus = 'done' | 'pending' | 'overdue' | 'upcoming'

export interface PlannedRow {
  event: LaneEvent
  status: PlannedStatus
  /** 對到這個事項的日誌（可能不只一筆，例如分兩天做完）。 */
  logs: LaneLog[]
  /** 第一筆實際完成日；沒有就是 null。 */
  doneOn: string | null
  offsetDays: number | null
  offsetText: string
}

/**
 * 規劃事項現在是什麼狀態。
 *
 * 四種刻意分開：**已做**（有日誌，事實）、**待記錄**（窗口內還沒記）、
 * **逾期**（窗口過了還沒記）、**未到**（窗口還沒開始）。
 * 「標完成但沒日誌」算待記錄或逾期而不是已做——完成是宣稱，日誌才是紀錄，
 * 產銷履歷要的是後者。
 */
export function plannedStatus(e: LaneEvent, logs: LaneLog[], today: string): PlannedStatus {
  if (logs.length) return 'done'
  if (e.start_on > today) return 'upcoming'
  return e.end_on < today ? 'overdue' : 'pending'
}

/** 面板上半部：規劃事項逐列。只看「規劃事項」色帶那些 kind，生育與巡查不是待辦。 */
export function plannedRows(events: LaneEvent[], placed: PlacedLog[], today: string): PlannedRow[] {
  const byEvent = new Map<string, LaneLog[]>()
  for (const p of placed) {
    if (!p.event) continue
    const list = byEvent.get(p.event.id) ?? []
    list.push(p.log)
    byEvent.set(p.event.id, list)
  }
  return events
    .filter((e) => LANE_OF_KIND[e.kind] === 'planned')
    .sort((a, b) => a.start_on.localeCompare(b.start_on) || a.id.localeCompare(b.id))
    .map((event) => {
      const logs = (byEvent.get(event.id) ?? []).sort((a, b) => a.done_on.localeCompare(b.done_on))
      const doneOn = logs[0]?.done_on ?? null
      const offsetDays = doneOn === null ? null : offsetFrom(event, doneOn)
      return {
        event,
        status: plannedStatus(event, logs, today),
        logs,
        doneOn,
        offsetDays,
        offsetText: offsetDays === null ? '' : offsetLabel(offsetDays),
      }
    })
}

/** 面板下半部：沒有對到任何規劃事項的實際作業，新的在前。 */
export function unplannedLogs(placed: PlacedLog[]): LaneLog[] {
  return placed
    .filter((p) => !p.event)
    .map((p) => p.log)
    .sort((a, b) => b.done_on.localeCompare(a.done_on) || b.id.localeCompare(a.id))
}

export type PanelRange = 'week' | 'month' | 'season'

/**
 * 範圍切換。`season`＝整期（不篩），其餘以今天為基準往前後各推。
 *
 * 規劃事項看的是「窗口有沒有跟範圍重疊」而不是「起日在不在範圍內」：
 * 一個跨三週的窗口不該因為起日在上個月就從「本月」消失。
 */
export function rangeBounds(range: PanelRange, today: string): { from: string; to: string } | null {
  if (range === 'season') return null
  const days = range === 'week' ? 7 : 30
  const t = Math.floor(Date.parse(today + 'T00:00:00Z') / 86400000)
  const iso = (n: number) => new Date(n * 86400000).toISOString().slice(0, 10)
  return { from: iso(t - days), to: iso(t + days) }
}

export const rowInRange = (row: PlannedRow, bounds: { from: string; to: string } | null): boolean =>
  !bounds || (row.event.start_on <= bounds.to && row.event.end_on >= bounds.from)

export const logInRange = (log: LaneLog, bounds: { from: string; to: string } | null): boolean =>
  !bounds || (log.done_on >= bounds.from && log.done_on <= bounds.to)


export interface PlanLike {
  id: string
  field_name: string
  planted_on: string
  end_on: string
  events: LaneEvent[]
}

export interface RawLog extends LaneLog {
  plan_id: string | null
  field_text: string
}

/**
 * 這一期作物的日誌。優先用 `plan_id`；沒綁規劃的用「同田區且落在本期期間」對回來——
 * 農民直接記一筆時不一定會選規劃，但那確實是這塊田這一期做的事，不該從圖上消失。
 *
 * 期間的起點往種植日之前推（與橫軸同一個上限）：整地、基肥就是在下田之前做的。
 *
 * 圖與「規劃 vs 實際」面板都叫這一支：兩邊各寫一次配對規則，遲早會對不起來。
 */
export function logsForPlan<T extends RawLog>(plan: PlanLike, logs: readonly T[]): T[] {
  const dayNum = (d: string) => Math.floor(Date.parse(d + 'T00:00:00Z') / DAY)
  const from = isoDate(dayNum(plan.planted_on) - PRE_PLANT_MAX)
  return logs.filter((l) =>
    l.plan_id
      ? l.plan_id === plan.id
      : l.field_text === plan.field_name && l.done_on >= from && l.done_on <= plan.end_on)
}

/**
 * 存檔後把回傳的日誌併進既有陣列：同 id 取代，新的加在後面。
 *
 * 抽出來是為了測得到「存檔之後**不用**重抓」這件事的另一半——
 * 只要這支函式算得出正確的新狀態，畫面就不需要再打一次 API。
 * （沒有重抓這件事本身用瀏覽器數 API 呼叫驗證，見 docs/ux-round4.md 第 3 步。）
 */
export function mergeSavedLog<T extends { id: string }>(logs: readonly T[], saved: T): T[] {
  const index = logs.findIndex((l) => l.id === saved.id)
  if (index < 0) return [...logs, saved]
  return logs.map((l) => (l.id === saved.id ? saved : l))
}
