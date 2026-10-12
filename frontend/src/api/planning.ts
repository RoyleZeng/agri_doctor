export type Kind = 'stage' | 'care' | 'nutrition' | 'pest'
export interface Farmer {
  id: string
  name: string
  // agency＝主管機關／改良場：只看區域地圖的鄉鎮匯總，沒有自己的農場工作區。
  role: 'farmer' | 'doctor' | 'agency' | 'admin'
  farm_name: string
  location: string
  weather_county?: string | null
  crops?: string[]
  plan_count?: number
  precision_enabled?: boolean
  share_region?: boolean
  /** 代建帳號首次登入必須改密碼；為真時前端把人擋在改密碼畫面。 */
  must_change_password?: boolean
  /** 這個連結怎麼來的：`code`＝農民給連結碼，`created`＝植物醫生代建。 */
  link_source?: 'code' | 'created'
  /** 代建帳號的邀請狀態；自己註冊的是 null。 */
  invite?: { state: 'pending' | 'expired' | 'active'; expires_at: string | null } | null
}
export interface CreatedFarmer extends Farmer {
  username: string
  /** 明文只在建立／重發那一次出現，之後查不到。 */
  one_time_password: string
  otp_expires_at: string | null
}
export interface CreateFarmerInput {
  name: string
  farm_name?: string
  location: string
  username?: string | null
  doctor_id?: string | null
}
export interface Source {
  id: string
  title: string
  publisher: string
  url: string
  fetched_at: string
  publication_date: string | null
  sha256: string
  status: string
}
export interface CropTemplate {
  key: string
  name: string
  aliases: string[]
  emoji: string
  days: number
  anchor: string
  scope: string
}
export interface Catalog {
  version: string
  notice: string
  sources: Source[]
  crops: CropTemplate[]
}
export interface GuidanceAttachment { kind: 'image' | 'video'; url: string; caption: string; source_url: string }
export interface FarmEvent {
  media?: GuidanceAttachment[]
  id: string
  kind: Kind
  title: string
  start_on: string
  end_on: string
  detail: string
  source_ids: string[]
  completed: boolean
  note: string
  updated_by: string
  basis: string
  /** 有對應農事日誌時是那筆紀錄；「已記錄」由此推導，不看 basis。 */
  log_id?: string | null
  log_done_on?: string | null
}
export interface PlanInput {
  farmer_id?: string
  crop_key: string | null
  crop_name: string
  field_name: string
  variety: string
  area: string
  planted_on: string
  end_on: string
  notes: string
}
export interface Plan extends PlanInput {
  id: string
  farmer_id: string
  archived: boolean
  version: number
  template_version: string | null
  sources: Source[]
  events: FarmEvent[]
}
export interface FarmNote {
  id: string
  body: string
  created_at: number
  author: string
  role: string
}
export interface RegionAlert {
  pest_text: string
  metric: string
  value: number
  warn_value: number
  basis: string
}
export interface Workspace {
  farmer: Farmer
  plans: Plan[]
  notes: FarmNote[]
  /** 這個農場所在鄉鎮目前達警戒的病蟲害；複合圖巡查色帶可以拿來加「附近已達警戒」提示。 */
  region_alerts: RegionAlert[]
}
export type EventInput = Omit<
  FarmEvent,
  'id' | 'media' | 'source_ids' | 'updated_by' | 'basis' | 'log_id' | 'log_done_on'
> & { version: number }

