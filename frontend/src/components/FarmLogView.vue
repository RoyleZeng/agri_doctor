<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  planning,
  type Farmer,
  type LogFormMode,
  type LogKind,
  type LogSummary,
  type Plan,
  type PrescriptionOption,
  type UnloggedEvent,
  type WorkLog,
} from '@/api/planning'
import { today } from '@/composables/farmDates'
import {
  LOG_KINDS,
  dayHeading,
  daySubtitle,
  groupByDay,
  isMonth,
  matchesFilters,
  monthLabel,
  monthRange,
  recentMonths,
  upsertLog,
} from '@/composables/farmLog'
import { toast } from '@/composables/toast'
import ComplianceMeter from './ComplianceMeter.vue'
import ExportDialog from './ExportDialog.vue'
import FarmModal from './FarmModal.vue'
import Icon from './Icon.vue'
import PlanCompare from './PlanCompare.vue'
import WorkLogCard from './WorkLogCard.vue'
import WorkLogQuickAdd from './WorkLogQuickAdd.vue'

const props = defineProps<{ farmer: Farmer; plans: Plan[]; user: Farmer }>()
const emit = defineEmits<{
  'plan-saved': [plan: Plan]
  'log-removed': [eventId: string]
  'restore-plan': [planId: string]
}>()

const route = useRoute()
const router = useRouter()
const todayIso = today()
const month = ref(isMonth(route.query.month) ? route.query.month : todayIso.slice(0, 7))
const kind = ref<LogKind | ''>('')
const field = ref('')
const logs = ref<WorkLog[]>([])
const total = ref(0)
const page = ref(1)
const summary = ref<LogSummary | null>(null)
const loading = ref(true)
const loadingMore = ref(false)
const error = ref('')
const highlight = ref(typeof route.query.log === 'string' ? route.query.log : '')
let pendingFocus = highlight.value
const quick = ref<{ key: number; title: string; mode: LogFormMode } | null>(null)
let quickSeq = 0
const removeTarget = ref<WorkLog | null>(null)
const removing = ref(false)
const rxOpen = ref(false)
const rxLoading = ref(false)
const rxError = ref('')
const rxOptions = ref<PrescriptionOption[]>([])
const announced = new Set<string>()
const exportOpen = ref(false)

const groups = computed(() => groupByDay(logs.value))
const months = computed(() => {
  const list = recentMonths(todayIso, 13)
  return list.includes(month.value) ? list : [month.value, ...list]
})
const kindChips = computed(() =>
  LOG_KINDS.filter((item) => (summary.value?.by_kind[item.key] ?? 0) > 0 || item.key === kind.value),
)
const fields = computed(() => summary.value?.fields ?? [])
const activePlans = computed(() => props.plans.filter((plan) => !plan.archived))
const yields = computed(() => summary.value?.yields ?? [])
const number = (value: number) => value.toLocaleString('zh-TW', { maximumFractionDigits: 3 })

let sequence = 0
async function load(quiet = false) {
  const mine = ++sequence
  if (!quiet) {
    loading.value = true
    error.value = ''
  }
  try {
    const [list, monthly] = await Promise.all([
      planning.logs(props.farmer.id, { ...monthRange(month.value), kind: kind.value, field: field.value, page: 1 }),
      planning.logSummary(props.farmer.id, month.value),
    ])
    if (mine !== sequence) return
    logs.value = list.items
    total.value = list.total
    page.value = 1
    summary.value = monthly
    loading.value = false
    if (pendingFocus) {
      const id = pendingFocus
      pendingFocus = ''
      await focusLog(id)
    }
  } catch (e) {
    if (mine !== sequence) return
    if (quiet) toast.error((e as Error).message)
    else error.value = (e as Error).message
  } finally {
    if (mine === sequence) loading.value = false
  }
}

async function loadMore() {
  loadingMore.value = true
  try {
    const next = await planning.logs(props.farmer.id, {
      ...monthRange(month.value), kind: kind.value, field: field.value, page: page.value + 1,
    })
    page.value = next.page
    total.value = next.total
    const known = new Set(logs.value.map((log) => log.id))
    logs.value = [...logs.value, ...next.items.filter((log) => !known.has(log.id))]
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    loadingMore.value = false
  }
}

