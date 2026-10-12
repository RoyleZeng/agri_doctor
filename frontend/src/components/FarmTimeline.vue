<script setup lang="ts">
import { computed, ref } from 'vue'
import type { FarmEvent, Plan, WorkLog, RegionAlert } from '@/api/planning'
import { day, iso, shortDate, today } from '@/composables/farmDates'
import {
  LANES,
  matchRegionAlert,
  LANE_OF_KIND,
  axisStart,
  logsForPlan,
  isOverdue,
  logColor,
  placeLogs,
  stackRows,
  type LaneKey,
  type LaneLog,
  type PlacedLog,
} from '@/composables/timelineLanes'
import Icon from './Icon.vue'

/**
 * 複合圖 v3：每期作物固定四條色帶（生育時期／規劃事項／實際作業／病蟲害巡查）。
 *
 * 「實際作業」是這一版新加的，來源是農事日誌而不是規劃——農民要看的是
 * 「我實際做了什麼」對上「當初打算做什麼」，兩條上下對齊才看得出早了還是晚了。
 * 規則（該落哪條色帶、早晚幾天、算不算逾期）在 `composables/timelineLanes.ts`，離線可測。
 */
const props = defineProps<{
  plans: Plan[]
  logs: WorkLog[]
  regionAlerts?: RegionAlert[]
  visibleLanes: LaneKey[]
  zoom: 'month' | 'week'
}>()
const emit = defineEmits<{
  select: [plan: Plan, event: FarmEvent]
  log: [plan: Plan, event: FarmEvent]
  openLog: [log: WorkLog]
}>()
const scroll = ref<HTMLElement>()
const now = today()
const nearbyAlert = (event: FarmEvent) => event.kind === 'pest' && matchRegionAlert(props.regionAlerts ?? [], event.title)

// 橫軸往種植日之前延伸，整地與基肥才畫得進來（規則與上限在 timelineLanes.axisStart）。
const start = computed(() =>
  axisStart(
    props.plans.map((p) => day(p.planted_on)),
    props.logs.map((l) => day(l.done_on)).filter(Number.isFinite),
  ),
)
const end = computed(() => Math.max(...props.plans.map((p) => day(p.end_on))))
const span = computed(() => end.value - start.value + 1)
const step = computed(() => (props.zoom === 'week' ? 7 : 30))
const width = computed(() =>
  Math.max(
    850,
    Math.ceil(span.value / step.value) * (props.zoom === 'week' ? 100 : 160),
  ),
)
const ticks = computed(() =>
  Array.from({ length: Math.ceil(span.value / step.value) }, (_, i) => {
    const d = iso(start.value + i * step.value)
    return {
      date: d,
      label: shortDate(d),
      year: d.slice(0, 4),
      left: ((i * step.value) / span.value) * 100,
    }
  }),
)
const lanes = computed(() => LANES.filter((l) => props.visibleLanes.includes(l.key)))
const position = (e: FarmEvent) => ({
  left: `${((day(e.start_on) - start.value) / span.value) * 100}%`,
  width: `${((day(e.end_on) - day(e.start_on) + 1) / span.value) * 100}%`,
})
// 「已記錄」由是否有日誌推導；只標完成的作業標「未記錄」。色塊太窄放不下標籤時不畫，面板裡仍看得到。
const barPx = (e: FarmEvent) =>
  ((day(e.end_on) - day(e.start_on) + 1) / span.value) * width.value
const logTag = (e: FarmEvent) =>
  LANE_OF_KIND[e.kind] === 'planned' && e.completed && barPx(e) >= 110
    ? e.log_id ? 'logged' : 'unlogged'
    : null
const tagPosition = (e: FarmEvent) => ({
  left: `calc(${((day(e.end_on) - start.value + 1) / span.value) * 100}% - 5px)`,
})
const todayPosition = computed(
  () => ((day(now) - start.value + 0.5) / span.value) * 100,
)
const hasToday = computed(
  () => todayPosition.value >= 0 && todayPosition.value <= 100,
)
function jumpToday() {
  if (scroll.value)
    scroll.value.scrollTo({
      left: Math.max(
        0,
        (width.value * todayPosition.value) / 100 -
          scroll.value.clientWidth / 2 +
          148,
      ),
      behavior: 'smooth',
    })
}

