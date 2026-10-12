<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import Icon from '@/components/Icon.vue'
import PageBar from '@/components/PageBar.vue'
import { toast } from '@/composables/toast'

/**
 * 農藥廠商查詢。
 *
 * 資料早就同步在本地（`PesticideDataQueryType`／`PesticideCompanyType`），但只能在
 * 「農業資料庫」那個通用瀏覽器裡一列一列翻。植物醫生實際會被問的是
 * 「我手上這罐為什麼不能用」「這家還有哪些藥」——那是廠商導向的問法。
 *
 * **邊界**（頁頂也寫給使用者看）：許可證回答的是「有沒有登記、登記在誰名下、到什麼時候」，
 * 不是「能不能用在這個作物的這個病蟲害上」。後者只有三源比對答得了。
 */
interface VendorHit { vendor: string; permits: number }
interface PermitRow {
  permit_text: string
  permit_kind: string
  raw_material: boolean
  vendor: string
  chinese_name: string
  en_name: string
  brand_name: string
  contents: string
  form_code: string
  pesticide_code: string
  expire_on: string | null
  revoked: boolean
  revocation_type: string
  revoked_on: string | null
  moa_codes: string[]
  detail_url: string
  status: 'active' | 'expiring' | 'expired' | 'revoked' | 'unknown'
}
interface Source { available: boolean; fetched_at: string | null; status: string; row_count: number; source_url: string }
interface Summary {
  total: number; active: number; expiring: number; expired: number; revoked: number; unknown: number
  moa_groups: { code: string; count: number }[]; moa_group_total: number
}

const router = useRouter()
const route = useRoute()

/** 從藥劑卡帶三元組過來時，先把篩選字串填好，讓人一眼看到「我是為了這個配方來的」。 */
const fromFormula = computed(() => {
  const q = route.query
  const ingredient = typeof q.q === 'string' ? q.q : ''
  const contents = typeof q.contents === 'string' ? q.contents : ''
  const formulation = typeof q.formulation === 'string' ? q.formulation : ''
  return ingredient && (contents || formulation)
    ? { ingredient, contents, formulation }
    : null
})
const query = ref('')
const hits = ref<VendorHit[]>([])
const searching = ref(false)
const source = ref<Source | null>(null)

const vendor = ref('')
const address = ref('')
const summary = ref<Summary | null>(null)
const rows = ref<PermitRow[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref('')

/** 清單內的第二層篩選：藥名／許可證號，以及只看有效／只看已撤銷。 */
const filter = ref(typeof route.query.q === 'string' && route.query.contents ? route.query.q : '')
const onlyRevoked = ref<boolean | null>(null)

let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { credentials: 'same-origin', headers: { 'X-Planning-Request': '1' } })
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.detail || '查詢失敗，請稍後再試')
  return response.json() as Promise<T>
}

watch(query, (value) => {
  clearTimeout(timer)
  const term = value.trim()
  if (!term) {
    hits.value = []
    return
  }
  searching.value = true
  // 逐鍵查 11,917 列沒有意義，等使用者打完再送。
  timer = setTimeout(() => void runSearch(term), 300)
})

async function runSearch(term: string) {
  const mine = ++sequence
  try {
    const result = await get<{ items: VendorHit[]; source: Source }>(`/vendors?q=${encodeURIComponent(term)}`)
    if (mine !== sequence) return
    hits.value = result.items
    source.value = result.source
  } catch (e) {
    if (mine === sequence) toast.error((e as Error).message)
  } finally {
    if (mine === sequence) searching.value = false
  }
}

