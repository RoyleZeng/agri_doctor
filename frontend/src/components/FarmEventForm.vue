<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import {
  planning,
  type FarmEvent,
  type Plan,
  type EventInput,
  type WorkLog,
} from '@/api/planning'
import { kinds, today } from '@/composables/farmDates'
import { toast } from '@/composables/toast'
import FarmModal from './FarmModal.vue'
import Icon from './Icon.vue'
import WorkLogForm from './WorkLogForm.vue'
import GuidanceMedia from './GuidanceMedia.vue'
const props = defineProps<{
  plan: Plan
  event?: FarmEvent
  readonly?: boolean
  initialRecord?: boolean
  /** 有值才顯示「記錄到日誌」；範例模式與未登入沒有農場可記。 */
  farmerId?: string
  canRecord?: boolean
  canRestore?: boolean
}>()
const emit = defineEmits<{
  close: []
  saved: [Plan]
  'plan-updated': [Plan]
  restore: []
  recorded: []
  'open-log': [FarmEvent]
}>()
const start =
  today() < props.plan.planted_on
    ? props.plan.planted_on
    : today() > props.plan.end_on
      ? props.plan.end_on
      : today()
const form = reactive<EventInput>({
  version: props.plan.version,
  title: props.event?.title || '',
  kind: props.event?.kind || 'care',
  start_on: props.event?.start_on || start,
  end_on: props.event?.end_on || start,
  detail: props.event?.detail || '',
  note: props.event?.note || '',
  completed: props.event?.completed || false,
})
const sources = computed(() =>
  props.plan.sources.filter((s) => props.event?.source_ids.includes(s.id)),
)
const busy = ref(false)
const logBusy = ref(false)
const error = ref('')
// 與後端同一條規則：只有農事／肥培是「做完」的作業；巡查窗口可以記巡田觀察，但不會被標完成；生育時期沒有動作。
const work = computed(() => !!props.event && ['care', 'nutrition'].includes(props.event.kind))
const pest = computed(() => props.event?.kind === 'pest')
const recordable = computed(
  () => !!props.event && !!props.farmerId && !!props.canRecord && (work.value || pest.value),
)
const archived = computed(() => props.plan.archived)
const guidance = computed(() => !!props.event && ['stage', 'pest'].includes(props.event.kind))
const tab = ref<'detail' | 'log'>(props.initialRecord && recordable.value ? 'log' : 'detail')
const logged = computed(() => work.value && !!props.event?.log_id)
const showTabs = computed(() => recordable.value && !archived.value && !logged.value)
const title = computed(() =>
  tab.value === 'log'
    ? pest.value ? '記錄巡田觀察' : '記錄到農事日誌'
    : props.event ? (guidance.value ? '生育與巡查建議' : '事項詳細內容') : '新增管理事項',
)

