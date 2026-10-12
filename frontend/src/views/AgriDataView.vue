<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageBar from '@/components/PageBar.vue'
import { agriData, type Dataset, type DataPage } from '@/api/agriData'
const route=useRoute(), router=useRouter()
const catalog=ref<Dataset[]>([]), result=ref<DataPage|null>(null), loading=ref(false), error=ref('')
const query=ref(''), name=ref('ImportantAgriculturalPestDiagnosticsType'), page=ref(1)
const groups=['病蟲害','施肥用藥','農業氣象']
const selected=computed(()=>catalog.value.find(d=>d.endpoint===name.value))
const group=computed(()=>selected.value?.group || '病蟲害')
const totalPages=computed(()=>Math.max(1,Math.ceil((result.value?.total || 0)/20)))
let sequence=0
const when=(v:string|null)=>v ? new Date(v).toLocaleString('zh-TW') : '尚未同步'
const valueText=(v:unknown)=>v===null || v===undefined || v==='' ? '未載明，請看原文' : typeof v==='number' ? v.toLocaleString('zh-TW',{maximumFractionDigits:2}) : typeof v==='object' ? JSON.stringify(v) : String(v)
function officialLink(value:unknown):string|null {
  if (typeof value !== 'string') return null
  try {
    const u=new URL(value)
    return ['https:','http:'].includes(u.protocol) && !u.username && !u.password && (u.hostname.endsWith('.gov.tw') || u.hostname==='gov.tw') ? u.href : null
  } catch {return null}
}
const stateText=(d:Dataset)=>!d.available
  ? `尚未有完整本地資料${d.last_error==='key_required' ? '；完整分頁需要會員金鑰' : d.last_error ? '；最近同步未成功' : '，等待背景同步'}。這不代表查無農藥、病蟲害或氣象資料。`
  : d.status==='stale' ? '顯示本地舊快照：已到更新時間或最近同步失敗，資料仍可查閱，請核對來源日期。' : '正在查詢本地已驗證快照，不需連線到官方 API。'
async function load() {
  const token=++sequence; loading.value=true; error.value=''; result.value=null
  name.value=typeof route.query.dataset==='string' ? route.query.dataset : 'ImportantAgriculturalPestDiagnosticsType'
  query.value=typeof route.query.q==='string' ? route.query.q : ''
  page.value=Math.max(1,Number(route.query.page)||1)
  try {
    const list=await agriData.datasets(); if(token!==sequence)return;catalog.value=list
    if (!selected.value) throw new Error('這個資料集尚未開放，請由上方分類重新選擇。')
    const data=await agriData.records(name.value,query.value,page.value)
    if(token===sequence)result.value=data
  } catch(e) {if(token===sequence)error.value=(e as Error).message}
  finally {if(token===sequence)loading.value=false}
}
function go(dataset=name.value,q=query.value,nextPage=1) {
  const next={dataset,q,page:String(nextPage)}
  if (route.query.dataset===dataset && route.query.q===q && String(route.query.page||'1')===String(nextPage)) void load()
  else void router.replace({path:'/agri-data',query:next})
}
function chooseGroup(label:string) {const next=catalog.value.find(d=>d.group===label);if(next)go(next.endpoint,'')}
watch(()=>route.fullPath,load,{immediate:true})
onBeforeUnmount(()=>++sequence)
</script>
<template>
  <PageBar title="農業資料庫" note="施肥用藥、病蟲害與農業氣象 · 本地保存，保留來源與日期" />
  <div class="wrap agri-page">
    <section class="card agri-search">
      <div class="agri-tabs" role="tablist" aria-label="資料分類">
        <button v-for="g in groups" :key="g" role="tab" :aria-selected="group===g" :class="{on:group===g}" @click="chooseGroup(g)">{{ g }}</button>
      </div>
      <form class="agri-filters" @submit.prevent="go()">
        <label>資料集<select aria-label="資料集" :value="name" @change="go(($event.target as HTMLSelectElement).value,'')"><optgroup v-for="g in groups" :key="g" :label="g"><option v-for="d in catalog.filter(v=>v.group===g)" :key="d.endpoint" :value="d.endpoint">{{ d.name }}{{ !d.available ? ' · 尚未收錄' : '' }}</option></optgroup></select></label>
        <label class="agri-query">{{ selected?.exact_search ? '完整藥名、商品名或代碼' : '作物、病徵、縣市或測站' }}<input v-model="query" maxlength="120" :placeholder="selected?.exact_search ? '完整名稱比對，避免單劑與複方混淆' : '輸入關鍵字；留空查看全部'" /></label>
        <button class="btn primary" type="submit" :disabled="loading">查本地資料</button>
      </form>
      <p class="agri-notice">{{ selected?.notice }}</p>
      <router-link v-if="group!=='農業氣象'" class="text-btn" to="/search">前往三源用藥比對 →</router-link>
    </section>
    <p v-if="error" class="card agri-message" role="alert">{{ error }} <button class="btn sm" @click="load">重試</button></p>
    <p v-if="loading" class="card agri-message" role="status">正在讀取本地資料…</p>
    <template v-else-if="result">
      <section class="card agri-status" :class="{stale:result.source.status==='stale',missing:!result.source.available}" role="status">
        <strong>{{ stateText(result.source) }}</strong>
        <div>已保存 {{ result.source.row_count.toLocaleString() }} 筆 · 本次符合 {{ result.total.toLocaleString() }} 筆</div>
        <div>本站取得：{{ when(result.source.fetched_at) }} · 最近同步嘗試：{{ when(result.source.last_attempt_at) }}</div>
        <div v-if="result.source.scope?.Start_time">本地觀測範圍：{{ result.source.scope.Start_time }} 至 {{ result.source.scope.End_time }}（同步時近 24 小時）</div>
        <details><summary>資料版本與同步狀態</summary><span>快照 {{ result.source.snapshot_id || '尚無' }} · 更新間隔 {{ result.source.hours }} 小時 · {{ result.source.last_error || '無同步錯誤' }}</span><br/><a :href="result.source.source_url" target="_blank" rel="noopener noreferrer">官方 API 文件 ↗</a></details>
      </section>
      <p v-if="!result.items.length && result.source.available" class="card agri-message">這份本地快照沒有符合的資料。{{ selected?.exact_search ? '請核對完整藥名或代碼；這不等於查無核准用藥。' : '可調整搜尋詞；資料未收錄不代表田間不存在。' }}</p>
      <div class="agri-records">
        <article v-for="item in result.items" :key="`${result.source.snapshot_id}-${item.id}`" class="card agri-record">
          <h2>{{ item.title }}</h2>
          <p v-if="item.observation_state" class="observation" :class="{old:item.observation_state!=='recent'}">{{ item.observation_state==='recent' ? '3 小時內測站觀測；非田區實測或預報' : item.observation_state==='outdated' ? '觀測已過期或時間異常，僅供歷史參考' : '觀測時間未載明，不作即時建議' }}</p>
          <div class="agri-preview"><template v-for="key in ['PubDate','ReceiptDate','TIME','City','CITY','Crop_Name','Question','Answer','symptom','TEMP','HUMD','RAIN','ExpireDate','RevocationType','ExpiryDate','Element']" :key="key"><p v-if="key in item.values"><b>{{ result.fields[key] || key }}：</b>{{ valueText(item.values[key]) }}</p></template></div>
          <details><summary>查看完整來源欄位</summary><dl><template v-for="(value,key) in item.values" :key="key"><dt>{{ result.fields[key] || key }}</dt><dd><a v-if="officialLink(value)" :href="officialLink(value)!" target="_blank" rel="noopener noreferrer">開啟官方原文 ↗</a><span v-else>{{ valueText(value) }}</span></dd></template></dl></details>
        </article>
      </div>
      <div v-if="result.total" class="agri-pages"><button class="btn sm" :disabled="page<=1" @click="go(name,query,page-1)">上一頁</button><span>第 {{ page }}／{{ totalPages }} 頁</span><button class="btn sm" :disabled="page>=totalPages" @click="go(name,query,page+1)">下一頁</button></div>
    </template>
    <details class="card agri-inventory"><summary>本地收錄狀態 · {{ catalog.length }} 個資料集</summary><button class="btn sm" @click="load">重新讀取收錄狀態</button><button v-for="d in catalog" :key="d.endpoint" class="inventory-row" @click="go(d.endpoint,'')"><span>{{ d.name }}</span><span>{{ d.available ? `${d.row_count.toLocaleString()} 筆` : '尚無完整資料' }} · {{ d.status==='ok' ? '已保存' : d.status==='stale' ? '待更新' : '待同步' }}</span></button></details>
  </div>
