/** 農事日誌畫面用的純函式：不 import 任何模組，讓 `npm run test:farm-log` 直接用 node 跑。 */

export type LogKind = 'spray' | 'fertilize' | 'irrigate' | 'fieldwork' | 'harvest' | 'tillage' | 'scout' | 'other'

// 名稱與後端 services/farm_log.KIND_LABELS 一致；卡片標題由後端產生，兩邊不一致會看起來像兩種類型。
export const LOG_KINDS: { key: LogKind; name: string; tone: string; icon: string }[] = [
  { key: 'spray', name: '施藥', tone: 'spray', icon: 'spray' },
  { key: 'fertilize', name: '施肥', tone: 'sack', icon: 'sack' },
  { key: 'irrigate', name: '灌溉', tone: 'drop', icon: 'drop' },
  { key: 'fieldwork', name: '田間作業', tone: 'work', icon: 'scissors' },
  { key: 'harvest', name: '採收', tone: 'harvest', icon: 'basket' },
  { key: 'tillage', name: '整地', tone: 'soil', icon: 'shovel' },
  { key: 'scout', name: '巡田', tone: 'eye', icon: 'eye' },
  { key: 'other', name: '其他', tone: 'other', icon: 'note' },
]

export const kindOf = (key: string) => LOG_KINDS.find((kind) => kind.key === key) ?? LOG_KINDS[LOG_KINDS.length - 1]

// 與後端 COMPLIANCE_FIELDS 同一份清單，只用來在表單上即時提示；存檔時的判定以後端算好的 compliance 為準。
const MATERIAL_FIELDS = ['material_name', 'material_supplier', 'material_lot', 'amount_value', 'amount_unit', 'area_text']
export const COMPLIANCE_FIELDS: Partial<Record<LogKind, string[]>> = {
  spray: MATERIAL_FIELDS,
  fertilize: MATERIAL_FIELDS,
  harvest: ['crop_text', 'area_text', 'yield_value', 'yield_unit'],
}

export const FIELD_LABELS: Record<string, string> = {
  material_name: '資材品名',
  material_supplier: '供應者',
  material_lot: '批號',
  amount_value: '用量',
  amount_unit: '用量單位',
  area_text: '面積',
  crop_text: '作物',
  yield_value: '產量',
  yield_unit: '產量單位',
}

export function missingFields(kind: LogKind | '', values: Record<string, unknown>): string[] {
  if (!kind) return []
  return (COMPLIANCE_FIELDS[kind] ?? []).filter((name) => !String(values[name] ?? '').trim())
}

/** 「用量」與「用量單位」同時缺時只說一次用量，農民看到的是一個欄位不是兩個。 */
export function missingText(fields: string[]): string {
  const labels = fields
    .filter((name) => !(name.endsWith('_unit') && fields.includes(name.replace('_unit', '_value'))))
    .map((name) => FIELD_LABELS[name] ?? name)
  return labels.join('、')
}

export const MOA_MISSING = '未帶入作用機制，輪替檢查將顯示無法判斷'

/** 施藥的作用機制來源；手動輸入的藥名不反查登記，所以只有處方或挑選的登記算數。 */
export function moaSource(kind: LogKind | '', hasPrescription: boolean, hasRegistration: boolean): 'na' | 'prescription' | 'registration' | 'missing' {
  if (kind !== 'spray') return 'na'
  if (hasPrescription) return 'prescription'
  return hasRegistration ? 'registration' : 'missing'
}

export function canBackfillRegistration(log: { kind: string; registration_snapshot: unknown; prescription_item_id: string | null }): boolean {
  return log.kind === 'spray' && !log.registration_snapshot && !log.prescription_item_id
}

/** 植物醫生可以替農場記錄，但別人記的只有農民本人或原記錄者能改。 */
export function canModifyLog(log: { farmer_id: string; created_by: string }, userId: string | null | undefined): boolean {
  return !!userId && (userId === log.farmer_id || userId === log.created_by)
}

export function saveLabel(options: { kind: LogKind | ''; fromEvent: boolean; editing: boolean }): string {
  if (options.editing) return '儲存修改'
  if (options.fromEvent) return '儲存到農事日誌並標完成'
  // 施藥會進輪替檢查的歷史，按鈕上講清楚，農民才知道這筆不是只給自己看的。
  if (options.kind === 'spray') return '儲存 · 並寫入施藥紀錄'
  return '儲存到農事日誌'
}

// ---- 日期：一律當日曆日處理，不經過瀏覽器時區 ----

const DAY = 86_400_000
const WEEKDAYS = '日一二三四五六'