export type LogKind = 'spray' | 'fertilize' | 'irrigate' | 'fieldwork' | 'harvest' | 'tillage' | 'scout' | 'other'
export interface LogPhoto {
  id: string
  log_id: string
  bytes: number
  width: number | null
  height: number | null
  taken_at: string
  /** exif＝相機記錄的拍攝時間；upload＝沒有可信的拍攝資訊，時間是上傳時間。 */
  taken_at_source: 'exif' | 'upload'
  lat: number | null
  lng: number | null
  caption: string
  sort_order: number
  created_at: string
  url?: string
  thumb_url: string
}
export interface TrashedPhoto extends LogPhoto {
  deleted_at: string
  restorable_until: string
}
export interface RegistrationSnapshot {
  registration_id: number
  registration_snapshot_id: number
  crop: { id: number; name: string } | null
  pest: { id: number; name: string } | null
  product_text: string
  dilution: string | null
  dose: string | null
  phi_days: number | null
  moa_codes: string[]
  banned: boolean
  crop_matches: boolean | null
  captured_at: string
}
export interface LogFields {
  done_on: string
  done_time: string | null
  kind: LogKind
  field_text: string
  crop_text: string
  title: string
  material_name: string
  material_supplier: string
  material_lot: string
  dilution: string
  amount_value: string
  amount_unit: string
  area_text: string
  yield_value: string
  yield_unit: string
  yield_destination: string
  labor_text: string
  pest_id: number | null
  pest_text: string
  note: string
}
export interface WorkLog extends LogFields {
  id: string
  farmer_id: string
  plan_id: string | null
  event_id: string | null
  prescription_item_id: string | null
  recommendation_id: string | null
  field_id: string | null
  phi_days: number | null
  phi_source: 'prescription' | 'registration' | 'none'
  registration_id: number | null
  registration_snapshot: RegistrationSnapshot | null
  compliance: { rule_version?: string; required?: string[]; missing?: string[] }
  created_by: string
  created_by_name: string
  created_by_role: 'farmer' | 'doctor' | 'admin' | ''
  kind_label: string
  application_record_id: string | null
  moa_codes: string[]
  photos: LogPhoto[]
  trashed_photos: TrashedPhoto[]
  prescription: { case_id: string; case_no: string; status: string } | null
  created_at: string
  updated_at: string
  editable_until: string
  locked: boolean
  /** 由規劃事項記錄時一併回傳更新後的規劃（事項已完成、版本已升）。 */
  plan?: Plan
  /** 由精準模式建議記錄時一併回傳（就地把建議卡變成「已記錄」，不用再打一次 GET）。 */
  recommendation?: { id: string; status: 'done'; work_log_id: string }
}
export type LogCreate = LogFields & {
  plan_id: string | null
  event_id: string | null
  prescription_item_id: string | null
  registration_id: number | null
  recommendation_id: string | null
}
export type LogPatch = Partial<LogFields> & { registration_id?: number | null }
/** 農事紀錄表單的入口：記一筆、帶入（規劃事項／巡查窗口／處方／精準模式建議）、修改或補選登記。 */
export type LogFormMode =
  | { type: 'new'; plan_id?: string; kind?: LogKind }
  | { type: 'prefill'; event_id?: string; prescription_item_id?: string; plan_id?: string; recommendation_id?: string }
  | { type: 'edit'; log: WorkLog; backfill?: boolean }
