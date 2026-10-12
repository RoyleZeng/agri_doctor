<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  planning,
  SECRET_NEEDS_REENTRY,
  type DataSource,
  type DataSourceKind,
  type FieldBoundary,
  type PrecisionFieldSummary,
  type SensorGroupDetail,
  type UavMission,
  type UavProduct,
} from '@/api/planning'
import { today } from '@/composables/farmDates'
import { toast } from '@/composables/toast'
import FarmModal from './FarmModal.vue'
import Icon from './Icon.vue'

/**
 * 資料來源頁：田區、感測群組與站點、data_sources 連線設定、無人機影像、GIS 邊界。
 * 示範階段真的會動的只有 CSV 這條路（見 app/precision/sources.py）；HTTP／MQTT／WMS 先能設定，
 * 「測試連線」老實回「尚未支援」，不假裝連得上。
 */
const props = defineProps<{ farmerId: string; owner: boolean; currentFieldId: string | null }>()
const emit = defineEmits<{ close: []; changed: [] }>()

const SOURCE_KINDS: { key: DataSourceKind; label: string }[] = [
  { key: 'sensor_http', label: '感測器 · HTTP 輪詢' },
  { key: 'sensor_mqtt', label: '感測器 · MQTT 訂閱' },
  { key: 'sensor_csv', label: '感測器 · CSV 上傳' },
  { key: 'uav_upload', label: '無人機上傳' },
  { key: 'gis_file', label: 'GIS 檔案上傳' },
  { key: 'gis_wms', label: 'GIS WMS 圖資' },
  { key: 'weather', label: '氣象預報' },
]
const STATUS_LABELS: Record<string, string> = { connected: '已連接', error: '連線異常', never: '尚未測試' }
const changedFlag = ref(false)
function markChanged() {
  changedFlag.value = true
}

// ---- 田區 ----
const fields = ref<PrecisionFieldSummary[]>([])
const selectedFieldId = ref<string | null>(props.currentFieldId)
const selectedField = computed(() => fields.value.find((f) => f.id === selectedFieldId.value) ?? null)
const newField = ref({ open: false, name: '', area_text: '', grid_cols: 6, grid_rows: 4 })
const fieldsError = ref('')

async function loadFields() {
  try {
    fields.value = (await planning.precisionFields(props.farmerId)).items
    if (!selectedFieldId.value && fields.value.length) selectedFieldId.value = fields.value[0].id
  } catch (e) {
    fieldsError.value = (e as Error).message
  }
}

