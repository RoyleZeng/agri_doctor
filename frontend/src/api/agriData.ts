export interface Dataset {
  endpoint: string; name: string; group: string; available: boolean; status: string
  row_count: number; snapshot_id: string | null; fetched_at: string | null
  scope?: Record<string,string>
  last_attempt_at: string | null; last_error: string | null; source_url: string; hours: number; notice: string; exact_search: boolean
}
export interface DataPage {
  source: Dataset; fields: Record<string,string>
  items: { id: number; title: string; values: Record<string, unknown>; observation_state: string | null }[]
  total: number; page: number; page_size: number; query: string
}
async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1/agri-data/${path}`, { credentials: 'include' })
  if (!response.ok) throw new Error(response.status === 401 ? '登入已過期，請重新登入。' : '本地資料庫暫時無法讀取，請稍後重試。')
  return response.json()
}
export const agriData = {
  datasets: () => get<Dataset[]>('datasets'),
  records: (name: string, q: string, page: number) => get<DataPage>(`${encodeURIComponent(name)}/records?${new URLSearchParams({q,page:String(page)})}`),
}