async function refreshSummary() {
  try {
    summary.value = await planning.logSummary(props.farmer.id, month.value)
  } catch {
    /* 摘要慢一步沒關係，列表已經是對的；下次載入會補上。 */
  }
}

watch([month, kind, field], () => void load(), { immediate: true })
watch(month, (value) => {
  // 月份放進網址，重新整理或分享連結時停在同一個月。
  if (route.query.month !== value) void router.replace({ query: { ...route.query, month: value, log: undefined } })
})
watch(
  () => route.query.rx,
  (rx) => {
    if (typeof rx !== 'string' || !rx) return
    openQuick('帶入處方記錄', { type: 'prefill', prescription_item_id: rx })
    void router.replace({ query: { ...route.query, rx: undefined } })
  },
  { immediate: true },
)

async function focusLog(id: string) {
  highlight.value = id
  await nextTick()
  const element = document.getElementById(`log-${id}`)
  if (element) element.scrollIntoView({ behavior: 'smooth', block: 'center' })
  else toast.info('這筆紀錄不在目前列表中，可能已被刪除，或在「再載入」之後。')
}

function focusFromMeter(id: string) {
  if (kind.value || field.value) {
    // 篩選中看不到那筆紀錄；清掉篩選重新載入後再定位。
    pendingFocus = id
    kind.value = ''
    field.value = ''
  } else void focusLog(id)
}

/** 匯出提醒裡點「補填」：紀錄可能在別的月份，先切到那個月再定位。 */
function focusIncomplete(item: { id: string; done_on: string }) {
  exportOpen.value = false
  const target = item.done_on.slice(0, 7)
  if (target === month.value) {
    focusFromMeter(item.id)
    return
  }
  pendingFocus = item.id
  kind.value = ''
  field.value = ''
  month.value = target
}

function openQuick(title: string, mode: LogFormMode) {
  quick.value = { key: ++quickSeq, title, mode }
}
const newLog = () =>
  openQuick('記一筆', { type: 'new', plan_id: activePlans.value.length === 1 ? activePlans.value[0].id : undefined })
const edit = (log: WorkLog) => openQuick(log.locked ? '修改備註' : '修改農事紀錄', { type: 'edit', log })
const backfill = (log: WorkLog) => openQuick('補選藥劑登記', { type: 'edit', log, backfill: true })
const recordEvent = (item: UnloggedEvent) => openQuick('補記規劃事項', { type: 'prefill', event_id: item.event_id })

function onSaved(saved: WorkLog) {
  const { plan, ...rest } = saved
  const log = rest as WorkLog
  if (plan) emit('plan-saved', plan)
  const existed = logs.value.some((item) => item.id === log.id)
  if (matchesFilters(log, { month: month.value, kind: kind.value, field: field.value })) {
    // 存檔後就地插入，不整頁重載；其他卡片上的狀態（展開的照片、捲動位置）都保留。
    logs.value = upsertLog(logs.value, log)
    if (!existed) total.value += 1
    highlight.value = log.id
  } else if (existed) {
    logs.value = logs.value.filter((item) => item.id !== log.id)
    total.value -= 1
  }
  if (!announced.has(log.id)) {
    announced.add(log.id)
    const elsewhere = !matchesFilters(log, { month: month.value, kind: '', field: '' })
    toast.ok(elsewhere ? `已儲存到 ${monthLabel(log.done_on.slice(0, 7))}的農事日誌` : '已儲存到農事日誌')
  }
  void refreshSummary()
}

function onUpdated(log: WorkLog) {
  logs.value = upsertLog(logs.value, log)
  void refreshSummary()
}

function onRestorePlan(planId: string) {
  quick.value = null
  emit('restore-plan', planId)
}

async function confirmRemove() {
  const target = removeTarget.value
  if (!target) return
  removing.value = true
  try {
    await planning.deleteLog(target.id)
    logs.value = logs.value.filter((log) => log.id !== target.id)
    total.value -= 1
    removeTarget.value = null
    if (target.event_id) emit('log-removed', target.event_id)
    toast.ok(target.event_id ? '已刪除紀錄；規劃事項維持已完成，會顯示為「未記錄」' : '已刪除紀錄')
    void refreshSummary()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    removing.value = false
  }
}

