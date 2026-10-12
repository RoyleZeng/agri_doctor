<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import { planning, type Farmer } from '@/api/planning'
import type { Comparison, ConsultCase, PrescriptionItem, RegistrationSearchItem, SearchItem, Suggest } from '@/api/types'
import AppBanner from '@/components/AppBanner.vue'
import ConsultSearch from '@/components/ConsultSearch.vue'
import RegistrationPicker from '@/components/RegistrationPicker.vue'
import ConsultReportPanel from '@/components/ConsultReportPanel.vue'
import Icon from '@/components/Icon.vue'
import { isModifiedField, phiStatus, replacePrescriptionItem } from '@/composables/consultState'
import { toast } from '@/composables/toast'

const route = useRoute()
const router = useRouter()
const me = ref<Farmer | null>(null)
const loading = ref(true)
const error = ref('')
const saving = ref(false)
const consultCase = ref<ConsultCase | null>(null)
const savedItemFields = ref<Record<string, string>>({})
const itemValues = (i: PrescriptionItem) => JSON.stringify([i.dilution, i.dose, i.frequency, i.advice])
const itemDirty = computed(() => consultCase.value?.items.some(i => savedItemFields.value[i.id] !== itemValues(i)) ?? false)
const locked = computed(() => consultCase.value?.status === 'issued')
function reportUpdated(value: ConsultCase) {
  if (consultCase.value?.id === value.id) consultCase.value = { ...value, items: consultCase.value.items }
}
const selectedPest = ref<SearchItem | null>(null)
const comparison = ref<Comparison | null>(null)
const crops = ref<Suggest[]>([])
let cropTimer: ReturnType<typeof setTimeout> | undefined

const form = ref({ farmer_text: '', crop_term: '', crop_id: null as number | null, crop_text: '', field_text: '', variety: '', area_text: '', expected_harvest_on: '' })

const hasCase = computed(() => consultCase.value !== null)
const caseCropId = computed(() => consultCase.value?.crop_id ?? null)
const recommendationRows = computed(() => comparison.value?.rows ?? [])

watch(() => form.value.crop_term, (term) => {
  clearTimeout(cropTimer)
  // 從候選選定後仍會寫回輸入框；這不是新的自由輸入，不能清掉已選作物。
  if (form.value.crop_id && term === form.value.crop_text) { crops.value = []; return }
  form.value.crop_id = null
  form.value.crop_text = ''
  if (!term.trim()) { crops.value = []; return }
  cropTimer = setTimeout(async () => {
    try { crops.value = await api.searchCrops(term.trim()) }
    catch { crops.value = [] }
  }, 220)
})

onMounted(async () => {
  try {
    me.value = await planning.me()
    if (!['doctor', 'admin'].includes(me.value.role)) throw new Error('診療開單僅限植物醫生帳號使用。')
    const caseId = typeof route.query.case === 'string' ? route.query.case : ''
    if (caseId) await loadCase(caseId)
  } catch (e) {
    error.value = (e as Error).message || '無法確認登入狀態。'
  } finally {
    loading.value = false
  }
})

async function loadCase(id: string) {
  consultCase.value = await api.consultCase(id)
  savedItemFields.value = Object.fromEntries(consultCase.value.items.map(i => [i.id, itemValues(i)]))
}

function chooseCrop(crop: Suggest) {
  form.value.crop_id = crop.id
  form.value.crop_text = crop.name
  form.value.crop_term = crop.name
  crops.value = []
}

async function createCase() {
  if (!form.value.crop_id) { toast.error('請先從候選清單選擇作物。'); return }
  if (!form.value.farmer_text.trim()) { toast.error('請填寫委託人姓名。'); return }
  saving.value = true
  try {
    const created = await api.createConsultCase({
      farmer_text: form.value.farmer_text.trim(), crop_id: form.value.crop_id,
      field_text: form.value.field_text.trim(), variety: form.value.variety.trim(),
      area_text: form.value.area_text.trim(), expected_harvest_on: form.value.expected_harvest_on || null,
    })
    consultCase.value = created
    await router.replace({ name: 'consult', query: { case: created.id } })
    toast.ok('已建立診療案件。')
  } catch (e) {
    toast.error(`建立失敗：${(e as Error).message}`)
  } finally { saving.value = false }
}

