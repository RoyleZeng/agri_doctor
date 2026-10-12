<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { economics, type MarketData, type CostData, type SourceMeta, type Quote } from '@/api/economics'
import Icon from './Icon.vue'

const props = defineProps<{ crops: string[]; farmerId: string }>()
const opened = ref(false)
const tab = ref<'market' | 'cost'>('market')
const query = ref(props.crops[0] || '')
const market = ref<MarketData | null>(null)
const costs = ref<CostData | null>(null)
const loading = ref(false)
const costLoading = ref(false)
const error = ref('')
const costError = ref('')
const product = ref('')
const marketCode = ref('')
const costQuery = ref(query.value)
const year = ref('')
const kind = ref('')
const costLimit = ref(8)
let sequence = 0
let costSequence = 0

const money = (n: number | null | undefined) => n == null ? '未載明' : n.toLocaleString('zh-TW', { maximumFractionDigits: 1 })
// 官方品項代碼仍依交易類別分開，避免跨類別同碼被混成一條走勢。
const productKey = (r: Quote) => `${r.category}:${r.crop_code}`
const products = computed(() => [...new Map((market.value?.rows || []).map(r => [productKey(r), `${r.crop}（${r.crop_code}）`])).entries()])
const markets = computed(() => [...new Map((market.value?.rows || []).filter(r => productKey(r) === product.value).map(r => [r.market_code, r.market])).entries()])
const series = computed(() => (market.value?.rows || []).filter(r => productKey(r) === product.value && r.market_code === marketCode.value).sort((a,b) => a.date.localeCompare(b.date)))
const latest = computed(() => series.value.at(-1))
const maxPrice = computed(() => Math.max(1, ...series.value.map(r => r.average || 0)))
const source = computed(() => tab.value === 'market' ? market.value : costs.value)
const busy = computed(() => tab.value === 'market' ? loading.value : costLoading.value)
const years = computed(() => [...new Set((costs.value?.documents || []).map(d => d.year).filter((y): y is number => y !== null))].sort((a,b)=>b-a))
const kinds = computed(() => [...new Set((costs.value?.documents || []).map(d => d.kind))])
const filtered = computed(() => (costs.value?.documents || []).filter(d =>
  (!costQuery.value.trim() || d.title.includes(costQuery.value.trim().replace('高麗菜', '甘藍').replace('蕃茄', '番茄').replace('西紅柿', '番茄')))
  && (!year.value || String(d.year) === year.value) && (!kind.value || d.kind === kind.value)))
const documents = computed(() => filtered.value.slice(0, costLimit.value))
const closed = computed(() => (market.value?.closed || []).filter(r => r.date === market.value?.to
  && (!marketCode.value || r.market_code === marketCode.value)
  && (!latest.value || r.category === latest.value.category)))

