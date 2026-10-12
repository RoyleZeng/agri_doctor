<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  planning,
  type Farmer,
  type LayerResponse,
  type LogFormMode,
  type Plan,
  type PrecisionOverview,
  type PrecisionStation,
  type Recommendation,
  type WorkLog,
} from '@/api/planning'
import { ageLabel, precisionUnlocked, tileState } from '@/composables/precisionState'
import { toast } from '@/composables/toast'
import DataSourcesPanel from './DataSourcesPanel.vue'
import AppBanner from './AppBanner.vue'
import Icon from './Icon.vue'
import MoistureChart from './MoistureChart.vue'
import RecommendationCard from './RecommendationCard.vue'
import SensorTile from './SensorTile.vue'
import StepStrip from './StepStrip.vue'
import WorkLogQuickAdd from './WorkLogQuickAdd.vue'
import ZoneMap from './ZoneMap.vue'
import ZoneTable from './ZoneTable.vue'

/**
 * 精準模式儀表板。Observe（感測）→ Analyze（分區比較）→ Decide（建議卡）→ Act（接受／已執行／略過），
 * 做完的事回饋到下一輪 Observe——這條回饋線就是「示範資料」與「正式功能」共用同一套表與畫面的理由。
 */
const props = defineProps<{ farmer: Farmer; user: Farmer; plans: Plan[] }>()
const emit = defineEmits<{ saved: [farmer: Farmer] }>()

const owner = computed(() => (props.user.role === 'admin' || props.user.id === props.farmer.id))
const overview = ref<PrecisionOverview | null>(null)
const loading = ref(true)
const error = ref('')
const fieldId = ref<string | null>(null)
const groupId = ref<string | 'all'>('all')
const layerKind = ref<'ndvi' | 'soil_moisture' | 'ph' | 'yield_prev'>('ndvi')
const layer = ref<LayerResponse | null>(null)
const chartLoading = ref(false)
const chart = ref<Awaited<ReturnType<typeof planning.precisionChart>> | null>(null)
const analyzing = ref(false)
const analyzeNote = ref('')
const busyId = ref<string | null>(null)
const quick = ref<{ key: number; rec: Recommendation } | null>(null)
const sourcesOpen = ref(false)
const enabling = ref(false)

const field = computed(() => overview.value?.field ?? null)
const hasField = computed(() => !!overview.value?.fields.length)
const unlocked = computed(() => precisionUnlocked(!!props.farmer.precision_enabled, hasField.value))
const isDemo = computed(() => !!field.value?.is_demo || (overview.value?.recommendations.some((r) => r.is_demo) ?? false))
const canDecide = computed(() => (props.user.role === 'admin' || props.user.id === props.farmer.id))

async function load(targetFieldId?: string) {
  loading.value = true
  error.value = ''
  try {
    const data = await planning.precisionOverview(props.farmer.id, targetFieldId)
    overview.value = data
    fieldId.value = data.field?.id ?? null
    groupId.value = 'all'
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
  }
}

