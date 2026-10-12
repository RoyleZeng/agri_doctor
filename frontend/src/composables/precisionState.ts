/** 精準模式畫面用的純函式：不 import 任何模組，讓 `npm run test:precision` 直接用 node 跑。
 *
 * 卡片的狀態轉換、按鈕可不可以按，都集中在這裡，跟畫面元件分開——這樣才能離線測，
 * 不用真的開瀏覽器點過一輪三個按鈕 × 五種狀態 × 兩種角色的組合。
 */

export type RecommendationStatus = 'proposed' | 'accepted' | 'done' | 'dismissed' | 'expired'
export type PrecisionKind = 'irrigate' | 'vrt_fertilizer'

export interface FiveR {
  material: string
  rate: string
  place: string
  time: string
  method: string
}

interface RecommendationLike {
  id: string
  kind: PrecisionKind
  status: RecommendationStatus
  title: string
  field_id: string
  event_id: string | null
  work_log_id: string | null
  status_note: string
  expired_reason: string
}

// ---- 誰能按三個決策按鈕 ----

/** 建議只能由農民本人接受、標記已執行或略過；植物醫生看得到卡片，但顯示「由農民決定」，不是把按鈕灰掉。 */
export function canDecide(userId: string | null | undefined, farmerId: string): boolean {
  return !!userId && userId === farmerId
}

export function showActions(status: RecommendationStatus, userId: string | null | undefined, farmerId: string): boolean {
  return status === 'proposed' && canDecide(userId, farmerId)
}

export function showDone(status: RecommendationStatus, userId: string | null | undefined, farmerId: string): boolean {
  return (status === 'proposed' || status === 'accepted') && canDecide(userId, farmerId)
}

// 卡片元件本身沒有 userId/farmerId 可比對，只拿到父層已經算好的 canDecide；這兩個只看狀態。
export function canAccept(status: RecommendationStatus): boolean {
  return status === 'proposed'
}

export function canMarkDone(status: RecommendationStatus): boolean {
  return status === 'proposed' || status === 'accepted'
}

// ---- 卡片文字 ----

const STATUS_LABELS: Record<RecommendationStatus, string> = {
  proposed: '建議中',
  accepted: '已接受',
  done: '已執行',
  dismissed: '已略過',
  expired: '已過期',
}

export function statusLabel(status: RecommendationStatus): string {
  return STATUS_LABELS[status]
}

/** 卡片底部「已執行 2 項」那一行；已決定的建議用這句取代三個按鈕。 */
export function decidedSummary(rec: RecommendationLike, doneOn?: string): string {
  switch (rec.status) {
    case 'accepted':
      return doneOn ? `已排入 ${doneOn} 待辦` : '已排入待辦'
    case 'done':
      return '已記錄到農事日誌'
    case 'dismissed':
      return rec.status_note ? `已略過：${rec.status_note}` : '已略過'
    case 'expired':
      return rec.expired_reason || '已過期，請重新分析'
    default:
      return ''
  }
}

const FIVE_R_ORDER: (keyof FiveR)[] = ['material', 'rate', 'place', 'time', 'method']
const FIVE_R_LABELS: Record<keyof FiveR, string> = {
  material: '資材',
  rate: '用量',
  place: '地點',
  time: '時間',
  method: '方式',
}

/** 5R chip 列表，固定順序；值是空字串的欄位不顯示，不畫一個空 chip。 */
export function fiveRChips(five_r: FiveR): { label: string; value: string }[] {
  return FIVE_R_ORDER.filter((key) => five_r[key]?.trim()).map((key) => ({ label: FIVE_R_LABELS[key], value: five_r[key] }))
}

/** 重新分析後要有一句結果；沒有新建議、沒有撤回，畫面也不能什麼都沒發生——原因清單就在旁邊。 */
export function analyzeSummary(newCount: number, withdrawnCount: number, noteCount: number): string {
  const parts: string[] = []
  if (newCount) parts.push(`${newCount} 項建議`)
  if (withdrawnCount) parts.push(`${withdrawnCount} 項已撤回`)
  if (parts.length) return `已更新：${parts.join('、')}。`
  if (noteCount) return '目前沒有新建議，請看下方原因。'
  return '目前沒有可以分析的資料。'
}

// ---- 分區圖色階 ----
// 停格顏色都放這裡，元件不各自寫一份，四個圖層看起來才會一致。

