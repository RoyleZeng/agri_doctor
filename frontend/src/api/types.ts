// 對應 backend app/schemas/common.py 的回傳結構。

export interface Crop {
  id: number
  name: string
  scientific_name?: string | null
  /** 舊的單一欄位，常為 null */
  fda_category?: string | null
  /** 實際用來查容許量的作物類別候選（由具體到概括） */
  fda_categories?: string[]
  aliases?: string[]
  description?: string | null
}

export interface Pest {
  id: number
  name: string
  kind: string
  scientific_name?: string | null
  english_name?: string | null
}

export interface Suggest {
  id: number
  name: string
  matched_on?: string | null
  alias?: string | null
}

export interface Mrl {
  ingredient_id: number | null
  ingredient_name: string
  ingredient_english: string | null
  limit_ppm: string | null
  limit_text: string | null
  matched_category: string | null
  /** crop_name | crop_alias | fda_category —— 命中的是本名還是大類 */
  match_basis: string | null
  /** 以化學群組容許量命中時的群組名稱 */
  group_name: string | null
  note: string | null
  found: boolean
}

export interface Issue {
  issue_type: string
  severity: 'info' | 'warning' | 'critical'
  summary: string
  detail: Record<string, unknown>
}

export interface KdaisEntry {
  document_title?: string | null
  product_text?: string | null
  dilution?: string | null
  phi?: string | null
  tolerance?: string | null
  moa?: string | null
  dose?: string | null
  remark?: string | null
  pdf_url?: string | null
  pdf_page?: number | null
  revision_date?: string | null
  confidence?: string
}

/** 這一列在某個來源的涵蓋狀況與版本。
 *
 * `version` 只有高改場會有值（PDF 修訂日期）；植保系統與食藥署的網頁沒有
 * 公告版本號，所以顯示時要說「以抓取時間為準」，不能把抓取時間當成官方版本。
 */
export interface RowSourceInfo {
  present: boolean
  version: string | null
  version_label: string | null
  document: string | null
  fetched_at: string | null
  snapshot_id: number | null
  detail: string | null
  url: string | null
}

export type SourceKey = 'ppm' | 'fda' | 'kdais'

export interface ComparisonRow {
  registration_id: number
  product_id: number
  product_name: string
  concentration: string | null
  formulation: string | null
  english_composition: string | null

  dilution_text: string | null
  dilution_value: number | null
  dose_per_hectare: string | null
  application_method: string | null
  precautions: string[]
  phi_days: number | null
  mix_warnings: string[]

  moa_codes: string[]
  ppm_mrl_text: string | null
  source_url: string | null

  mrls: Mrl[]
  kdais_recommended: boolean
  kdais_entries: KdaisEntry[]
  banned: boolean
  issues: Issue[]
  sources: Record<SourceKey, RowSourceInfo>
}

export interface RotationGroup {
  code: string
  label: string
  product_names: string[]
}

export interface Rotation {
  groups: RotationGroup[]
  unknown_products: string[]
  note: string
}

export interface SourceDocument {
  title: string
  crop_text: string | null
  revision_date: string | null
  pdf_url: string | null
}

export interface SourceInfo {
  label: string
  fetched_at: string | null
  row_count: number
  status: string
  snapshot_id?: number | null
  /** 來源文件本身是否帶版本號；目前只有高改場的 PDF 有修訂日期。 */
  has_document_version?: boolean
  documents?: SourceDocument[]
  complete?: boolean
  coverage?: CoverageSnapshot
}

export interface PestProfile {
  symptoms?: string | null
  transmission?: string | null
  cultural_control?: string | null
}

export interface Comparison {
  crop: Crop
  pest: Pest
  profile: PestProfile | null
  rows: ComparisonRow[]
  rotation: Rotation
  issues: Issue[]
  sources: Record<string, SourceInfo>
  coverage: { state: string; label: string; message: string; source_url: string; fetched_at: string | null }
}

/** 診療案件的處方檢查結果；`block` 不能開立，`warning` 需由醫師判讀。 */
export type CheckLevel = 'ok' | 'warning' | 'block'
export interface PrescriptionCheck {
  type: 'banned' | 'phi' | 'rotation' | string
  level: CheckLevel
  message: string
  [key: string]: unknown
}

export interface PrescriptionItem {
  id: string
  case_id: string
  registration_id: number | null
  product_text: string
  ingredient_text: string
  moa_text: string
  dilution: string | null
  dose: string | null
  frequency: string
  phi_days: number | null
  advice: string
  source_values: Record<string, unknown>
  checks: PrescriptionCheck[]
  sort_order: number
  modified_fields: string[]
  case_version: number
}

export interface ConsultCaseFields {
  id: string
  doctor_id: string
  farmer_id: string | null
  farmer_text: string
  case_no: string
  plan_id: string | null
  crop_id: number | null
  crop_text: string
  variety: string
  field_text: string
  area_text: string
  expected_harvest_on: string | null
  examined_by: string[]
  symptoms: string
  diagnosis_pest_id: number | null
  diagnosis_text: string
  diagnosis_basis: string[]
  cultural_control: string
  status: 'draft' | 'saved' | 'issued'
  issued_at: string | null
  version: number
  created_at: string
  updated_at: string
}