async function save() {
  error.value = ''
  busy.value = true
  try {
    emit(
      'saved',
      await planning.saveEvent(props.plan, props.event?.id, {
        ...form,
        completed: ['care', 'nutrition'].includes(form.kind) && form.completed,
      }),
    )
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

function onLogged(log: WorkLog) {
  // 規劃要就地更新（事項完成、版本升級），但面板先別關：照片可能還在上傳。
  if (log.plan) emit('plan-updated', log.plan)
}
function onLogDone() {
  emit('recorded')
  toast.ok(pest.value ? '巡田觀察已記錄到農事日誌' : '已記錄到農事日誌，事項標為完成')
  emit('close')
}
</script>
<template>
  <FarmModal
    :title="title"
    :wide="tab === 'log'"
    :sheet="tab === 'log'"
    @close="!busy && !logBusy && emit('close')"
  >
    <div v-if="event && archived && canRecord" class="archived-note" role="note">
      <Icon name="warn" :size="16" />
      <span>這一期規劃已封存，不能再記錄事項或修改內容。</span>
      <button v-if="canRestore" type="button" class="btn sm" @click="emit('restore')">恢復規劃</button>
      <span v-else class="form-hint">請農民恢復規劃後再記錄。</span>
    </div>
    <div v-if="logged && event" class="logged-note">
      <Icon name="check" :size="16" />
      <span>已記錄到農事日誌</span>
      <button type="button" class="text-btn" @click="emit('open-log', event)">查看紀錄 →</button>
    </div>
    <div v-else-if="work && event?.completed && recordable && !archived && tab === 'detail'" class="unlogged-note">
      <span>已標完成，但沒有農事紀錄。</span>
      <button type="button" class="text-btn" @click="tab = 'log'">補記到日誌 →</button>
    </div>
    <div v-if="showTabs" class="event-tabs" role="tablist" aria-label="事項動作">
      <button type="button" role="tab" :aria-selected="tab === 'detail'" :class="{ on: tab === 'detail' }" :disabled="logBusy" @click="tab = 'detail'">
        {{ work ? '事項與日期' : '建議內容' }}
      </button>
      <button type="button" role="tab" :aria-selected="tab === 'log'" :class="{ on: tab === 'log' }" @click="tab = 'log'">
        {{ pest ? '記錄巡田觀察' : '記錄到日誌' }}
      </button>
    </div>

    <WorkLogForm
      v-if="tab === 'log' && event && farmerId"
      :farmer-id="farmerId"
      :plans="[plan]"
      :mode="{ type: 'prefill', event_id: event.id }"
      @saved="onLogged"
      @done="onLogDone"
      @cancel="tab = 'detail'"
      @busy="logBusy = $event"
      @restore-plan="emit('restore')"
    />

    <article v-else-if="guidance && event" class="guidance-detail">
      <p class="farm-eyebrow">{{ plan.crop_name }} · {{ plan.field_name }} · {{ pest ? '病蟲害巡查' : '生育時期' }}</p>
      <h2>{{ event.title }}</h2>
      <p class="muted">參考期間 {{ event.start_on }} — {{ event.end_on }}</p>
      <div class="source-note">{{ pest ? '這是觀察窗口，不代表本田已發病。發現異常請記錄位置、徵兆與照片，再由植物醫生判讀。' : '生育時期為規劃估計，請以品種、天氣及現場生長情形確認。' }}</div>
      <h3>{{ pest ? '觀察與防治建議' : '本階段建議事項' }}</h3>
      <p class="guidance-text">{{ event.detail || '此事項尚未補充詳細建議，請參考下方來源或詢問植物醫生。' }}</p>
      <GuidanceMedia :items="event.media || []" />
      <div v-if="sources.length" class="event-sources">
        <strong>參考依據</strong>
        <a v-for="s in sources" :key="s.id" :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title }} ↗<small>{{ s.publisher }}</small></a>
      </div>
      <p class="form-hint">內容隨本期規劃保存 · 範本 {{ plan.template_version || '自訂' }}</p>
      <div class="form-actions"><button class="btn" @click="emit('close')">關閉</button><button v-if="pest && recordable && !archived" class="btn primary" @click="tab = 'log'">記錄巡田觀察</button></div>
    </article>

    <form v-else class="farm-form" @submit.prevent="save">
      <p class="form-hint">
        {{ plan.crop_name }} · {{ plan.field_name
        }}<span v-if="event">
          ·
          {{
            event.basis === 'template_estimate'
              ? '範本估計日期'
              : '人工新增或調整'
          }}</span
        >
      </p>
      <label
        >事項名稱<input
          v-model="form.title"
          required
          maxlength="120"
          :readonly="readonly"
      /></label>
      <label
        >管理類別<select v-model="form.kind" :disabled="readonly">
          <option v-for="k in kinds.filter(k => k.key === 'care' || k.key === 'nutrition')" :key="k.key" :value="k.key">
            {{ k.name }}
          </option>
        </select></label
      >
      <div class="form-grid">
        <label
          >開始日期<input
            v-model="form.start_on"
            type="date"
            required
            :min="plan.planted_on"
            :max="plan.end_on"
            :readonly="readonly" /></label
        ><label
          >結束日期<input
            v-model="form.end_on"
            type="date"
            required
            :min="form.start_on"
            :max="plan.end_on"
            :readonly="readonly"
        /></label>
      </div>
      <label
        >管理與觀察說明<textarea
          v-model="form.detail"
          rows="4"
          maxlength="3000"
          :readonly="readonly"
        />
      </label>
      <div v-if="form.kind === 'pest'" class="source-note">
        這是巡查提醒，尚不能判定您的田區已發病。有異常時記下位置與徵兆，讓植物醫生進一步確認。
      </div>
      <label
        >田間紀錄<textarea
          v-model="form.note"
          rows="3"
          maxlength="2000"
          placeholder="例如：北側田埂附近發現葉片異常，約5株。"
          :readonly="readonly"
        />
      </label>
      <div v-if="sources.length" class="event-sources">
        <strong>參考依據</strong>
        <p v-if="event?.basis === 'user_adjusted'" class="form-hint">
          內容經人工調整，下列連結為原範本參考，未驗證人工修改內容。
        </p>
        <a
          v-for="s in sources"
          :key="s.id"
          :href="s.url"
          target="_blank"
          rel="noopener noreferrer"
          >{{ s.title }} ↗<small
            >{{ s.publisher }} · 抓取 {{ s.fetched_at.slice(0, 10) }}</small
          ></a
        >
        <p class="form-hint">
          來源支持一般管理與觀察；圖上的日期為規劃估計，未結合當地天氣。
        </p>
      </div>
      <p v-else class="form-hint">自訂事項，尚無官方來源對照。</p>
      <p v-if="event" class="form-hint">最近記錄者：{{ event.updated_by }}</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="form-actions">
        <button
          class="btn"
          type="button"
          :disabled="busy"
          @click="emit('close')"
        >
          {{ readonly ? '關閉' : '取消' }}</button
        ><button v-if="!readonly" class="btn primary" :disabled="busy">
          {{ busy ? '儲存中…' : '儲存事項' }}
        </button>
      </div>
    </form>
  </FarmModal>