function statusText(data: SourceMeta | null) {
  if (!data) return ''
  if (data.stale) return '尚未完成更新，顯示本地舊快照；請核對取得時間與資料期間。'
  if (data.status === 'not_synced') return '尚未收錄這個查詢的完整本地資料，已排入背景同步範圍；這不代表沒有行情。'
  if (data.status === 'rate_limited') return '官方查詢額度已用完，請稍後再試；管理者可設定會員金鑰。'
  if (data.status === 'key_required') return '官方要求授權，請管理者設定 MOA_API_KEY。'
  if (data.status === 'partial' || data.status === 'pagination_error') return '資料分頁尚未完整取得，目前不顯示部分資料為完整行情。'
  if (data.status === 'schema_drift') return '官方資料格式或筆數有異常，暫停發布新資料。'
  return data.status === 'ok' ? '' : '暫時無法取得官方資料；這不代表沒有行情或文件。'
}
async function loadMarket() {
  const value = query.value.trim()
  if (!value) return
  const mine = ++sequence
  loading.value = true; error.value = ''; market.value = null
  product.value = ''; marketCode.value = ''
  try {
    const data = await economics.market(value)
    if (mine === sequence) market.value = data
  } catch(e) {
    if (mine === sequence) error.value = (e as Error).message
  } finally { if (mine === sequence) loading.value = false }
}
async function loadCosts() {
  const mine = ++costSequence
  costLoading.value = true; costError.value = ''
  try { const data = await economics.costs(); if (mine === costSequence) costs.value = data }
  catch(e) { if (mine === costSequence) costError.value = (e as Error).message }
  finally { if (mine === costSequence) costLoading.value = false }
}
function toggle() {
  opened.value = !opened.value
  if (!opened.value) return
  if (tab.value === 'market' && !market.value && !loading.value) void loadMarket()
  if (tab.value === 'cost' && !costs.value && !costLoading.value) void loadCosts()
}
function chooseCrop(crop: string) {
  query.value = crop; costQuery.value = crop
  if (opened.value && tab.value === 'market') void loadMarket()
  else {
    // 成本目錄已在本機，切作物只篩文件；回行情分頁時才耗用官方查詢額度。
    ++sequence; market.value = null; loading.value = false
  }
}
watch(tab, value => {
  if (value === 'cost' && !costs.value && !costLoading.value) void loadCosts()
  if (value === 'market' && !market.value && !loading.value) void loadMarket()
})
watch(product, () => { marketCode.value = '' })
watch([costQuery, year, kind], () => { costLimit.value = 8 })
watch(() => props.crops.join("\u0000"), () => {
  const crops = props.crops
  if (!crops.includes(query.value)) chooseCrop(crops[0] || '')
})
watch(() => props.farmerId, () => { ++sequence; market.value = null; opened.value = false; loading.value = false })
onBeforeUnmount(() => { ++sequence; ++costSequence })
</script>

