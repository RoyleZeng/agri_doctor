export interface SourceMeta {
  status: string
  stale: boolean
  cached: boolean
  complete: boolean
  error: string | null
  fetched_at: string | null
  snapshot_id: string | null
  source_url: string
  key_configured: boolean
  notice: string
}
export interface Quote {
  date: string; crop_code: string; crop: string; market_code: string; market: string; category: string
  average: number | null; upper: number | null; middle: number | null; lower: number | null; volume_kg: number | null
}
export interface MarketData extends SourceMeta {
  rows: Quote[]
  closed: { date: string; market_code: string; market: string; category: string }[]
  query: string; requested_crop: string; from: string; to: string; ignored_rows: number
}
export interface CostDocument { title: string; year: number | null; group: string; kind: string; url: string | null }
export interface CostData extends SourceMeta { documents: CostDocument[]; query: string; total_documents: number }

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1/economics/${path}`, { credentials: 'include' })
  if (!response.ok) throw new Error(response.status === 401 ? '登入已過期，請重新登入。' : '暫時無法讀取農業開放資料，請稍後再試。')
  return response.json()
}
export const economics = {
  market: (crop: string) => get<MarketData>(`market?${new URLSearchParams({ crop, days: '7' })}`),
  costs: () => get<CostData>('costs'),
}
