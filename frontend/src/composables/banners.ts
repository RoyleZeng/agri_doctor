/** 橫幅的排序、關閉與作用機制比對：不 import 任何模組，讓 `npm run test:banners` 直接用 node 跑。
 *
 * 為什麼這幾條要測：橫幅最常見的錯法是「該出現的沒出現」或「關掉之後永遠不再出現」，
 * 兩種都不會讓建置失敗，而且要等到真的遇到抗藥性連三次、或等七天之後才看得到。
 */

export type BannerTone = 'crit' | 'warn' | 'info' | 'demo' | 'ok'

export interface Banner {
  /** 同一條橫幅在不同時間要算成同一條（用來記關閉狀態）；由 tone＋內容算出來。 */
  id: string
  tone: BannerTone
  title: string
  body?: string
  /** 可關閉的橫幅關掉之後隔多久再出現；`0`＝不可關閉。 */
  dismissDays?: number
}

/** 嚴重的在上。同一頁最多兩條——第三條開始沒有人會讀。 */
export const TONE_ORDER: BannerTone[] = ['crit', 'warn', 'info', 'demo', 'ok']
export const MAX_VISIBLE = 2

/**
 * 內容雜湊。關閉狀態要綁在「這一條訊息」上，不是綁在 tone 上：
 * 警戒的病蟲害換了一種、資料來源換了一個，就該重新出現，不能被上一條的關閉狀態蓋掉。
 */
export function bannerId(tone: BannerTone, ...parts: (string | number | null | undefined)[]): string {
  const text = parts.filter((p) => p !== null && p !== undefined && p !== '').join('|')
  let hash = 5381
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0
  return `${tone}:${(hash >>> 0).toString(36)}`
}

/** 關閉紀錄：id → 關閉當下的時間戳（毫秒）。 */
export type Dismissals = Record<string, number>

export function isDismissed(banner: Banner, dismissals: Dismissals, now: number): boolean {
  if (!banner.dismissDays) return false // 不可關閉的橫幅永遠顯示
  const at = dismissals[banner.id]
  if (!at) return false
  return now - at < banner.dismissDays * 86_400_000
}

/**
 * 決定這一頁實際要畫哪幾條。
 *
 * 排序用 tone 的嚴重度，同嚴重度維持傳進來的順序——呼叫端知道哪一條比較貼近使用者現在在做的事。
 */
export function visibleBanners(banners: Banner[], dismissals: Dismissals, now: number): Banner[] {
  return banners
    .filter((b) => !isDismissed(b, dismissals, now))
    .map((b, index) => ({ b, index }))
    .sort((x, y) =>
      TONE_ORDER.indexOf(x.b.tone) - TONE_ORDER.indexOf(y.b.tone) || x.index - y.index)
    .slice(0, MAX_VISIBLE)
    .map((entry) => entry.b)
}

/** 過期的關閉紀錄要清掉，否則 localStorage 會一直長大。 */
export function pruneDismissals(dismissals: Dismissals, now: number, keepDays = 30): Dismissals {
  const out: Dismissals = {}
  for (const [id, at] of Object.entries(dismissals)) {
    if (now - at < keepDays * 86_400_000) out[id] = at
  }
  return out
}

/* ---- 作用機制比對（輪替橫幅的「換一支不同機制的藥」用） ---- */

/**
 * 把 PPM 的原始代碼正規化成 `FRAC 3` 這種形式，跟後端 `resistance.parse_moa_codes` 同一套規則：
 * 去掉括號註解、取分號前那段當代表。兩邊不一致的話，後端說要排除 FRAC 3、
 * 前端卻因為字串長得不一樣而沒排到，畫面就會推薦一支正要避開的藥。
 */
export function normalizeMoa(raw: string): string | null {
  const text = raw.replace(/[（(].*?[)）]/g, '').trim()
  const match = /\b(FRAC|IRAC|HRAC)\b/i.exec(text)
  if (!match) return null
  const scheme = match[1]!.toUpperCase()
  const group = text.slice(match.index + match[0].length).replace(/^[\s:：]+/, '').split(';')[0]!.trim()
  return group ? `${scheme} ${group}` : null
}

export const normalizeMoaList = (codes: string[] | null | undefined): string[] =>
  [...new Set((codes ?? []).map(normalizeMoa).filter((c): c is string => !!c))]

/** 這支藥是不是正好用了要避開的機制。 */
export function usesExcludedMoa(codes: string[] | null | undefined, exclude: string[]): boolean {
  if (!exclude.length) return false
  const mine = new Set(normalizeMoaList(codes))
  return exclude.some((code) => mine.has(code))
}

export interface MoaSplit<T> {
  /** 可以換的：沒有用到要避開的機制。 */
  allowed: T[]
  /** 用了要避開的機制的：**不刪掉**，收起來讓使用者自己決定要不要看。 */
  excluded: T[]
  /** 機制未載明的：不能當成安全，跟 allowed 分開標。 */
  unknown: T[]
}

/**
 * 依作用機制把候選分成三堆。
 *
 * 刻意不直接丟掉「同機制」那堆：平台的立場是把對不起來的地方標出來，不是替植物醫生做決定。
 * 有時候就是只剩那一支藥可用，畫面要讓他看得到並自己承擔判斷。
 */
export function splitByMoa<T>(items: T[], codesOf: (item: T) => string[] | null | undefined,
                              exclude: string[]): MoaSplit<T> {
  const split: MoaSplit<T> = { allowed: [], excluded: [], unknown: [] }
  for (const item of items) {
    const codes = normalizeMoaList(codesOf(item))
    if (!codes.length) split.unknown.push(item)
    else if (usesExcludedMoa(codesOf(item), exclude)) split.excluded.push(item)
    else split.allowed.push(item)
  }
  return split
}
