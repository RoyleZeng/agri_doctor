<script setup lang="ts">
import { computed, ref } from 'vue'

import type { Plan, WorkLog } from '@/api/planning'
import Icon from '@/components/Icon.vue'
import { shortDate, today } from '@/composables/farmDates'
import { kindOf } from '@/composables/farmLog'
import {
  logInRange,
  plannedRows,
  rangeBounds,
  rowInRange,
  unplannedLogs,
  type LaneLog,
  type PanelRange,
  type PlacedLog,
  type PlannedRow,
} from '@/composables/timelineLanes'

/**
 * 「規劃 vs 實際」：在同一個畫面回答「當初打算做什麼」與「實際做了什麼」。
 *
 * 第四輪回饋是「實際作業不明顯，而且想在同畫面比較」。複合圖上下兩條色帶已經對齊，
 * 但色帶上放不下人力、照片數、資材這些細節；這個面板把同一份配對結果換成逐列的樣子。
 *
 * **資料完全來自 `placed`（`timelineLanes.placeLogs` 的結果），不另打 API**——
 * 面板與圖說的是同一件事，兩邊各自去要資料遲早會不一致。
 */
const props = defineProps<{
  plans: Plan[]
  /** 每一期作物的配對結果，由 FarmDashboard 算好傳進來。 */
  placedByPlan: Map<string, PlacedLog[]>
  logsById: Map<string, WorkLog>
}>()
const emit = defineEmits<{
  record: [plan: Plan, eventId: string]
  view: [log: WorkLog]
  add: [plan: Plan]
}>()

const RANGES: { key: PanelRange; label: string }[] = [
  { key: 'week', label: '本週' },
  { key: 'month', label: '本月' },
  { key: 'season', label: '整期' },
]
const range = ref<PanelRange>('week')
const now = today()
const bounds = computed(() => rangeBounds(range.value, now))

interface Section {
  plan: Plan
  rows: PlannedRow[]
  unplanned: LaneLog[]
}

const sections = computed<Section[]>(() =>
  props.plans.map((plan) => {
    const placed = props.placedByPlan.get(plan.id) ?? []
    return {
      plan,
      rows: plannedRows(plan.events, placed, now).filter((r) => rowInRange(r, bounds.value)),
      unplanned: unplannedLogs(placed).filter((l) => logInRange(l, bounds.value)),
    }
  }),
)

const STATUS: Record<PlannedRow['status'], { label: string; cls: string }> = {
  done: { label: '已做', cls: 'ok' },
  pending: { label: '待記錄', cls: 'pending' },
  overdue: { label: '逾期', cls: 'late' },
  upcoming: { label: '未到', cls: 'upcoming' },
}

const counts = computed(() => {
  const all = sections.value.flatMap((s) => s.rows)
  return {
    done: all.filter((r) => r.status === 'done').length,
    todo: all.filter((r) => r.status === 'pending' || r.status === 'overdue').length,
    extra: sections.value.reduce((n, s) => n + s.unplanned.length, 0),
  }
})

/** 一列實際作業的摘要：人力、照片、資材——色帶上放不下的就放這裡。 */
function summary(log: LaneLog): string {
  const full = props.logsById.get(log.id)
  if (!full) return ''
  return [
    full.material_name,
    [full.amount_value, full.amount_unit].filter(Boolean).join(''),
    full.labor_text,
    full.photos.length ? `${full.photos.length} 張照片` : '',
  ].filter(Boolean).join(' · ')
}
const fullLog = (log: LaneLog) => props.logsById.get(log.id)
</script>