export type LayerKind = 'ndvi' | 'soil_moisture' | 'ph' | 'yield_prev'

interface Palette {
  domain: [number, number]
  stops: string[] // 5 段，由低到高
  unit: string
  legendValues: number[] // 圖例上標示的刻度
}

export const PALETTES: Record<LayerKind, Palette> = {
  ndvi: { domain: [0.3, 0.75], stops: ['#e8dcc3', '#d5e6c3', '#a9cf94', '#6fb36a', '#2f8f5b'], unit: '', legendValues: [0.3, 0.4, 0.5, 0.6, 0.7] },
  soil_moisture: { domain: [15, 45], stops: ['#f9eee6', '#e9dcc9', '#b9cfe0', '#7ba7c9', '#2f6fb5'], unit: '%', legendValues: [15, 22, 30, 37, 45] },
  ph: { domain: [5, 7.5], stops: ['#f9eee6', '#f0e2d6', '#e3d4e6', '#c6a8d6', '#8a5fb0'], unit: '', legendValues: [5, 5.6, 6.2, 6.9, 7.5] },
  yield_prev: { domain: [2, 6], stops: ['#f9eee6', '#f6dfc4', '#f0c98f', '#c4923f', '#9a6208'], unit: '噸/ha', legendValues: [2, 3, 4, 5, 6] },
}

function hex(n: number): string {
  return Math.round(n).toString(16).padStart(2, '0')
}

function mixHex(a: string, b: string, t: number): string {
  const parse = (v: string) => [1, 3, 5].map((i) => parseInt(v.slice(i, i + 2), 16))
  const [ar, ag, ab] = parse(a)
  const [br, bg, bb] = parse(b)
  return `#${hex(ar + (br - ar) * t)}${hex(ag + (bg - ag) * t)}${hex(ab + (bb - ab) * t)}`
}

/** 給定圖層與數值，回一個可以直接當 fill 用的色碼；缺值回中性灰，不是白色（白色看起來像資料是 0，不是缺）。 */
export function colorForValue(kind: LayerKind, value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '#e2e7e1'
  const { domain, stops } = PALETTES[kind]
  const t = Math.max(0, Math.min(1, (value - domain[0]) / (domain[1] - domain[0])))
  const scaled = t * (stops.length - 1)
  const index = Math.min(stops.length - 2, Math.floor(scaled))
  return mixHex(stops[index], stops[index + 1], scaled - index)
}

// ---- 感測小卡（SensorTile）----

export type TileState = 'warn' | 'ok'

/** 低於（或高於，依 mode）門檻才標黃；門檻來自 API 的 rules，不寫死在元件裡。 */
export function tileState(value: number | null, threshold: number | null, mode: 'below' | 'above' = 'below'): TileState {
  if (value === null || threshold === null) return 'ok'
  return (mode === 'below' ? value < threshold : value > threshold) ? 'warn' : 'ok'
}

export function ageLabel(minutes: number | null): string {
  if (minutes === null) return '尚無資料'
  if (minutes < 1) return '剛剛更新'
  if (minutes < 60) return `${minutes} 分鐘前`
  const hours = Math.round(minutes / 60)
  return hours < 24 ? `${hours} 小時前` : `${Math.round(hours / 24)} 天前`
}

// ---- sparkline / 折線圖 ----

export function pointRange(values: number[], pad = 0.1): [number, number] {
  if (!values.length) return [0, 1]
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (min === max) return [min - 1, max + 1]
  const margin = (max - min) * pad
  return [min - margin, max + margin]
}

/** SVG polyline 的 points 字串；y 軸往下是正的，所以要把數值翻轉。 */
export function toPolyline(values: number[], width: number, height: number): string {
  if (values.length < 2) return ''
  const [min, max] = pointRange(values)
  const span = max - min || 1
  return values
    .map((value, index) => {
      const x = (index / (values.length - 1)) * width
      const y = height - ((value - min) / span) * height
      return `${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')
}

// ---- 精準模式啟用門檻 ----

/** 分頁一律存在；沒開啟精準模式又沒有示範資料時，畫面顯示說明＋啟用按鈕，不是整個分頁消失。 */
export function precisionUnlocked(precisionEnabled: boolean, hasField: boolean): boolean {
  return precisionEnabled || hasField
}