/** 案件層級的抗藥性輪替摘要（後端 `services/consult_checks.rotation_summary`）。 */
export interface RotationSummary {
  level: 'crit' | 'ok' | 'unknown'
  message: string
  /** 一路共用到底的機制，正規化成「FRAC 3」。 */
  groups: string[]
  /** 連續第幾次（含本次）。 */
  streak: number
  sequence: { applied_on: string; product_text: string; groups: string[]; current: boolean }[]
  /** 給 RegistrationPicker 預篩用：要換掉的就是這些機制。 */
  exclude_groups: string[]
  checked_on: string
}
export interface ConsultCase extends ConsultCaseFields { items: PrescriptionItem[]; rotation?: RotationSummary }
export interface ConsultCaseSummary extends ConsultCaseFields {
  prescriptions: { product_text: string; dilution: string | null; phi_days: number | null; check_level: CheckLevel }[]
}
export interface RegistrationSearchItem extends ComparisonRow {
  crop: Crop
  pest: Pest
  coverage: Comparison['coverage']
  applicable: boolean | null
  approved_crops: { id: number; name: string }[]
  can_add: boolean
  reason: string
}

export interface CoverageSnapshot {
  state: string; stored: number; verified: number; discovered: number | null
  failed: number; pending: number; percent: number | null; complete: boolean
  status?: string; snapshot_id?: number; limited_run?: boolean; is_active?: boolean
  started_at?: string; finished_at?: string | null; heartbeat_at?: string | null
}
export interface CoverageOverview { active: CoverageSnapshot; latest: CoverageSnapshot; source_url: string; message: string }
export interface SourceUpdate {
  id: number; source: string; kind: string; title: string; before: Record<string, unknown> | null
  after: Record<string, unknown> | null; source_url: string | null; effective_date: string | null
  detected_at: string; snapshot_id: number; previous_snapshot_id: number | null
}
export interface UpdateFeed {
  total: number; items: SourceUpdate[]; notice: string
  sources: { source: string; status: string; finished_at: string | null; published: boolean; complete: boolean }[]
}

export interface IssueListItem {
  id: number
  issue_type: string
  severity: 'info' | 'warning' | 'critical'
  crop: string | null
  crop_id: number | null
  pest: string | null
  pest_id: number | null
  summary: string
  detail: Record<string, unknown>
}

export interface KdaisPendingRow {
  id: number
  document_title: string | null
  crop_text: string | null
  pest_text: string | null
  product_text: string | null
  moa_text: string | null
  dilution_text: string | null
  dose_text: string | null
  phi_text: string | null
  tolerance_text: string | null
  method_text: string | null
  remark: string | null
  raw_row: Record<string, string> | null
  pdf_url: string | null
  pdf_page: number | null
  revision_date: string | null
  confidence: string
}


/* ---------- 混合搜尋 ---------- */

export interface SearchItem {
  /** crop | disease | insect | weed | other */
  kind: string
  id: number
  name: string
  alias: string | null
  scientific_name: string | null
  english_name?: string | null
  count: number
  /** 例如「12 種作物有核准用藥」 */
  count_label: string
}

export interface SearchGroup {
  key: string
  label: string
  items: SearchItem[]
}

export interface SearchResult {
  query: string
  term?: string
  /** 一次打兩個詞（「芒果 炭疽」）時，直接解析出的組合 */
  direct: {
    crop: { id: number; name: string }
    pest: { id: number; name: string }
    registration_count: number
  } | null
  groups: SearchGroup[]
  total: number
}

export interface PestCropCard {
  id: number
  name: string
  aliases: string[]
  fda_category: string | null
  ppm_count: number
  coverage?: string
  fda_count: number
  kdais_count: number
  kdais_has_document: boolean
  critical: number
  warning: number
  info: number
}

export interface PestCrops {
  pest: Pest
  crops: PestCropCard[]
}

export interface PestCard {
  id: number
  name: string
  kind: string
  aliases: string[]
  scientific_name: string | null
  ppm_count: number
  coverage?: string
  fda_count: number
  kdais_count: number
  kdais_has_document: boolean
  critical: number
  warning: number
  info: number
}

export interface CropPests {
  crop: {
    id: number
    name: string
    scientific_name: string | null
    fda_category: string | null
    aliases: string[]
  }
  pests: PestCard[]
}

export interface IngredientOverview {
  ingredient: {
    id: number
    name: string
    english_name: string | null
    frac_code: string | null
    irac_code: string | null
    banned: boolean
    /** 以化學群組訂容許量時，群組成分 */
    mrl_group: { id: number; name: string; english_name: string | null } | null
  }
  stats: { mrl_categories: number; registrations: number; kdais_rows: number }
  limits: {
    crop_category: string
    limit_ppm: string | null
    limit_text: string | null
    note: string | null
  }[]
  usages: {
    crop_id: number
    crop: string
    items: {
      pest_id: number
      pest: string
      pest_kind: string
      product: string
      moa: string | null
      dilution: string | null
      phi_days: number | null
    }[]
  }[]
}
