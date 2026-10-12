import type {
  Comparison, Crop, CropPests, IngredientOverview, IssueListItem, KdaisPendingRow, Pest,
  PestCrops, SearchResult,
  SourceInfo, Suggest,
  CoverageOverview, UpdateFeed, ConsultCase, ConsultCaseSummary, PrescriptionItem,
  RegistrationSearchItem,
} from './types'

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const { headers: suppliedHeaders, ...options } = init ?? {}
  const resp = await fetch(`${BASE}${path}`, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', 'X-Planning-Request': '1', ...suppliedHeaders },
    ...options,
  })
  if (!resp.ok) {
    // FastAPI 的錯誤訊息放在 detail，直接透出來給使用者看比較有用。
    let message = `${resp.status} ${resp.statusText}`
    try {
      const body = await resp.json()
      if (body?.detail) {
        if (typeof body.detail === 'string') message = body.detail
        else if (typeof body.detail?.message === 'string') message = body.detail.message
      }
    } catch {
      /* 回應不是 JSON，用狀態碼當訊息 */
    }
    throw new Error(message)
  }
  return resp.json() as Promise<T>
}

const qs = (params: Record<string, string | number | undefined>) => {
  const search = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') search.set(k, String(v))
  }
  return search.toString()
}

export const api = {
  coverage: () => request<CoverageOverview>('/coverage'),
  updates: (offset = 0) => request<UpdateFeed>(`/updates?days=7&limit=10&offset=${offset}`),
  updateSchedule: () => request<{ name: string; status: string; next_run_at: string | null; schedule_label: string }[]>('/update-schedule'),
  /** 一個搜尋框查作物／病害／害蟲，並支援「芒果 炭疽」這種兩個詞的輸入。 */
  search: (q: string) => request<SearchResult>(`/search?${qs({ q })}`),

  /** 三源目前的資料狀態與版本。 */
  sourceStatus: () => request<Record<string, SourceInfo>>('/sources'),

  /** 先選病蟲害時，列出有核准用藥的作物與三源筆數。 */
  cropsForPest: (pestId: number) => request<PestCrops>(`/pests/${pestId}/crops`),

  /** 先選作物時的反向：列出這個作物有核准用藥的病蟲害。 */
  pestsForCrop: (cropId: number) => request<CropPests>(`/crops/${cropId}/pest-cards`),

  searchCrops: (q: string) =>
    request<Suggest[]>(`/crops/search?${qs({ q, limit: 20 })}`),

  getCrop: (id: number) => request<Crop>(`/crops/${id}`),

  recommend: (cropId: number, pestId: number) =>
    request<Comparison>(`/recommend?${qs({ crop_id: cropId, pest_id: pestId })}`),

  /** 藥名或成分名搜尋會逐筆比對三個來源，呼叫端必須 debounce。 */
  /** 藥劑卡那一行：這個配方有幾張許可證、幾家廠商。三元組任一項對不到就 matched=false。 */
  formulationPermits: (row: { english_composition: string | null; concentration: string | null; formulation: string | null }) =>
    request<{
      matched: boolean
      reason: 'unmappable' | 'not_found' | null
      summary: { key: string; permits: number; vendors: number; revoked: number; all_revoked: boolean } | null
    }>(
      `/vendors/formulations/summary?${qs({
        ingredient: row.english_composition ?? '',
        contents: row.concentration ?? '',
        formulation: row.formulation ?? '',
      })}`,
    ),
  searchRegistrations: (q: string, cropId?: number) =>
    request<{ total: number; offset: number; limit: number; items: RegistrationSearchItem[]; notice: string }>(
      `/registrations/search?${qs({ q, crop_id: cropId, limit: 30 })}`,
    ),

  consultCases: () => request<{ total: number; items: ConsultCaseSummary[] }>('/consult/cases'),
  consultCase: (id: string) => request<ConsultCase>(`/consult/cases/${encodeURIComponent(id)}`),
  issueConsultCase: (id: string, version: number) => request<ConsultCase>(`/consult/cases/${encodeURIComponent(id)}/issue`, { method: 'POST', body: JSON.stringify({ version }) }),
  consultReport: async (id: string): Promise<Blob> => {
    const response = await fetch(`/api/v1/consult/cases/${encodeURIComponent(id)}/report.pdf`, { credentials: 'same-origin' })
    if (!response.ok) throw new Error('無法取得診斷書，請確認登入與案件權限後重試。')
    return response.blob()
  },
  createConsultCase: (data: Record<string, unknown>) =>
    request<ConsultCase>('/consult/cases', { method: 'POST', body: JSON.stringify(data) }),
  patchConsultCase: (id: string, data: Record<string, unknown>) =>
    request<ConsultCase>(`/consult/cases/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(data) }),
  addConsultItem: (caseId: string, registrationId: number, version: number) =>
    request<PrescriptionItem>(`/consult/cases/${encodeURIComponent(caseId)}/items`, {
      method: 'POST', body: JSON.stringify({ registration_id: registrationId, version }),
    }),
  patchConsultItem: (caseId: string, itemId: string, data: Record<string, unknown>) =>
    request<PrescriptionItem>(`/consult/cases/${encodeURIComponent(caseId)}/items/${encodeURIComponent(itemId)}`, {
      method: 'PATCH', body: JSON.stringify(data),
    }),
  deleteConsultItem: (caseId: string, itemId: string, version: number) =>
    request<{ ok: boolean; case_version: number }>(
      `/consult/cases/${encodeURIComponent(caseId)}/items/${encodeURIComponent(itemId)}?${qs({ version })}`,
      { method: 'DELETE' },
    ),

  searchIngredients: (q: string) =>
    request<{ id: number; name: string; english_name: string | null }[]>(
      `/ingredients/search?${qs({ q })}`,
    ),

  /** 成分頁需要的全部資料，一次拿完。 */
  ingredientOverview: (id: number) => request<IngredientOverview>(`/ingredients/${id}/overview`),

  ingredientMrl: (id: number) => request<{
    ingredient: { id: number; name: string; english_name: string | null }
    limits: { crop_category: string; limit_ppm: string | null; limit_text: string | null; note: string | null }[]
  }>(`/ingredients/${id}/mrl`),

  ingredientUsages: (id: number) => request<{
    crop_id: number; crop: string; pest_id: number; pest: string
    product: string; dilution: string | null; phi_days: number | null
  }[]>(`/ingredients/${id}/usages`),

  issues: (params: { issue_type?: string; severity?: string; limit?: number; offset?: number }) =>
    request<{ total: number; items: IssueListItem[] }>(`/issues?${qs(params)}`),

  issueSummary: () =>
    request<{ by_type: { issue_type: string; severity: string; count: number }[]; total: number }>(
      '/issues/summary',
    ),

  snapshots: () => request<{
    id: number; source: string; status: string; is_active: boolean
    row_count: number; started_at: string | null; finished_at: string | null
    error: string | null; meta: Record<string, unknown> | null
  }[]>('/admin/snapshots'),

  triggerCrawl: (source: string) =>
    request<{ status: string }>(`/admin/crawl/${source}`, { method: 'POST' }),

  triggerRebuild: () => request<{ status: string }>('/admin/rebuild', { method: 'POST' }),

  kdaisDocuments: () => request<{
    id: number; title: string; crop_text: string | null
    revision_date: string | null; pdf_url: string; file_size: string | null
    parsed_at: string | null; parse_note: string | null
  }[]>('/admin/kdais/documents'),

  kdaisPending: (offset = 0) =>
    request<{ total: number; items: KdaisPendingRow[] }>(
      `/admin/kdais/pending?${qs({ offset, limit: 50 })}`,
    ),

  verifyKdaisRow: (id: number, payload: Record<string, unknown>) =>
    request<{ status: string }>(`/admin/kdais/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }),
}