async function openPrescriptions() {
  rxOpen.value = true
  rxLoading.value = true
  rxError.value = ''
  try {
    rxOptions.value = await planning.prescriptions(props.farmer.id)
  } catch (e) {
    rxError.value = (e as Error).message
  } finally {
    rxLoading.value = false
  }
}

function chooseRx(item: PrescriptionOption) {
  rxOpen.value = false
  openQuick('帶入處方記錄', { type: 'prefill', prescription_item_id: item.id })
}
</script>

<template>
  <div class="flog">
    <div class="flog-head">
      <div>
        <h2>農事日誌</h2>
        <p>記錄實際做了什麼，跟規劃分開看。施藥紀錄會提供給抗藥性輪替檢查。</p>
      </div>
      <div class="flog-actions">
        <button class="btn" @click="exportOpen = true"><Icon name="download" :size="16" />匯出農事紀錄</button>
        <button class="btn" @click="openPrescriptions"><Icon name="file" :size="16" />帶入處方</button>
        <button class="btn primary" @click="newLog"><Icon name="plus" :size="16" />記一筆</button>
      </div>
    </div>

    <div class="flog-bar">
      <div class="chips" role="group" aria-label="依作業類型篩選">
        <button type="button" class="chip" :class="{ on: !kind }" :aria-pressed="!kind" @click="kind = ''">
          全部 <span class="cnt">{{ summary?.total ?? 0 }}</span>
        </button>
        <button
          v-for="item in kindChips"
          :key="item.key"
          type="button"
          class="chip"
          :class="{ on: kind === item.key }"
          :aria-pressed="kind === item.key"
          @click="kind = item.key"
        >
          {{ item.name }} <span class="cnt">{{ summary?.by_kind[item.key] ?? 0 }}</span>
        </button>
      </div>
      <div class="flog-selects">
        <select v-model="field" aria-label="田區">
          <option value="">全部田區</option>
          <option v-for="name in fields" :key="name" :value="name">{{ name }}</option>
        </select>
        <select v-model="month" aria-label="月份">
          <option v-for="item in months" :key="item" :value="item">{{ monthLabel(item) }}</option>
        </select>
      </div>
    </div>

    <div class="flog-layout">
      <div class="flog-list">
        <div v-if="loading" class="farm-loading" role="status">
          <Icon name="note" :size="26" />
          <p>正在載入農事紀錄⋯</p>
        </div>
        <section v-else-if="error" class="card farm-empty" role="alert">
          <Icon name="warn" :size="32" />
          <h2>暫時無法載入農事日誌</h2>
          <p>{{ error }}</p>
          <button class="btn primary" @click="load()">重新載入</button>
        </section>
        <section v-else-if="!logs.length" class="card farm-empty">
          <Icon name="note" :size="34" />
          <h2>{{ kind || field ? '沒有符合篩選的紀錄' : `${monthLabel(month)}還沒有農事紀錄` }}</h2>
          <p>施藥、施肥、採收當天記下來，之後匯出產銷履歷紀錄表就不用回想補填。</p>
          <button class="btn primary" @click="newLog">＋ 記一筆</button>
        </section>
        <template v-else>
          <section v-for="group in groups" :key="group.date" class="flog-day">
            <div class="flog-dh">
              <b>{{ dayHeading(group.date) }}</b><span>{{ daySubtitle(group, todayIso) }}</span>
            </div>
            <div class="card flog-card">
              <WorkLogCard
                v-for="log in group.items"
                :key="log.id"
                :log="log"
                :user-id="user.id"
                :admin="user.role === 'admin'"
                :highlighted="highlight === log.id"
                @edit="edit"
                @remove="removeTarget = $event"
                @backfill="backfill"
                @updated="onUpdated"
                @refresh="load(true)"
              />
            </div>
          </section>
          <div v-if="logs.length < total" class="flog-more">
            <button class="btn" :disabled="loadingMore" @click="loadMore">
              {{ loadingMore ? '載入中⋯' : `再載入（還有 ${total - logs.length} 筆）` }}
            </button>
          </div>
        </template>
      </div>

      <aside class="flog-side">
        <section class="card">
          <div class="card-h"><span class="title">本月摘要</span><span class="small muted">{{ monthLabel(month) }}</span></div>
          <div class="card-b">
            <div class="flog-sum">
              <div class="c"><div class="k">紀錄筆數</div><div class="v">{{ summary?.total ?? '—' }}</div></div>
              <div class="c"><div class="k">照片</div><div class="v">{{ summary?.photo_count ?? '—' }}<small>張</small></div></div>
              <div class="c"><div class="k">施藥</div><div class="v">{{ summary?.spray_count ?? '—' }}<small>次</small></div></div>
              <div class="c">
                <div class="k">採收產量</div>
                <div class="v">{{ yields.length ? number(yields[0].total) : '—' }}<small>{{ yields[0]?.unit }}</small></div>
              </div>
            </div>
            <p v-if="yields.length > 1" class="flog-note">
              另有 {{ yields.slice(1).map((item) => `${number(item.total)} ${item.unit}`).join('、') }}
            </p>
            <p v-if="summary?.unparsed_yields" class="flog-note">
              {{ summary.unparsed_yields }} 筆產量不是純數字（例如「約兩籃」），沒有列入加總。
            </p>
            <ComplianceMeter
              v-if="summary"
              class="flog-meter"
              :total="summary.total"
              :compliance="summary.compliance"
              @focus="focusFromMeter"
            />
          </div>
        </section>
        <section v-if="summary" class="card">
          <div class="card-h"><span class="title">與規劃對照</span><span class="small muted">{{ monthLabel(month) }}</span></div>
          <div class="card-b">
            <PlanCompare :compare="summary.plan_compare" :can-record="true" @record="recordEvent" />
          </div>
        </section>
        <section class="card">
          <div class="card-h"><span class="title">匯出農事紀錄</span></div>
          <div class="card-b">
            <div class="flog-opt"><span>期間</span><span>{{ monthLabel(month) }}</span></div>
            <div class="flog-opt"><span>田區</span><span>{{ field || `全部（${fields.length} 區）` }}</span></div>
            <div class="flog-opt"><span>格式</span><span>PDF 紀錄表／Excel</span></div>
            <p class="flog-note">欄位依產銷履歷驗證基準（作業時間、資材品名／供應者／批號、用量、場區、面積、產量）；紀錄保存 3 年以上。</p>
            <button class="btn primary flog-export" @click="exportOpen = true">
              <Icon name="download" :size="16" />設定並產出紀錄表
            </button>
          </div>
        </section>
      </aside>
    </div>

    <WorkLogQuickAdd
      v-if="quick"
      :key="quick.key"
      :title="quick.title"
      :farmer-id="farmer.id"
      :plans="plans"
      :fields="fields"
      :mode="quick.mode"
      @saved="onSaved"
      @close="quick = null"
      @restore-plan="onRestorePlan"
    />
    <ExportDialog
      v-if="exportOpen"
      :farmer="farmer"
      :month="month"
      :fields="fields"
      :field="field"
      @close="exportOpen = false"
      @focus="focusIncomplete"
    />
    <FarmModal v-if="removeTarget" title="刪除農事紀錄" @close="!removing && (removeTarget = null)">
      <div class="farm-form">
        <p>確定刪除「{{ removeTarget.title }}」？紀錄與照片會一起刪除。</p>
        <p v-if="removeTarget.kind === 'spray'" class="form-hint">對應的施藥紀錄也會移除，輪替檢查不再讀到這次施藥。</p>
        <p v-if="removeTarget.event_id" class="form-hint">規劃事項會維持已完成，並顯示「未記錄」，之後可以補記。</p>
        <div class="form-actions">
          <button class="btn" :disabled="removing" @click="removeTarget = null">取消</button>
          <button class="btn primary" :disabled="removing" @click="confirmRemove">{{ removing ? '刪除中⋯' : '確認刪除' }}</button>
        </div>
      </div>
    </FarmModal>
    <FarmModal v-if="rxOpen" title="帶入處方" wide @close="rxOpen = false">
      <div class="farm-form">
        <p class="form-hint">只列出植物醫生已儲存或已開立的處方；草稿還會修改，不能記成已執行。</p>
        <p v-if="rxLoading" class="quiet-state">正在載入處方⋯</p>
        <p v-else-if="rxError" class="form-error" role="alert">{{ rxError }}</p>
        <p v-else-if="!rxOptions.length" class="quiet-state">目前沒有可帶入的處方。</p>
        <div v-else class="flog-rx">
          <button v-for="item in rxOptions" :key="item.id" type="button" class="flog-rx-row" @click="chooseRx(item)">
            <span class="main">
              <b>{{ item.product_text }}</b>
              <small
                >處方 {{ item.case_no }} · {{ item.crop_text }}{{ item.field_text ? ` · ${item.field_text}` : '' }} · 稀釋
                {{ item.dilution || '—' }} · 採收期 {{ item.phi_days === null ? '未載明' : `${item.phi_days} 天` }}</small
              >
            </span>
            <span class="state">
              <span class="tag" :class="item.status === 'issued' ? 'ok' : 'info'">{{ item.status === 'issued' ? '已開立' : '已儲存' }}</span>
              <small v-if="item.logged_count">已記錄 {{ item.logged_count }} 次 · 最近 {{ item.last_logged_on }}</small>
            </span>
          </button>
        </div>
      </div>
    </FarmModal>
  </div>