async function choosePest(pest: SearchItem) {
  if (!caseCropId.value) return
  selectedPest.value = pest
  comparison.value = null
  try {
    comparison.value = await api.recommend(caseCropId.value, pest.id)
  } catch (e) { toast.error(`無法取得核准用藥：${(e as Error).message}`) }
}

/* ---- 案件層級的抗藥性輪替橫幅 ----
 * 逐項提示（每個處方項目一行）保留當細節；這裡講的是「整張處方連續第幾次同機制」，
 * 那是逐項各自成立、加起來才看得出來的事，也是上一輪留下的缺口。
 */
const rotation = computed(() => consultCase.value?.rotation ?? null)
const rotationAlert = computed(() => (rotation.value?.level === 'crit' ? rotation.value : null))
/** 施藥序列：8/22 A（FRAC 1）→ … → 本次。把判斷依據攤開，不要只丟一句結論。 */
const rotationSequence = computed(() =>
  (rotationAlert.value?.sequence ?? []).map((s) => {
    const codes = s.groups.join('、') || '機制未載明'
    // 最後一筆後端已經叫「本次處方」，前面再加一次「本次」會變成「本次 本次處方」。
    return s.current ? `本次（${codes}）` : `${cropDate(s.applied_on)} ${s.product_text}（${codes}）`
  }).join(' → '))

/** 「換一支不同機制的藥」：開登記挑選器，並把要避開的機制帶進去預篩。 */
const swapOpen = ref(false)

async function pickReplacement(item: RegistrationSearchItem) {
  swapOpen.value = false
  await addItem(item)
}

async function addItem(item: RegistrationSearchItem | { registration_id: number }) {
  if (!consultCase.value || saving.value || locked.value) return
  saving.value = true
  try {
    const saved = await api.addConsultItem(consultCase.value.id, item.registration_id, consultCase.value.version)
    consultCase.value.items.push(saved)
    consultCase.value.version = saved.case_version
    savedItemFields.value[saved.id] = itemValues(saved)
    toast.ok('已加入處方，並完成採收期與輪替檢查。')
  } catch (e) { toast.error(`無法加入：${(e as Error).message}`) }
  finally { saving.value = false }
}

async function saveItem(item: PrescriptionItem) {
  if (!consultCase.value || saving.value || locked.value) return
  saving.value = true
  try {
    const saved = await api.patchConsultItem(consultCase.value.id, item.id, {
      version: consultCase.value.version, dilution: item.dilution, dose: item.dose,
      frequency: item.frequency, advice: item.advice,
    })
    // 不能 reload 整份案件，否則其他處方卡仍在編輯、尚未送出的內容會消失。
    consultCase.value.items = replacePrescriptionItem(consultCase.value.items, saved)
    consultCase.value.version = saved.case_version
    savedItemFields.value[saved.id] = itemValues(saved)
    toast.ok('處方已儲存並重新檢查。')
  } catch (e) { toast.error(`儲存失敗：${(e as Error).message}`) }
  finally { saving.value = false }
}

async function removeItem(item: PrescriptionItem) {
  if (!consultCase.value || saving.value || locked.value) return
  saving.value = true
  try {
    const result = await api.deleteConsultItem(consultCase.value.id, item.id, consultCase.value.version)
    consultCase.value.items = consultCase.value.items.filter(i => i.id !== item.id)
    consultCase.value.version = result.case_version
    delete savedItemFields.value[item.id]
    toast.ok('已移除處方項目。')
  } catch (e) { toast.error(`無法移除：${(e as Error).message}`) }
  finally { saving.value = false }
}

function startNewCase() {
  consultCase.value = null
  selectedPest.value = null
  comparison.value = null
  form.value = { farmer_text: '', crop_term: '', crop_id: null, crop_text: '', field_text: '', variety: '', area_text: '', expected_harvest_on: '' }
  void router.replace({ name: 'consult' })
}

