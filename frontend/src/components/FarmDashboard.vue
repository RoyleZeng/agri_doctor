<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import {
  planning,
  type Catalog,
  type Farmer,
  type FarmEvent,
  type Kind,
  type Plan,
  type WorkLog,
  type Workspace,
} from '@/api/planning'
import FarmEventForm from '@/components/FarmEventForm.vue'
import FarmLogView from '@/components/FarmLogView.vue'
import PrecisionView from '@/components/PrecisionView.vue'
import FarmModal from '@/components/FarmModal.vue'
import FarmPlanForm from '@/components/FarmPlanForm.vue'
import FarmTimeline from '@/components/FarmTimeline.vue'
import FarmWeather from '@/components/FarmWeather.vue'
import FarmEconomics from '@/components/FarmEconomics.vue'
import Icon from '@/components/Icon.vue'
import { kinds, plus, shortDate, stageOf, today } from '@/composables/farmDates'
import AppBanner from '@/components/AppBanner.vue'
import PlanVsActualPanel from '@/components/PlanVsActualPanel.vue'
import RecordDrawer from '@/components/RecordDrawer.vue'
import {
  bannerId,
  pruneDismissals,
  visibleBanners,
  type Banner,
  type Dismissals,
} from '@/composables/banners'
import { MAX_LOG_PAGES, createRecordFlow } from '@/composables/recordFlow'
import { LANES, PRE_PLANT_MAX, logsForPlan, matchRegionAlert, placeLogs, type LaneKey } from '@/composables/timelineLanes'
import { toast } from '@/composables/toast'
import { LANE_KINDS, visibleKinds as kindsOfLanes, workspaceView } from '@/composables/workspaceView'

/**
 * 工作區儀表板：複合圖＋下方卡片。農民看自己的（`/farm`）與植物醫生看農友的
 * （`/farmers/:id`）是同一份畫面，差別只在 `owner`，所以抽成元件由兩個 view 共用。
 *
 * 檢視設定（已選作物、時程／清單、縮放、色帶）放在 `composables/workspaceView`，
 * 元件重建也不會歸零——切農民不該讓人重挑一次。
 */
const props = defineProps<{
  workspace: Workspace
  user: Farmer | null
  catalog: Catalog | null
  demo: boolean
  owner: boolean
  tab: 'plan' | 'log' | 'precision'
}>()
const emit = defineEmits<{ 'update:workspace': [workspace: Workspace]; reload: [] }>()

const router = useRouter()

const busy = ref(false)
const eventEdit = ref<{ plan: Plan; event?: FarmEvent; record?: boolean } | null>(null)
const sourceOpen = ref(false)
const archiveTarget = ref<Plan | null>(null)
const note = ref('')
const tasksMode = ref<'week' | 'overdue' | 'all'>('week')

/** 有登入、且不是示範資料，才記得了日誌。 */
const canRecord = computed(() => !!props.user && !props.demo)
const workspace = computed(() => props.workspace)
const user = computed(() => props.user)
const catalog = computed(() => props.catalog)
const demo = computed(() => props.demo)
const owner = computed(() => props.owner)
const tab = computed(() => props.tab)
const selectedIds = computed({
  get: () => workspaceView.selectedIds,
  set: (v: string[]) => (workspaceView.selectedIds = v),
})
const showArchived = computed({
  get: () => workspaceView.showArchived,
  set: (v: boolean) => (workspaceView.showArchived = v),
})
const view = computed({
  get: () => workspaceView.view,
  set: (v: 'timeline' | 'list') => (workspaceView.view = v),
})
const zoom = computed({
  get: () => workspaceView.zoom,
  set: (v: 'month' | 'week') => (workspaceView.zoom = v),
})
const visibleLanes = computed(() => workspaceView.visibleLanes)
/** 匯出 SVG 與「事項清單」檢視仍以 kind 為單位（後端端點沒改），由色帶換算。 */
const visibleKinds = computed(() => kindsOfLanes())
const planForm = computed({
  get: () => workspaceView.planFormOpen,
  set: (v: boolean) => (workspaceView.planFormOpen = v),
})

const emoji = (p: Plan) => props.catalog?.crops.find((c) => c.key === p.crop_key)?.emoji || '🌱'

function selectPlan(id: string) {
  workspaceView.selectedIds = workspaceView.selectedIds.includes(id)
    ? workspaceView.selectedIds.filter((s) => s !== id)
    : [...workspaceView.selectedIds, id]
}

function toggleLane(lane: LaneKey) {
  workspaceView.visibleLanes = workspaceView.visibleLanes.includes(lane)
    ? workspaceView.visibleLanes.filter((k) => k !== lane)
    : [...workspaceView.visibleLanes, lane]
}

function chooseEventPlan(event: Event) {
  const select = event.target as HTMLSelectElement
  const plan = chosen.value.find(p => p.id === select.value)
  if (plan) eventEdit.value = { plan }
  select.value = ''
}