export interface LogPage {
  total: number
  page: number
  page_size: number
  items: WorkLog[]
}
export interface LogPrefill {
  kind: LogKind | null
  plan_id: string | null
  event_id: string | null
  prescription_item_id: string | null
  recommendation_id: string | null
  field_text: string
  crop_text: string
  area_text: string
  amount_value: string
  amount_unit: string
  crop_id: number | null
  plan_archived: boolean
  title: string
  material_name: string
  dilution: string
  pest_text: string
  event: {
    id: string
    kind: Kind
    title: string
    start_on: string
    end_on: string
    completed: boolean
    log_id: string | null
  } | null
  prescription: {
    case_id: string
    case_no: string
    product_text: string
    dose: string | null
    phi_days: number | null
    moa_codes: string[]
  } | null
  recommendation: {
    id: string
    kind: PrecisionKind
    title: string
    rationale: string
    five_r: FiveR
    payload: IrrigatePayload | VrtFertilizerPayload
  } | null
}
export interface PrescriptionOption {
  id: string
  case_id: string
  case_no: string
  status: 'saved' | 'issued'
  issued_at: string | null
  crop_text: string
  field_text: string
  product_text: string
  dilution: string | null
  dose: string | null
  phi_days: number | null
  moa_codes: string[]
  logged_count: number
  last_logged_on: string | null
}
export interface UnloggedEvent {
  event_id: string
  plan_id: string
  title: string
  kind: Kind
  start_on: string
  end_on: string
  completed: boolean
  crop_name: string
  field_name: string
}
export interface LogSummary {
  month: string
  start: string
  end: string
  total: number
  by_kind: Partial<Record<LogKind, number>>
  photo_count: number
  spray_count: number
  yields: { unit: string; total: number }[]
  unparsed_yields: number
  compliance: {
    complete: number
    incomplete: { id: string; done_on: string; title: string; missing: string[] }[]
  }
  plan_compare: { total: number; logged: number; unlogged: UnloggedEvent[] }
  fields: string[]
}
export type ExportFormat = 'pdf' | 'xlsx'
export interface ExportParams {
  from: string
  to: string
  field?: string
  format: ExportFormat
  photos: boolean
  gps: boolean
  phi: boolean
}
export interface PhiIssue {
  harvest_id: string
  harvest_on: string
  field_text: string
  crop_text: string
  message: string
}
export interface ExportPreview {
  total: number
  photo_count: number
  max_photos: number
  too_many_photos: boolean
  fields: string[]
  rule_version: string
  rule_changed: number
  incomplete: { id: string; done_on: string; title: string; missing: string[] }[]
  phi: { checked: number; violations: PhiIssue[]; unknown: PhiIssue[] }
}
// ---- 精準模式（示範資料） ----

export type PrecisionKind = 'irrigate' | 'vrt_fertilizer'
export type RecommendationStatus = 'proposed' | 'accepted' | 'done' | 'dismissed' | 'expired'
export interface FiveR {
  material: string
  rate: string
  place: string
  time: string
  method: string
}
export interface IrrigateZone {
  code: string
  label: string
  mm: number
  avg_pct: number
  latest_pct: number
  area_ha: number | null
  volume_m3: number | null
}
export interface IrrigatePayload {
  zones: IrrigateZone[]
  total_volume_m3: number | null
  warnings: string[]
}
export interface VrtZone {
  code: string
  label: string
  ndvi: number
  class_key: string
  class_label: string
  multiplier: number
  rate_kg_per_ha: number
  area_ha: number | null
  amount_kg: number | null
}
export interface VrtFertilizerPayload {
  layer_id: string
  captured_on: string
  base_rate_kg_per_ha: number
  zones: VrtZone[]
  total_kg: number | null
  uniform_total_kg: number | null
  difference_kg: number | null
  disclaimer: string
  warnings: string[]
}
export interface Recommendation {
  id: string
  field_id: string
  field_name: string
  kind: PrecisionKind
  status: RecommendationStatus
  status_label: string
  title: string
  rationale: string
  five_r: FiveR
  payload: IrrigatePayload | VrtFertilizerPayload
  evidence: Record<string, unknown>
  proposed_at: string
  expires_at: string
  event_id: string | null
  work_log_id: string | null
  rule_version: string
  decided_by: string | null
  decided_at: string | null
  status_note: string
  expired_reason: string
  is_demo: boolean
  created_at: string
  updated_at: string
}
export interface PrecisionNote {
  kind: PrecisionKind
  reason: string
  message: string
  zones: string[]
}
export interface AnalyzeResult {
  items: Recommendation[]
  notes: PrecisionNote[]
  withdrawn: Recommendation[]
}
export interface SensorPoint {
  ts: string
  value: number
  unit?: string
}
export interface PrecisionStation {
  id: string
  code: string
  kind: 'soil' | 'weather'
  group_id: string | null
  zone_code: string | null
  grid_col: number | null
  grid_row: number | null
  source: string
  latest: Record<string, SensorPoint>
  sparkline: Record<string, SensorPoint[]>
}
export interface PrecisionZone {
  code: string
  label: string
  area_ha: number | null
  cells: [number, number][]
}
export interface PrecisionGroup {
  id: string
  name: string
  metrics: string[]
}
export interface PrecisionField {
  id: string
  name: string
  area_text: string
  grid_cols: number
  grid_rows: number
  is_demo: boolean
  zones: PrecisionZone[]
  groups: PrecisionGroup[]
  stations: PrecisionStation[]
  sensor_age_minutes: number | null
  ndvi_captured_on: string | null
  boundary: { source: BoundarySource; imported_at: string; outside_cells: [number, number][] } | null
}
export interface PrecisionFieldSummary {
  id: string
  name: string
  area_text: string
  is_demo: boolean
}
/** 田區 CRUD 端點回傳的完整形狀（含格數），跟總覽端點給地點 chip 用的精簡版 PrecisionFieldSummary 不同。 */
export interface FieldRecord extends PrecisionFieldSummary {
  grid_cols: number
  grid_rows: number
}
export interface PrecisionRules {
  irrigate: { metric: string; threshold_pct: number; window_hours: number; rain_skip_mm_48h: number; rain_skip_probability_pct: number }
  vrt_fertilizer: {
    min_days_since_fertilizer: number
    max_layer_age_days: number
    classes: { key: string; label: string; multiplier: number }[]
  }
}
export interface PrecisionOverview {
  precision_enabled: boolean
  fields: PrecisionFieldSummary[]
  field: PrecisionField | null
  recommendations: Recommendation[]
  rules: PrecisionRules
}
export type LayerKind = 'ndvi' | 'soil_moisture' | 'ph' | 'yield_prev'
export interface FieldLayer {
  id: string
  kind: LayerKind
  captured_on: string
  source_text: string
  cell_values: Record<string, number>
  zone_values: Record<string, number>
}
export interface LayerResponse {
  field: { grid_cols: number; grid_rows: number }
  layer: FieldLayer | null
}
export interface ChartData {
  metric: string
  unit: string
  threshold_pct: number | null
  series: Record<string, SensorPoint[]>
  rain: SensorPoint[]
  stages: { title: string; start_on: string; end_on: string }[]
}