<template>
  <section class="card pva" aria-label="規劃與實際對照">
    <div class="pva-head">
      <div>
        <h2>規劃 vs 實際</h2>
        <p>{{ counts.done }} 項已做 · {{ counts.todo }} 項待記錄 · {{ counts.extra }} 筆規劃外</p>
      </div>
      <div class="segmented no-print">
        <button
          v-for="r in RANGES"
          :key="r.key"
          type="button"
          :class="{ active: range === r.key }"
          @click="range = r.key"
        >
          {{ r.label }}
        </button>
      </div>
    </div>

    <div class="pva-body">
      <div v-for="section in sections" :key="section.plan.id" class="pva-plan">
        <h3>{{ section.plan.crop_name }} · {{ section.plan.field_name }}</h3>

        <p class="pva-sub">規劃事項</p>
        <button
          v-for="row in section.rows"
          :key="row.event.id"
          type="button"
          class="pva-row"
          @click="row.logs.length && fullLog(row.logs[0]!)
            ? emit('view', fullLog(row.logs[0]!)!)
            : emit('record', section.plan, row.event.id)"
        >
          <span class="st" :class="STATUS[row.status].cls">{{ STATUS[row.status].label }}</span>
          <span class="pva-main">
            <b>{{ row.event.title }}</b>
            <small>
              規劃 {{ shortDate(row.event.start_on) }}–{{ shortDate(row.event.end_on) }}
              <template v-if="row.doneOn">
                · 實際 {{ shortDate(row.doneOn) }}（{{ row.offsetText }}）
              </template>
              <template v-if="row.logs.length">· {{ summary(row.logs[0]!) }}</template>
            </small>
          </span>
          <span class="pva-act">{{ row.logs.length ? '看紀錄' : '記錄' }}</span>
        </button>
        <p v-if="!section.rows.length" class="quiet-state">這個範圍內沒有規劃事項。</p>

        <p class="pva-sub">
          規劃外的實際作業
          <button type="button" class="text-btn no-print" @click="emit('add', section.plan)">＋ 記一筆</button>
        </p>
        <button
          v-for="log in section.unplanned"
          :key="log.id"
          type="button"
          class="pva-row"
          @click="fullLog(log) && emit('view', fullLog(log)!)"
        >
          <span class="st extra"><Icon :name="kindOf(log.kind).icon as 'note'" :size="13" /></span>
          <span class="pva-main">
            <b>{{ log.title || kindOf(log.kind).name }}</b>
            <small>{{ shortDate(log.done_on) }}<template v-if="summary(log)"> · {{ summary(log) }}</template></small>
          </span>
          <span class="pva-act">看紀錄</span>
        </button>
        <p v-if="!section.unplanned.length" class="quiet-state">這個範圍內沒有規劃外的作業。</p>
      </div>
      <p v-if="!sections.length" class="quiet-state">先選一種作物，這裡會列出它的規劃與實際作業。</p>
    </div>
  </section>
</template>

<style scoped>
.pva { display: flex; flex-direction: column; max-height: 780px; }
.pva-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 18px;
  border-bottom: 1px solid var(--line);
  flex-wrap: wrap;
}
.pva-head h2 { font-size: 16px; }
.pva-head p { font-size: 12px; color: var(--muted); margin-top: 3px; }
.pva-body { overflow-y: auto; padding: 4px 0 12px; }
.pva-plan { padding: 10px 18px; }
.pva-plan h3 { font-size: 14px; color: var(--brand); margin-bottom: 6px; }
.pva-sub {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-size: 11px;
  color: var(--muted);
  letter-spacing: 0.04em;
  margin: 12px 0 4px;
}
.pva-row {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  border: 0;
  background: none;
  text-align: left;
  padding: 8px 4px;
  border-bottom: 1px solid #eef1ec;
  cursor: pointer;
}
.pva-row:hover { background: var(--bg); }
.st {
  flex: none;
  min-width: 46px;
  height: 22px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 0 7px;
}
.st.ok { background: #e4f0ec; color: #245c45; }
.st.pending { background: #eef2f5; color: #3a628c; }
.st.late { background: #fdf4e3; color: #8a6414; }
.st.upcoming { background: #f1f3f0; color: var(--muted); }
.st.extra { background: #efe9f6; color: #5b3d8a; min-width: 22px; }
.pva-main { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.pva-main b { font-size: 13px; }
.pva-main small { font-size: 11px; color: var(--muted); line-height: 1.5; }
.pva-act { flex: none; font-size: 12px; color: var(--brand); }

@media (max-width: 1150px) {
  .pva { max-height: none; }
}
</style>
