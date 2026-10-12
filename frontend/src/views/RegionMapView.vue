<script setup lang="ts">
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  planning,
  PlanningError,
  type RegionDistrictDetail,
  type RegionDistrictRow,
  type RegionSource,
  type RegionSummary,
  type SurveyInput,
} from '@/api/planning'
import { toast } from '@/composables/toast'
import { session, updateMe } from '@/stores/session'

/**
 * 區域作物與病蟲害地圖。角色不同看到的範圍不同（後端已經做過濾，這裡不再自己藏資料）：
 * 農民只看得到自己所在鄉鎮，植物醫生與主管機關／改良場看得到全高雄市；
 * 主動調查登錄與匯出報表只有植物醫生／機關看得到按鈕。
 */
const SOURCES: { key: RegionSource | ''; label: string }[] = [
  { key: '', label: '全部' },
  { key: 'consult', label: '診斷案件' },
  { key: 'scout', label: '巡田通報' },
  { key: 'survey', label: '主動調查' },
]
// 沒有資料時的灰色；有資料時依 summary.legend 的切點分級套暖色系（淺到深）。
const SCALE = ['#fdf3df', '#f6dfa6', '#ecb96a', '#d98a3a', '#b8541d']
const NO_DATA = '#e8ede8'

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}
function daysAgoStr(n: number) {
  const d = new Date()
  d.setDate(d.getDate() - n)
  return d.toISOString().slice(0, 10)
}

/**
 * 一個 `status` 取代原本 `loading`／`error`／`summary.available` 三個交錯的判斷。
 *
 * 第三輪回饋「區域地圖一直閃」的原因就是那三個旗標互相交錯：地圖的 `<div>` 被包在
 * `v-else` 裡，422 一來整塊（含 div）就被拿掉，Leaflet 還綁著已經不在 DOM 的節點；
 * 使用者一動篩選 `error` 被清空，div 重新出現但 map 綁的是舊的，於是畫面空白 → 又 422 → 又消失。
 * 現在地圖容器永遠在 DOM，狀態只用遮罩蓋在上面。
 */
type Status = 'idle' | 'loading' | 'empty' | 'error' | 'ready'
const status = ref<Status>('idle')
const emptyReason = ref<'district_missing' | 'county_unsupported' | ''>('')
const emptyMessage = ref('')
const error = ref('')

const user = computed(() => session.me)
const source = ref<RegionSource | ''>('')
const dateFrom = ref(daysAgoStr(30))
const dateTo = ref(todayStr())

const summary = ref<RegionSummary | null>(null)
const districts = ref<RegionDistrictRow[]>([])
const selected = ref<string | null>(null)
const detail = ref<RegionDistrictDetail | null>(null)
const detailLoading = ref(false)
const geo = ref<GeoJSON.FeatureCollection | null>(null)

const mapEl = ref<HTMLDivElement | null>(null)
let map: L.Map | null = null
let geoLayer: L.GeoJSON | null = null

const canManage = computed(() => user.value?.role === 'doctor' || user.value?.role === 'agency' || user.value?.role === 'admin')
/** 空狀態是「還沒設定」不是「壞了」；只有鄉鎮沒填這一種，使用者自己就能解決。 */
const canFixEmpty = computed(() => emptyReason.value === 'district_missing')
const busy = computed(() => status.value === 'idle' || status.value === 'loading')
const ready = computed(() => status.value === 'ready')
const alertDistricts = computed(() => new Set((summary.value?.alerts ?? []).map((a) => a.district)).size)
const filters = computed(() => ({
  from: dateFrom.value, to: dateTo.value, source: source.value || undefined,
}))

function colorFor(row: { count: number | null; area_ha: number | null; metric: number | null }): string {
  const legend = summary.value?.legend ?? []
  const value = row.count ?? row.area_ha ?? row.metric
  if (value == null || legend.length < 2) return NO_DATA
  for (let i = 0; i < legend.length - 1; i++) {
    if (value <= legend[i + 1] || i === legend.length - 2) return SCALE[Math.min(i, SCALE.length - 1)]
  }
  return SCALE[SCALE.length - 1]
}