async function openVendor(name: string) {
  loading.value = true
  error.value = ''
  vendor.value = name
  try {
    const params = new URLSearchParams()
    if (filter.value.trim()) params.set('q', filter.value.trim())
    if (onlyRevoked.value !== null) params.set('revoked', String(onlyRevoked.value))
    const result = await get<{
      vendor: string; address: string; summary: Summary; items: PermitRow[]; total: number; source: Source
    }>(`/vendors/${encodeURIComponent(name)}/permits?${params}`)
    vendor.value = result.vendor
    address.value = result.address
    summary.value = result.summary
    rows.value = result.items
    total.value = result.total
    source.value = result.source
    hits.value = []
  } catch (e) {
    error.value = (e as Error).message
    summary.value = null
    rows.value = []
  } finally {
    loading.value = false
  }
}

// 換篩選條件就重查同一家；沒選廠商時不做事。
watch([filter, onlyRevoked], () => {
  if (!vendor.value) return
  clearTimeout(timer)
  timer = setTimeout(() => void openVendor(vendor.value), 300)
})

onBeforeUnmount(() => clearTimeout(timer))

/** 「三源比對」：帶中文藥名進既有查詢；那裡才會回答能不能用在這個作物上。 */
function compare(row: PermitRow) {
  const name = row.chinese_name || row.brand_name
  if (!name) {
    toast.info('這張許可證沒有中文藥名，請用英文名或成分到用藥查詢手動比對。')
    return
  }
  void router.push({ path: '/search', query: { q: name } })
}

const product = (row: PermitRow) =>
  [row.contents, row.brand_name, row.form_code].filter(Boolean).join(' ')

const STATUS: Record<PermitRow['status'], string> = {
  active: '有效', expiring: '一年內到期', expired: '已逾期', revoked: '已撤銷', unknown: '期限未載明',
}
const fetchedAt = computed(() =>
  source.value?.fetched_at ? source.value.fetched_at.slice(0, 10) : '未同步')
</script>