/** 規劃事項排版：同一條色帶上時間重疊的事項往下排，避免互相蓋住。 */
function eventRows(plan: Plan, lane: LaneKey) {
  const rows: FarmEvent[][] = []
  for (const e of plan.events
    .filter((e) => LANE_OF_KIND[e.kind] === lane)
    .sort((a, b) => a.start_on.localeCompare(b.start_on))) {
    let row = rows.find((r) => r[r.length - 1].end_on < e.start_on)
    if (!row) {
      row = []
      rows.push(row)
    }
    row.push(e)
  }
  return rows
}

/** 配對規則在 `timelineLanes.logsForPlan`，跟「規劃 vs 實際」面板共用同一份。 */
function planLogs(plan: Plan): LaneLog[] {
  return logsForPlan(plan, props.logs.map((l) => ({
    id: l.id,
    kind: l.kind,
    title: l.title || l.kind_label,
    done_on: l.done_on,
    event_id: l.event_id,
    photo_count: l.photos.length,
    plan_id: l.plan_id,
    field_text: l.field_text,
  })))
}

interface Dot extends PlacedLog {
  left: number
  row: number
  source: WorkLog
}

/** 一期作物的所有日誌點（含要畫到巡查色帶的巡田），已算好位置與第幾列。 */
const dotsByPlan = computed(() => {
  const byId = new Map(props.logs.map((l) => [l.id, l]))
  const result = new Map<string, Dot[]>()
  for (const plan of props.plans) {
    const placed = placeLogs(plan.events, planLogs(plan))
    const lefts = placed.map(
      (p) => ((day(p.anchor) - start.value + 0.5) / span.value) * 100,
    )
    // 疊點用 px 判斷：同樣差 3 天，「每週」刻度分得開、「30 天」刻度會重疊。
    // 膠囊有文字，最小間距要比圓點大，不然兩顆會疊在一起看不出是兩筆。
    const rows = stackRows(
      lefts.map((l) => (l * width.value) / 100),
      96,
    )
    result.set(
      plan.id,
      placed.map((p, i) => ({
        ...p,
        left: lefts[i],
        row: rows[i],
        source: byId.get(p.log.id) as WorkLog,
      })),
    )
  }
  return result
})
const dots = (plan: Plan, lane: 'actual' | 'pest') =>
  (dotsByPlan.value.get(plan.id) ?? []).filter((d) => d.lane === lane)

const dotTitle = (d: Dot) =>
  [
    `${d.log.done_on}　${d.source.kind_label}`,
    d.log.title,
    d.event ? `對照規劃事項「${d.event.title}」· ${d.offsetText}` : '未對應規劃事項',
    d.photo ? `附 ${d.log.photo_count} 張照片` : '',
  ]
    .filter(Boolean)
    .join('\n')

/** 色帶高度：規劃事項看有幾列事項，實際作業看點疊了幾列。 */
function laneHeight(plan: Plan, lane: LaneKey) {
  if (lane === 'actual') {
    // 膠囊比圓點高也比較寬，色帶要跟著長高；規格要求至少 54px。
    const rows = dots(plan, 'actual')
    return Math.max(54, Math.max(1, Math.max(0, ...rows.map((d) => d.row + 1))) * 30 + 14)
  }
  const rows = Math.max(1, eventRows(plan, lane).length)
  const extra = lane === 'pest' ? Math.max(0, ...dots(plan, 'pest').map((d) => d.row + 1)) : 0
  return rows * 44 + extra * 22 + 12
}

const overdueEvent = (e: FarmEvent) => isOverdue(e, now)