/**
 * 讀 summary 決定狀態，`available` 才繼續抓 districts 與 geo。
 *
 * 不用 `Promise.all` 一次打三支：鄉鎮沒填時那兩支必然回 422，
 * 併發打出去只是製造兩個註定失敗的請求，還會讓錯誤訊息比真正的原因先到。
 */
// 快速切篩選或鄉鎮時，只接受最後一次請求，避免慢回來的舊資料覆蓋新選項。
let loadSequence = 0
let detailSequence = 0
async function loadAll() {
  const mine = ++loadSequence
  const query = { ...filters.value }
  ++detailSequence
  detail.value = null
  detailLoading.value = false
  status.value = 'loading'
  error.value = ''
  try {
    const s = await planning.regionSummary(query)
    if (mine !== loadSequence) return
    summary.value = s
    if (!s.available) {
      emptyReason.value = s.reason || 'county_unsupported'
      emptyMessage.value = s.message || '尚未提供這個區域的地圖資料'
      status.value = 'empty'
      return
    }
    emptyReason.value = ''
    const d = await planning.regionDistricts(query)
    if (mine !== loadSequence) return
    if (!geo.value) {
      const result = await planning.regionGeo()
      if (mine !== loadSequence) return
      geo.value = result
    }
    districts.value = d.districts
    styleLayer()
    status.value = 'ready'
    const district = d.districts.find((row) => row.district === selected.value)?.district ?? d.districts[0]?.district
    if (district) await selectDistrict(district)
  } catch (e) {
    if (mine !== loadSequence) return
    // 只相容舊版「鄉鎮未填」的 422；日期等其他驗證錯誤不能被誤報成未設定。
    if (e instanceof PlanningError && e.status === 422 && e.message.includes('尚未填寫鄉鎮')) {
      emptyReason.value = 'district_missing'
      emptyMessage.value = e.message
      summary.value = null
      status.value = 'empty'
      return
    }
    error.value = e instanceof PlanningError ? e.message : '載入區域地圖失敗，請稍後再試'
    status.value = 'error'
  }
}

async function selectDistrict(district: string) {
  const mine = ++detailSequence
  selected.value = district
  // 重新套色把「選中」畫出來。setStyle 會整組重跑 featureStyle，前一個選中的自然還原，
  // 不用自己記上一個是誰；這也是點鄉鎮唯一的視覺回饋（以前只有瀏覽器的方框 focus ring）。
  geoLayer?.setStyle(featureStyle)
  detail.value = null
  detailLoading.value = true
  try {
    const result = await planning.regionDistrictDetail(district, { ...filters.value })
    if (mine === detailSequence) detail.value = result
  } catch (e) {
    if (mine === detailSequence) toast.error(e instanceof PlanningError ? e.message : '載入鄉鎮資料失敗')
  } finally {
    if (mine === detailSequence) detailLoading.value = false
  }
}

function rowFor(district: string) {
  return districts.value.find((d) => d.district === district)
}

/** 只在第一次有 geo 時建圖層並 fitBounds；之後改篩選只換顏色，使用者的縮放位置要留著。 */
let fitted = false

function featureStyle(feature?: GeoJSON.Feature): L.PathOptions {
  const name = feature?.properties?.district as string | undefined
  const row = name ? rowFor(name) : undefined
  // 選中的鄉鎮用加粗描邊標出來。第四輪回饋「點鄉鎮出現方框」就是因為以前沒有這個樣式：
  // 畫面上唯一的回饋是瀏覽器給 SVG path 的預設 focus ring，而那個 ring 畫的是外接矩形，
  // 看起來像鄉鎮變成一個方塊。現在選中畫的是鄉鎮自己的形狀。
  const chosen = !!name && name === selected.value
  return {
    fillColor: row ? colorFor(row) : NO_DATA,
    fillOpacity: chosen ? 0.9 : 0.8,
    // 邊界要夠明顯：底圖是彩色的 OSM，細淺色線會整片糊掉，看不出是 38 個鄉鎮。
    color: chosen ? '#245C45' : row?.alert ? '#c0392b' : '#5d7268',
    weight: chosen ? 3 : row?.alert ? 3 : 1.2,
  }
}