// ---- 資料來源頁：田區 CRUD、感測群組與站點、data_sources、UAV、GIS 邊界 ----

export type DataSourceKind = 'sensor_http' | 'sensor_mqtt' | 'sensor_csv' | 'uav_upload' | 'gis_file' | 'gis_wms' | 'weather'
export type DataSourceStatus = 'connected' | 'error' | 'never'
/** 機密欄位遮罩後的固定值，跟 backend/app/precision/crypto.py 對應。 */
export const SECRET_MASK = '已設定（不顯示明文）'
/** 換過 SECRET_KEY 之後解不回來的舊密文：顯示成「已設定」會讓人以為連線還能用，所以另外標出來。 */
export const SECRET_NEEDS_REENTRY = '需重新輸入'
export interface DataSource {
  id: string
  kind: DataSourceKind
  label: string
  config: Record<string, string>
  status: DataSourceStatus
  last_sync_at: string | null
  last_error: string
  created_at: string
  updated_at: string
}
export interface GroupStation {
  id: string
  code: string
  kind: 'soil' | 'weather'
  zone_id: string | null
  grid_col: number | null
  grid_row: number | null
}
export interface SensorGroupDetail {
  id: string
  field_id: string
  name: string
  source_id: string | null
  metrics: string[]
  is_demo: boolean
  stations: GroupStation[]
}
export type UavProduct = 'ndvi' | 'rgb' | 'multispectral'
export interface UavMission {
  id: string
  field_id: string
  flown_on: string
  product: UavProduct
  product_label: string
  file_key: string
  crs: string
  status: 'uploaded' | 'processing' | 'done' | 'failed'
  note: string
  is_demo: boolean
  created_at: string
}
export type BoundarySource = 'hand_grid' | 'upload' | 'cadastral_wms'
export interface FieldBoundary {
  id: string
  field_id: string
  geojson: Record<string, unknown>
  source: BoundarySource
  outside_cells: [number, number][]
  imported_at: string
}
export interface ReadingUploadResult {
  inserted: number
  skipped_duplicates: number
  total_in_file: number
}