/**
 * 每一期作物自己的「種植前」區段：從橫軸起點到這一期的種植日。
 *
 * 是「每期」不是「整張圖」——多種作物時軸起點取最早那一期，晚種的那期在前面那一大段
 * 本來就還沒下田，標出來比留白誠實。寬度為 0（軸起點就是種植日）時不畫。
 */
function prePlant(plan: Plan) {
  const days = day(plan.planted_on) - start.value
  if (days <= 0) return null
  return { width: `${(days / span.value) * 100}%` }
}
</script>
<template>
  <div class="timeline-help">
    <span class="legend-note"
      ><i class="swatch care" />農事<i class="swatch nutri" />肥培<i
        class="swatch dot-sample"
      />實際作業（來自日誌）<i class="swatch late" />逾期未記錄</span
    ><button class="btn sm" :disabled="!hasToday" @click="jumpToday">
      定位今天
    </button>
  </div>
  <div
    ref="scroll"
    class="timeline-scroll"
    tabindex="0"
    aria-label="作物複合時程圖，可左右捲動"
  >
    <div class="timeline" :style="{ width: `${width + 148}px` }">
      <div class="axis">
        <div class="row-label"><span class="label-text">田區 / 管理項目</span></div>
        <div class="track axis-track">
          <span
            v-for="t in ticks"
            :key="t.date"
            class="tick"
            :style="{ left: t.left + '%' }"
            ><small>{{ t.year }}</small
            >{{ t.label }}</span
          >
        </div>
      </div>
      <section
        v-for="plan in plans"
        :key="plan.id"
        class="plan-group"
        :aria-label="`${plan.field_name} ${plan.crop_name}時程`"
      >
        <div class="group-title">
          <strong>{{ plan.crop_name }}</strong
          ><span>{{ plan.field_name }} · {{ plan.variety || '未填品種' }}</span
          ><span class="period">{{ plan.planted_on }} — {{ plan.end_on }}</span>
        </div>
        <div v-for="lane in lanes" :key="lane.key" class="timeline-row">
          <div class="row-label">
            <Icon :name="lane.icon" :size="15" /><span class="label-text"
              >{{ lane.name }}<small>{{ lane.note }}</small></span
            >
          </div>
          <div
            class="track"
            :class="lane.key"
            :style="{ minHeight: `${laneHeight(plan, lane.key)}px` }"
          >
            <span
              v-if="prePlant(plan)"
              class="pre-plant"
              :class="{ stage: lane.key === 'stage' }"
              :style="prePlant(plan)!"
              ><small v-if="lane.key === lanes[0].key">種植前</small></span
            >
            <span
              v-for="t in ticks"
              :key="t.date"
              class="grid-line"
              :style="{ left: t.left + '%' }"
            />
            <div
              v-if="hasToday"
              class="today-line"
              :style="{ left: todayPosition + '%' }"
            >
              <span v-if="lane.key === lanes[0].key">今天</span>
            </div>
            <div
              v-for="(row, index) in eventRows(plan, lane.key)"
              :key="index"
              class="event-lane"
            >
              <template v-for="event in row" :key="event.id">
                <button
                  class="event-bar"
                  :class="[
                    event.kind,
                    {
                      completed: event.completed,
                      'has-tag': logTag(event),
                      late: overdueEvent(event),
                      alerted: !!nearbyAlert(event),
                    },
                  ]"
                  :style="position(event)"
                  :title="`${event.title} · ${event.start_on} 至 ${event.end_on}${overdueEvent(event) ? ' · 逾期未記錄' : ''}${nearbyAlert(event) ? ' · 附近已達警戒（區域彙整，非本田診斷）' : ''}`"
                  :aria-label="`${plan.crop_name} ${event.title}，${event.start_on}至${event.end_on}${event.completed ? (event.log_id ? '，已完成並已記錄' : '，已完成') : overdueEvent(event) ? '，逾期未記錄' : ''}`"
                  @click="emit('select', plan, event)"
                >
                  <Icon v-if="event.completed" name="check" :size="12" /><span>{{
                    event.title
                  }}</span>
                  <small v-if="nearbyAlert(event)" class="nearby-alert">附近已達警戒</small>
                </button>
                <button
                  v-if="logTag(event) === 'logged'"
                  class="log-tag logged"
                  :style="tagPosition(event)"
                  :aria-label="`查看「${event.title}」的農事紀錄`"
                  @click="emit('log', plan, event)"
                >
                  已記錄
                </button>
                <button
                  v-else-if="logTag(event) === 'unlogged'"
                  class="log-tag unlogged"
                  :style="tagPosition(event)"
                  :aria-label="`「${event.title}」已標完成但未記錄，點選補記`"
                  @click="emit('select', plan, event)"
                >
                  未記錄
                </button>
              </template>
            </div>
            <!-- 實際作業：帶文字的膠囊。第四輪回饋「實際作業不夠明顯」——
                 16px 的圓點要 hover 才知道是什麼，膠囊直接把做了什麼寫在圖上。 -->
            <template v-if="lane.key === 'actual'">
            <button
              v-for="d in dots(plan, 'actual')"
              :key="d.log.id"
              class="log-chip"
              :class="{ linked: !!d.event }"
              :style="{
                left: d.left + '%',
                top: `${d.row * 30 + 8}px`,
                '--chip': logColor(d.log.kind),
              }"
              :title="dotTitle(d)"
              :aria-label="`農事紀錄：${d.log.done_on} ${d.source.kind_label}${d.log.title ? '，' + d.log.title : ''}${d.event ? '，對照規劃事項' + d.event.title + '，' + d.offsetText : ''}`"
              @click="emit('openLog', d.source)"
            >
              <span class="chip-dot" />
              <span class="chip-text">{{ d.log.title || d.source.kind_label }}</span>
              <span v-if="d.photo" class="photo-dot" />
            </button>
            </template>
            <template v-else-if="lane.key === 'pest'">
            <button
              v-for="d in dots(plan, 'pest')"
              :key="d.log.id"
              class="log-dot"
              :class="{ linked: !!d.event }"
              :style="{
                left: d.left + '%',
                top: `${eventRows(plan, 'pest').length * 44 + d.row * 26 + 8}px`,
                background: logColor(d.log.kind),
              }"
              :title="dotTitle(d)"
              :aria-label="`農事紀錄：${d.log.done_on} ${d.source.kind_label}${d.log.title ? '，' + d.log.title : ''}${d.event ? '，對照規劃事項' + d.event.title + '，' + d.offsetText : ''}`"
              @click="emit('openLog', d.source)"
            >
              <span v-if="d.photo" class="photo-dot" />
            </button>
            </template>
            <span
              v-if="
                lane.key === 'actual' &&
                !dots(plan, 'actual').length
              "
              class="empty-row"
              >這一期還沒有農事紀錄</span
            >
            <span
              v-else-if="lane.key !== 'actual' && !eventRows(plan, lane.key).length"
              class="empty-row"
              >尚無事項</span
            >
          </div>
        </div>
      </section>
    </div>
  </div>