<template>
  <section class="card farm-economics no-print">
    <button class="econ-toggle" :aria-expanded="opened" @click="toggle">
      <span class="econ-icon"><Icon name="basket" :size="20" /></span>
      <span><strong>市場行情與生產成本</strong><small>看成交行情、查年度成本文件，協助安排這一期收成</small></span>
      <span class="econ-open">{{ opened ? '收合' : '查看資料' }} <Icon name="right" :size="14" /></span>
    </button>
    <div v-if="opened" class="econ-body">
      <div class="econ-tabs" role="tablist" aria-label="農場經營參考">
        <button role="tab" :aria-selected="tab === 'market'" :class="{on: tab === 'market'}" @click="tab = 'market'">市場行情</button>
        <button role="tab" :aria-selected="tab === 'cost'" :class="{on: tab === 'cost'}" @click="tab = 'cost'">生產成本文件</button>
      </div>
      <div class="econ-crops"><span>已選作物</span><button v-for="crop in [...new Set(crops)]" :key="crop" class="chip" :class="{on: query === crop}" @click="chooseCrop(crop)">{{ crop }}</button></div>
      <p v-if="statusText(source)" class="econ-alert" role="status">{{ statusText(source) }}</p>
      <template v-if="tab === 'market'">
        <form class="econ-filters" @submit.prevent="loadMarket">
          <label>作物搜尋<input v-model="query" maxlength="60" placeholder="例如：番茄、高麗菜" /></label>
          <button class="btn sm primary" :disabled="!query.trim() || loading">{{ loading ? '查詢中…' : '查詢近 7 天' }}</button>
        </form>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <div v-if="loading" class="econ-empty" role="status">正在取得官方成交行情，農場複合圖仍可操作…</div>
        <template v-else-if="market">
          <p class="econ-notice">{{ market.notice }} 資料期間：{{ market.from }} 至 {{ market.to }}。</p>
          <div v-if="products.length" class="econ-filters">
            <label>市場品項<select v-model="product"><option value="">請選擇品項，不合併不同品種</option><option v-for="[code,name] in products" :key="code" :value="code">{{ name }}</option></select></label>
            <label>交易市場<select v-model="marketCode" :disabled="!product"><option value="">請選擇市場</option><option v-for="[code,name] in markets" :key="code" :value="code">{{ name }}</option></select></label>
          </div>
          <p v-if="closed.length" class="econ-alert">{{ market.to }} 有{{ marketCode ? '此市場' : '部分市場' }}休市紀錄，休市不當成 0 元。</p>
          <div v-if="latest" class="econ-result">
            <div class="quote">
              <span>{{ latest.crop }} · {{ latest.market }}</span>
              <p><b>{{ money(latest.average) }}</b><small>元／公斤</small></p>
              <span>{{ latest.date }} 最近一筆紀錄 · 交易量 {{ money(latest.volume_kg) }} 公斤</span>
              <div class="quote-range">上價 {{ money(latest.upper) }} ／ 中價 {{ money(latest.middle) }} ／ 下價 {{ money(latest.lower) }}</div>
            </div>
            <div class="econ-trend" aria-label="近七天每日成交均價">
              <div v-for="row in series" :key="row.date + row.category" class="trend-row">
                <time>{{ row.date.slice(5) }}</time><div class="trend-track"><i :style="{width: `${(row.average ?? 0) / maxPrice * 100}%`}" /></div><b>{{ money(row.average) }}</b>
              </div>
              <small>只列官方有回傳的日期；缺日不補零。</small>
            </div>
          </div>
          <p v-else-if="products.length" class="econ-empty">選擇明確的品項與交易市場後，查看成交價格與走勢。</p>
          <p v-else-if="market.status === 'ok'" class="econ-empty">此搜尋詞近 7 天未取得成交品項。可調整名稱或到官方查詢；不代表作物沒有市場價值。</p>
          <p v-if="market.ignored_rows" class="econ-alert">有 {{ market.ignored_rows }} 筆日期或代碼無法辨識，未納入顯示。</p>
        </template>
      </template>
      <template v-else>
        <p class="econ-notice">這裡查的是官方年度文件。調查表、填表範例與成本相關文件分開標示，不能直接當成本田區每公頃成本。</p>
        <div class="econ-filters">
          <label>成本文件搜尋<input v-model="costQuery" maxlength="60" placeholder="作物或文件名稱；留空查看全部" /></label>
          <label>年度<select v-model="year"><option value="">全部年度</option><option v-for="y in years" :key="y" :value="String(y)">{{ y }}</option></select></label>
          <label>文件用途<select v-model="kind"><option value="">全部用途</option><option v-for="k in kinds" :key="k">{{ k }}</option></select></label>
          <button class="text-btn" @click="costQuery = ''; year = ''; kind = ''">查看全部文件</button>
        </div>
        <p v-if="costError" class="form-error" role="alert">{{ costError }}</p>
        <p v-if="costLoading" class="econ-empty" role="status">正在取得官方成本文件目錄…</p>
        <template v-else-if="costs">
          <p class="econ-count">{{ filtered.length }} 筆符合條件 · 完整目錄 {{ costs.total_documents }} 筆</p>
          <div class="cost-docs">
            <article v-for="(doc,i) in documents" :key="`${doc.title}-${doc.year}-${i}`" class="cost-document">
              <span class="cost-year">{{ doc.year ?? '未載明年度' }}</span>
              <div><strong>{{ doc.title }}</strong><small>{{ doc.kind }} · 官方群組：{{ doc.group }}</small></div>
              <a v-if="doc.url" class="btn sm" :href="doc.url" target="_blank" rel="noopener noreferrer">開啟官方文件 ↗</a>
              <span v-else class="missing-link">官方連結不完整</span>
            </article>
          </div>
          <p v-if="!documents.length && costs.status === 'ok'" class="econ-empty">沒有符合條件的文件，可清空搜尋或切換年度；這不是「生產成本為零」。</p>
          <button v-if="filtered.length > costLimit" class="btn sm" @click="costLimit += 12">再顯示 12 筆</button>
        </template>
      </template>
      <button v-if="!busy && (error || costError || (source && source.status !== 'ok'))" class="btn sm" @click="tab === 'market' ? loadMarket() : loadCosts()">重新查詢</button>
      <div class="econ-source"><span>來源：農業部農業資料開放平臺 · 本地資料庫<template v-if="source?.fetched_at"> · 本站取得 {{ new Date(source.fetched_at).toLocaleString('zh-TW') }}</template></span><a href="https://data.moa.gov.tw/api.aspx" target="_blank" rel="noopener noreferrer">官方 API 專區 ↗</a></div>
      <details v-if="source?.snapshot_id" class="econ-snapshot"><summary>查看資料快照識別碼</summary><code>{{ source.snapshot_id }}</code></details>
    </div>
  </section>
</template>