// ---- 區域作物與病蟲害地圖 ----
export type RegionSource = 'consult' | 'scout' | 'plan' | 'survey'
export interface RegionFilters {
  county?: string
  district?: string
  from?: string
  to?: string
  crop?: string
  pest?: string
  source?: RegionSource | ''
}
/** count/area_ha/metric 同時是 null，但 sample_insufficient 是 true：那格有農場貢獻但不到 3 戶，顯示「樣本不足」；
 * 三個都是 null 且 sample_insufficient 是 false：這格本來就沒人通報，不是同一件事。 */
export interface RegionKpi {
  count: number | null
  area_ha: number | null
  metric: number | null
  sample_insufficient: boolean
}
export interface RegionCropCount {
  crop_text: string
  count: number
}
export interface RegionSummary {
  county: string
  available: boolean
  /** available=false 時為什麼：鄉鎮沒填（可以去設定）或這個縣市還沒有界線檔（使用者無能為力）。 */
  reason?: 'district_missing' | 'county_unsupported' | null
  message?: string
  kpi: RegionKpi
  top_crops: RegionCropCount[]
  alerts: (RegionAlert & { district: string })[]
  /** 色階切點（含頭尾），前端依落點分級套色，不自己寫死門檻。 */
  legend: number[]
}
export interface RegionDistrictRow extends RegionKpi {
  district: string
  alert: boolean
}
export interface RegionDistrictItem extends RegionKpi {
  crop_text: string
  pest_text: string | null
}
export interface RegionLinkedFarm {
  id: string
  name: string
  farm_name: string
}
export interface RegionDistrictDetail {
  county: string
  district: string
  items: RegionDistrictItem[]
  top_crops: RegionCropCount[]
  alerts: (RegionAlert & { district?: string })[]
  /** 只有植物醫生看得到；機關與農民不會有這個欄位。 */
  linked_farms?: RegionLinkedFarm[]
}
export interface RegionReportRow extends RegionKpi {
  district: string
  day: string
  crop_text: string
  pest_text: string | null
  source: RegionSource
  farms: number | null
}
export interface SurveyInput {
  district: string
  surveyed_on: string
  crop_text: string
  pest_text: string
  trap_count: number
  traps?: number
  note?: string
}

export class PlanningError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
  }
}