function savedPlan(p: Plan) {
  if (!props.workspace) return
  const index = props.workspace.plans.findIndex((v) => v.id === p.id)
  if (index < 0) {
    props.workspace.plans.push(p)
    workspaceView.showArchived = false
    workspaceView.selectedIds.push(p.id)
  } else props.workspace.plans[index] = p
  planForm.value = false
  eventEdit.value = null
  archiveTarget.value = null
  void loadLogs()
  toast.ok('已儲存到農場規劃')
}

/** 只換規劃，不重抓日誌——抽屜存檔時已經拿到新的 log 了。 */
function replacePlanOnly(p: Plan) {
  if (!props.workspace) return
  const index = props.workspace.plans.findIndex((v) => v.id === p.id)
  if (index >= 0) props.workspace.plans[index] = p
}

function replacePlan(p: Plan) {
  if (!props.workspace) return
  const index = props.workspace.plans.findIndex((v) => v.id === p.id)
  if (index >= 0) props.workspace.plans[index] = p
  // 剛記完的那一筆要馬上出現在「實際作業」色帶；期間沒變，watch 不會自己重抓。
  void loadLogs()
}

function clearEventLog(eventId: string) {
  void loadLogs()
  for (const p of props.workspace?.plans ?? [])
    for (const e of p.events)
      if (e.id === eventId) {
        e.log_id = null
        e.log_done_on = null
      }
}

function requestRestore(planId?: string) {
  const plan = props.workspace?.plans.find((p) => p.id === planId)
  if (!plan) return
  if (!owner.value) {
    toast.info('這一期規劃已封存，請農民在「我的農場」恢復規劃後再記錄。')
    return
  }
  eventEdit.value = null
  archiveTarget.value = plan
}

/* ---- 就地記錄抽屜 ----
 * 第四輪回饋「點色塊跳到日誌頁很煩」：四個入口（色塊、膠囊、面板、接下來 7 天）
 * 全部改成在本頁開抽屜。只有抽屜裡的「在農事日誌中開啟」才離開這一頁。
 * 狀態與「存檔後不重抓」由可注入的 `createRecordFlow` 負責，測試釘住。
 */

/** 日誌頁的網址；農民看自己的、植物醫生看農友的。 */
const logPath = computed(() =>
  props.user?.id === workspace.value.farmer.id ? '/farm/log' : `/farmers/${workspace.value.farmer.id}/log`)

const drawerMode = computed(() => {
  const state = drawer.value
  if (!state) return null
  if (state.kind === 'view') return { type: 'view' as const, log: state.log }
  if (state.kind === 'record') return { type: 'prefill' as const, event_id: state.eventId, plan_id: state.plan.id }
  return { type: 'new' as const, plan_id: state.plan.id }
})
const drawerHref = computed(() =>
  drawer.value?.kind === 'view'
    ? `${logPath.value}?month=${drawer.value.log.done_on.slice(0, 7)}&log=${drawer.value.log.id}`
    : undefined)

/** 點膠囊或面板的「看紀錄」：唯讀抽屜。 */
function openWorkLog(log: WorkLog) {
  eventEdit.value = null
  flow.openView(log)
}

function openLog(e: FarmEvent) {
  const log = flow.logFor(e.log_id)
  if (log) return openWorkLog(log)
  // 日誌不在已抓的範圍內（期間外）時才退回日誌頁，不要假裝抽屜打得開。
  if (e.log_id) void router.push({ path: logPath.value, query: { month: e.log_done_on?.slice(0, 7), log: e.log_id } })
}

/** 點規劃色塊、面板的「記錄」或「接下來 7 天」：開抽屜記一筆並標完成。 */
function recordForEvent(plan: Plan, eventId: string) {
  eventEdit.value = null
  flow.openRecord(plan, eventId)
}

function recordUnplanned(plan: Plan) {
  eventEdit.value = null
  flow.openNew(plan)
}

/** 存檔後就地更新，不重抓；後端一併回傳的規劃塞回 workspace。 */
function drawerSaved(log: WorkLog) {
  const plan = flow.saved(log)
  if (plan) replacePlanOnly(plan as Plan)
  toast.ok('已記錄到農事日誌')
}

function complete(p: Plan, e: FarmEvent) {
  if (e.log_id) return openLog(e)
  // 完成與日誌一起存；讓本人確認實際日期，不能把規劃日期當成已發生的作業。
  if (demo.value) {
    eventEdit.value = { plan: p, event: e }
    return
  }
  recordForEvent(p, e.id)
}