export function dayNumber(isoDate: string): number {
  const [year, month, day] = isoDate.split('-').map(Number)
  return Date.UTC(year, month - 1, day) / DAY
}

export function addDays(isoDate: string, days: number): string {
  return new Date((dayNumber(isoDate) + days) * DAY).toISOString().slice(0, 10)
}

export function shortDay(isoDate: string): string {
  return `${Number(isoDate.slice(5, 7))}/${Number(isoDate.slice(8, 10))}`
}

export function dayHeading(isoDate: string): string {
  const weekday = new Date(dayNumber(isoDate) * DAY).getUTCDay()
  return `${shortDay(isoDate)} 週${WEEKDAYS[weekday]}`
}

export function monthRange(month: string): { from: string; to: string } {
  const [year, number] = month.split('-').map(Number)
  const last = new Date(Date.UTC(year, number, 0)).getUTCDate()
  return { from: `${month}-01`, to: `${month}-${String(last).padStart(2, '0')}` }
}

export function recentMonths(today: string, count = 13): string[] {
  let year = Number(today.slice(0, 4))
  let month = Number(today.slice(5, 7))
  const months: string[] = []
  for (let i = 0; i < count; i++) {
    months.push(`${year}-${String(month).padStart(2, '0')}`)
    month -= 1
    if (month === 0) {
      month = 12
      year -= 1
    }
  }
  return months
}

export function monthLabel(month: string): string {
  return `${Number(month.slice(0, 4))} 年 ${Number(month.slice(5, 7))} 月`
}

export const isMonth = (value: unknown): value is string =>
  typeof value === 'string' && /^\d{4}-(0[1-9]|1[0-2])$/.test(value)

// ---- 列表 ----

interface Sortable {
  id: string
  done_on: string
  done_time: string | null
  created_at: string
}

/** 與後端列表同一個排序：作業日新到舊、有時間的在前、同時間後記的在前。 */
export function compareLogs(a: Sortable, b: Sortable): number {
  if (a.done_on !== b.done_on) return a.done_on < b.done_on ? 1 : -1
  if (a.done_time !== b.done_time) {
    if (a.done_time === null) return 1
    if (b.done_time === null) return -1
    return a.done_time < b.done_time ? 1 : -1
  }
  if (a.created_at !== b.created_at) return a.created_at < b.created_at ? 1 : -1
  return a.id < b.id ? -1 : 1
}

/** 存檔後把回傳的紀錄放進列表，不整頁重載；同一筆就換掉，其他卡片不動。 */
export function upsertLog<T extends Sortable>(logs: T[], log: T): T[] {
  const others = logs.filter((item) => item.id !== log.id)
  return [...others, log].sort(compareLogs)
}

export function groupByDay<T extends { done_on: string; photos: unknown[] }>(logs: T[]) {
  const groups: { date: string; items: T[]; photoCount: number }[] = []
  for (const log of logs) {
    let group = groups[groups.length - 1]
    if (!group || group.date !== log.done_on) {
      group = { date: log.done_on, items: [], photoCount: 0 }
      groups.push(group)
    }
    group.items.push(log)
    group.photoCount += log.photos.length
  }
  return groups
}

export function daySubtitle(group: { date: string; items: unknown[]; photoCount: number }, today: string): string {
  const parts = [`${group.items.length} 筆`, group.photoCount ? `${group.photoCount} 張照片` : '無照片']
  if (group.date === today) parts.unshift('今天')
  return parts.join(' · ')
}

export function matchesFilters(
  log: { done_on: string; kind: string; field_text: string },
  filters: { month: string; kind: string; field: string },
): boolean {
  const { from, to } = monthRange(filters.month)
  return log.done_on >= from && log.done_on <= to
    && (!filters.kind || log.kind === filters.kind)
    && (!filters.field || log.field_text === filters.field)
}

export function exportRangeError(from: string, to: string): string {
  if (!from || !to) return '請選擇起訖日期'
  if (from > to) return '起始日期不能晚於結束日期'
  return ''
}

/** 下載檔名：農場名稱裡的路徑與保留字元換掉，避免在 Windows 存不了檔。 */
export function exportFileName(farmName: string, from: string, to: string, format: 'pdf' | 'xlsx'): string {
  const name = farmName.replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '') || '農場'
  return `${name}_農事紀錄表_${from.replaceAll('-', '')}-${to.replaceAll('-', '')}.${format}`
}

/** 安全採收期只在有登記快照時算；沒有來源就不顯示，不猜。 */
export function harvestAfter(doneOn: string, phiDays: number | null | undefined): string | null {
  return phiDays === null || phiDays === undefined ? null : addDays(doneOn, phiDays)
}