async function request<T>(
  path: string,
  method = 'GET',
  data?: unknown,
  asBlob = false,
  base: '/planning' | '/precision' | '/region' = '/planning',
): Promise<T> {
  let response: Response
  // 照片走 multipart：讓瀏覽器自己帶 boundary，不能手動設 Content-Type。
  const multipart = data instanceof FormData
  try {
    response = await fetch(`/api/v1${base}${path}`, {
      method,
      credentials: 'same-origin',
      headers: multipart
        ? { 'X-Planning-Request': '1' }
        : { 'Content-Type': 'application/json', 'X-Planning-Request': '1' },
      body: data === undefined ? undefined : multipart ? data : JSON.stringify(data),
    })
  } catch {
    throw new PlanningError(
      '暫時連不上伺服器，請確認連線後再試；尚未儲存本次操作。',
      0,
    )
  }
  if (!response.ok) {
    let message = `無法完成操作（${response.status}），請稍後重試。`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail))
        message = body.detail
          .map((e: { msg: string }) => e.msg.replace('Value error, ', ''))
          .join('；')
    } catch {
      /* Non-JSON proxy errors. */
    }
    throw new PlanningError(message, response.status)
  }
  return asBlob ? (response.blob() as Promise<T>) : response.json()
}
const enc = encodeURIComponent
function query(params: Record<string, string | number | null | undefined>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params))
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value))
  const text = search.toString()
  return text ? `?${text}` : ''
}
export const planning = {
  saveLocation: (location: string) => request<Farmer>('/me/location', 'PATCH', { location }),
  weatherCounty: (county: string, farmer_id?: string) => request<Farmer>('/weather-county', 'PATCH', { county, farmer_id }),
  catalog: () => request<Catalog>('/catalog'),
  example: () => request<Workspace>('/example'),
  diagram: (farmerId: string, ids: string[], kinds: Kind[], demo = false) => {
    const query = new URLSearchParams({
      plan_ids: ids.join(','),
      kinds: kinds.join(','),
    })
    return request<Blob>(
      `${demo ? '/example.svg' : `/farmers/${encodeURIComponent(farmerId)}/diagram.svg`}?${query}`,
      'GET',
      undefined,
      true,
    )
  },
  me: () => request<Farmer>('/me'),
  login: (data: unknown) => request<Farmer>('/login', 'POST', data),
  register: (data: unknown) => request<Farmer>('/register', 'POST', data),
  logout: () => request('/logout', 'POST'),
  workspace: (id: string) =>
    request<Workspace>(`/farmers/${encodeURIComponent(id)}`),
  links: () => request<Farmer[]>('/links'),
  invite: () =>
    request<{ code: string; expires_at: number }>('/invite', 'POST'),
  link: (code: string) => request<Farmer>('/links', 'POST', { code }),
  unlink: (id: string) => request(`/links/${encodeURIComponent(id)}`, 'DELETE'),
  createFarmer: (data: CreateFarmerInput) => request<CreatedFarmer>('/farmers', 'POST', data),
  resetOtp: (id: string) =>
    request<{ username: string; one_time_password: string; otp_expires_at: string | null }>(
      `/farmers/${encodeURIComponent(id)}/reset-otp`, 'POST'),
  changePassword: (password: string) => request<Farmer>('/me/password', 'POST', { password }),
  welcomeCard: (id: string, password: string) =>
    request<Blob>(`/farmers/${encodeURIComponent(id)}/welcome-card.pdf`, 'POST', { password }, true),
  createPlan: (data: PlanInput) => request<Plan>('/plans', 'POST', data),
  archive: (p: Plan) =>
    request<Plan>(`/plans/${p.id}`, 'PATCH', {
      version: p.version,
      archived: !p.archived,
    }),
  saveEvent: (p: Plan, id: string | undefined, data: EventInput) =>
    request<Plan>(
      `/plans/${p.id}/events${id ? '/' + id : ''}`,
      id ? 'PUT' : 'POST',
      data,
    ),
  note: (id: string, body: string) =>
    request(`/farmers/${id}/notes`, 'POST', { body }),

  logs: (farmerId: string, params: { from?: string; to?: string; kind?: LogKind | ''; field?: string; page?: number }) =>
    request<LogPage>(`/farmers/${enc(farmerId)}/logs${query(params)}`),
  createLog: (farmerId: string, data: LogCreate) =>
    request<WorkLog>(`/farmers/${enc(farmerId)}/logs`, 'POST', data),
  patchLog: (id: string, data: LogPatch) => request<WorkLog>(`/logs/${enc(id)}`, 'PATCH', data),
  deleteLog: (id: string) => request<{ ok: boolean }>(`/logs/${enc(id)}`, 'DELETE'),
  uploadPhoto: (logId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<LogPhoto>(`/logs/${enc(logId)}/photos`, 'POST', form)
  },
  deletePhoto: (logId: string, photoId: string) =>
    request<{ ok: boolean }>(`/logs/${enc(logId)}/photos/${enc(photoId)}`, 'DELETE'),
  restorePhoto: (logId: string, photoId: string) =>
    request<LogPhoto>(`/logs/${enc(logId)}/photos/${enc(photoId)}/restore`, 'POST'),
  logPrefill: (farmerId: string, params: { plan_id?: string; event_id?: string; prescription_item_id?: string; recommendation_id?: string }) =>
    request<LogPrefill>(`/farmers/${enc(farmerId)}/logs/prefill${query(params)}`),
  prescriptions: (farmerId: string) =>
    request<PrescriptionOption[]>(`/farmers/${enc(farmerId)}/prescriptions`),
  logSummary: (farmerId: string, month: string) =>
    request<LogSummary>(`/farmers/${enc(farmerId)}/logs/summary${query({ month })}`),
  exportPreview: (farmerId: string, params: { from: string; to: string; field?: string }) =>
    request<ExportPreview>(`/farmers/${enc(farmerId)}/logs/export/preview${query(params)}`),
  exportLogs: (farmerId: string, params: ExportParams) =>
    request<Blob>(
      `/farmers/${enc(farmerId)}/logs/export${query({
        from: params.from,
        to: params.to,
        field: params.field,
        format: params.format,
        photos: params.photos ? 1 : 0,
        gps: params.gps ? 1 : 0,
        phi: params.phi ? 1 : 0,
      })}`,
      'GET',
      undefined,
      true,
    ),

  togglePrecision: (enabled: boolean, farmer_id?: string) => request<Farmer>('/me', 'PATCH', { enabled, farmer_id }, false, '/precision'),
  toggleShareRegion: (enabled: boolean) => request<Farmer>('/me/share-region', 'PATCH', { enabled }),
  precisionOverview: (farmerId: string, fieldId?: string) =>
    request<PrecisionOverview>(`/farmers/${enc(farmerId)}/overview${query({ field_id: fieldId })}`, 'GET', undefined, false, '/precision'),
  precisionLayer: (fieldId: string, kind: LayerKind) =>
    request<LayerResponse>(`/fields/${enc(fieldId)}/layers/${enc(kind)}`, 'GET', undefined, false, '/precision'),
  precisionChart: (fieldId: string, metric: string, hours = 720) =>
    request<ChartData>(`/fields/${enc(fieldId)}/chart${query({ metric, hours })}`, 'GET', undefined, false, '/precision'),
  precisionAnalyze: (fieldId: string) =>
    request<AnalyzeResult>(`/fields/${enc(fieldId)}/analyze`, 'POST', undefined, false, '/precision'),
  precisionAccept: (id: string) =>
    request<Recommendation & { event_id: string }>(`/recommendations/${enc(id)}/accept`, 'POST', undefined, false, '/precision'),
  precisionDismiss: (id: string, reason: string) =>
    request<Recommendation>(`/recommendations/${enc(id)}/dismiss`, 'POST', { reason }, false, '/precision'),

  // ---- 資料來源頁 ----
  precisionFields: (farmerId: string) =>
    request<{ items: PrecisionFieldSummary[] }>(`/farmers/${enc(farmerId)}/fields`, 'GET', undefined, false, '/precision'),
  createPrecisionField: (farmerId: string, data: { name: string; area_text?: string; grid_cols?: number; grid_rows?: number }) =>
    request<FieldRecord>(`/farmers/${enc(farmerId)}/fields`, 'POST', data, false, '/precision'),
  patchPrecisionField: (fieldId: string, data: Partial<{ name: string; area_text: string; grid_cols: number; grid_rows: number }>) =>
    request<FieldRecord>(`/fields/${enc(fieldId)}`, 'PATCH', data, false, '/precision'),
  deletePrecisionField: (fieldId: string) =>
    request<{ ok: boolean }>(`/fields/${enc(fieldId)}`, 'DELETE', undefined, false, '/precision'),

  sensorGroups: (fieldId: string) =>
    request<{ items: SensorGroupDetail[] }>(`/fields/${enc(fieldId)}/sensor-groups`, 'GET', undefined, false, '/precision'),
  createSensorGroup: (fieldId: string, data: { name: string; source_id?: string | null; metrics?: string[] }) =>
    request<SensorGroupDetail>(`/fields/${enc(fieldId)}/sensor-groups`, 'POST', data, false, '/precision'),
  deleteSensorGroup: (groupId: string) =>
    request<{ ok: boolean }>(`/sensor-groups/${enc(groupId)}`, 'DELETE', undefined, false, '/precision'),
  createStation: (groupId: string, data: { code: string; kind: 'soil' | 'weather'; zone_id?: string | null; grid_col?: number | null; grid_row?: number | null }) =>
    request<GroupStation>(`/sensor-groups/${enc(groupId)}/stations`, 'POST', data, false, '/precision'),
  deleteStation: (stationId: string) =>
    request<{ ok: boolean }>(`/stations/${enc(stationId)}`, 'DELETE', undefined, false, '/precision'),
  uploadReadings: (groupId: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ReadingUploadResult>(`/sensor-groups/${enc(groupId)}/readings`, 'POST', form, false, '/precision')
  },

  dataSources: (farmerId: string) =>
    request<{ items: DataSource[]; secret_key_is_default: boolean }>(
      `/farmers/${enc(farmerId)}/data-sources`, 'GET', undefined, false, '/precision'),
  createDataSource: (farmerId: string, data: { kind: DataSourceKind; label: string; config?: Record<string, string> }) =>
    request<DataSource>(`/farmers/${enc(farmerId)}/data-sources`, 'POST', data, false, '/precision'),
  patchDataSource: (id: string, data: { label?: string; config?: Record<string, string> }) =>
    request<DataSource>(`/data-sources/${enc(id)}`, 'PATCH', data, false, '/precision'),
  deleteDataSource: (id: string) =>
    request<{ ok: boolean }>(`/data-sources/${enc(id)}`, 'DELETE', undefined, false, '/precision'),
  testDataSource: (id: string) =>
    request<DataSource>(`/data-sources/${enc(id)}/test`, 'POST', undefined, false, '/precision'),

  uavMissions: (fieldId: string) =>
    request<{ items: UavMission[] }>(`/fields/${enc(fieldId)}/uav`, 'GET', undefined, false, '/precision'),
  uploadUavMission: (fieldId: string, flownOn: string, product: UavProduct, file: File) => {
    const form = new FormData()
    form.append('flown_on', flownOn)
    form.append('product', product)
    form.append('file', file)
    return request<UavMission>(`/fields/${enc(fieldId)}/uav`, 'POST', form, false, '/precision')
  },

  boundary: (fieldId: string) =>
    request<{ boundary: FieldBoundary | null }>(`/fields/${enc(fieldId)}/boundary`, 'GET', undefined, false, '/precision'),
  importBoundary: (fieldId: string, geojson: Record<string, unknown>, crs: string, source: BoundarySource = 'upload') =>
    request<FieldBoundary>(`/fields/${enc(fieldId)}/boundary`, 'POST', { geojson, crs, source }, false, '/precision'),
  deleteBoundary: (fieldId: string) =>
    request<{ ok: boolean }>(`/fields/${enc(fieldId)}/boundary`, 'DELETE', undefined, false, '/precision'),

  // ---- 區域作物與病蟲害地圖 ----
  regionGeo: (county?: string) => request<GeoJSON.FeatureCollection>(`/geo${query({ county })}`, 'GET', undefined, false, '/region'),
  regionSummary: (params: RegionFilters = {}) =>
    request<RegionSummary>(`/summary${query({ ...params })}`, 'GET', undefined, false, '/region'),
  regionDistricts: (params: RegionFilters = {}) =>
    request<{ county: string; available: boolean; districts: RegionDistrictRow[] }>(
      `/districts${query({ ...params })}`, 'GET', undefined, false, '/region',
    ),
  regionDistrictDetail: (district: string, params: Omit<RegionFilters, 'district'> = {}) =>
    request<RegionDistrictDetail>(`/districts/${enc(district)}${query({ ...params })}`, 'GET', undefined, false, '/region'),
  regionReports: (params: RegionFilters & { page?: number } = {}) =>
    request<{ page: number; has_more: boolean; items: RegionReportRow[] }>(
      `/reports${query({ ...params })}`, 'GET', undefined, false, '/region',
    ),
  regionExport: (params: RegionFilters = {}) =>
    request<Blob>(`/export.xlsx${query({ ...params })}`, 'GET', undefined, true, '/region'),
  createSurvey: (data: SurveyInput) => request<{ id: string }>('/surveys', 'POST', data, false, '/region'),
  reaggregateRegion: () => request<{ rows: number; removed: number }>('/reaggregate', 'POST', undefined, false, '/region'),
}