function highest(checks: PrescriptionItem['checks']) {
  return checks.reduce((best, check) => ({ block: 3, warning: 2, ok: 1 }[check.level] > { block: 3, warning: 2, ok: 1 }[best] ? check.level : best), 'ok' as 'ok' | 'warning' | 'block')
}
function checkMessage(item: PrescriptionItem) {
  return item.checks.filter((check) => check.level !== 'ok').map((check) => check.message).join(' ')
}
function cropDate(date: string | null) { return date ? date.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$2/$3') : '未填' }
function rowPhiStatus(row: Comparison['rows'][number]) {
  return phiStatus(row.phi_days, consultCase.value?.expected_harvest_on ?? null)
}
function sourceState(row: RegistrationSearchItem | Comparison['rows'][number], source: 'ppm' | 'fda' | 'kdais') {
  return row.sources[source]?.present ? source : 'absent'
}
</script>

<template>
  <div class="wrap consult-page">
    <div class="demo-report-link"><span>診斷書展示 · 暫定版型</span><a href="/demo/diagnosis-report.pdf" target="_blank" rel="noopener">開啟示範 PDF ↗</a><router-link to="/identify">病蟲害輔助辨識展示 →</router-link></div>
    <div v-if="loading" class="card state"><Icon name="refresh" :size="18" /> 正在確認診療工作區⋯</div>
    <div v-else-if="error" class="card state error"><Icon name="warn" :size="18" /> {{ error }}<router-link to="/farm" class="btn sm">前往登入</router-link></div>

    <section v-else-if="!hasCase" class="case-create card">
      <div class="card-h"><div><p class="eyebrow">診療開單</p><h1>建立診療案件</h1></div><span class="tag brand">植物醫生</span></div>
      <form class="create-form" @submit.prevent="createCase">
        <label>委託人<input v-model="form.farmer_text" required placeholder="農戶或現場委託人姓名" /></label>
        <label class="crop-field">作物
          <input v-model="form.crop_term" required autocomplete="off" placeholder="輸入後從候選清單選擇" />
          <div v-if="crops.length" class="crop-menu"><button v-for="crop in crops" :key="crop.id" type="button" @click="chooseCrop(crop)">{{ crop.name }}</button></div>
        </label>
        <label>田區<input v-model="form.field_text" placeholder="例如：東側一區" /></label>
        <label>品種<input v-model="form.variety" placeholder="例如：愛文" /></label>
        <label>面積<input v-model="form.area_text" placeholder="例如：0.8 公頃" /></label>
        <label>預計採收<input v-model="form.expected_harvest_on" type="date" /></label>
        <p class="form-hint"><Icon name="info" :size="15" /> 採收日可讓系統在加入處方時檢查安全採收期；未填時會提示人工確認。</p>
        <button class="btn primary" :disabled="saving">{{ saving ? '建立中⋯' : '建立案件，開始開單' }}</button>
      </form>
    </section>

    <template v-else-if="consultCase">
      <section class="card casebar">
        <span class="case-no">案件 {{ consultCase.case_no }}</span>
        <span>農戶 <b>{{ consultCase.farmer_text }}</b></span>
        <span>作物 <b>{{ consultCase.crop_text }}{{ consultCase.variety ? ` · ${consultCase.variety}` : '' }}</b>{{ consultCase.area_text ? ` · ${consultCase.area_text}` : '' }}</span>
        <span>預計採收 <b class="num">{{ cropDate(consultCase.expected_harvest_on) }}</b></span>
        <button class="btn sm new-case" @click="startNewCase"><Icon name="plus" :size="14" /> 新增案件</button>
      </section>

      <!-- 案件層級的抗藥性輪替：不可關閉，因為它講的是這張處方本身的問題，不是可以「知道了」的提示。 -->
      <AppBanner
        v-if="rotationAlert"
        tone="crit"
        :title="`這張處方連續第 ${rotationAlert.streak} 次使用 ${rotationAlert.groups.join('、')}`"
        :body="`${consultCase.farmer_text}${consultCase.field_text ? ' · ' + consultCase.field_text : ''} 近期施藥：${rotationSequence}。建議改用不同作用機制。`"
      >
        <button class="btn" type="button" :disabled="locked || saving" @click="swapOpen = !swapOpen">
          {{ swapOpen ? '收起' : '換一支不同機制的藥' }}
        </button>
      </AppBanner>
      <section v-if="rotationAlert && swapOpen" class="card swap-picker">
        <RegistrationPicker
          :crop-id="consultCase.crop_id"
          :crop-name="consultCase.crop_text"
          :exclude-groups="rotationAlert.exclude_groups"
          @pick="pickReplacement"
          @cancel="swapOpen = false"
        />
      </section>

      <div class="consult-layout">
        <section class="work-area">
          <div v-if="locked" class="alert">本案已開立並鎖定，處方與診察紀錄僅供檢視及重印。</div>
          <ConsultSearch v-else-if="consultCase.crop_id" :crop-id="consultCase.crop_id" :crop-name="consultCase.crop_text" @pest="choosePest" @registration="addItem" />
          <div v-else class="alert warn">此案件沒有可對應的標準作物，無法自動比對核准登記。</div>
          <p v-if="!locked" class="search-help">可從病蟲害查看核准用藥，或直接以藥劑／成分名搜尋。選取病蟲害不會自動寫入診斷結果。</p>
          <section v-if="locked" class="card issued-preview"><div class="card-h"><h2>已開立診斷書</h2><a class="btn sm" :href="`/api/v1/consult/cases/${consultCase.id}/report.pdf`" target="_blank" rel="noopener">開啟 PDF ↗</a></div><iframe :src="`/api/v1/consult/cases/${consultCase.id}/report.pdf#view=FitH&navpanes=0`" title="已開立診斷書 PDF" /><p class="small muted">暫定版型 · 重印沿用原始紀錄。如裝置無法預覽，請點「開啟 PDF」。</p></section>

          <section v-if="selectedPest && !locked" class="recommendations">
            <div class="section-head"><div><p class="eyebrow">核准候選</p><h2>{{ consultCase.crop_text }} × {{ selectedPest.name }}</h2></div><span class="muted small">{{ recommendationRows.length }} 筆登記</span></div>
            <div v-if="!recommendationRows.length" class="card empty">目前正式快照沒有此組合的核准登記；這不代表官方不存在，請依資料覆蓋狀態再核對。</div>
            <article v-for="row in recommendationRows" :key="row.registration_id" class="card recommendation" :class="{ blocked: row.banned }">
              <div class="recommendation-title"><div><h3>{{ row.product_name }}</h3><p>{{ row.english_composition || '成分資料待確認' }} · {{ row.moa_codes.join('、') || '作用機制待確認' }}</p></div><div class="recommendation-action"><button class="btn sm" :class="row.banned || rowPhiStatus(row).blocked ? 'blocked-btn' : 'primary'" :disabled="row.banned || rowPhiStatus(row).blocked || saving" @click="addItem(row)">{{ row.banned ? '不可開立' : rowPhiStatus(row).blocked ? rowPhiStatus(row).message : '＋ 加入處方' }}</button><span v-if="!consultCase.expected_harvest_on" class="harvest-hint"><Icon name="info" :size="13" />未填採收日</span></div></div>
              <div class="facts-row"><span>稀釋<b class="num">{{ row.dilution_text || '—' }}</b></span><span>安全採收期<b class="num">{{ row.phi_days == null ? '待確認' : `${row.phi_days} 天` }}</b></span><span>每公頃量<b>{{ row.dose_per_hectare || '—' }}</b></span></div>
              <div class="pills"><span v-for="source in ['ppm', 'fda', 'kdais'] as const" :key="source" class="pill" :class="sourceState(row, source)">{{ source === 'ppm' ? '植保' : source === 'fda' ? '食藥' : '高改' }}</span></div>
            </article>
          </section>
          <section v-else-if="!locked" class="card welcome"><Icon name="search" :size="22" /><div><h2>從田間問題開始搜尋</h2><p>先選病蟲害查看適用登記，或以藥劑名稱直接加入。系統會在寫入前檢查禁用、採收期和近兩次的同田區輪替。</p></div></section>
        </section>

        <aside class="card prescription">
          <div class="card-h prescription-head"><div><p class="eyebrow">{{ locked ? '已開立處方' : '處方草稿' }}</p><h2>用藥建議單</h2></div><span class="tag brand">{{ consultCase.items.length }} 項</span></div>
          <div v-if="!consultCase.items.length" class="rx-empty"><Icon name="note" :size="24" />尚未加入用藥。從左側搜尋後，系統會先完成開單檢查。</div>
          <fieldset v-for="item in consultCase.items" :key="item.id" class="rx-item" :disabled="locked || saving">
            <div class="rx-title"><div><h3>{{ item.product_text }}</h3><p>{{ item.ingredient_text }} · {{ item.moa_text || '作用機制待確認' }}</p></div><button class="icon-button" aria-label="移除處方項目" :disabled="saving" @click="removeItem(item)"><Icon name="close" :size="16" /></button></div>
            <div class="rx-fields"><label><span>稀釋 <em v-if="isModifiedField(item, 'dilution')" class="modified-tag">已修改</em></span><input v-model="item.dilution" /></label><label><span>每公頃量 <em v-if="isModifiedField(item, 'dose')" class="modified-tag">已修改</em></span><input v-model="item.dose" /></label><label><span>施用頻率 <em v-if="isModifiedField(item, 'frequency')" class="modified-tag">已修改</em></span><input v-model="item.frequency" placeholder="請填寫" /></label><label class="wide"><span>醫師囑咐 <em v-if="isModifiedField(item, 'advice')" class="modified-tag">已修改</em></span><textarea v-model="item.advice" rows="2" placeholder="可補充天候、施作與觀察建議" /></label></div>
            <div class="check-line" :class="highest(item.checks)"><Icon :name="highest(item.checks) === 'ok' ? 'check' : 'warn'" :size="15" /><span>{{ checkMessage(item) || '已通過開單檢查。' }}</span></div>
            <div class="rx-actions"><span>安全採收期：<b>{{ item.phi_days == null ? '待確認' : `${item.phi_days} 天` }}</b></span><span class="rx-buttons"><router-link v-if="consultCase.status !== 'draft' && consultCase.farmer_id" class="btn sm" :to="{ path: `/farmers/${consultCase.farmer_id}`, query: { tab: 'log', rx: item.id } }">已執行，記到日誌</router-link><button class="btn sm" :disabled="saving" @click="saveItem(item)">儲存變更</button></span></div>
          </fieldset>
          <ConsultReportPanel :case-data="consultCase" :item-dirty="itemDirty" :busy="saving" @updated="reportUpdated" @working="saving = $event" />
        </aside>
      </div>
    </template>
  </div>
</template>

<style scoped>
.swap-picker { padding: 14px 16px; margin-bottom: 12px; }
.consult-page { min-height: 600px; }
.issued-preview { margin-top: 14px; overflow: hidden; }.issued-preview h2 { font-size: 17px; }.issued-preview iframe { width: 100%; height: 900px; border: 0; }.issued-preview > p { padding: 12px 18px; }.rx-item:disabled button { opacity: .45; cursor: not-allowed; }
.demo-report-link { display: flex; flex-wrap: wrap; gap: 8px 20px; align-items: center; padding: 12px 16px; margin-bottom: 18px; border: 1px dashed var(--line-strong); border-radius: 10px; font-size: 13px; }.demo-report-link span { color: var(--muted); }.rx-item { border: 0; margin: 0; min-width: 0; } .rx-item:disabled { background: #fafbf9; }
.state { display: flex; align-items: center; gap: 10px; padding: 22px; color: var(--muted); }.state.error { color: var(--crit); }.state .btn { margin-left: auto; }
.case-create { max-width: 780px; margin: 36px auto; overflow: visible; }.case-create h1 { font-size: 24px; }.create-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; padding: 24px; }.create-form label, .rx-fields label { display: flex; flex-direction: column; gap: 5px; color: var(--ink-2); font-size: 13px; font-weight: 600; }.create-form input, .rx-fields input, .rx-fields textarea { width: 100%; border: 1px solid var(--line-strong); border-radius: 8px; padding: 8px 10px; background: #fff; color: var(--ink); font: inherit; font-size: 14px; outline-color: var(--brand); }.crop-field { position: relative; }.crop-menu { position: absolute; z-index: 4; top: 66px; left: 0; right: 0; max-height: 220px; overflow: auto; border: 1px solid var(--line); border-radius: 10px; background: #fff; box-shadow: 0 10px 24px rgba(20,30,25,.12); }.crop-menu button { display: block; width: 100%; padding: 9px 12px; border: 0; background: #fff; text-align: left; font: inherit; cursor: pointer; }.crop-menu button:hover { background: var(--brand-soft); }.form-hint { grid-column: 1 / -1; display: flex; gap: 7px; margin: 0; color: var(--muted); font-size: 13px; }.create-form .btn { justify-self: start; }
.casebar { display: flex; align-items: center; flex-wrap: wrap; gap: 12px 20px; margin-bottom: 16px; padding: 12px 20px; color: var(--ink-2); font-size: 13px; }.case-no { color: var(--ink); font-size: 15px; font-weight: 700; }.new-case { margin-left: auto; }
.consult-layout { display: grid; grid-template-columns: minmax(0, 1fr) 460px; gap: 22px; align-items: start; }.work-area { min-width: 0; }.search-help { margin: 10px 0 24px; color: var(--muted); font-size: 13px; }.recommendations { display: flex; flex-direction: column; gap: 12px; }.section-head { display: flex; align-items: end; justify-content: space-between; gap: 16px; }.section-head h2, .prescription h2, .welcome h2 { font-size: 17px; }.recommendation { overflow: hidden; }.recommendation.blocked { border-color: #edc5c0; }.recommendation-title { display: flex; justify-content: space-between; align-items: start; gap: 16px; padding: 16px 18px 12px; }.recommendation-title h3, .rx-title h3 { margin: 0; font-size: 16px; }.recommendation-title p, .rx-title p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }.recommendation-action { display: flex; flex-direction: column; align-items: end; gap: 5px; }.blocked-btn { border-color: var(--line-strong); color: var(--muted); background: #f6f8fa; }.harvest-hint { display: inline-flex; align-items: center; gap: 4px; color: var(--warn); font-size: 11px; white-space: nowrap; }.facts-row { display: flex; padding: 0 18px 13px; }.facts-row span { flex: 1; padding-right: 12px; color: var(--muted); font-size: 12px; }.facts-row b { display: block; color: var(--ink); font-size: 15px; }.recommendation .pills { padding: 10px 18px; border-top: 1px solid var(--line); background: #fafbf9; }.welcome { display: flex; align-items: start; gap: 14px; padding: 24px; color: var(--brand); }.welcome p { margin-top: 5px; color: var(--ink-2); font-size: 14px; line-height: 1.75; }.empty { padding: 18px; color: var(--muted); font-size: 14px; }
.prescription { position: sticky; top: 16px; overflow: hidden; }.prescription-head { background: var(--brand-soft); }.rx-empty { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 42px 24px; color: var(--muted); text-align: center; font-size: 14px; }.rx-item { padding: 15px 18px; border-bottom: 1px solid var(--line); }.rx-title { display: flex; align-items: start; justify-content: space-between; gap: 8px; }.icon-button { display: grid; place-items: center; border: 0; background: transparent; color: var(--muted); cursor: pointer; padding: 3px; }.icon-button:hover { color: var(--crit); }.rx-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }.rx-fields label { font-size: 11px; }.rx-fields label > span { display: flex; align-items: center; gap: 5px; }.modified-tag { display: inline-flex; align-items: center; height: 18px; padding: 0 6px; border-radius: 999px; background: var(--warn-soft); color: var(--warn); font-size: 10px; font-style: normal; font-weight: 700; }.rx-fields input, .rx-fields textarea { padding: 6px 8px; font-size: 13px; }.rx-fields .wide { grid-column: 1 / -1; }.check-line { display: flex; gap: 6px; align-items: start; padding: 8px 10px; border-radius: 8px; font-size: 12px; line-height: 1.5; }.check-line.ok { color: var(--kdais-ink); background: var(--kdais-soft); }.check-line.warning { color: #6d4506; background: var(--warn-soft); }.check-line.block { color: #7a2119; background: var(--crit-soft); }.rx-actions { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 10px; color: var(--muted); font-size: 12px; }.rx-actions b { color: var(--ink-2); }.rx-buttons { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
@media (max-width: 1120px) { .consult-layout { grid-template-columns: minmax(0, 1fr); }.prescription { position: static; }.case-create { margin-top: 0; } }
@media (max-width: 660px) { .create-form { grid-template-columns: 1fr; }.casebar { align-items: start; flex-direction: column; gap: 5px; }.new-case { margin: 8px 0 0; }.recommendation-title { flex-direction: column; }.recommendation-title .btn { width: 100%; }.facts-row span { min-width: 0; }.facts-row { overflow-x: auto; }.facts-row span { min-width: 110px; }.case-create { margin: 0; } }
</style>
