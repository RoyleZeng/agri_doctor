<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { planning, type Farmer, type Plan } from '@/api/planning'
import { stageOf, today } from '@/composables/farmDates'
import { toast } from '@/composables/toast'
const props = defineProps<{ farmer: Farmer; plans: Plan[]; owner: boolean; demo: boolean; compact?: boolean }>()
const emit = defineEmits<{ saved: [Farmer] }>()
interface Forecast {
  status: string; county: string; fetched_at: string | null; source_url: string; resolution: string; message: string
  periods: { start_at: string; end_at: string; description: string | null; rain_probability: number | null; min_temp: number | null; max_temp: number | null }[]
  advice: { key: string; title: string; text: string; basis: string; periods: string[] }[]
}
const counties = ref<string[]>([])
const county = ref('')
const data = ref<Forecast | null>(null)
const error = ref('')
const loading = ref(false)
const saving = ref(false)
const current = ref(Date.now())
let seq = 0
let timer: ReturnType<typeof setInterval> | undefined
const activePlans = computed(() => props.plans.filter(p => !p.archived && p.planted_on <= today() && p.end_on >= today()))
const valid = computed(() => data.value?.status === 'ok' && data.value.periods.some(p => new Date(p.end_at).getTime() > current.value))
const currentAdvice = computed(() => {
  const starts = new Set((data.value?.periods || []).filter(p => new Date(p.end_at).getTime() > current.value).map(p => p.start_at))
  return (data.value?.advice || []).filter(a => a.periods.some(start => starts.has(start)))
})
// 複合圖 v3 把天氣縮成一列：預報不是每天要讀的東西，佔掉第一屏會把複合圖擠下去。
const expanded = ref(!props.compact)
const nextPeriod = computed(() => (data.value?.periods || []).find(p => new Date(p.end_at).getTime() > current.value) || null)
const summary = computed(() => {
  if (loading.value) return '正在取得預報…'
  if (error.value) return error.value
  if (!county.value) return '尚未選擇農場縣市'
  if (!valid.value) return data.value?.status === 'not_configured' ? '氣象資料尚未連接' : '目前沒有有效預報'
  const p = nextPeriod.value
  if (!p) return '目前沒有有效預報'
  const rain = p.rain_probability === null ? '降雨機率未提供' : `降雨 ${p.rain_probability}%`
  return `${p.description || '天氣現象未提供'} · ${p.min_temp ?? '—'}–${p.max_temp ?? '—'}°C · ${rain}`
})
const when = (v: string) => new Intl.DateTimeFormat('zh-TW', { timeZone:'Asia/Taipei', month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit', hour12:false }).format(new Date(v))
async function load() {
  const token = ++seq
  data.value = null; error.value = ''
  if (!county.value) { loading.value = false; return }
  loading.value = true
  try {
    const r = await fetch(`/api/v1/weather/forecast?county=${encodeURIComponent(county.value)}`, {cache:'no-store'})
    if (!r.ok) throw new Error('暫時無法讀取預報，請稍後重試。')
    const body = await r.json() as Forecast
    if (token === seq) { data.value = body; current.value = Date.now() }
  } catch (e) { if (token === seq) error.value = (e as Error).message }
  finally { if (token === seq) loading.value = false }
}
function initCounty() {
  const matches = counties.value.filter(c => props.farmer.location.replaceAll('台','臺').includes(c))
  county.value = props.farmer.weather_county || (matches.length === 1 ? matches[0]! : '') || (props.demo ? '臺南市' : '')
}
async function save() {
  saving.value = true
  try { const farmer = await planning.weatherCounty(county.value, props.farmer.id); emit('saved', farmer); toast.ok('已保存農場天氣縣市') }
  catch (e) { toast.error((e as Error).message) }
  finally { saving.value = false }
}
onMounted(async () => {
  try { const r = await fetch('/api/v1/weather/counties'); if (!r.ok) throw new Error(); counties.value = await r.json(); initCounty() }
  catch { error.value = '無法讀取縣市清單，請稍後重試。' }
  timer = setInterval(() => { current.value = Date.now(); if (data.value?.status === 'ok' && !valid.value) void load() }, 60000)
})
onUnmounted(() => { ++seq; clearInterval(timer) })
watch(county, load)
watch(() => props.farmer.id, initCounty)
</script>
<template>
  <section class="card farm-weather" :class="{ compact }" aria-label="天氣與田間照護">
    <button v-if="compact" class="weather-row" :aria-expanded="expanded" @click="expanded = !expanded">
      <span class="wx-label">天氣</span><strong>{{ county || '未設定縣市' }}</strong><span class="wx-sum">{{ summary }}</span>
      <span v-if="valid && currentAdvice.length" class="tag warn">{{ currentAdvice.length }} 則照護提醒</span>
      <span class="wx-more">{{ expanded ? '收合' : '展開 36 小時預報' }}</span>
    </button>
    <template v-if="!compact || expanded">
    <div class="weather-head"><div><span class="eyebrow">照顧作物，也看天氣</span><h2>天氣與田間照護</h2><p>中央氣象署 · 縣市今明36小時預報</p></div><div class="weather-controls">
      <label for="weather-county">查看縣市</label><select id="weather-county" v-model="county"><option value="">請選擇農場縣市</option><option v-for="c in counties" :key="c">{{ c }}</option></select>
      <button class="btn sm" :disabled="!county || loading" @click="load">更新預報</button>
      <button v-if="owner && county && county !== farmer.weather_county" class="btn sm" :disabled="saving" @click="save">設為農場天氣縣市</button>
    </div></div>
    <p class="weather-scope">{{ demo ? '示範農場預設查看臺南市，可切換縣市。' : '跨縣市田區可切換查看；縣市預報不代表每塊田的微氣候。' }} {{ !demo && !farmer.weather_county && county ? '目前依農場地區推定，尚未保存設定。' : '' }}</p>
    <div v-if="loading" class="weather-state" role="status">正在取得氣象署預報…</div>
    <div v-else-if="error" class="weather-state" role="alert">{{ error }}</div>
    <div v-else-if="!county" class="weather-state">先選擇農場所在縣市，查看短期天氣與照護提醒。</div>
    <template v-else-if="data">
      <div v-if="!valid" class="weather-state" role="status"><strong>{{ data.status === 'not_configured' ? '氣象資料尚未連接' : '目前沒有有效預報' }}</strong><p>{{ data.message }}</p></div>
      <template v-else>
        <div class="forecast-grid"><article v-for="p in data.periods.filter(p => new Date(p.end_at).getTime() > current)" :key="p.start_at"><time>{{ when(p.start_at) }} — {{ when(p.end_at) }}</time><h3>{{ p.description || '天氣現象未提供' }}</h3><strong>{{ p.min_temp ?? '—' }}–{{ p.max_temp ?? '—' }}<small>°C</small></strong><p>降雨機率 <b>{{ p.rain_probability === null ? '未提供' : `${p.rain_probability}%` }}</b></p></article></div>
        <div class="care-cues"><h3>照護提醒 <span>平台規則 · 非氣象署農務指令</span></h3><article v-for="a in currentAdvice" :key="a.key"><strong>{{ a.title }}</strong><p>{{ a.text }}</p><small>{{ a.basis }}</small></article><p v-if="!currentAdvice.length">目前有效且有提供數值的預報，未達平台的降雨、高溫或低溫提醒門檻；這不代表適合施藥或不需巡田。</p>
          <div v-if="activePlans.length" class="weather-crops"><span v-for="p in activePlans" :key="p.id">{{ p.crop_name }} · {{ p.field_name }} · {{ stageOf(p) }}</span><p>以上是已選作物的目前規劃階段，請依苗株、土壤及現地情況判讀；不自動變更農事日期。</p></div>
        </div>
      </template>
      <div class="weather-foot"><span v-if="data.fetched_at">本站取得 {{ when(data.fetched_at) }}（非官方發布時間）</span><a :href="data.source_url" target="_blank" rel="noopener">中央氣象署資料來源 ↗</a></div>
      <p v-if="valid" class="weather-scope">{{ data.message }}</p>
    </template>
    </template>
  </section>
</template>
<style scoped>
.farm-weather{margin:22px 0;overflow:hidden}.farm-weather.compact{margin:0}.weather-row{display:flex;align-items:center;gap:10px;width:100%;padding:13px 18px;background:none;border:0;cursor:pointer;text-align:left;font-size:13px;color:var(--ink)}.weather-row .wx-label{font-size:12px;color:var(--brand);font-weight:600}.weather-row .wx-sum{color:var(--ink-2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.weather-row .wx-more{margin-left:auto;font-size:12px;color:var(--brand);white-space:nowrap}.farm-weather.compact .weather-head{border-top:1px solid var(--line)}.weather-head{display:flex;justify-content:space-between;gap:20px;padding:22px 24px 12px;align-items:center}.eyebrow{font-size:12px;color:var(--brand);font-weight:600}.weather-head h2{font-size:20px;margin:4px 0}.weather-head p,.weather-scope{font-size:12px;color:var(--ink-2)}.weather-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap;font-size:13px}.weather-controls select{padding:9px;border:1px solid var(--line);border-radius:8px;background:white}.weather-scope{padding:0 24px 14px;line-height:1.7}.weather-state{margin:0 24px 22px;padding:18px;background:#f4f6ef;border-radius:10px;font-size:14px}.weather-state p{margin-top:6px;line-height:1.7}.forecast-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:0 24px 20px}.forecast-grid article{background:#f3f7f1;border:1px solid #e2e9de;border-radius:10px;padding:16px}.forecast-grid time{font-size:11px;color:var(--ink-2)}.forecast-grid h3{font-size:14px;margin:10px 0}.forecast-grid strong{font-size:28px;color:var(--brand)}.forecast-grid small{font-size:13px}.forecast-grid p{margin-top:9px;font-size:13px}.care-cues{padding:18px 24px;background:#fcfcf8;border-top:1px solid var(--line)}.care-cues h3{font-size:15px;margin-bottom:12px}.care-cues h3 span{font-size:11px;font-weight:400;color:var(--ink-2);margin-left:8px}.care-cues article{margin-bottom:14px;font-size:14px}.care-cues p{font-size:13px;line-height:1.7}.care-cues small{font-size:11px;color:var(--ink-2)}.weather-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;padding:14px 24px;font-size:11px;color:var(--ink-2)}.weather-crops{border-top:1px solid var(--line);padding-top:12px;margin-top:14px}.weather-crops span{display:inline-block;background:#edf2e8;padding:5px 9px;border-radius:6px;font-size:12px;margin:0 8px 8px 0}.weather-crops p{color:var(--ink-2);font-size:12px}@media(max-width:760px){.weather-row{flex-wrap:wrap}.weather-row .wx-more{margin-left:0}.weather-head{align-items:flex-start;flex-direction:column;padding:18px}.weather-controls{width:100%}.forecast-grid{grid-template-columns:1fr;padding:0 18px 18px}.weather-state{margin:0 18px 18px}.weather-scope{padding:0 18px 12px}.care-cues{padding:18px}.weather-foot{padding:14px 18px}}
.weather-row .wx-label, .weather-row > strong { white-space: nowrap; flex-shrink: 0; }
</style>