<template>
  <PageBar title="農藥廠商查詢" note="許可證與撤銷資料 · 農業部資料開放平臺">
    <span v-if="source" class="src-note">本地同步 {{ fetchedAt }} · {{ source.row_count }} 張</span>
  </PageBar>

  <div class="wrap vendors">
    <!-- 邊界寫在最前面，不是塞在頁尾小字：看到「有效」就開藥是這一頁最可能造成的誤用。 -->
    <p class="scope">
      <Icon name="info" :size="15" />
      許可證回答的是「這支藥有沒有登記、登記在誰名下、到什麼時候」，<b>不是能不能用在這個作物的這個病蟲害上</b>。
      用藥仍請回到三源比對確認登記範圍、稀釋倍數與安全採收期；到期日只是提醒，不作為開單的擋關條件。
    </p>

    <p v-if="fromFormula" class="from-formula">
      <Icon name="file" :size="14" />
      <span>
        從三源比對帶過來的配方：<b>{{ fromFormula.ingredient }}</b>
        <template v-if="fromFormula.contents"> · {{ fromFormula.contents }}</template>
        <template v-if="fromFormula.formulation"> · {{ fromFormula.formulation }}</template>。
        選一家廠商後，下方清單已先用這個成分名篩過。
      </span>
    </p>

    <label class="v-search">
      <Icon name="search" :size="18" />
      <input
        v-model="query"
        type="search"
        autocomplete="off"
        placeholder="輸入廠商名稱，例如「興農」"
        aria-label="搜尋農藥廠商"
      />
      <span v-if="searching" class="small muted">搜尋中⋯</span>
    </label>

    <div v-if="hits.length" class="v-hits">
      <button v-for="hit in hits" :key="hit.vendor" type="button" @click="openVendor(hit.vendor)">
        <span>{{ hit.vendor }}</span><small>{{ hit.permits }} 張許可證</small>
      </button>
    </div>
    <p v-else-if="query.trim() && !searching" class="quiet-state">找不到符合「{{ query.trim() }}」的廠商。</p>

    <p v-if="error" class="form-error" role="alert">{{ error }}</p>

    <div v-if="summary" class="v-layout">
      <section class="card v-card">
        <h2>{{ vendor }}</h2>
        <p class="v-addr">{{ address || '此資料集未提供地址' }}</p>
        <div class="v-stat">
          <div><b>{{ summary.active }}</b><span>有效</span></div>
          <div><b class="warn-num">{{ summary.expiring }}</b><span>一年內到期</span></div>
          <div><b class="crit-num">{{ summary.revoked }}</b><span>已撤銷</span></div>
        </div>
        <p class="v-more">
          另有已逾期 {{ summary.expired }} 張<template v-if="summary.unknown">、期限未載明 {{ summary.unknown }} 張</template>；
          共 {{ summary.total }} 張。
        </p>
        <h3>作用機制（未撤銷者）</h3>
        <div class="v-moa">
          <span v-for="g in summary.moa_groups" :key="g.code">{{ g.code }}<small>{{ g.count }}</small></span>
          <small v-if="summary.moa_group_total > summary.moa_groups.length" class="muted">
            另有 {{ summary.moa_group_total - summary.moa_groups.length }} 種機制未列出
          </small>
          <span v-if="!summary.moa_groups.length" class="quiet-state">此廠商的許可證未載明作用機制。</span>
        </div>
      </section>

      <section class="card v-table">
        <div class="v-filters">
          <label class="v-filter">
            <Icon name="search" :size="14" />
            <input v-model="filter" type="search" placeholder="篩選藥名、廠牌或許可證號" aria-label="篩選許可證" />
          </label>
          <div class="chips">
            <button type="button" class="chip" :class="{ on: onlyRevoked === null }" @click="onlyRevoked = null">全部</button>
            <button type="button" class="chip" :class="{ on: onlyRevoked === false }" @click="onlyRevoked = false">未撤銷</button>
            <button type="button" class="chip" :class="{ on: onlyRevoked === true }" @click="onlyRevoked = true">已撤銷</button>
          </div>
          <span class="small muted">{{ total }} 張</span>
        </div>

        <div class="v-scroll">
          <table class="tb">
            <thead>
              <tr><th>藥劑</th><th>許可證</th><th>有效期限</th><th>作用機制</th><th></th></tr>
            </thead>
            <tbody>
              <!-- 已撤銷的整列淡紅但**照樣列出**：植物醫生要能回答「手上這罐為什麼不能用」。 -->
              <tr v-for="row in rows" :key="row.permit_text" :class="{ rev: row.revoked }">
                <td class="n">
                  {{ row.chinese_name || row.brand_name || '（未載明藥名）' }}
                  <small>{{ row.en_name }}<template v-if="product"> · {{ product(row) }}</template></small>
                  <span v-if="row.raw_material" class="tag">原體</span>
                </td>
                <td>
                  {{ row.permit_text || '—' }}
                  <small v-if="row.permit_kind">{{ row.permit_kind }}</small>
                </td>
                <td>
                  <span :class="{ 'warn-num': row.status === 'expiring', 'crit-num': row.status === 'expired' }">
                    {{ row.expire_on || '未載明' }}
                  </span>
                  <small>{{ STATUS[row.status] }}<template v-if="row.revocation_type"> · {{ row.revocation_type }}</template></small>
                </td>
                <td><small>{{ row.moa_codes.join('、') || '未載明' }}</small></td>
                <td>
                  <button type="button" class="btn sm" @click="compare(row)">三源比對</button>
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="!rows.length && !loading" class="quiet-state">沒有符合條件的許可證。</p>
          <p v-if="loading" class="quiet-state">載入中⋯</p>
        </div>
        <p class="v-foot">
          清單含已撤銷的許可證，用來說明「為什麼這罐不能再用」。
          資料來源：農業部資料開放平臺 {{ source?.source_url }}；本地同步 {{ fetchedAt }}。
        </p>
      </section>
    </div>

    <div v-else-if="!query.trim()" class="card farm-empty">
      <Icon name="search" :size="34" />
      <h2>先找一家廠商</h2>
      <p>輸入廠商名稱，就能看到它名下的許可證、有效期限與作用機制分布。</p>
    </div>
  </div>
</template>