</template>
<style scoped>
.event-bar.alerted { outline: 2px solid var(--crit); outline-offset: -2px; }
.nearby-alert { color: var(--crit); font-size: 11px; font-weight: 700; margin-left: 5px; white-space: nowrap; }
.timeline-help {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 22px;
  color: var(--ink-2);
  font-size: 12px;
  background: #fbfcfa;
  border-top: 1px solid var(--line);
}
.legend-note {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.swatch {
  width: 11px;
  height: 11px;
  border-radius: 3px;
  margin-left: 8px;
}
.swatch:first-child {
  margin-left: 0;
}
.swatch.care {
  background: #e4f0ec;
  border-left: 3px solid #4b8a71;
}
.swatch.nutri {
  background: #eaf0f8;
  border-left: 3px solid #7798bd;
}
.swatch.dot-sample {
  border-radius: 50%;
  background: #b23b2d;
}
.swatch.late {
  background: #fff;
  outline: 1.5px dashed var(--warn);
  outline-offset: -1px;
}
.timeline-scroll {
  overflow: auto;
  max-height: 780px;
  overscroll-behavior: contain;
  border-top: 1px solid var(--line);
  border-radius: 0 0 14px 14px;
}
.timeline {
  min-width: 100%;
  font-size: 12px;
}
.axis,
.timeline-row {
  display: flex;
}
.axis {
  height: 60px;
  position: sticky;
  top: 0;
  background: #fafbf9;
  z-index: 6;
  border-bottom: 1px solid var(--line);
}
.row-label {
  position: sticky;
  left: 0;
  width: 148px;
  flex: none;
  z-index: 3;
  background: #fff;
  border-right: 1px solid var(--line);
  padding: 14px;
  display: flex;
  align-items: center;
  gap: 7px;
  color: var(--ink-2);
}
.label-text {
  display: flex;
  flex-direction: column;
  line-height: 1.35;
}
.label-text small {
  color: var(--muted);
  font-size: 10px;
}
.axis .row-label {
  background: #fafbf9;
  font-weight: 600;
}
.track {
  position: relative;
  flex: 1;
  min-width: 0;
  padding: 6px 0;
}
.track.actual {
  background: #fcfdfb;
}
/* 種植前：淡灰底。生育時期色帶整段蓋掉，不讓範本估計的綠色延伸到還沒下田的日子。 */
.pre-plant {
  position: absolute;
  top: 0;
  bottom: 0;
  left: 0;
  background: #f1f3f0;
  border-right: 1px solid #dfe4dd;
  pointer-events: none;
}
.pre-plant.stage {
  background: #eceeea;
}
.pre-plant small {
  position: absolute;
  right: 6px;
  top: 6px;
  font-size: 10px;
  color: var(--muted);
  white-space: nowrap;
}
.tick {
  position: absolute;
  top: 9px;
  font-weight: 600;
  padding-left: 8px;
  line-height: 1.5;
}
.tick small {
  display: block;
  font-size: 10px;
  color: var(--muted);
  font-weight: 400;
}
.grid-line {
  position: absolute;
  top: 0;
  bottom: 0;
  border-left: 1px dashed #e7ece6;
  pointer-events: none;
}
.timeline-row {
  border-bottom: 1px solid #edf0eb;
}
.group-title {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 13px 16px;
  background: #f2f6ef;
  color: var(--ink-2);
  position: sticky;
  left: 0;
  width: min(calc(100vw - 100px), 1250px);
}
.group-title strong {
  color: var(--brand);
  font-size: 15px;
}
.period {
  margin-left: auto;
  color: var(--muted);
  font-size: 11px;
}
.event-lane {
  height: 44px;
  position: relative;
}
.event-bar {
  position: absolute;
  top: 5px;
  height: 34px;
  border: 1px solid transparent;
  border-left: 3px solid;
  border-radius: 5px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  text-align: left;
  font-size: 12px;
  overflow: hidden;
  min-width: 8px;
}
.event-bar span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.event-bar:hover {
  filter: brightness(0.95);
  box-shadow: 0 2px 6px #23352320;
}
.stage {
  background: #e3eddb;
  color: #426136;
  border-left-color: #6f925a;
}
.care {
  background: #e4f0ec;
  color: #245c45;
  border-left-color: #4b8a71;
}
.nutrition {
  background: #eaf0f8;
  color: #3a628c;
  border-left-color: #7798bd;
}
.pest {
  background: repeating-linear-gradient(
    135deg,
    #fbf0dc,
    #fbf0dc 7px,
    #f8eace 7px,
    #f8eace 9px
  );
  color: #865e20;
  border-left-color: #bc913f;
}
.completed {
  opacity: 0.6;
}
/* 逾期未記錄：黃色虛線框。用 outline 不用 border，才不會把色塊的寬度往外撐掉一天。 */
.event-bar.late {
  outline: 1.5px dashed var(--warn);
  outline-offset: -1px;
}
.event-bar.has-tag {
  padding-right: 60px;
}
/* 帶文字的膠囊。`--chip` 由 kind 決定，跟日誌卡同色。 */
.log-chip {
  position: absolute;
  display: flex;
  align-items: center;
  gap: 5px;
  height: 24px;
  max-width: 170px;
  margin-left: -8px;
  padding: 0 8px 0 6px;
  border: 1px solid #e2e7e1;
  border-left: 3px solid var(--chip);
  border-radius: 999px;
  background: #fff;
  cursor: pointer;
  font-size: 11px;
  color: var(--ink-2);
  box-shadow: 0 1px 3px #23352318;
  z-index: 2;
}
.log-chip:hover { box-shadow: 0 2px 8px #23352330; }
.chip-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--chip); flex: none; }
.chip-text { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
/* 有對應規劃事項的膠囊往上拉一條虛線，指向它正上方的規劃色塊。 */
.log-chip.linked::before {
  content: '';
  position: absolute;
  left: 8px;
  bottom: 100%;
  height: 12px;
  border-left: 1.5px dashed #b7c3b6;
}
.log-chip .photo-dot { position: static; width: 7px; height: 7px; border: 0; flex: none; }

.log-dot {
  position: absolute;
  width: 16px;
  height: 16px;
  margin-left: -8px;
  border: 1.5px solid #fff;
  border-radius: 50%;
  padding: 0;
  cursor: pointer;
  box-shadow: 0 1px 3px #23352333;
  z-index: 2;
}
.log-dot:hover {
  transform: scale(1.25);
}
/* 有對應規劃事項的點加一圈外環，一眼看得出這一點是「照規劃做的」。 */
.log-dot.linked {
  box-shadow: 0 0 0 2px #ffffff, 0 0 0 3.5px #c8d4c4;
}
.photo-dot {
  position: absolute;
  top: -3px;
  right: -3px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #7b5ea7;
  border: 1px solid #fff;
}
.log-tag {
  position: absolute;
  top: 13px;
  transform: translateX(-100%);
  z-index: 1;
  height: 18px;
  padding: 0 7px;
  border: 0;
  border-radius: 999px;
  background: #fff;
  font-size: 10px;
  font-weight: 700;
  line-height: 18px;
  white-space: nowrap;
  cursor: pointer;
}
.log-tag.logged {
  color: var(--kdais-ink);
  box-shadow: inset 0 0 0 1px #b9d9c6;
}
.log-tag.unlogged {
  color: var(--warn);
  box-shadow: inset 0 0 0 1px #ecd29b;
}
.log-tag:hover {
  text-decoration: underline;
}
.today-line {
  position: absolute;
  top: 0;
  bottom: 0;
  border-left: 1.5px solid #c16645;
  pointer-events: none;
  z-index: 2;
}
.today-line span {
  position: absolute;
  top: -10px;
  left: -15px;
  background: #c16645;
  color: white;
  font-size: 10px;
  border-radius: 4px;
  padding: 1px 4px;
  white-space: nowrap;
}
.empty-row {
  display: block;
  padding: 10px;
  color: var(--muted);
}
@media (max-width: 768px) {
  .timeline-help {
    padding: 10px 12px;
  }
  .period {
    display: none;
  }
  .group-title {
    width: calc(100vw - 34px);
  }
  /* 手機：色帶標籤縮成圖示，把寬度讓給圖；點加大到 20px 才點得到。 */
  .row-label {
    width: 46px;
    padding: 10px 8px;
    justify-content: center;
  }
  .label-text {
    display: none;
  }
  .log-dot {
    width: 20px;
    height: 20px;
    margin-left: -10px;
  }
  .log-chip { max-width: 120px; height: 26px; }
}
@media print {
  .timeline-scroll {
    overflow: visible;
    max-height: none;
  }
  .timeline {
    width: 100% !important;
  }
  .timeline-help {
    display: none;
  }
  .row-label {
    width: 100px;
  }
  .event-bar {
    font-size: 8px;
    padding: 0 2px;
  }
  .group-title {
    width: 100%;
  }
  .timeline {
    print-color-adjust: exact;
  }
}
</style>
