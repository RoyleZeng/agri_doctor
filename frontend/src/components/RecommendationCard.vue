<script setup lang="ts">
import { computed, ref } from 'vue'
import type { IrrigatePayload, Recommendation, VrtFertilizerPayload } from '@/api/planning'
import { canAccept, canMarkDone, decidedSummary, fiveRChips, statusLabel } from '@/composables/precisionState'
import Icon from './Icon.vue'

const props = defineProps<{ rec: Recommendation; canDecide: boolean; busy?: boolean }>()
const emit = defineEmits<{ accept: []; done: []; dismiss: [reason: string] }>()
// 誰能按由父層的 canDecide 決定（農民本人）；還能不能按由狀態決定——兩個條件分開，元件裡不重算身分比對。
const showButtons = computed(() => props.canDecide && canAccept(props.rec.status))
const showDoneButton = computed(() => props.canDecide && canMarkDone(props.rec.status) && !showButtons.value)

const irrigatePayload = computed(() => (props.rec.kind === 'irrigate' ? (props.rec.payload as IrrigatePayload) : null))
const vrtPayload = computed(() => (props.rec.kind === 'vrt_fertilizer' ? (props.rec.payload as VrtFertilizerPayload) : null))
const chips = computed(() => fiveRChips(props.rec.five_r))
const kindTag = computed(() => (props.rec.kind === 'irrigate' ? { text: '48 小時內', tone: 'warn' } : { text: '14 天內追肥', tone: 'info' }))
const dismissing = ref(false)
const reason = ref('')

function confirmDismiss() {
  emit('dismiss', reason.value.trim())
  dismissing.value = false
  reason.value = ''
}
</script>

<template>
  <div class="rec">
    <div class="t">
      <span><Icon :name="rec.kind === 'irrigate' ? 'drop' : 'sack'" :size="16" />{{ rec.title }}</span>
      <span v-if="rec.status === 'proposed'" class="tag" :class="kindTag.tone">{{ kindTag.text }}</span>
      <span v-else class="tag" :class="{ ok: rec.status === 'done' || rec.status === 'accepted', warn: rec.status === 'expired', info: rec.status === 'dismissed' }">
        {{ statusLabel(rec.status) }}
      </span>
    </div>

    <p v-if="irrigatePayload?.warnings.length || vrtPayload?.warnings.length" class="rec-warn">
      <Icon name="warn" :size="14" />{{ (irrigatePayload?.warnings ?? vrtPayload?.warnings ?? [])[0] }}
    </p>
    <div class="why">{{ rec.rationale }}</div>

    <table v-if="vrtPayload" class="rx">
      <thead>
        <tr>
          <th>分區</th>
          <th>面積</th>
          <th>氮 kg/ha</th>
          <th>本區用量</th>
          <th>對照均一施</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="zone in vrtPayload.zones" :key="zone.code">
          <td>{{ zone.code }} {{ zone.label }}</td>
          <td class="num">{{ zone.area_ha === null ? '未填' : `${zone.area_ha.toFixed(2)} ha` }}</td>
          <td class="num">{{ zone.rate_kg_per_ha }}</td>
          <td class="num">{{ zone.amount_kg === null ? '—' : `${zone.amount_kg.toFixed(1)} kg` }}</td>
          <td class="num muted">
            {{ zone.amount_kg === null || zone.area_ha === null ? '—' : `${(vrtPayload!.base_rate_kg_per_ha * zone.area_ha).toFixed(1)} kg` }}
          </td>
        </tr>
      </tbody>
    </table>

    <div class="r5">
      <span v-for="chip in chips" :key="chip.label">{{ chip.label }} <b>{{ chip.value }}</b></span>
    </div>

    <div v-if="showButtons" class="acts">
      <template v-if="!dismissing">
        <button type="button" class="btn primary" :disabled="busy" @click="emit('accept')">轉成農事待辦</button>
        <button type="button" class="btn" :disabled="busy" @click="emit('done')">已執行，記到日誌</button>
        <button type="button" class="btn ghost" :disabled="busy" @click="dismissing = true">略過</button>
      </template>
      <template v-else>
        <input v-model="reason" class="rec-reason" maxlength="500" placeholder="略過原因（選填）" @keydown.enter.prevent="confirmDismiss" />
        <button type="button" class="btn" :disabled="busy" @click="confirmDismiss">確認略過</button>
        <button type="button" class="btn ghost" :disabled="busy" @click="dismissing = false">取消</button>
      </template>
    </div>
    <div v-else-if="showDoneButton" class="acts">
      <button type="button" class="btn primary" :disabled="busy" @click="emit('done')">已執行，記到日誌</button>
      <span class="xs muted">已排入農事待辦，做完後回來記一筆。</span>
    </div>
    <p v-else-if="!canDecide && rec.status === 'proposed'" class="rec-decide-note">由農民決定</p>
    <p v-else class="rec-decided">
      <Icon :name="rec.status === 'expired' ? 'warn' : 'check'" :size="14" />{{ decidedSummary(rec) }}
    </p>
  </div>
</template>

<style scoped>
.rec {
  padding: 14px 18px;
  border-bottom: 1px solid var(--line);
}
.rec:last-child {
  border-bottom: none;
}
.rec .t {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  font-weight: 700;
  font-size: 15px;
}
.rec .t > span:first-child {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.rec-warn {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 8px 0 0;
  padding: 7px 10px;
  border-radius: 8px;
  background: var(--warn-soft);
  color: #6d4506;
  font-size: 12px;
}
.rec .why {
  font-size: 13px;
  color: var(--ink-2);
  margin-top: 6px;
  line-height: 1.6;
}
.rx {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin-top: 8px;
}
.rx th {
  text-align: left;
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
  padding: 3px 6px;
  border-bottom: 1px solid var(--line);
}
.rx td {
  padding: 5px 6px;
  border-bottom: 1px dashed var(--line);
}
.rx tr:last-child td {
  border-bottom: none;
}
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.r5 {
  display: flex;
  flex-wrap: wrap;
  gap: 5px;
  margin-top: 8px;
}
.r5 span {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  background: #f4f0fa;
  color: #4a2f75;
  font-size: 11px;
  font-weight: 600;
}
.r5 span b {
  font-weight: 500;
  color: var(--ink-2);
}
.acts {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 10px;
}
.acts .btn {
  height: 34px;
  font-size: 13px;
}
.rec-reason {
  flex: 1;
  min-width: 160px;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--line-strong);
  border-radius: 8px;
  font: inherit;
  font-size: 13px;
}
.rec-decide-note,
.rec-decided {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 0 0;
  font-size: 13px;
  color: var(--muted);
}
@media (max-width: 660px) {
  .acts {
    flex-direction: column;
    align-items: stretch;
  }
  .acts .btn {
    height: 44px;
    font-size: 14px;
  }
}
</style>