function styleLayer() {
  if (!map || !geo.value) return
  if (!geoLayer) {
    geoLayer = L.geoJSON(geo.value, {
      style: featureStyle,
      onEachFeature: (feature, layer) => {
        const name = feature.properties?.district as string
        layer.bindTooltip(name)
        layer.on('click', () => selectDistrict(name))
      },
    }).addTo(map)
    // 地圖容器在 Leaflet 初始化當下可能還沒排版完（高度 0），這時算出來的縮放會離譜。
    map.invalidateSize()
    if (!fitted) {
      const bounds = geoLayer.getBounds()
      if (bounds.isValid()) {
        map.fitBounds(bounds, { padding: [12, 12] })
        fitted = true
      } else console.warn('[region] 鄉鎮界沒有有效範圍，維持預設視角')
    }
    return
  }
  // 改篩選走這條：setStyle 重新套色，不 remove() 再 addTo()。
  // 舊版每次都重建圖層，等於每次篩選都閃一下，而且會把使用者拉好的縮放位置蓋掉。
  geoLayer.setStyle(featureStyle)
}

onMounted(async () => {
  // 角色與登入狀態走 session store，不再自己打一次 me()：路由守衛進來前已經備妥。
  if (!user.value) {
    status.value = 'empty'
    emptyReason.value = ''
    return
  }
  await nextTick()
  if (mapEl.value) {
    map = L.map(mapEl.value, { scrollWheelZoom: false }).setView([22.9, 120.5], 9)
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; OpenStreetMap contributors',
      maxZoom: 18,
    }).addTo(map)
  }
  await loadAll()
})

onBeforeUnmount(() => {
  ++loadSequence
  ++detailSequence
  map?.remove()
  map = null
})

watch([source, dateFrom, dateTo], () => {
  // `empty` 跟篩選無關（鄉鎮沒填、縣市沒有界線檔），再打一次只會拿到同一個答案。
  if (status.value === 'empty') return
  detail.value = null
  selected.value = null
  void loadAll()
})

// ---- 主動調查登錄（植物醫生／機關） ----
const showSurveyForm = ref(false)
const surveySaving = ref(false)
const surveyForm = ref<SurveyInput>({
  district: '', surveyed_on: todayStr(), crop_text: '', pest_text: '', trap_count: 0, traps: 1, note: '',
})

function openSurveyForm() {
  surveyForm.value = { district: selected.value ?? '', surveyed_on: todayStr(), crop_text: '', pest_text: '', trap_count: 0, traps: 1, note: '' }
  showSurveyForm.value = true
}

async function submitSurvey() {
  surveySaving.value = true
  try {
    await planning.createSurvey(surveyForm.value)
    toast.ok('已登錄主動調查')
    showSurveyForm.value = false
    await loadAll()
  } catch (e) {
    toast.error(e instanceof PlanningError ? e.message : '登錄失敗，請稍後再試')
  } finally {
    surveySaving.value = false
  }
}

const reaggregating = ref(false)
async function triggerReaggregate() {
  reaggregating.value = true
  try {
    const result = await planning.reaggregateRegion()
    // 清掉的列要講出來：那代表某些通報的來源日誌被撤回或改了日期，地圖上的數字會跟著變少。
    toast.ok(result.removed
      ? `已重新彙整，共 ${result.rows} 筆；另清掉 ${result.removed} 筆來源已不在的舊資料`
      : `已重新彙整，共 ${result.rows} 筆`)
    await loadAll()
  } catch (e) {
    toast.error(e instanceof PlanningError ? e.message : '重新彙整失敗')
  } finally {
    reaggregating.value = false
  }
}

const exporting = ref(false)
async function exportXlsx() {
  exporting.value = true
  try {
    const blob = await planning.regionExport(filters.value)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `區域彙整報表_${summary.value?.county ?? ''}.xlsx`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    toast.error(e instanceof PlanningError ? e.message : '匯出失敗')
  } finally {
    exporting.value = false
  }
}