</template>
<style scoped>
.agri-page{padding-bottom:40px}.agri-search{padding:20px 24px}.agri-tabs{display:flex;gap:24px;border-bottom:1px solid var(--line);margin-bottom:18px}.agri-tabs button{background:none;border:0;padding:0 0 14px;font:inherit;color:var(--muted);cursor:pointer}.agri-tabs button.on{color:var(--brand);border-bottom:2px solid var(--brand);font-weight:700}.agri-filters{display:flex;gap:14px;align-items:end;flex-wrap:wrap}.agri-filters label{display:grid;gap:6px;font-size:13px;min-width:0}.agri-query{flex:1}.agri-filters input,.agri-filters select{border:1px solid var(--line);border-radius:10px;background:white;color:var(--ink);font:inherit;padding:10px;max-width:100%;min-width:0}.agri-notice{font-size:13px;color:var(--ink-2);line-height:1.8;margin:16px 0 8px}.agri-message,.agri-status{padding:18px 24px;margin:16px 0}.agri-status{font-size:13px;line-height:1.9}.agri-status.stale{border-color:var(--warn);background:var(--warn-soft)}.agri-status.missing{border-style:dashed}.agri-status details{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.agri-records{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}.agri-record{padding:20px;min-width:0}.agri-record h2{font-size:17px;line-height:1.7;margin:0 0 12px}.agri-preview p{font-size:13px;line-height:1.8;margin:6px 0;overflow-wrap:anywhere}.agri-preview b{font-weight:500;color:var(--muted)}.agri-record details{margin-top:12px;font-size:13px}.agri-record summary{color:var(--brand);cursor:pointer}.agri-record dl{display:grid;grid-template-columns:120px 1fr;gap:10px;line-height:1.7}.agri-record dt{color:var(--muted)}.agri-record dd{margin:0;overflow-wrap:anywhere;white-space:pre-wrap}.observation{background:var(--brand-soft);padding:8px;border-radius:8px;font-size:12px}.observation.old{background:var(--warn-soft);color:var(--warn)}.agri-pages{display:flex;align-items:center;justify-content:center;gap:18px;margin:20px 0;font-size:13px}.agri-inventory{padding:18px 24px;margin-top:18px}.inventory-row{display:flex;justify-content:space-between;gap:15px;width:100%;border:0;border-bottom:1px solid var(--line);background:none;font:inherit;font-size:13px;padding:12px 0;text-align:left;cursor:pointer}.inventory-row span:last-child{color:var(--muted)}
@media(max-width:700px){.agri-records{grid-template-columns:1fr}.agri-search,.agri-status,.agri-record,.agri-message{padding:16px}.agri-filters label{width:100%;flex:auto}.agri-record dl{grid-template-columns:95px 1fr}.inventory-row{flex-direction:column;gap:4px}}
</style>