<style scoped>
.farm-economics { margin: 16px 0; overflow: hidden; }
.econ-toggle { display: flex; align-items: center; gap: 12px; width: 100%; padding: 18px 22px; border: 0; background: #fff; text-align: left; color: var(--ink); cursor: pointer; font: inherit; }
.econ-toggle strong { font-size: 15px; }.econ-toggle small { display: block; color: var(--muted); font-size: 12px; margin-top: 3px; }
.econ-icon { color: var(--brand); background: var(--brand-soft); padding: 9px; border-radius: 10px; }.econ-open { margin-left: auto; display: flex; align-items: center; white-space: nowrap; color: var(--brand); font-size: 12px; }
.econ-body { padding: 0 22px 18px; border-top: 1px solid var(--line); }
.econ-tabs { display: flex; gap: 20px; margin-bottom: 16px; border-bottom: 1px solid var(--line); }.econ-tabs button { background: none; border: 0; padding: 14px 0; font: inherit; color: var(--muted); cursor: pointer; }.econ-tabs button.on { color: var(--brand); border-bottom: 2px solid var(--brand); font-weight: 700; }
.econ-crops { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); margin-bottom: 14px; }
.econ-filters { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 12px 0; }.econ-filters label { display: grid; gap: 5px; font-size: 12px; color: var(--ink-2); min-width: 0; }
.econ-filters input,.econ-filters select { max-width: 100%; padding: 9px 10px; border: 1px solid var(--line); border-radius: 8px; background: white; font: inherit; color: var(--ink); }
.econ-notice,.econ-source,.econ-snapshot { font-size: 12px; color: var(--muted); line-height: 1.7; }.econ-notice { margin: 14px 0; }
.econ-empty { padding: 22px 14px; margin: 12px 0; border: 1px dashed var(--line-strong); background: var(--bg); border-radius: 10px; color: var(--ink-2); font-size: 13px; }
.econ-alert { padding: 10px 12px; background: var(--warn-soft); color: var(--warn); border-radius: 8px; margin: 12px 0; font-size: 12px; }
.econ-result { display: grid; grid-template-columns: 1fr 1fr; gap: 24px; padding: 18px; border: 1px solid var(--line); border-radius: 12px; margin: 12px 0; }.quote { font-size: 13px; }.quote p { margin: 12px 0; }.quote b { font-size: 36px; font-variant-numeric: tabular-nums; color: var(--brand); }.quote small { margin-left: 8px; color: var(--muted); }.quote-range { margin-top: 16px; font-size: 12px; color: var(--ink-2); }
.trend-row { display: flex; align-items: center; gap: 10px; font-size: 12px; margin-bottom: 9px; }.trend-row time { width: 40px; }.trend-row b { width: 55px; text-align: right; }.trend-track { flex: 1; height: 7px; background: var(--brand-soft); border-radius: 5px; }.trend-track i { display: block; height: 100%; background: var(--brand); border-radius: 5px; }.econ-trend>small { font-size: 11px; color: var(--muted); }
.econ-count { color: var(--muted); font-size: 12px; }.cost-document { display: flex; align-items: center; gap: 14px; padding: 14px 0; border-bottom: 1px solid var(--line); }.cost-year { color: var(--brand); font-variant-numeric: tabular-nums; font-weight: 600; }.cost-document>div { flex: 1; min-width: 0; }.cost-document strong { font-size: 14px; }.cost-document small { display: block; color: var(--muted); font-size: 12px; margin-top: 5px; }.cost-docs { margin-bottom: 12px; }.missing-link { color: var(--muted); font-size: 12px; border: 1px dashed var(--line); padding: 6px; border-radius: 6px; }
.econ-source { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; padding-top: 15px; }.econ-snapshot code { overflow-wrap: anywhere; }
@media(max-width: 700px) { .econ-toggle { padding: 15px; gap: 8px; }.econ-toggle small { max-width: 210px; }.econ-icon { display: none; }.econ-body { padding: 0 15px 16px; }.econ-result { grid-template-columns: 1fr; padding: 14px; }.econ-filters label { width: 100%; }.cost-document { flex-wrap: wrap; }.cost-document>div { flex-basis: 70%; }.cost-document a,.cost-document .missing-link { margin-left: 48px; } }
</style>