// ---- 農民：同意匿名彙整 ----
const togglingShare = ref(false)
async function toggleShareRegion() {
  if (!user.value) return
  togglingShare.value = true
  try {
    const updated = await planning.toggleShareRegion(!user.value.share_region)
    updateMe(updated)
    toast.ok(updated.share_region ? '已同意匿名彙整供區域統計' : '已取消同意匿名彙整')
  } catch (e) {
    toast.error(e instanceof PlanningError ? e.message : '設定失敗，請稍後再試')
  } finally {
    togglingShare.value = false
  }
}
</script>

<template>
  <div class="region-view">
    <div v-if="!user" class="hint card">
      <p>請先<router-link to="/farm">登入農場工作區</router-link>才能查看區域地圖。</p>
    </div>
    <template v-else>
      <div class="ph">
        <div>
          <h1>區域作物與病蟲害地圖 · {{ summary?.county ?? '高雄市' }}</h1>
          <p>
            把診療案件、農事日誌巡田通報、作物規劃與主動調查彙總到鄉鎮層級，不顯示個別農場。
            <template v-if="user.role === 'farmer'">目前只看得到您自己所在的鄉鎮。</template>
          </p>
        </div>
        <div v-if="user.role === 'farmer'" class="share-box">
          <label class="share-toggle" :class="{ disabled: togglingShare }">
            同意匿名彙整供區域統計
            <input type="checkbox" role="switch" :checked="!!user.share_region" :disabled="togglingShare" @change="toggleShareRegion" />
          </label>
          <!-- 撤回的效果要先講清楚：巡田通報與診斷案件會回溯消失，但已經算進鄉鎮的種植面積是匯總值，
               拆不回單一農場、也就不可能回溯移除。不寫的話，之後有人撤回卻看到自己那區面積沒變，
               會覺得系統沒守承諾。 -->
          <p class="share-note">
            撤回後不再計入之後的統計；已彙整的鄉鎮種植面積是匯總值，不會回溯移除。
          </p>
        </div>
      </div>

      <div class="fbar">
        <label class="input">從 <input type="date" v-model="dateFrom" /></label>
        <label class="input">到 <input type="date" v-model="dateTo" /></label>
        <div class="chips">
          <button
            v-for="s in SOURCES" :key="s.key" type="button"
            class="chip" :class="{ on: source === s.key }" @click="source = s.key"
          >{{ s.label }}</button>
        </div>
        <div class="actions">
          <button v-if="canManage" class="btn sm" type="button" :disabled="reaggregating" @click="triggerReaggregate">
            {{ reaggregating ? '彙整中…' : '重新彙整' }}
          </button>
          <button v-if="canManage" class="btn sm" type="button" :disabled="exporting" @click="exportXlsx">
            {{ exporting ? '匯出中…' : '匯出區域報表' }}
          </button>
        </div>
      </div>

        <div class="kpis">
          <div class="kpi">
            <div class="k">{{ source ? SOURCES.find((s) => s.key === source)?.label : '通報件數' }}</div>
            <!-- 載入中用骨架撐同樣高度：KPI 一縮，下面的篩選列與地圖就會跳。 -->
            <div v-if="busy" class="sk"></div>
            <div v-else class="v">
              <template v-if="ready && summary?.kpi.sample_insufficient">樣本不足</template>
              <template v-else-if="ready && summary?.kpi.count != null">{{ summary.kpi.count }}<small>件</small></template>
              <template v-else>—</template>
            </div>
          </div>
          <div class="kpi">
            <div class="k">達警戒鄉鎮</div>
            <div v-if="busy" class="sk"></div>
            <div v-else class="v" :style="{ color: alertDistricts > 0 ? '#c0392b' : undefined }">
              {{ ready ? alertDistricts : '—' }}
            </div>
          </div>
          <div class="kpi">
            <div class="k">種植面積</div>
            <div v-if="busy" class="sk"></div>
            <div v-else class="v">
              <template v-if="ready && summary?.kpi.area_ha != null">{{ summary.kpi.area_ha }}<small>ha</small></template>
              <template v-else>—</template>
            </div>
          </div>
          <div class="kpi">
            <div class="k">TOP 作物</div>
            <div v-if="busy" class="sk"></div>
            <div v-else class="v" style="font-size: 17px">{{ (ready && summary?.top_crops[0]?.crop_text) || '—' }}</div>
          </div>
        </div>

        <div class="layout">
          <section class="card">
            <div class="card-h">
              <span class="title">鄉鎮發生分布</span>
              <span class="small muted">紅框＝達警戒；點鄉鎮看詳細</span>
            </div>
            <!-- 地圖容器永遠 render。Leaflet 綁的是這個 node，被 v-if 拿掉就再也畫不出來了
                 （這正是「區域地圖一直閃」的成因），所以狀態一律用上面那層遮罩表示。 -->
            <div class="mapbox">
              <div ref="mapEl" class="map"></div>
              <div v-if="status !== 'ready'" class="ov">
                <span v-if="busy" class="small muted">載入鄉鎮資料…</span>
                <div v-else-if="status === 'empty'" class="ov-card">
                  <b>{{ canFixEmpty ? '還不知道您的田在哪個鄉鎮' : '這個區域還沒有地圖資料' }}</b>
                  <p>{{ emptyMessage }}</p>
                  <router-link v-if="canFixEmpty" class="btn sm primary" to="/farm?settings=location&return=region">
                    到農場設定填寫鄉鎮
                  </router-link>
                </div>
                <div v-else-if="status === 'error'" class="ov-card">
                  <b>載入區域地圖時發生問題</b>
                  <p>{{ error }}</p>
                  <button class="btn sm" type="button" @click="loadAll">重試</button>
                </div>
              </div>
            </div>
            <div class="legend">
              <template v-if="summary?.legend.length">
                <span>{{ summary.legend[0] }}</span>
                <i v-for="(c, i) in SCALE" :key="i" :style="{ background: c }"></i>
                <span>{{ summary.legend[summary.legend.length - 1] }}+</span>
              </template>
              <span v-else class="muted">目前沒有可分級的資料</span>
            </div>
          </section>

          <aside class="side">
            <section class="card" v-if="ready && detail">
              <div class="card-h">
                <span class="title">{{ detail.district }}</span>
                <span class="small muted">已選鄉鎮</span>
              </div>
              <div class="card-b">
                <p v-if="detailLoading" class="muted small">載入中…</p>
                <template v-else>
                  <div v-if="!detail.items.length" class="muted small">這段期間沒有資料。</div>
                  <div v-for="(item, i) in detail.items.slice(0, 8)" :key="i" class="pl">
                    <span>{{ item.crop_text }}<template v-if="item.pest_text"> · {{ item.pest_text }}</template></span>
                    <b v-if="item.sample_insufficient" class="muted">樣本不足</b>
                    <b v-else-if="item.count != null">{{ item.count }} 件</b>
                    <b v-else-if="item.area_ha != null">{{ item.area_ha }} ha</b>
                    <b v-else-if="item.metric != null">平均 {{ item.metric }}</b>
                  </div>
                  <div v-for="a in detail.alerts" :key="a.pest_text" class="alert">
                    <b>{{ a.pest_text }}</b> 已達警戒（{{ a.value }} ≥ {{ a.warn_value }}，{{ a.metric }}）
                  </div>
                  <template v-if="detail.linked_farms?.length">
                    <router-link
                      v-for="f in detail.linked_farms" :key="f.id"
                      class="btn sm" style="margin-top: 6px" :to="`/farmers/${f.id}`"
                    >{{ f.farm_name }}（{{ f.name }}）→ 開單</router-link>
                  </template>
                  <button v-if="canManage" class="btn sm" type="button" style="margin-top: 10px" @click="openSurveyForm">
                    登錄主動調查
                  </button>
                </template>
              </div>
            </section>

            <section class="card" v-if="ready && summary?.top_crops.length">
              <div class="card-h"><span class="title">TOP 作物</span></div>
              <div class="card-b bars">
                <div v-for="c in summary.top_crops" :key="c.crop_text" class="br">
                  <span>{{ c.crop_text }}</span>
                  <i class="b" :style="{ width: `${(c.count / summary.top_crops[0].count) * 100}%` }"></i>
                  <span class="n">{{ c.count }}</span>
                </div>
              </div>
            </section>

            <p class="xs muted" style="margin: 0 4px">
              資料來源：本平台診療案件（診斷結果）、農事日誌「巡田觀察」通報、作物規劃（種植面積）、主動調查（人工登錄）。
              匯總到鄉鎮，農場需在此頁同意「同意匿名彙整供區域統計」才會被算進去；鄉鎮內貢獻農場數不足 3 戶時顯示「樣本不足」。
            </p>
          </aside>
        </div>

      <div v-if="showSurveyForm" class="modal-backdrop" @click.self="showSurveyForm = false">
        <form class="card modal" @submit.prevent="submitSurvey">
          <div class="card-h"><span class="title">登錄主動調查</span></div>
          <div class="card-b form-grid">
            <label>鄉鎮 <input v-model="surveyForm.district" required maxlength="20" /></label>
            <label>調查日期 <input type="date" v-model="surveyForm.surveyed_on" required /></label>
            <label>作物 <input v-model="surveyForm.crop_text" required maxlength="80" /></label>
            <label>病蟲害 <input v-model="surveyForm.pest_text" required maxlength="160" /></label>
            <label>每板隻數 <input type="number" v-model.number="surveyForm.trap_count" min="0" step="0.1" required /></label>
            <label>黏板數 <input type="number" v-model.number="surveyForm.traps" min="1" /></label>
            <label class="full">備註 <textarea v-model="surveyForm.note" maxlength="500"></textarea></label>
          </div>
          <div class="card-b" style="display: flex; gap: 10px; justify-content: flex-end">
            <button type="button" class="btn sm" @click="showSurveyForm = false">取消</button>
            <button type="submit" class="btn sm primary" :disabled="surveySaving">{{ surveySaving ? '儲存中…' : '儲存' }}</button>
          </div>
        </form>
      </div>
    </template>
  </div>
