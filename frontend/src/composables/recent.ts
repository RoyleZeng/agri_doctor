import { reactive } from 'vue'

/** 最近查過的 作物 × 病蟲害，存在瀏覽器本機。
 *
 * 植物醫生一天會來回比對同幾組，回頭找比重新搜快。
 * 只存識別資訊與三源筆數，不存藥劑內容 —— 資料會更新，快取住反而危險。
 */
export interface RecentQuery {
  cropId: number
  pestId: number
  cropName: string
  pestName: string
  ppm: number
  fda: number
  kdais: number
  at: number
}

const KEY = 'agri-doctor.recent'
const LIMIT = 6

function load(): RecentQuery[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    return Array.isArray(parsed) ? (parsed as RecentQuery[]).slice(0, LIMIT) : []
  } catch {
    return []
  }
}

const state = reactive<{ items: RecentQuery[] }>({ items: load() })

export function remember(entry: Omit<RecentQuery, 'at'>): void {
  const items = state.items.filter(
    (r) => !(r.cropId === entry.cropId && r.pestId === entry.pestId),
  )
  items.unshift({ ...entry, at: Date.now() })
  state.items = items.slice(0, LIMIT)
  try {
    localStorage.setItem(KEY, JSON.stringify(state.items))
  } catch {
    /* 無痕視窗或封鎖 storage 時就只在這次工作階段有效 */
  }
}

export const recentQueries = state