<style scoped>
.vendors { padding-bottom: 24px; }
.src-note { font-size: 12px; color: var(--muted); }
.scope {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  background: #eef2f5;
  border: 1px solid #dfe4e8;
  border-radius: 12px;
  padding: 12px 14px;
  margin: 16px 0;
}
.v-search {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 46px;
  padding: 0 14px;
  border: 1px solid var(--line-strong);
  border-radius: 12px;
  background: #fff;
}
.v-search input { flex: 1; border: 0; font-size: 15px; background: none; }
.v-hits { display: flex; flex-direction: column; margin-top: 8px; border: 1px solid var(--line); border-radius: 12px; overflow: hidden; background: #fff; }
.v-hits button {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 0;
  background: none;
  padding: 11px 14px;
  text-align: left;
  font-size: 14px;
  cursor: pointer;
  border-bottom: 1px solid var(--line);
}
.v-hits button:last-child { border-bottom: 0; }
.v-hits button:hover { background: var(--bg); }
.v-hits small { margin-left: auto; color: var(--muted); font-size: 12px; }

.v-layout { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 18px; align-items: start; margin-top: 18px; }
.v-card { padding: 16px 18px; }
.v-card h2 { font-size: 18px; }
.v-card h3 { font-size: 12px; color: var(--muted); margin: 16px 0 8px; letter-spacing: 0.04em; }
.v-addr { font-size: 13px; color: var(--muted); margin-top: 4px; line-height: 1.6; }
.v-stat { display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-top: 14px; }
.v-stat > div { padding: 10px; border-radius: 10px; background: #fafbf9; border: 1px solid var(--line); text-align: center; }
.v-stat b { display: block; font-size: 20px; }
.v-stat span { font-size: 11px; color: var(--muted); }
.warn-num { color: #8a6414; }
.crit-num { color: #a8342a; }
.v-more { font-size: 12px; color: var(--muted); margin-top: 10px; line-height: 1.6; }
.v-moa { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
.v-moa > span { font-size: 12px; background: var(--bg); border-radius: 6px; padding: 4px 8px; }
.v-moa small { color: var(--muted); margin-left: 5px; }

.v-table { overflow: hidden; }
.v-filters { display: flex; align-items: center; gap: 10px; padding: 12px 14px; border-bottom: 1px solid var(--line); flex-wrap: wrap; }
.v-filter { display: flex; align-items: center; gap: 6px; height: 34px; padding: 0 10px; border: 1px solid var(--line); border-radius: 9px; }
.v-filter input { border: 0; font-size: 13px; background: none; }
.v-filters .chips { display: flex; gap: 6px; }
.v-filters .chip { height: 30px; font-size: 13px; padding: 0 12px; border-radius: 999px; border: 1px solid var(--line-strong); background: #fff; cursor: pointer; }
.v-filters .chip.on { background: var(--ink); border-color: var(--ink); color: #fff; }
.v-scroll { max-height: 640px; overflow-y: auto; }
.tb { width: 100%; border-collapse: collapse; font-size: 13px; }
.tb th { text-align: left; font-size: 12px; color: var(--muted); font-weight: 600; padding: 10px 14px; border-bottom: 1px solid var(--line); background: #fafbf9; position: sticky; top: 0; }
.tb td { padding: 11px 14px; border-bottom: 1px solid var(--line); vertical-align: top; }
.tb .n { font-weight: 600; }
.tb .n small, .tb td small { display: block; font-weight: 400; color: var(--muted); font-size: 12px; }
/* 撤銷的整列淡紅——看得出來不能用，但仍在清單裡。 */
.tb .rev td { background: #fff7f6; }
.v-foot { font-size: 12px; color: var(--muted); padding: 10px 14px; border-top: 1px solid var(--line); line-height: 1.6; }

@media (max-width: 1100px) {
  .v-layout { grid-template-columns: 1fr; }
}
.from-formula {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  background: var(--bg);
  border-radius: 10px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
</style>