async function createField() {
  if (!newField.value.name.trim()) return
  try {
    const field = await planning.createPrecisionField(props.farmerId, {
      name: newField.value.name.trim(), area_text: newField.value.area_text.trim(),
      grid_cols: newField.value.grid_cols, grid_rows: newField.value.grid_rows,
    })
    fields.value.push(field)
    selectedFieldId.value = field.id
    newField.value = { open: false, name: '', area_text: '', grid_cols: 6, grid_rows: 4 }
    markChanged()
    toast.ok(`已新增田區「${field.name}」`)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function removeField(field: PrecisionFieldSummary) {
  if (!confirm(`確定刪除田區「${field.name}」？感測群組、圖層與紀錄一併刪除。`)) return
  try {
    await planning.deletePrecisionField(field.id)
    fields.value = fields.value.filter((f) => f.id !== field.id)
    if (selectedFieldId.value === field.id) selectedFieldId.value = fields.value[0]?.id ?? null
    markChanged()
    toast.ok('已刪除田區')
  } catch (e) {
    toast.error((e as Error).message)
  }
}

// ---- 感測群組與站點 ----
const groups = ref<SensorGroupDetail[]>([])
const groupsLoading = ref(false)
const newGroup = ref({ open: false, name: '' })
const newStation = ref<Record<string, { code: string; kind: 'soil' | 'weather' }>>({})
const uploadingGroup = ref<string | null>(null)

async function loadGroups() {
  if (!selectedFieldId.value) {
    groups.value = []
    return
  }
  groupsLoading.value = true
  try {
    groups.value = (await planning.sensorGroups(selectedFieldId.value)).items
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    groupsLoading.value = false
  }
}

async function createGroup() {
  if (!selectedFieldId.value || !newGroup.value.name.trim()) return
  try {
    groups.value.push(await planning.createSensorGroup(selectedFieldId.value, { name: newGroup.value.name.trim() }))
    newGroup.value = { open: false, name: '' }
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function removeGroup(group: SensorGroupDetail) {
  if (!confirm(`刪除感測群組「${group.name}」？站點與讀數一併刪除。`)) return
  try {
    await planning.deleteSensorGroup(group.id)
    groups.value = groups.value.filter((g) => g.id !== group.id)
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

function stationForm(groupId: string) {
  return newStation.value[groupId] ?? (newStation.value[groupId] = { code: '', kind: 'soil' })
}

async function addStation(group: SensorGroupDetail) {
  const form = stationForm(group.id)
  if (!form.code.trim()) return
  try {
    group.stations.push(await planning.createStation(group.id, { code: form.code.trim(), kind: form.kind }))
    newStation.value[group.id] = { code: '', kind: 'soil' }
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function removeStation(group: SensorGroupDetail, stationId: string) {
  try {
    await planning.deleteStation(stationId)
    group.stations = group.stations.filter((s) => s.id !== stationId)
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function uploadReadings(group: SensorGroupDetail, event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  uploadingGroup.value = group.id
  try {
    const result = await planning.uploadReadings(group.id, file)
    toast.ok(`匯入 ${result.inserted} 筆讀數${result.skipped_duplicates ? `，略過 ${result.skipped_duplicates} 筆重複` : ''}`)
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    uploadingGroup.value = null
  }
}

// ---- data_sources ----
const sources = ref<DataSource[]>([])
const newSource = ref({ open: false, kind: 'sensor_http' as DataSourceKind, label: '', endpoint: '', api_key: '', topic: '' })
const testingSource = ref<string | null>(null)
// 這頁存的密碼是用 SECRET_KEY 加密的；金鑰還是開發預設值時要在這裡講，
// 因為啟動 log 的警告只有管理者看得到，真正在這頁輸入密碼的人看不到。
const secretKeyIsDefault = ref(false)

/** 換過金鑰後解不回來的欄位。顯示「已設定」會讓人以為連線還能用，實際上送不出正確密碼。 */
function secretsNeedingReentry(source: DataSource): string[] {
  return Object.entries(source.config ?? {})
    .filter(([, value]) => value === SECRET_NEEDS_REENTRY)
    .map(([key]) => key)
}

async function loadSources() {
  try {
    const result = await planning.dataSources(props.farmerId)
    sources.value = result.items
    secretKeyIsDefault.value = result.secret_key_is_default
  } catch (e) {
    toast.error((e as Error).message)
  }
}

function configFor(kind: DataSourceKind, form: typeof newSource.value): Record<string, string> {
  if (kind === 'sensor_http' || kind === 'gis_wms') return { endpoint: form.endpoint, api_key: form.api_key }
  if (kind === 'sensor_mqtt') return { endpoint: form.endpoint, topic: form.topic, password: form.api_key }
  return {}
}

async function createSource() {
  if (!newSource.value.label.trim()) return
  try {
    sources.value.push(await planning.createDataSource(props.farmerId, {
      kind: newSource.value.kind, label: newSource.value.label.trim(),
      config: configFor(newSource.value.kind, newSource.value),
    }))
    newSource.value = { open: false, kind: 'sensor_http', label: '', endpoint: '', api_key: '', topic: '' }
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function testSource(source: DataSource) {
  testingSource.value = source.id
  try {
    const updated = await planning.testDataSource(source.id)
    const index = sources.value.findIndex((s) => s.id === source.id)
    if (index >= 0) sources.value[index] = updated
    if (updated.status === 'connected') toast.ok(`${updated.label}：${STATUS_LABELS[updated.status]}`)
    else toast.warn(`${updated.label}：${updated.last_error}`)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    testingSource.value = null
  }
}

async function removeSource(source: DataSource) {
  if (!confirm(`刪除資料來源「${source.label}」？使用它的感測群組會失去連線設定。`)) return
  try {
    await planning.deleteDataSource(source.id)
    sources.value = sources.value.filter((s) => s.id !== source.id)
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

// ---- 無人機影像 ----
const missions = ref<UavMission[]>([])
const uavForm = ref({ flown_on: today(), product: 'ndvi' as UavProduct })
const uavUploading = ref(false)

async function loadMissions() {
  if (!selectedFieldId.value) {
    missions.value = []
    return
  }
  try {
    missions.value = (await planning.uavMissions(selectedFieldId.value)).items
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function uploadUav(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file || !selectedFieldId.value) return
  uavUploading.value = true
  try {
    const mission = await planning.uploadUavMission(selectedFieldId.value, uavForm.value.flown_on, uavForm.value.product, file)
    missions.value.unshift(mission)
    toast.ok(mission.status === 'done' ? '已處理完成' : `狀態：${mission.status}`)
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    uavUploading.value = false
  }
}

// ---- GIS 邊界 ----
const boundary = ref<FieldBoundary | null>(null)
const boundaryText = ref('')
const boundaryCrs = ref<'EPSG:4326' | 'EPSG:3826'>('EPSG:4326')
const boundaryBusy = ref(false)

async function loadBoundary() {
  if (!selectedFieldId.value) {
    boundary.value = null
    return
  }
  try {
    boundary.value = (await planning.boundary(selectedFieldId.value)).boundary
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function importBoundary() {
  if (!selectedFieldId.value || !boundaryText.value.trim()) return
  let geojson: Record<string, unknown>
  try {
    geojson = JSON.parse(boundaryText.value)
  } catch {
    toast.error('不是合法的 JSON，請確認貼上的內容')
    return
  }
  boundaryBusy.value = true
  try {
    boundary.value = await planning.importBoundary(selectedFieldId.value, geojson, boundaryCrs.value)
    boundaryText.value = ''
    toast.ok(`邊界已匯入，${boundary.value.outside_cells.length} 格落在邊界外`)
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    boundaryBusy.value = false
  }
}

async function removeBoundary() {
  if (!selectedFieldId.value || !confirm('移除這個田區的邊界？分區圖會退回示範格子（沒有內外之分）。')) return
  try {
    await planning.deleteBoundary(selectedFieldId.value)
    boundary.value = null
    markChanged()
  } catch (e) {
    toast.error((e as Error).message)
  }
}

watch(selectedFieldId, () => {
  void loadGroups()
  void loadMissions()
  void loadBoundary()
})

onMounted(async () => {
  await Promise.all([loadFields(), loadSources()])
  await Promise.all([loadGroups(), loadMissions(), loadBoundary()])
})

function close() {
  emit('close')
  if (changedFlag.value) emit('changed')
}
</script>

<template>
  <FarmModal title="資料來源與地點" wide @close="close">
    <div class="ds">
      <p class="form-hint ds-intro">
        感測器可以多組、地點可以多個；無人機與 GIS 的資料進來後，分區圖層會自動更新。目前除「示範」外皆未連接。
      </p>
      <p v-if="fieldsError" class="form-error" role="alert">{{ fieldsError }}</p>

      <section class="ds-block">
        <div class="ds-block-h">
          <span class="title">地點（田區）</span>
          <button v-if="owner" type="button" class="text-btn" @click="newField.open = !newField.open">＋ 新增田區</button>
        </div>
        <div v-for="field in fields" :key="field.id" class="ds-site" :class="{ on: field.id === selectedFieldId }">
          <button type="button" class="ds-site-name" @click="selectedFieldId = field.id">
            {{ field.name }}<small>{{ field.area_text || '面積未填' }}{{ field.is_demo ? ' · 示範資料' : '' }}</small>
          </button>
          <button v-if="owner && !field.is_demo" type="button" class="text-btn danger" @click="removeField(field)">刪除</button>
        </div>
        <p v-if="!fields.length" class="quiet-state">還沒有田區。</p>
        <form v-if="newField.open" class="farm-form ds-inline-form" @submit.prevent="createField">
          <div class="form-grid three">
            <label>田區名稱<input v-model="newField.name" required maxlength="160" /></label>
            <label>面積<input v-model="newField.area_text" maxlength="60" placeholder="例如：0.5 公頃" /></label>
            <label
              >格數<span class="ds-grid-inputs"
                ><input v-model.number="newField.grid_cols" type="number" min="1" max="20" /> ×
                <input v-model.number="newField.grid_rows" type="number" min="1" max="20"
              /></span>
            </label>
          </div>
          <div class="form-actions">
            <button type="button" class="btn" @click="newField.open = false">取消</button>
            <button class="btn primary" type="submit">新增</button>
          </div>
        </form>
      </section>

      <template v-if="selectedField">
        <section class="ds-block">
          <div class="ds-block-h">
            <span class="title">感測器群組 · {{ selectedField.name }}</span>
            <button v-if="owner" type="button" class="text-btn" @click="newGroup.open = !newGroup.open">＋ 新增群組</button>
          </div>
          <p v-if="groupsLoading" class="quiet-state">載入中⋯</p>
          <div v-for="group in groups" :key="group.id" class="ds-group">
            <div class="ds-group-h">
              <b>{{ group.name }}</b>
              <span class="tag" :class="group.is_demo ? 'info' : group.stations.length ? 'ok' : 'warn'">
                {{ group.is_demo ? '示範資料' : group.stations.length ? `${group.stations.length} 站` : '尚未設定站點' }}
              </span>
              <label v-if="owner" class="btn sm ds-upload-btn">
                <Icon name="download" :size="13" />{{ uploadingGroup === group.id ? '上傳中⋯' : 'CSV 上傳讀數' }}
                <input type="file" accept=".csv" :disabled="uploadingGroup === group.id" @change="uploadReadings(group, $event)" />
              </label>
              <button v-if="owner" type="button" class="text-btn danger" @click="removeGroup(group)">刪除</button>
            </div>
            <div v-if="group.stations.length" class="ds-stations">
              <span v-for="station in group.stations" :key="station.id" class="tag moa ds-station">
                {{ station.code }}（{{ station.kind === 'soil' ? '土壤' : '氣象' }}）
                <button v-if="owner" type="button" aria-label="刪除站點" @click="removeStation(group, station.id)"><Icon name="close" :size="10" /></button>
              </span>
            </div>
            <form v-if="owner" class="ds-station-form" @submit.prevent="addStation(group)">
              <input v-model="stationForm(group.id).code" placeholder="站代碼，例如 S-1" maxlength="20" />
              <select v-model="stationForm(group.id).kind">
                <option value="soil">土壤</option>
                <option value="weather">氣象</option>
              </select>
              <button type="submit" class="btn sm">＋ 站點</button>
            </form>
          </div>
          <p v-if="!groups.length && !groupsLoading" class="quiet-state">這個田區還沒有感測群組。</p>
          <form v-if="newGroup.open" class="farm-form ds-inline-form" @submit.prevent="createGroup">
            <label>群組名稱<input v-model="newGroup.name" required maxlength="80" placeholder="例如：土壤組 A" /></label>
            <div class="form-actions">
              <button type="button" class="btn" @click="newGroup.open = false">取消</button>
              <button class="btn primary" type="submit">新增</button>
            </div>
          </form>
          <p class="ds-flow">接法：<b>HTTP 輪詢</b>（廠商 API 金鑰）· <b>MQTT</b>（訂閱 topic）· <b>CSV 上傳</b>（沒有 API 的舊設備）</p>
        </section>

        <section class="ds-block">
          <div class="ds-block-h"><span class="title">無人機影像（UAV） · {{ selectedField.name }}</span></div>
          <div v-for="mission in missions" :key="mission.id" class="ds-item">
            <span
              ><b>{{ mission.flown_on }} {{ mission.product_label }}</b>
              <small>{{ mission.note || (mission.status === 'done' ? '已處理' : mission.status) }}</small></span
            >
            <span class="tag" :class="{ ok: mission.status === 'done', warn: mission.status === 'failed', info: mission.is_demo }">
              {{ mission.is_demo ? '示範資料' : mission.status === 'done' ? '處理完成' : mission.status === 'failed' ? '失敗' : mission.status }}
            </span>
          </div>
          <p v-if="!missions.length" class="quiet-state">還沒有上傳過無人機成果。</p>
          <form v-if="owner" class="ds-uav-form" @submit.prevent>
            <input v-model="uavForm.flown_on" type="date" :max="today()" />
            <select v-model="uavForm.product">
              <option value="ndvi">NDVI</option>
              <option value="multispectral">多光譜</option>
              <option value="rgb">RGB（僅留存）</option>
            </select>
            <label class="btn sm">
              <Icon name="download" :size="13" />{{ uavUploading ? '上傳中⋯' : '上傳 CSV' }}
              <input type="file" accept=".csv" :disabled="uavUploading" @change="uploadUav" />
            </label>
          </form>
          <p class="ds-flow">格式：<b>CSV</b>（col,row,value，示範階段）· 之後可接 GeoTIFF 或影像服務 API</p>
        </section>

        <section class="ds-block">
          <div class="ds-block-h">
            <span class="title">GIS 邊界 · {{ selectedField.name }}</span>
            <span class="tag" :class="boundary ? 'ok' : 'warn'">{{ boundary ? `已匯入（${boundary.source}）` : '未匯入' }}</span>
          </div>
          <p v-if="boundary" class="ds-boundary-info">
            匯入於 {{ new Date(boundary.imported_at).toLocaleString('zh-TW') }}；{{ boundary.outside_cells.length }} 格落在邊界外。
            <button v-if="owner" type="button" class="text-btn danger" @click="removeBoundary">移除邊界</button>
          </p>
          <p v-else class="quiet-state">田區邊界目前是手繪格子（示範），格子沒有內外之分。</p>
          <form v-if="owner" class="farm-form ds-inline-form" @submit.prevent="importBoundary">
            <label
              >GeoJSON（Polygon／MultiPolygon）
              <textarea v-model="boundaryText" rows="4" placeholder='{"type":"Polygon","coordinates":[[[121.5,24.0],...]]}' />
            </label>
            <div class="form-grid">
              <label
                >座標系統<select v-model="boundaryCrs">
                  <option value="EPSG:4326">EPSG:4326（WGS84 經緯度）</option>
                  <option value="EPSG:3826">EPSG:3826（TWD97 地籍常用）</option>
                </select></label
              >
              <div class="form-actions ds-boundary-submit">
                <button class="btn primary" type="submit" :disabled="boundaryBusy || !boundaryText.trim()">
                  {{ boundaryBusy ? '匯入中⋯' : '匯入邊界' }}
                </button>
              </div>
            </div>
          </form>
          <p class="ds-flow">接法：<b>檔案上傳</b>（GeoJSON）· 之後可接 WMS／WFS；座標一律轉 EPSG:4326 存。</p>
        </section>
      </template>

      <section class="ds-block">
        <div class="ds-block-h">
          <span class="title">連線設定（data_sources）</span>
          <button v-if="owner" type="button" class="text-btn" @click="newSource.open = !newSource.open">＋ 新增來源</button>
        </div>
        <p v-if="secretKeyIsDefault" class="ds-key-warning">
          <Icon name="info" :size="16" />
          <span
            >金鑰為開發預設，正式環境請設定。這頁存的密碼是用伺服器的
            <code>SECRET_KEY</code> 加密的，預設值寫在原始碼裡，等同未加密。</span
          >
        </p>
        <div v-for="source in sources" :key="source.id" class="ds-item">
          <span
            ><b>{{ source.label }}</b>
            <small>{{ SOURCE_KINDS.find((k) => k.key === source.kind)?.label }}{{ source.last_error ? ' · ' + source.last_error : '' }}</small>
            <small v-if="secretsNeedingReentry(source).length" class="ds-reentry"
              >⚠ {{ secretsNeedingReentry(source).join('、') }} 需重新輸入（金鑰已更換，舊的解不回來）</small
            ></span
          >
          <span class="ds-item-acts">
            <span class="tag" :class="{ ok: source.status === 'connected', warn: source.status === 'error', info: source.status === 'never' }">
              {{ STATUS_LABELS[source.status] }}
            </span>
            <button v-if="owner" type="button" class="text-btn" :disabled="testingSource === source.id" @click="testSource(source)">
              {{ testingSource === source.id ? '測試中⋯' : '測試連線' }}
            </button>
            <button v-if="owner" type="button" class="text-btn danger" @click="removeSource(source)">刪除</button>
          </span>
        </div>
        <p v-if="!sources.length" class="quiet-state">還沒有設定任何連線來源。</p>
        <form v-if="newSource.open" class="farm-form ds-inline-form" @submit.prevent="createSource">
          <div class="form-grid">
            <label
              >種類<select v-model="newSource.kind">
                <option v-for="option in SOURCE_KINDS" :key="option.key" :value="option.key">{{ option.label }}</option>
              </select></label
            >
            <label>名稱<input v-model="newSource.label" required maxlength="120" placeholder="例如：土壤組 A 廠商 API" /></label>
          </div>
          <div v-if="['sensor_http', 'sensor_mqtt', 'gis_wms'].includes(newSource.kind)" class="form-grid">
            <label>連線位址<input v-model="newSource.endpoint" placeholder="https:// 或 mqtt://" /></label>
            <label v-if="newSource.kind === 'sensor_mqtt'">Topic<input v-model="newSource.topic" /></label>
            <label v-else>API 金鑰<input v-model="newSource.api_key" type="password" autocomplete="off" /></label>
          </div>
          <div class="form-actions">
            <button type="button" class="btn" @click="newSource.open = false">取消</button>
            <button class="btn primary" type="submit">新增</button>
          </div>
        </form>
      </section>
    </div>
  </FarmModal>
</template>

<style scoped>
.ds {
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.ds-intro {
  margin: -8px 0 0;
}
.ds-block-h {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 8px;
}
.ds-block-h .title {
  font-size: 14px;
  font-weight: 700;
}
.ds-block-h .text-btn {
  margin-left: auto;
}
.ds-site {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 8px;
}
.ds-site.on {
  background: #f4f0fa;
  box-shadow: inset 3px 0 0 #5b3d8a;
}
.ds-site-name {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  border: 0;
  background: transparent;
  padding: 4px 0;
  font: inherit;
  font-weight: 600;
  font-size: 14px;
  color: var(--ink);
  text-align: left;
}
.ds-site-name small {
  font-weight: 400;
  font-size: 12px;
  color: var(--muted);
}
.ds-group {
  padding: 10px 0;
  border-top: 1px dashed var(--line);
}
.ds-group-h {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.ds-upload-btn {
  position: relative;
  margin-left: auto;
  overflow: hidden;
}
.ds-upload-btn input {
  position: absolute;
  inset: 0;
  opacity: 0;
  cursor: pointer;
}
.ds-stations {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}
.ds-station {
  gap: 6px;
}
.ds-station button {
  display: inline-flex;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 0;
}
.ds-station-form {
  display: flex;
  gap: 6px;
  margin-top: 8px;
}
.ds-station-form input {
  flex: 1;
  min-width: 0;
  padding: 6px 8px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font: inherit;
  font-size: 13px;
}
.ds-flow {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
  margin: 10px 0 0;
  flex-wrap: wrap;
}
.ds-flow b {
  color: var(--ink-2);
  font-weight: 600;
}
.ds-key-warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 0 0 10px;
  padding: 10px 12px;
  border: 1px solid #f2dfb3;
  border-radius: 10px;
  background: #fdf3df;
  color: #6d4506;
  font-size: 13px;
  line-height: 1.6;
}
.ds-key-warning code {
  font-size: 12px;
}
.ds-reentry {
  color: #b8770b;
  font-weight: 600;
}
.ds-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 0;
  border-top: 1px dashed var(--line);
  font-size: 13px;
}
.ds-item > span:first-child {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.ds-item small {
  color: var(--muted);
  font-size: 12px;
}
.ds-item-acts {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.ds-uav-form {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  flex-wrap: wrap;
}
.ds-uav-form select,
.ds-uav-form input[type='date'] {
  height: 32px;
}
.ds-boundary-info {
  margin: 0;
  font-size: 13px;
  color: var(--ink-2);
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.ds-boundary-submit {
  align-items: flex-end;
}
.ds-inline-form {
  margin-top: 10px;
  padding: 12px;
  border: 1px dashed var(--line-strong);
  border-radius: 10px;
}
.ds-grid-inputs {
  display: flex;
  align-items: center;
  gap: 6px;
}
.ds-grid-inputs input {
  width: 56px;
}
.three {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}
.danger {
  color: var(--crit);
}
@media (max-width: 660px) {
  .three,
  .form-grid {
    grid-template-columns: 1fr;
  }
}
</style>