</template>

<style scoped>
.flog-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; margin-bottom: 14px; }
.flog-head h2 { font-size: 20px; }
.flog-head p { margin-top: 2px; color: var(--farm-muted); font-size: 13px; }
.flog-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; }
.flog-bar { display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 16px; }
.flog-bar .chip { font: inherit; font-size: 14px; }
.flog-selects { margin-left: auto; display: flex; gap: 8px; }
.flog-layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 22px; align-items: start; }
.flog-list { min-width: 0; }
.flog-day { margin-bottom: 18px; }
.flog-dh { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.flog-dh b { font-size: 15px; }
.flog-dh span { font-size: 12px; color: var(--muted); }
.flog-card { overflow: hidden; }
.flog-more { padding: 6px; text-align: center; }
.flog-side { position: sticky; top: 16px; display: flex; flex-direction: column; gap: 16px; }
.flog-sum { display: grid; grid-template-columns: repeat(2, 1fr); gap: 8px; }
.flog-sum .c { padding: 10px 12px; border-radius: 10px; background: #fafbf9; border: 1px solid var(--line); }
.flog-sum .k { font-size: 12px; color: var(--muted); }
.flog-sum .v { font-size: 20px; font-weight: 700; line-height: 1.2; font-variant-numeric: tabular-nums; }
.flog-sum .v small { margin-left: 2px; font-size: 12px; font-weight: 500; color: var(--muted); }
.flog-note { margin: 8px 0 0; font-size: 12px; color: var(--muted); }
.flog-meter { margin-top: 14px; }
.flog-opt { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 14px; }
.flog-opt span:last-child { color: var(--ink-2); font-size: 13px; text-align: right; }
.flog-export { width: 100%; margin-top: 12px; }
.flog-rx { display: flex; flex-direction: column; gap: 8px; }
.flog-rx-row { display: flex; align-items: center; gap: 12px; width: 100%; padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px; background: #fff; text-align: left; color: var(--ink); font: inherit; }
.flog-rx-row:hover { border-color: var(--brand); background: var(--brand-soft); }
.flog-rx-row .main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.flog-rx-row .main small { font-size: 12px; color: var(--muted); }
.flog-rx-row .state { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.flog-rx-row .state small { font-size: 11px; color: var(--muted); white-space: nowrap; }
@media (max-width: 1100px) {
  .flog-layout { grid-template-columns: minmax(0, 1fr); }
  .flog-side { position: static; }
}
@media (max-width: 660px) {
  .flog { padding-bottom: 76px; }
  .flog-head { flex-direction: column; align-items: stretch; }
  /* 手機上「記一筆」固定在拇指範圍，田邊單手就能開始記錄。 */
  .flog-actions .primary { position: fixed; left: 16px; right: 16px; bottom: calc(16px + env(safe-area-inset-bottom)); z-index: 20; height: 52px; font-size: 16px; box-shadow: 0 10px 30px #142a2940; }
  .flog-bar .chips { flex-wrap: nowrap; overflow-x: auto; width: 100%; padding-bottom: 2px; }
  .flog-bar .chip { flex: none; }
  .flog-selects { margin-left: 0; width: 100%; }
  .flog-selects select { flex: 1; min-width: 0; }
  .flog-rx-row { flex-direction: column; align-items: stretch; }
  .flog-rx-row .state { flex-direction: row; align-items: center; }
}
</style>