async function loadLayer() {
  if (!fieldId.value) return
  try {
    layer.value = await planning.precisionLayer(fieldId.value, layerKind.value)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

const focusZone = computed(() => {
  const water = overview.value?.recommendations.find((r) => r.kind === 'irrigate' && r.status === 'proposed')
  if (water) return (water.payload as { zones: { code: string }[] }).zones[0]?.code
  const stations = field.value?.stations.filter((s) => s.kind === 'soil' && s.zone_code) ?? []
  const driest = stations.reduce<{ code: string; value: number } | null>((min, s) => {
    const value = s.latest.soil_moisture?.value
    if (value === undefined || !s.zone_code) return min
    return !min || value < min.value ? { code: s.zone_code, value } : min
  }, null)
  return driest?.code ?? field.value?.zones[0]?.code ?? null
})

async function loadChart() {
  if (!fieldId.value) return
  chartLoading.value = true
  try {
    chart.value = await planning.precisionChart(fieldId.value, 'soil_moisture', 24 * 30)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    chartLoading.value = false
  }
}

watch(fieldId, () => {
  layerKind.value = 'ndvi'
  void loadLayer()
  void loadChart()
})
watch(layerKind, () => void loadLayer())
void load()

function selectField(id: string) {
  if (id === fieldId.value) return
  void load(id)
}

const filteredStations = computed(() => {
  const stations = field.value?.stations ?? []
  return groupId.value === 'all' ? stations : stations.filter((s) => s.group_id === groupId.value)
})

function latestAgeMinutes(station: PrecisionStation, metric: string): number | null {
  const ts = station.latest[metric]?.ts
  return ts ? Math.round((Date.now() - new Date(ts).getTime()) / 60000) : null
}
function sparklineRange(station: PrecisionStation | undefined, metric: string): [number, number] | null {
  const points = station?.sparkline[metric]
  if (!points?.length) return null
  const values = points.map((p) => p.value)
  return [Math.min(...values), Math.max(...values)]
}

const tiles = computed(() => {
  const soil = filteredStations.value.filter((s) => s.kind === 'soil')
  const weather = filteredStations.value.filter((s) => s.kind === 'weather')
  const threshold = overview.value?.rules.irrigate.threshold_pct ?? null
  const out: { key: string; icon: 'drop' | 'thermometer' | 'flask' | 'leaf'; label: string; value: string; unit?: string; state: 'ok' | 'warn'; sub: string; age: string }[] = []

  const driest = soil.reduce<PrecisionStation | null>((min, s) => {
    const value = s.latest.soil_moisture?.value
    return value !== undefined && (!min || value < (min.latest.soil_moisture?.value ?? Infinity)) ? s : min
  }, null)
  if (driest) {
    const value = driest.latest.soil_moisture!.value
    out.push({
      key: 'soil_moisture', icon: 'drop', label: `${driest.zone_code ?? ''} 區土壤水分`.trim(),
      value: value.toFixed(0), unit: '%', state: tileState(value, threshold, 'below'),
      sub: threshold !== null && value < threshold ? `低於閾值 ${threshold}%` : '正常',
      age: ageLabel(latestAgeMinutes(driest, 'soil_moisture')),
    })
  }
  const soilTemps = soil.map((s) => s.latest.soil_temp?.value).filter((v): v is number => v !== undefined)
  if (soilTemps.length) {
    const range = soil.flatMap((s) => sparklineRange(s, 'soil_temp') ?? [])
    out.push({
      key: 'soil_temp', icon: 'thermometer', label: '土壤溫度',
      value: (soilTemps.reduce((a, b) => a + b, 0) / soilTemps.length).toFixed(1), unit: '°C', state: 'ok',
      sub: range.length ? `日內變化 ${Math.min(...range).toFixed(0)}–${Math.max(...range).toFixed(0)}` : '—',
      age: ageLabel(latestAgeMinutes(soil[0], 'soil_temp')),
    })
  }
  const ecs = soil.map((s) => s.latest.ec?.value).filter((v): v is number => v !== undefined)
  if (ecs.length) {
    out.push({
      key: 'ec', icon: 'flask', label: '土壤 EC', value: (ecs.reduce((a, b) => a + b, 0) / ecs.length).toFixed(2), unit: 'dS/m',
      state: 'ok', sub: '穩定', age: ageLabel(latestAgeMinutes(soil[0], 'ec')),
    })
  }
  const station = weather[0]
  if (station?.latest.air_temp) {
    out.push({
      key: 'air_temp', icon: 'thermometer', label: '氣象站 · 氣溫', value: station.latest.air_temp.value.toFixed(1), unit: '°C',
      state: 'ok', sub: '氣象站 · 田區內', age: ageLabel(latestAgeMinutes(station, 'air_temp')),
    })
  }
  if (station?.latest.humidity) {
    out.push({
      key: 'humidity', icon: 'leaf', label: '氣象站 · 濕度', value: station.latest.humidity.value.toFixed(0), unit: '%',
      state: 'ok', sub: '氣象站 · 田區內', age: ageLabel(latestAgeMinutes(station, 'humidity')),
    })
  }
  return out
})

const stepDetail = computed(() => {
  const f = field.value
  const recs = overview.value?.recommendations.filter((r) => r.status === 'proposed') ?? []
  const ndviDate = f?.ndvi_captured_on
    ? new Date(f.ndvi_captured_on).toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric' })
    : '尚無影像'
  const lowest = (() => {
    const cells = layer.value?.layer?.zone_values
    if (!cells) return ''
    const codes = Object.keys(cells)
    if (!codes.length) return ''
    const min = codes.reduce((a, b) => (cells[a] < cells[b] ? a : b))
    return `；${min} 區生長勢與水分相對偏低`
  })()
  return {
    observe: f ? `${f.stations.length} 個感測點 · ${ageLabel(f.sensor_age_minutes)}更新 · 上次 NDVI 影像 ${ndviDate}` : '尚無感測資料',
    analyze: f ? `田區分成 ${f.zones.length} 個管理分區${lowest}` : '尚無田區可分析',
    decide: recs.length ? `${recs.length} 項建議待你確認：${recs.map((r) => r.title).join('、')}` : '目前沒有待確認的建議',
    act: '確認後轉成農事待辦；做完記在日誌，回饋到下一輪分析',
  }
})

function updateRecommendation(updated: Recommendation) {
  if (!overview.value) return
  const index = overview.value.recommendations.findIndex((r) => r.id === updated.id)
  if (index >= 0) overview.value.recommendations[index] = updated
  else overview.value.recommendations.push(updated)
}

async function accept(rec: Recommendation) {
  busyId.value = rec.id
  try {
    updateRecommendation(await planning.precisionAccept(rec.id))
    toast.ok(`已接受，排入今天的農事待辦：${rec.title}`)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busyId.value = null
  }
}

function done(rec: Recommendation) {
  quick.value = { key: Date.now(), rec }
}

function onLogSaved(log: WorkLog) {
  if (!log.recommendation || !overview.value) return
  const rec = overview.value.recommendations.find((r) => r.id === log.recommendation!.id)
  if (rec) updateRecommendation({ ...rec, status: 'done', work_log_id: log.recommendation.work_log_id })
  quick.value = null
  toast.ok('已記錄到農事日誌，建議標記為已執行')
}

async function dismiss(rec: Recommendation, reason: string) {
  busyId.value = rec.id
  try {
    updateRecommendation(await planning.precisionDismiss(rec.id, reason))
    toast.ok('已略過這則建議')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busyId.value = null
  }
}

async function analyze() {
  if (!fieldId.value) return
  analyzing.value = true
  analyzeNote.value = ''
  try {
    const result = await planning.precisionAnalyze(fieldId.value)
    for (const item of [...result.items, ...result.withdrawn]) updateRecommendation(item)
    const parts: string[] = []
    if (result.items.length) parts.push(`${result.items.length} 項建議`)
    if (result.withdrawn.length) parts.push(`${result.withdrawn.length} 項已撤回：${result.withdrawn.map((r) => r.title).join('、')}`)
    if (!parts.length && result.notes.length) analyzeNote.value = result.notes.map((n) => n.message).join('；')
    toast.ok(parts.length ? `重新分析完成：${parts.join('，')}` : '重新分析完成，目前沒有新建議')
    void loadChart()
    void loadLayer()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    analyzing.value = false
  }
}

async function enablePrecision() {
  enabling.value = true
  try {
    emit('saved', await planning.togglePrecision(true, props.farmer.id))
    toast.ok('已啟用精準模式')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    enabling.value = false
  }
}

async function toggleDemo(enabled: boolean) {
  try {
    emit('saved', await planning.togglePrecision(enabled, props.farmer.id))
  } catch (e) {
    toast.error((e as Error).message)
  }
}
</script>

<template>
  <div class="precision">
    <div v-if="loading" class="farm-loading" role="status">
      <Icon name="leaf" :size="26" />
      <p>正在準備精準模式⋯</p>
    </div>
    <section v-else-if="error" class="card farm-empty" role="alert">
      <Icon name="warn" :size="32" />
      <h2>暫時無法載入精準模式</h2>
      <p>{{ error }}</p>
      <button class="btn primary" @click="load()">重新載入</button>
    </section>

    <section v-else-if="!unlocked" class="card farm-empty">
      <Icon name="leaf" :size="34" />
      <h2>精準模式：分區管理、感測、依資料給建議</h2>
      <p>依田區分區監測土壤與環境，系統比較差異並給出補灌、施肥建議；接受後轉成待辦，做完記在日誌，決定權一直在你。</p>
      <button v-if="owner" class="btn primary" :disabled="enabling" @click="enablePrecision">
        {{ enabling ? '啟用中⋯' : '啟用精準模式' }}
      </button>
      <p v-else class="form-hint">這座農場尚未啟用精準模式。</p>
    </section>

    <template v-else>
      <div class="precision-head">
        <div>
          <h2>精準模式</h2>
          <p>示範資料展示分區管理、感測與建議；接上實際感測器或無人機影像後自動換成真實值。</p>
        </div>
        <button class="btn" @click="sourcesOpen = true"><Icon name="link" :size="15" />資料來源與地點</button>
      </div>

      <!-- 示範資料改用共用的 AppBanner（demo tone）：全站的「整頁要先講的一句話」統一一種長相。 -->
      <AppBanner
        v-if="isDemo"
        tone="demo"
        title="示範資料"
        body="這個測試帳號的感測值、NDVI 與分區都是合成資料，用來展示功能；接上實際感測器或無人機影像後會自動換成真實值。"
      >
        <label v-if="owner" class="demo-toggle" :class="{ disabled: enabling }">
          精準模式
          <input
            type="checkbox"
            role="switch"
            :checked="!!farmer.precision_enabled"
            :disabled="enabling"
            @change="toggleDemo(($event.target as HTMLInputElement).checked)"
          />
        </label>
      </AppBanner>
      <p v-if="!isDemo && !canDecide" class="quiet-state precision-guest">已連結的植物醫生檢視畫面；接受、已執行、略過只有農民本人能操作。</p>

      <div v-if="overview && overview.fields.length" class="sitebar">
        <span class="lbl">地點</span>
        <button
          v-for="f in overview.fields"
          :key="f.id"
          type="button"
          class="chip"
          :class="{ on: f.id === fieldId }"
          :aria-pressed="f.id === fieldId"
          @click="selectField(f.id)"
        >
          {{ f.name }}<template v-if="f.area_text"> · {{ f.area_text }}</template>
        </button>
        <template v-if="field && field.groups.length">
          <span class="sep" />
          <span class="lbl">感測群組</span>
          <button type="button" class="chip" :class="{ on: groupId === 'all' }" @click="groupId = 'all'">全部</button>
          <button
            v-for="group in field.groups"
            :key="group.id"
            type="button"
            class="chip"
            :class="{ on: groupId === group.id }"
            @click="groupId = group.id"
          >
            {{ group.name }}
          </button>
        </template>
        <span v-if="field" class="src">
          <i class="ok" />感測 {{ ageLabel(field.sensor_age_minutes) }}
          <i :class="field.ndvi_captured_on ? 'ok' : 'warn'" />NDVI
          {{ field.ndvi_captured_on ? new Date(field.ndvi_captured_on).toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric' }) : '尚無' }}
          <i :class="field.boundary ? 'ok' : 'warn'" />GIS 地籍邊界 {{ field.boundary ? '已匯入' : '未匯入' }}
        </span>
      </div>

      <StepStrip :observe="stepDetail.observe" :analyze="stepDetail.analyze" :decide="stepDetail.decide" :act="stepDetail.act" />

      <div v-if="tiles.length" class="tiles">
        <SensorTile v-for="tile in tiles" :key="tile.key" :icon="tile.icon" :label="tile.label" :value="tile.value" :unit="tile.unit" :state="tile.state" :sub="tile.sub" :age="tile.age" />
      </div>

      <div v-if="field" class="layout">
        <ZoneMap
          :grid-cols="field.grid_cols"
          :grid-rows="field.grid_rows"
          :zones="field.zones"
          :stations="filteredStations"
          :layer="layer?.layer ?? null"
          :kind="layerKind"
          :outside-cells="field.boundary?.outside_cells"
          @update:kind="layerKind = $event"
        >
          <template #table>
            <ZoneTable
              :zones="field.zones"
              :ndvi="layerKind === 'ndvi' ? (layer?.layer?.zone_values ?? null) : null"
              :moisture="Object.fromEntries(filteredStations.filter((s) => s.zone_code && s.latest.soil_moisture).map((s) => [s.zone_code as string, s.latest.soil_moisture!.value]))"
              :threshold-pct="overview?.rules.irrigate.threshold_pct ?? null"
            />
          </template>
        </ZoneMap>

        <section class="card">
          <div class="card-h decide-head">
            <span class="title">決策建議 · 待確認 {{ overview?.recommendations.filter((r) => r.status === 'proposed').length ?? 0 }} 項</span>
            <!-- 重新分析會重算並寫入建議，對植物醫生是寫入動作，唯讀檢視不給。 -->
            <button v-if="canDecide" class="btn sm" :disabled="analyzing" @click="analyze">
              <Icon name="refresh" :size="14" />{{ analyzing ? '分析中⋯' : '重新分析' }}
            </button>
          </div>
          <p v-if="analyzeNote" class="analyze-note"><Icon name="info" :size="13" />{{ analyzeNote }}</p>
          <RecommendationCard
            v-for="rec in overview?.recommendations ?? []"
            :key="rec.id"
            :rec="rec"
            :can-decide="canDecide"
            :busy="busyId === rec.id"
            @accept="accept(rec)"
            @done="done(rec)"
            @dismiss="(reason) => dismiss(rec, reason)"
          />
          <p v-if="!overview?.recommendations.length" class="quiet-state rec-empty">目前沒有建議，按「重新分析」看看田間狀況。</p>
        </section>
      </div>

      <MoistureChart
        v-if="focusZone && chart"
        :zone-code="focusZone"
        :series="chart.series[focusZone] ?? []"
        :rain="chart.rain"
        :stages="chart.stages"
        :threshold-pct="chart.threshold_pct"
        :loading="chartLoading"
      />
    </template>

    <WorkLogQuickAdd
      v-if="quick"
      :key="quick.key"
      title="已執行，記到日誌"
      :farmer-id="farmer.id"
      :plans="plans"
      :mode="{ type: 'prefill', recommendation_id: quick.rec.id } as LogFormMode"
      @saved="onLogSaved"
      @close="quick = null"
    />
    <DataSourcesPanel
      v-if="sourcesOpen"
      :farmer-id="farmer.id"
      :owner="owner"
      :current-field-id="fieldId"
      @close="sourcesOpen = false"
      @changed="load(fieldId ?? undefined)"
    />
  </div>
</template>

<style scoped>
.demo-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #4a2f75; white-space: nowrap; }
.demo-toggle.disabled { opacity: 0.6; }
.precision-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-bottom: 14px;
}
.precision-head h2 {
  font-size: 20px;
}
.precision-head p {
  margin-top: 2px;
  color: var(--farm-muted);
  font-size: 13px;
}
.precision-guest {
  margin: -6px 0 16px;
}
.sitebar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.sitebar .lbl {
  font-size: 12px;
  color: var(--muted);
  font-weight: 600;
  margin-right: 2px;
}
.sitebar .chip {
  height: 32px;
  font-size: 13px;
}
.sitebar .sep {
  width: 1px;
  height: 22px;
  background: var(--line);
  margin: 0 6px;
}
.sitebar .src {
  margin-left: auto;
  font-size: 12px;
  color: var(--muted);
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.sitebar .src i {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
  margin-left: 8px;
}
.sitebar .src i.ok {
  background: var(--kdais);
}
.sitebar .src i.warn {
  background: var(--warn);
}
.tiles {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 16px;
}
.layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 400px;
  gap: 22px;
  align-items: start;
  margin-bottom: 22px;
}
.decide-head {
  background: #f4f0fa;
}
.analyze-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: 10px 18px;
  background: #fafbf9;
  color: var(--ink-2);
  font-size: 13px;
  border-bottom: 1px solid var(--line);
}
.rec-empty {
  padding: 24px 18px;
  text-align: center;
}
@media (max-width: 1100px) {
  .layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 660px) {
  .precision-head {
    flex-direction: column;
    align-items: stretch;
  }
  .sitebar .src {
    margin-left: 0;
    width: 100%;
  }
}
</style>