</template>

<style scoped>
.region-view { max-width: 1376px; margin: 0 auto; }
.hint { color: #617066; padding: 24px; }
.card { background: #fff; border: 1px solid #e2e7e1; border-radius: 14px; box-shadow: 0 1px 2px rgba(20, 30, 25, .04), 0 6px 16px rgba(20, 30, 25, .05); }
.card-h { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 16px 20px; border-bottom: 1px solid #e2e7e1; }
.card-b { padding: 18px 20px; }
.title { font-size: 15px; font-weight: 600; }
.muted { color: #7b877f; }
.small { font-size: 13px; }
.xs { font-size: 12px; }
.error { color: #c0392b; }

.ph { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 14px; flex-wrap: wrap; }
.ph h1 { font-size: 24px; font-weight: 700; margin: 0; }
.ph p { color: #7b877f; margin: 4px 0 0; font-size: 14px; }

.share-box { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.share-toggle { display: flex; align-items: center; gap: 8px; font-size: 13px; color: #44514a; white-space: nowrap; }
.share-toggle.disabled { opacity: .6; }
.share-note { margin: 0; font-size: 12px; color: #7b877f; text-align: right; max-width: 34ch; line-height: 1.5; }

.fbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.fbar .input { display: flex; align-items: center; gap: 6px; height: 36px; padding: 0 10px; border: 1px solid #cfd6ce; border-radius: 10px; font-size: 13px; }
.fbar .input input { border: none; font-size: 13px; }
.chips { display: flex; gap: 8px; flex-wrap: wrap; }
.chip { height: 34px; padding: 0 14px; border-radius: 999px; border: 1px solid #cfd6ce; background: #fff; color: #44514a; font-size: 14px; font-weight: 500; cursor: pointer; }
.chip.on { background: #1b2620; border-color: #1b2620; color: #fff; }
.actions { margin-left: auto; display: flex; gap: 8px; }

.btn { display: inline-flex; align-items: center; justify-content: center; gap: 8px; height: 40px; padding: 0 16px; border-radius: 10px; border: 1px solid #cfd6ce; background: #fff; color: #1b2620; font-weight: 500; font-size: 14px; cursor: pointer; white-space: nowrap; }
.btn.primary { background: #245c45; border-color: #245c45; color: #fff; }
.btn.sm { height: 32px; padding: 0 12px; font-size: 13px; }
.btn:disabled { opacity: .6; cursor: default; }

.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-bottom: 16px; }
.kpi { padding: 12px 16px; background: #fff; border: 1px solid #e2e7e1; border-radius: 12px; }
.kpi .k { font-size: 12px; color: #7b877f; }
.kpi .v { font-size: 24px; font-weight: 700; line-height: 1.2; }
.kpi .v small { font-size: 12px; font-weight: 500; color: #7b877f; margin-left: 3px; }

.layout { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 18px; align-items: start; }
.mapbox { position: relative; margin: 16px 20px; border-radius: 12px; overflow: hidden; }
/* z-index:0 讓 Leaflet 內部那堆 pane（400–800）關在自己的堆疊脈絡裡，
   不然縮放鈕與 attribution（z-index 800）會浮在遮罩上面。 */
.map { height: 520px; position: relative; z-index: 0; }
/* 遮罩蓋在地圖上，不取代它：底下的 Leaflet 容器一直活著，狀態變化不會讓圖重建。 */
/* Leaflet 會讓每個鄉鎮的 path 可被 focus，瀏覽器預設畫的 ring 是外接矩形——
   那正是回饋裡的「方框」。滑鼠點選的回饋改用上面的加粗描邊，
   鍵盤仍然要看得見焦點，所以只關掉 :focus、保留 :focus-visible。 */
.map :deep(.leaflet-interactive:focus) { outline: none; }
.map :deep(.leaflet-interactive:focus-visible) {
  outline: none;
  stroke: #245c45;
  stroke-width: 4;
  stroke-dasharray: 5 3;
}
.ov { position: absolute; inset: 0; z-index: 1; display: flex; align-items: center; justify-content: center; background: rgba(244, 246, 243, .82); }
.ov-card { padding: 16px 20px; text-align: center; max-width: 280px; }
.ov-card b { display: block; font-size: 15px; margin-bottom: 4px; }
.ov-card p { font-size: 13px; color: #44514a; margin: 0 0 10px; line-height: 1.6; }
.sk { height: 29px; margin-top: 2px; border-radius: 6px; background: linear-gradient(90deg, #eef1ec 25%, #f6f8f4 50%, #eef1ec 75%); background-size: 200% 100%; animation: sk 1.2s linear infinite; }
@keyframes sk { from { background-position: 200% 0; } to { background-position: -200% 0; } }
.legend { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #7b877f; padding: 0 20px 14px; }
.legend i { display: inline-block; width: 22px; height: 12px; border-radius: 3px; }

.side { display: flex; flex-direction: column; gap: 14px; }
.pl { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 0; border-bottom: 1px solid #e2e7e1; font-size: 14px; }
.pl:last-child { border-bottom: none; }
.alert { display: flex; gap: 8px; padding: 10px 12px; border-radius: 10px; background: #fbebe9; color: #7a2119; font-size: 13px; margin-top: 8px; }
.bars { display: flex; flex-direction: column; gap: 8px; }
.br { display: grid; grid-template-columns: 70px 1fr 40px; gap: 8px; align-items: center; font-size: 13px; }
.br .b { height: 14px; border-radius: 4px; background: #245c45; display: block; }
.br .n { text-align: right; color: #44514a; }

.modal-backdrop { position: fixed; inset: 0; background: rgba(20, 30, 25, .35); display: flex; align-items: center; justify-content: center; z-index: 50; }
.modal { width: 420px; max-width: 92vw; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px 14px; }
.form-grid label { display: flex; flex-direction: column; gap: 4px; font-size: 13px; color: #44514a; }
.form-grid label.full { grid-column: 1 / -1; }
.form-grid input, .form-grid textarea { border: 1px solid #cfd6ce; border-radius: 8px; padding: 6px 10px; font-size: 14px; font-family: inherit; }

@media (max-width: 960px) {
  .layout { grid-template-columns: 1fr; }
  .kpis { grid-template-columns: repeat(2, 1fr); }
}
</style>