async function archive() {
  if (!archiveTarget.value) return
  busy.value = true
  try {
    savedPlan(await planning.archive(archiveTarget.value))
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function saveNote() {
  if (!props.workspace || !note.value.trim()) return
  busy.value = true
  try {
    await planning.note(props.workspace.farmer.id, note.value.trim())
    note.value = ''
    const w = await planning.workspace(props.workspace.farmer.id)
    props.workspace.notes = w.notes
    toast.ok('紀錄已儲存，農民與已連結的植物醫生皆可查看')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function exportPlan() {
  if (!props.workspace) return
  busy.value = true
  try {
    const blob = await planning.diagram(
      props.workspace.farmer.id,
      chosen.value.map((p) => p.id),
      visibleKinds.value,
      demo.value,
    )
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = '農場作物管理複合圖.svg'
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
    toast.ok('已產生 SVG 圖表，可放大查看或用瀏覽器列印')
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

const taskItems = computed(() =>
  chosen.value
    .flatMap((p) =>
      p.events
        .filter((e) => e.kind === 'care' || e.kind === 'nutrition')
        .map((e) => ({ p, e })),
    )
    .sort(
      (a, b) =>
        Number(a.e.completed) - Number(b.e.completed) ||
        a.e.start_on.localeCompare(b.e.start_on),
    ),
)

const weekTasks = computed(() =>
  taskItems.value.filter(
    ({ e }) =>
      !e.completed && e.start_on <= plus(today(), 6) && e.end_on >= today(),
  ),
)

const overdue = computed(() =>
  taskItems.value.filter(({ e }) => !e.completed && e.end_on < today()),
)

const legacyCompleted = computed(() => chosen.value.flatMap(p => p.events).filter(e => ['care', 'nutrition'].includes(e.kind) && e.completed && !e.log_id).length)
const tasks = computed(() =>
  tasksMode.value === 'week'
    ? weekTasks.value
    : tasksMode.value === 'overdue'
      ? overdue.value
      : taskItems.value,
)

const attention = computed(() =>
  chosen.value.flatMap((p) =>
    p.events
      .filter(
        (e) =>
          e.kind === 'pest' && e.start_on <= today() && e.end_on >= today(),
      )
      .map((e) => ({ p, e })),
  ),
)

const allSources = computed(() => {
  const sources = chosen.value.flatMap((p) => p.sources)
  return [...new Map(sources.map((s) => [s.id + s.sha256, s])).values()]
})

const displayDate = computed(() =>
  new Intl.DateTimeFormat('zh-TW', {
    month: 'long',
    day: 'numeric',
    weekday: 'long',
    timeZone: 'Asia/Taipei',
  }).format(new Date()),
)

const visiblePlans = computed(
  () =>
    props.workspace?.plans.filter((p) => p.archived === workspaceView.showArchived) ||
    [],
)

const chosen = computed(() =>
  visiblePlans.value.filter((p) => workspaceView.selectedIds.includes(p.id)),
)

/* ── 實際作業色帶的來源：農事日誌 ───────────────────────────────
 * 複合圖 v3 多了一條「實際作業」。資料用既有的 `GET /farmers/{id}/logs?from&to`，
 * 範圍是目前看得到的規劃期間聯集，切作物不重抓（只有期間變了才重抓）。
 */
/* 抽屜狀態與日誌載入都交給可注入的 `createRecordFlow`：
 * 「存檔之後不重抓」是一件「沒有發生的事」，只有注入假 API 數呼叫次數才測得到
 * （`npm run test:record-flow`），所以那段邏輯不留在這個 .vue 裡。 */
const flow = createRecordFlow<Plan, WorkLog>(planning)
const logs = flow.logs
const logsTruncated = flow.truncated
const drawer = flow.drawer

const logRange = computed(() => {
  const plans = visiblePlans.value
  if (!plans.length) return null
  // 起點比最早的種植日再往前推：複合圖的橫軸會延伸到種植前，整地那類日誌得抓得到才畫得出來。
  return {
    from: plus(plans.map((p) => p.planted_on).sort()[0]!, -PRE_PLANT_MAX),
    to: plans.map((p) => p.end_on).sort().at(-1)!,
  }
})

const loadLogs = () =>
  flow.loadLogs(demo.value || !props.workspace ? null : props.workspace.farmer.id, logRange.value)

watch(
  () => [props.workspace?.farmer.id, logRange.value?.from, logRange.value?.to, tab.value].join('|'),
  () => {
    if (tab.value === 'plan') void loadLogs()
  },
  { immediate: true },
)

/** 圖與面板共用同一份配對結果（純函式，不另打 API）。 */
const laneLogs = computed(() =>
  logs.value.map((l) => ({
    id: l.id, kind: l.kind, title: l.title || l.kind_label, done_on: l.done_on,
    event_id: l.event_id, photo_count: l.photos.length,
    plan_id: l.plan_id, field_text: l.field_text,
  })))
const placedByPlan = computed(() => {
  const map = new Map<string, ReturnType<typeof placeLogs>>()
  for (const plan of chosen.value) map.set(plan.id, placeLogs(plan.events, logsForPlan(plan, laneLogs.value)))
  return map
})
const logsById = computed(() => new Map(logs.value.map((l) => [l.id, l])))

/** 指標列的「本週日誌」：最近 7 天（含今天）記了幾筆。 */
const weekLogs = computed(() => {
  const since = plus(today(), -6)
  return logs.value.filter((l) => l.done_on >= since && l.done_on <= today()).length
})

/**
 * 巡查提醒對上區域警戒：`region_alerts` 是上一輪就已經回傳、前端一直沒用的欄位。
 * 比對用病蟲害名稱字串——農場規劃的巡查事項沒有 pest_id，只有標題。
 */
const alertTexts = computed(() =>
  (props.workspace?.region_alerts ?? []).map((a) => a.pest_text).filter(Boolean),
)
const regionAlertFor = (e: FarmEvent) =>
  matchRegionAlert(props.workspace?.region_alerts ?? [], e.title)

/* ---- 區域警戒橫幅 ----
 * 只在「區域達警戒」**而且**「自己正好有對應的巡查窗口」時才出現。
 * 只有其中一個條件就不是這個農場現在要做的事，出現了只會變成每次都跳過的噪音。
 */
const alertedEvents = computed(() =>
  attention.value.filter(({ e }) => regionAlertFor(e)))

const regionBanner = computed<Banner | null>(() => {
  const hit = alertedEvents.value[0]
  if (!hit) return null
  const alert = regionAlertFor(hit.e)!
  const district = props.workspace?.farmer.location || '本鄉鎮'
  return {
    // 換一種病蟲害就是新的一條，不會被上一條的關閉狀態蓋掉。
    id: bannerId('warn', district, alert.pest_text),
    tone: 'warn',
    title: `${district}近期${alert.pest_text}通報已達警戒`,
    body: `您的${hit.p.crop_name}目前在「${hit.e.title}」的巡查窗口內（${alert.metric} ${alert.value} ≥ ${alert.warn_value}，${alert.basis}）。`
      + '這是區域彙整結果，不代表您的田區已發生；建議這幾天多看一次，有徵兆就記一筆巡田觀察。',
    dismissDays: 7,
  }
})

/* 關閉狀態存 localStorage；讀寫都包 try/catch，無痕視窗讀不到就當成沒關過。 */
const dismissals = ref<Dismissals>(readDismissals())
function readDismissals(): Dismissals {
  try {
    const raw = localStorage.getItem('banners')
    const parsed: unknown = raw ? JSON.parse(raw) : null
    return parsed && typeof parsed === 'object' ? pruneDismissals(parsed as Dismissals, Date.now()) : {}
  } catch {
    return {}
  }
}
function dismissBanner(banner: Banner) {
  dismissals.value = { ...dismissals.value, [banner.id]: Date.now() }
  try {
    localStorage.setItem('banners', JSON.stringify(dismissals.value))
  } catch {
    /* 存不了就只是這次不記得，不影響操作 */
  }
}
const banners = computed(() =>
  visibleBanners(regionBanner.value ? [regionBanner.value] : [], dismissals.value, Date.now()))

const laneLegend = LANES

// 農友切換後表單不能仍指向前一位；日誌與精準元件也用 farmer.id 隔離本地狀態。
watch(() => props.workspace.farmer.id, () => {
  eventEdit.value = null
  archiveTarget.value = null
  note.value = ''
})
</script>

<template>
      <div v-if="demo" class="demo-notice no-print">
        <span
          ><strong>操作範例</strong> ·
          田區與日期為示範資料；可點選圖表查看內容。</span
        ><router-link to="/farm">{{
          user ? '回到我的工作區 →' : '建立自己的農場 →'
        }}</router-link>
      </div>
      <template v-if="tab === 'plan'">
      <!-- 區域警戒：放在內容最上面，因為它是「今天要先知道的事」，不是圖表的附註。 -->
      <AppBanner
        v-for="banner in banners"
        :key="banner.id"
        :tone="banner.tone"
        :title="banner.title"
        :body="banner.body"
        dismissible
        @dismiss="dismissBanner(banner)"
      >
        <router-link class="btn" to="/region">看附近病蟲害</router-link>
      </AppBanner>
      <section v-if="chosen.length" class="farm-overview" aria-label="農場重點資訊">
        <div v-if="!demo" class="agri-workspace-links no-print">
          <router-link class="btn sm" :to="{path:'/agri-data',query:{dataset:'ImportantAgriculturalPestDiagnosticsType',q:chosen[0]?.crop_name}}">查病蟲害圖鑑與歷史案例</router-link>
          <router-link class="btn sm" :to="{path:'/agri-data',query:{dataset:'FertilizerIntegrationInfoType'}}">查肥料登記與成分</router-link>
          <router-link class="btn sm" :to="{path:'/agri-data',query:{dataset:'AutoWeatherStationType',q:workspace.farmer.weather_county || ''}}">查農業氣象觀測</router-link>
        </div>
        <FarmEconomics
          v-if="!demo"
          :key="workspace.farmer.id"
          :farmer-id="workspace.farmer.id"
          :crops="chosen.map(p => p.crop_name)"
        />
        <div class="farm-work-grid no-print">
          <section class="card todo-card">
            <div class="card-h">
              <div>
                <h2>接下來，照顧這些事</h2>
                <p>
                  {{ shortDate(today()) }} — {{ shortDate(plus(today(), 6)) }} ·
                  以已選作物為準
                </p>
              </div>
              <Icon name="calendar" :size="22" />
            </div>
            <div class="task-tabs">
              <button
                :class="{ active: tasksMode === 'week' }"
                @click="tasksMode = 'week'"
              >
                未來7天 <b>{{ weekTasks.length }}</b></button
              ><button
                :class="{ active: tasksMode === 'overdue' }"
                @click="tasksMode = 'overdue'"
              >
                逾期未記錄 <b>{{ overdue.length }}</b></button
              ><button
                :class="{ active: tasksMode === 'all' }"
                @click="tasksMode = 'all'"
              >
                全部作業
              </button>
            </div>
            <p v-if="legacyCompleted" class="source-note legacy-completed">{{ legacyCompleted }} 項舊作業已標完成但未記錄，請確認實際日期後補記。<button class="text-btn" @click="tasksMode = 'all'">查看全部作業</button></p>
            <div class="todo-scroll">
              <div v-for="{ p, e } in tasks" :key="e.id" class="todo-item">
                <button
                  class="complete-button"
                  :class="{ done: e.completed }"
                  :aria-label="`${e.log_id ? '查看紀錄' : '完成並記錄'}：${e.title}`"
                  :aria-pressed="e.completed"
                  :disabled="busy || showArchived"
                  @click="complete(p, e)"
                >
                  <Icon v-if="e.completed" name="check" :size="16" /></button
                ><button
                  class="todo-description"
                  @click="recordForEvent(p, e.id)"
                >
                  <strong :class="{ finished: e.completed }">{{
                    e.title
                  }}</strong
                  ><span
                    >{{ p.crop_name }} · {{ p.field_name }}
                    <small
                      >{{ shortDate(e.start_on) }}–{{
                        shortDate(e.end_on)
                      }}</small
                    ><span v-if="e.log_id" class="tag ok todo-tag">已記錄</span
                    ><span v-else-if="e.completed" class="tag warn todo-tag">未記錄</span></span
                  ></button
                ><Icon name="right" :size="14" />
              </div>
              <p v-if="!tasks.length" class="quiet-state">
                {{
                  tasksMode === 'week'
                    ? '未來7天沒有待辦，可查看完整時程或新增事項。'
                    : tasksMode === 'overdue'
                      ? '目前沒有逾期未記錄的作業。'
                      : '尚無管理事項，可從圖表下方新增。'
                }}
              </p>
            </div>
          </section>
          <section class="card patrol-card">
            <div class="card-h">
              <div>
                <h2><Icon name="bug" :size="18" />田間多留意</h2>
                <p>目前生育階段的巡查提醒</p>
              </div>
              <span class="tag warn">觀察用</span>
            </div>
            <div class="patrol-list">
              <button
                v-for="{ p, e } in attention"
                :key="e.id"
                @click="eventEdit = { plan: p, event: e }"
              >
                <span
                  ><strong
                    >{{ e.title
                    }}<span
                      v-if="regionAlertFor(e)"
                      class="tag alarm"
                      :title="`${regionAlertFor(e)?.metric}：${regionAlertFor(e)?.value} ≥ ${regionAlertFor(e)?.warn_value} · ${regionAlertFor(e)?.basis}`"
                      >附近已達警戒</span
                    ></strong
                  ><small>{{ p.crop_name }} · {{ p.field_name }}</small></span
                ><Icon name="right" :size="15" />
              </button>
              <p v-if="!attention.length" class="quiet-state">
                目前沒有對應的巡查提醒。無提醒不代表沒有病蟲害。
              </p>
            </div>
            <!-- 橫幅已經在講同一件事時就不重複；橫幅被關掉之後這行還在，資訊不會整個消失。 -->
            <p v-if="alertTexts.length && !banners.length" class="patrol-foot alarm-foot">
              本鄉鎮目前達警戒：{{ alertTexts.join('、') }}。這是區域彙整結果，不代表您的田區已發生。
            </p>
            <p class="patrol-foot">有異常時記錄徵兆與位置，請植物醫生確認。</p>
          </section>
          <section class="card wx-card">
            <FarmWeather
              compact
              :farmer="workspace.farmer"
              :plans="chosen"
              :owner="owner"
              :demo="demo"
              @saved="workspace.farmer = $event"
            />
          </section>
        </div>

      </section>
      <div class="farm-metrics">
        <div class="card metric">
          <span class="metric-icon green"><Icon name="leaf" :size="22" /></span>
          <div>
            <span>{{ showArchived ? '已封存規劃' : '農場作物規劃' }}</span>
            <p>
              {{ visiblePlans.length
              }}<small
                >期 /
                {{ new Set(visiblePlans.map((p) => p.crop_name)).size }}
                種作物</small
              >
            </p>
          </div>
        </div>
        <div class="card metric">
          <span class="metric-icon blue"
            ><Icon name="calendar" :size="22"
          /></span>
          <div>
            <span>未來7天待辦</span>
            <p>
              {{ weekTasks.length
              }}<small
                >項作業<template v-if="overdue.length">
                  · 逾期 {{ overdue.length }}</template
                ></small
              >
            </p>
          </div>
        </div>
        <div class="card metric">
          <span class="metric-icon amber"><Icon name="bug" :size="22" /></span>
          <div>
            <span>目前適合巡查</span>
            <p>{{ attention.length }}<small>項觀察 · 尚非發病通報</small></p>
          </div>
        </div>
        <div v-if="!demo" class="card metric">
          <span class="metric-icon violet"><Icon name="note" :size="22" /></span>
          <div>
            <span>本週農事紀錄</span>
            <p>{{ weekLogs }}<small>筆 · 近 7 天</small></p>
          </div>
        </div>
      </div>

      <section class="crop-chips">
        <div class="chip-row">
          <span class="chip-label">看哪些作物</span>
          <span v-for="p in visiblePlans" :key="p.id" class="chip-wrap">
          <button
            class="crop-chip"
            :class="{ on: selectedIds.includes(p.id) }"
            :aria-pressed="selectedIds.includes(p.id)"
            :title="`${p.field_name} · ${p.variety || '品種未填'} · ${stageOf(p)}`"
            @click="selectPlan(p.id)"
          >
            <span class="crop-symbol">{{ emoji(p) }}</span
            ><strong>{{ p.crop_name }}</strong
            ><small>{{ p.field_name }}</small>
          </button>
          <!-- 封存入口。第三輪把作物大卡換成 chip 時，這個動作連同大卡一起消失了，
               只剩沒人打得開的確認對話框；走查時才發現。 -->
          <button
            v-if="owner && selectedIds.includes(p.id)"
            class="chip-archive no-print"
            type="button"
            :title="`${p.archived ? '恢復' : '封存'}「${p.crop_name} · ${p.field_name}」這一期`"
            :aria-label="`${p.archived ? '恢復' : '封存'}${p.crop_name} ${p.field_name}`"
            @click="archiveTarget = p"
          >
            {{ p.archived ? '恢復' : '封存' }}
          </button>
          </span>
          <button
            v-if="visiblePlans.length"
            class="text-btn"
            @click="selectedIds = visiblePlans.map((p) => p.id)"
          >
            全選
          </button>
          <label class="check-label no-print"
            ><input v-model="showArchived" type="checkbox" />顯示封存</label
          >
        </div>
        <p v-if="chosen.length" class="chip-stage">
          <span v-for="p in chosen" :key="p.id"
            >{{ p.crop_name }} · {{ stageOf(p) }}</span
          >
        </p>
        <div v-if="!visiblePlans.length" class="card farm-empty">
          <Icon name="leaf" :size="36" />
          <h2>
            {{
              showArchived ? '還沒有封存的作物' : '從第一塊田、第一種作物開始'
            }}
          </h2>
          <p>
            {{
              owner
                ? '填入種植日，就能建立這一期的作物時程。'
                : '農民建立作物後，您就能在這裡一起管理。'
            }}
          </p>
          <button
            v-if="owner && !showArchived"
            class="btn primary"
            @click="planForm = true"
          >
            ＋ 建立第一份規劃
          </button>
        </div>
      </section>

      <template v-if="chosen.length">
        <section class="card farm-chart">
          <div class="chart-heading">
            <div>
              <span class="farm-eyebrow">一張圖，看懂每一塊田</span>
              <h2>
                作物管理複合圖 <span>{{ chosen.length }} 期作物</span>
              </h2>
            </div>
            <div class="chart-actions no-print">
              <div class="segmented">
                <button
                  :class="{ active: view === 'timeline' }"
                  @click="view = 'timeline'"
                >
                  <Icon name="calendar" :size="15" />時程圖</button
                ><button
                  :class="{ active: view === 'list' }"
                  @click="view = 'list'"
                >
                  <Icon name="menu" :size="15" />事項清單
                </button>
              </div>
              <button
                class="btn sm"
                :disabled="busy || !visibleLanes.length"
                @click="exportPlan"
              >
                <Icon name="download" :size="15" />匯出圖表
              </button>
            </div>
          </div>
          <div class="chart-controls">
            <div class="legend">
              <button
                v-for="l in laneLegend"
                :key="l.key"
                :class="[l.key, { off: !visibleLanes.includes(l.key) }]"
                :aria-pressed="visibleLanes.includes(l.key)"
                :aria-label="`${l.name}（${l.note}）`"
                :title="l.note"
                @click="toggleLane(l.key)"
              >
                <i />{{ l.name }}
              </button>
            </div>
            <label class="zoom-label no-print"
              >日期刻度<select v-model="zoom">
                <option value="month">30天</option>
                <option value="week">每週</option>
              </select></label
            >
          </div>
          <p class="chart-notice">{{ catalog?.notice }}</p>
          <p v-if="!visibleLanes.length" class="quiet-state">
            請至少選擇一條色帶。
          </p>
          <FarmTimeline
            v-else-if="view === 'timeline'"
            :plans="chosen"
            :logs="logs"
            :region-alerts="workspace.region_alerts || []"
            :visible-lanes="visibleLanes"
            :zoom="zoom"
            @select="(p, e) => (LANE_KINDS.planned.includes(e.kind) && !demo
              ? recordForEvent(p, e.id)
              : (eventEdit = { plan: p, event: e }))"
            @log="(_p, e) => openLog(e)"
            @open-log="openWorkLog"
          />
          <div v-else class="event-list-view">
            <div v-for="p in chosen" :key="p.id">
              <h3>{{ emoji(p) }} {{ p.crop_name }} · {{ p.field_name }}</h3>
              <button
                v-for="e in p.events.filter((e) =>
                  visibleKinds.includes(e.kind),
                )"
                :key="e.id"
                @click="eventEdit = { plan: p, event: e }"
              >
                <span class="list-kind" :class="e.kind">{{
                  kinds.find((k) => k.key === e.kind)?.name
                }}</span
                ><span
                  ><strong>{{ e.title }} {{ e.completed ? '✓' : '' }}<span v-if="e.log_id" class="tag ok todo-tag">已記錄</span
                    ><span v-else-if="e.completed && (e.kind === 'care' || e.kind === 'nutrition')" class="tag warn todo-tag">未記錄</span></strong
                  ><small>{{ e.start_on }} — {{ e.end_on }}</small></span
                ><Icon name="right" :size="16" />
              </button>
              <p v-if="!p.events.length" class="quiet-state">
                尚無事項，請新增自訂管理內容。
              </p>
            </div>
          </div>
          <p v-if="logsTruncated" class="chart-notice">
            這段期間的農事紀錄超過 {{ MAX_LOG_PAGES * 50 }} 筆，「實際作業」只畫得下其中一部分；完整紀錄請看農事日誌。
          </p>
          <div class="chart-bottom no-print">
            <button class="text-btn" @click="sourceOpen = true">
              <Icon name="file" :size="15" />查看參考資料與日期依據
            </button>
            <div v-if="!demo && !showArchived" class="inline-controls">
              <label for="event-plan" class="small">新增事項至</label
              ><select
                id="event-plan"
                aria-label="選擇要新增事項的作物"
                @change="chooseEventPlan"
              >
                <option value="">選擇田區與作物…</option>
                <option v-for="p in chosen" :key="p.id" :value="p.id">
                  {{ p.field_name }} · {{ p.crop_name }}
                </option>
              </select>
            </div>
          </div>
        </section>

        <!-- 規劃 vs 實際：跟複合圖用同一份配對結果（placedByPlan），不另打 API。 -->
        <PlanVsActualPanel
          v-if="!demo"
          :plans="chosen"
          :placed-by-plan="placedByPlan"
          :logs-by-id="logsById"
          @record="recordForEvent"
          @view="openWorkLog"
          @add="recordUnplanned"
        />

        <section v-if="!demo" class="card shared-notes no-print">
          <div class="card-h">
            <div>
              <h2>農場共同紀錄</h2>
              <p>農民與已連結的植物醫生都能看到，方便下次接續追蹤。</p>
            </div>
            <Icon name="note" :size="22" />
          </div>
          <div class="notes-layout">
            <form class="farm-form" @submit.prevent="saveNote">
              <label
                >新增田間觀察或追蹤建議<textarea
                  v-model="note"
                  required
                  maxlength="2000"
                  rows="3"
                  placeholder="例如：今日巡視溪畔一區，請下次留意北側稻株葉片。"
                /></label
              ><button class="btn primary" :disabled="busy || !note.trim()">
                {{ busy ? '處理中…' : '儲存共同紀錄' }}
              </button>
            </form>
            <div class="notes-feed">
              <article v-for="n in workspace.notes" :key="n.id">
                <div>
                  <strong>{{ n.author }}</strong
                  ><span
                    >{{ n.role === 'admin' ? '管理者' : n.role === 'doctor' ? '植物醫生工作區' : '農民' }} ·
                    {{
                      new Date(n.created_at * 1000).toLocaleString('zh-TW', {
                        timeZone: 'Asia/Taipei',
                      })
                    }}</span
                  >
                </div>
                <p>{{ n.body }}</p>
              </article>
              <p v-if="!workspace.notes.length" class="quiet-state">
                尚無共同紀錄，寫下這次的田間觀察吧。
              </p>
            </div>
          </div>
        </section>

      </template>
      <div v-else-if="visiblePlans.length" class="card farm-empty">
        <Icon name="leaf" :size="32" />
        <h2>選一種，或一次比較多種作物</h2>
        <p>點選上方作物卡片，即可查看待辦與複合時程。</p>
        <button
          class="btn primary"
          @click="selectedIds = visiblePlans.map((p) => p.id)"
        >
          選取全部作物
        </button>
      </div>
      </template>
      <FarmLogView
        v-else-if="tab === 'log' && user"
        :key="`log-${workspace.farmer.id}`"
        :farmer="workspace.farmer"
        :plans="workspace.plans"
        :user="user"
        @plan-saved="replacePlan"
        @log-removed="clearEventLog"
        @restore-plan="requestRestore"
      />
      <PrecisionView
        v-else-if="tab === 'precision' && user"
        :key="`precision-${workspace.farmer.id}`"
        :farmer="workspace.farmer"
        :user="user"
        :plans="workspace.plans"
        @saved="workspace.farmer = $event"
      />

    <RecordDrawer
      v-if="drawer && drawerMode"
      :farmer-id="workspace.farmer.id"
      :plans="workspace.plans"
      :mode="drawerMode"
      :title="drawer.title"
      :log-href="drawerHref"
      @close="drawer = null"
      @saved="drawerSaved"
      @restore-plan="requestRestore"
    />
    <FarmPlanForm
      v-if="owner && planForm && catalog"
      :catalog="catalog"
      :farmer-id="workspace.farmer.id"
      @close="planForm = false"
      @saved="savedPlan"
    />
    <FarmEventForm
      v-if="eventEdit"
      :plan="eventEdit.plan"
      :event="eventEdit.event"
      :initial-record="eventEdit.record"
      :readonly="demo || showArchived"
      :farmer-id="workspace.farmer.id"
      :can-record="canRecord"
      :can-restore="owner"
      @close="eventEdit = null"
      @saved="savedPlan"
      @plan-updated="replacePlan"
      @recorded="loadLogs"
      @restore="requestRestore(eventEdit?.plan.id)"
      @open-log="openLog"
    />
    <FarmModal
      v-if="archiveTarget"
      :title="archiveTarget.archived ? '恢復作物規劃' : '封存這一期作物'"
      @close="!busy && (archiveTarget = null)"
      ><div class="farm-form">
        <p>
          「{{ archiveTarget.field_name }} · {{ archiveTarget.crop_name }}」{{
            archiveTarget.archived
              ? '將回到作物清單，可繼續記錄。'
              : '封存後會從目前作物清單移出。時程與紀錄會保留，可隨時恢復。'
          }}
        </p>
        <div class="form-actions">
          <button class="btn" :disabled="busy" @click="archiveTarget = null">
            取消</button
          ><button class="btn primary" :disabled="busy" @click="archive">
            {{ archiveTarget.archived ? '恢復規劃' : '確認封存' }}
          </button>
        </div>
      </div></FarmModal
    >
    <FarmModal
      v-if="sourceOpen"
      title="資料來源與規劃依據"
      wide
      @close="sourceOpen = false"
      ><div class="farm-form">
        <div class="source-note">
          生育日期是範本估計，依本期長度換算，尚未依品種、期作、所在地或天氣校正。請以田間實際生育與植物醫生判讀調整。
        </div>
        <p>
          官方資料經整理成一般管理與巡查事項。舊文獻中的藥劑、劑量不轉入本規劃；用藥請回到平台的用藥查詢確認最新登記。
        </p>
        <article
          v-for="s in allSources"
          :key="s.id + s.sha256"
          class="source-entry"
        >
          <a :href="s.url" target="_blank" rel="noopener noreferrer"
            ><strong>{{ s.title }} ↗</strong></a
          >
          <p>{{ s.publisher }} · 抓取 {{ s.fetched_at.slice(0, 10) }}</p>
          <p>
            文獻發布日期：{{
              s.publication_date || '未結構化取得，請見原文'
            }}；抓取日不等於官方更新日。
          </p>
          <details>
            <summary>原始資料校驗碼</summary>
            <code>{{ s.sha256 }}</code>
          </details>
        </article>
        <p v-if="!allSources.length" class="form-hint">
          已選作物沒有官方範本來源，事項由使用者自行建立。
        </p>
      </div></FarmModal
    >
</template>

<style scoped>
/* v3 指標只作摘要，窄螢幕也不能堆成四張大卡把複合圖推到第三屏。 */
.farm-metrics { gap: 8px; }
.metric { padding: 10px 12px; gap: 9px; }
.metric-icon { width: 30px; height: 30px; border-radius: 9px; }
.metric p { font-size: 20px; }
.metric small { font-size: 11px; margin-left: 5px; }
@media (max-width: 760px) {
  .farm-metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); margin-bottom: 12px; }
  .metric { padding: 8px 10px; }
  .metric-icon { display: none; }
  .metric small { display: inline; font-size: 10px; white-space: normal; }
  .metric p { white-space: normal; line-height: 1.25; }
  .chart-heading { padding: 14px 16px; gap: 10px; }
  .chart-heading h2 { font-size: 18px; }
  .chart-controls { padding: 0 16px 10px; gap: 10px; }
}
</style>

<style scoped>
.agri-workspace-links { display:flex; flex-wrap:wrap; gap:8px; margin:14px 0; }
</style>