</template>
<style scoped>
.guidance-detail { display: grid; gap: 16px; }
.guidance-detail h2, .guidance-detail h3, .guidance-detail p { margin: 0; }
.guidance-text { white-space: pre-wrap; line-height: 1.9; }
.event-sources {
  padding: 14px;
  border: 1px solid var(--line);
  border-radius: 10px;
  font-size: 13px;
}
.event-sources a {
  display: block;
  margin: 9px 0;
}
.event-sources small {
  display: block;
  color: var(--ink-2);
  font-size: 11px;
}
.event-tabs {
  display: inline-flex;
  margin-bottom: 16px;
  border: 1px solid var(--line-strong);
  border-radius: 9px;
  overflow: hidden;
}
.event-tabs button {
  border: 0;
  background: #fff;
  padding: 7px 14px;
  font-size: 13px;
  color: var(--ink-2);
  white-space: nowrap;
}
.event-tabs button + button {
  border-left: 1px solid var(--line-strong);
}
.event-tabs button.on {
  background: var(--ink);
  color: #fff;
  font-weight: 600;
}
.archived-note,
.logged-note,
.unlogged-note {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 14px;
  padding: 10px 12px;
  border-radius: 10px;
  font-size: 13px;
}
.archived-note {
  background: var(--warn-soft);
  color: #6d4506;
}
.archived-note span:first-of-type {
  flex: 1;
}
.logged-note {
  background: var(--kdais-soft);
  color: var(--kdais-ink);
}
.unlogged-note {
  background: var(--warn-soft);
  color: #6d4506;
}
.logged-note .text-btn,
.unlogged-note .text-btn {
  margin-left: auto;
  padding: 0;
}
@media (max-width: 660px) {
  .event-tabs {
    display: flex;
  }
  .event-tabs button {
    flex: 1;
    padding: 9px 6px;
  }
}
</style>
