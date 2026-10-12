<script setup lang="ts">
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import {
  planning,
  PlanningError,
  type LogFields,
  type LogFormMode,
  type LogKind,
  type LogPatch,
  type LogPhoto,
  type LogPrefill,
  type Plan,
  type WorkLog,
} from '@/api/planning'
import type { RegistrationSearchItem } from '@/api/types'
import { today } from '@/composables/farmDates'
import {
  LOG_KINDS,
  MOA_MISSING,
  canBackfillRegistration,
  harvestAfter,
  missingFields,
  missingText,
  moaSource,
  saveLabel,
  shortDay,
} from '@/composables/farmLog'
import type { IconName } from './icons'
import Icon from './Icon.vue'
import PhotoUploader from './PhotoUploader.vue'
import RegistrationPicker from './RegistrationPicker.vue'

/**
 * 農事紀錄表單本體。三個入口（記一筆、規劃事項、處方）與修改共用；外層決定放在 modal、bottom sheet，
 * 或直接嵌在事項面板裡（PlanToLog：不用跳到日誌頁）。
 */
const props = defineProps<{
  farmerId: string
  plans: Plan[]
  fields?: string[]
  mode: LogFormMode
}>()
const emit = defineEmits<{
  saved: [log: WorkLog]
  done: []
  cancel: []
  'restore-plan': [planId: string]
  busy: [value: boolean]
}>()

interface Section { material: boolean; dilution: boolean; amount: boolean; yield: boolean; pest: boolean }
// 每種作業顯示的欄位；沒顯示的欄位送出時清空，免得切換類型後殘留的藥名跑進「灌溉」的標題。
const SECTIONS: Record<LogKind, Section> = {
  spray: { material: true, dilution: true, amount: true, yield: false, pest: true },
  fertilize: { material: true, dilution: true, amount: true, yield: false, pest: false },
  irrigate: { material: false, dilution: false, amount: true, yield: false, pest: false },
  fieldwork: { material: true, dilution: false, amount: true, yield: false, pest: false },
  harvest: { material: false, dilution: false, amount: false, yield: true, pest: false },
  tillage: { material: true, dilution: false, amount: true, yield: false, pest: false },
  scout: { material: false, dilution: false, amount: false, yield: false, pest: true },
  other: { material: true, dilution: false, amount: true, yield: false, pest: true },
}
const TEXT_KEYS = [
  'field_text', 'crop_text', 'title', 'material_name', 'material_supplier', 'material_lot', 'dilution', 'amount_value',
  'amount_unit', 'area_text', 'yield_value', 'yield_unit', 'yield_destination', 'labor_text', 'pest_text', 'note',
] as const

const editing = props.mode.type === 'edit' ? props.mode.log : null
const locked = !!editing?.locked
const todayIso = today()

interface FormState extends Omit<LogFields, 'kind'> {
  kind: LogKind | ''
  plan_id: string
}
const form = reactive<FormState>({
  done_on: editing?.done_on ?? todayIso,
  done_time: editing?.done_time ?? null,
  kind: editing?.kind ?? (props.mode.type === 'new' ? props.mode.kind ?? '' : ''),
  plan_id: editing ? editing.plan_id ?? '' : props.mode.type === 'edit' ? '' : props.mode.plan_id ?? '',
  pest_id: editing?.pest_id ?? null,
  field_text: '', crop_text: '', title: '', material_name: '', material_supplier: '', material_lot: '', dilution: '',
  amount_value: '', amount_unit: '', area_text: '', yield_value: '', yield_unit: '', yield_destination: '',
  labor_text: '', pest_text: '', note: '',
})
if (editing) for (const key of TEXT_KEYS) form[key] = editing[key]

interface Picked {
  id: number
  label: string
  moaCodes: string[]
  phiDays: number | null
  dilution: string
  pest: string
  banned: boolean
  cropMatches: boolean | null
}
const snapshot = editing?.registration_snapshot ?? null
// 比對用快照裡的登記編號：上游刪掉登記列後外鍵會是 null，但這筆紀錄的依據沒有變。
const originalRegistrationId = snapshot?.registration_id ?? null
const registration = ref<Picked | null>(
  snapshot
    ? {
        id: snapshot.registration_id,
        label: snapshot.product_text,
        moaCodes: snapshot.moa_codes ?? [],
        phiDays: snapshot.phi_days,
        dilution: snapshot.dilution ?? '',
        pest: snapshot.pest?.name ?? '',
        banned: snapshot.banned,
        cropMatches: snapshot.crop_matches,
      }
    : null,
)
const picking = ref(props.mode.type === 'edit' && !!props.mode.backfill)
const prefill = ref<LogPrefill | null>(null)
const loading = ref(props.mode.type === 'prefill')
const cropId = ref<number | null>(null)
const busy = ref(false)
const error = ref('')
const archivedPlan = ref('')
const savedLog = ref<WorkLog | null>(null)
const uploadFailed = ref(0)
const uploader = ref<InstanceType<typeof PhotoUploader> | null>(null)

const hasPrescription = computed(() => !!(prefill.value?.prescription_item_id || editing?.prescription_item_id))
const recommendation = computed(() => prefill.value?.recommendation ?? null)
// 建議帶入的日誌類型固定（irrigate→補灌、vrt_fertilizer→施肥），改成別的類型存檔會被後端拒絕，乾脆鎖住選項。
const recommendationKind = computed(() => (recommendation.value?.kind === 'vrt_fertilizer' ? 'fertilize' : recommendation.value ? 'irrigate' : null))
const rx = computed(() => prefill.value?.prescription ?? null)
const rxCaseNo = computed(() => rx.value?.case_no ?? editing?.prescription?.case_no ?? '')
const fromEvent = computed(() => !!prefill.value?.event_id)
const activePlans = computed(() => props.plans.filter((plan) => !plan.archived))
const fieldOptions = computed(() => [
  ...new Set([...activePlans.value.map((plan) => plan.field_name), ...(props.fields ?? [])].filter(Boolean)),
])
const section = computed(() => SECTIONS[form.kind || 'other'])
const moa = computed(() => moaSource(form.kind, hasPrescription.value, !!registration.value))
const missing = computed(() => missingFields(form.kind, form as unknown as Record<string, unknown>))
const backfillable = computed(() => !!editing && canBackfillRegistration(editing))
const canChangeRegistration = computed(
  () => !hasPrescription.value && (!locked || backfillable.value) && !savedLog.value,
)
const showDrug = computed(
  () => form.kind === 'spray' && (!locked || backfillable.value || !!registration.value || hasPrescription.value),
)
const harvestDate = computed(() =>
  registration.value ? harvestAfter(form.done_on, registration.value.phiDays) : null,
)
const label = computed(() => saveLabel({ kind: form.kind, fromEvent: fromEvent.value, editing: !!editing }))
const banner = computed(() => {
  const data = prefill.value
  if (!data) return ''
  if (data.recommendation)
    return `記錄精準模式建議「${data.recommendation.title}」。已依建議帶入田區與用量，確認無誤即可存；存檔後這則建議會標記為已執行。`
  if (data.event?.kind === 'pest')
    return `巡查提醒「${data.event.title}」：記下看到的情況。巡查窗口是提醒，記錄後不會被標完成。`
  if (data.event)
    return `記錄規劃事項「${data.event.title}」· 規劃窗口 ${shortDay(data.event.start_on)} – ${shortDay(data.event.end_on)}。存檔後事項標為完成，規劃日期不變。`
  if (data.prescription)
    return `帶入處方 ${data.prescription.case_no}：${data.prescription.product_text}${data.dilution ? ` · 稀釋 ${data.dilution}` : ''}。確認實際用量後即可存。`
  return ''
})
const unitHint = computed(() =>
  ({ spray: '公升', fertilize: '公斤', irrigate: '分鐘或 mm' } as Partial<Record<LogKind, string>>)[form.kind || 'other'] ?? '',
)
const notePlaceholder = computed(() =>
  form.kind === 'scout'
    ? '看到的徵兆、位置與株數，例如：北側田埂附近葉片有斑點，約 5 株'
    : form.kind === 'spray'
      ? '天候與風況，例如：早上 6 點噴，風小'
      : '補充說明',
)
const icon = (name: string) => name as IconName

let suppressPlanWatch = false
let cropSequence = 0
async function resolveCrop(planId: string) {
  const mine = ++cropSequence
  try {
    const data = await planning.logPrefill(props.farmerId, { plan_id: planId })
    if (mine === cropSequence) cropId.value = data.crop_id
  } catch {
    if (mine === cropSequence) cropId.value = null
  }
}
function applyPlan(plan: Plan | undefined) {
  if (!plan) {
    cropSequence++
    cropId.value = null
    return
  }
  form.field_text = plan.field_name
  form.crop_text = plan.crop_name
  form.area_text = form.area_text || plan.area
  void resolveCrop(plan.id)
}
watch(
  () => form.plan_id,
  (id, previous) => {
    if (id !== previous && !suppressPlanWatch) applyPlan(activePlans.value.find((plan) => plan.id === id))
  },
)

function chooseField(name: string) {
  form.field_text = name
  // 修改時連結不能改；新增時田區剛好只對應一期規劃，就順手帶入那一期（作物、面積、登記候選）。
  if (editing) return
  const matches = activePlans.value.filter((plan) => plan.field_name === name)
  if (matches.length === 1) form.plan_id = matches[0].id
  else if (activePlans.value.find((plan) => plan.id === form.plan_id)?.field_name !== name) form.plan_id = ''
}

function pick(item: RegistrationSearchItem) {
  const text = [item.concentration, item.product_name, item.formulation].filter(Boolean).join(' ')
  const previous = registration.value
  if (!locked) {
    // 只覆蓋空白或上一次自動帶入的值；農民自己打的商品名、倍數不動。
    if (!form.material_name.trim() || form.material_name === previous?.label) form.material_name = text
    if (!form.dilution.trim() || form.dilution === previous?.dilution) form.dilution = item.dilution_text ?? ''
    if (!form.pest_text.trim() || form.pest_text === previous?.pest) {
      form.pest_id = item.pest.id
      form.pest_text = item.pest.name
    }
  }
  registration.value = {
    id: item.registration_id,
    label: text,
    moaCodes: item.moa_codes,
    phiDays: item.phi_days,
    dilution: item.dilution_text ?? '',
    pest: item.pest.name,
    banned: item.banned,
    cropMatches: item.applicable,
  }
  picking.value = false
}

onMounted(async () => {
  if (props.mode.type === 'prefill') {
    const { event_id, prescription_item_id, plan_id, recommendation_id } = props.mode
    suppressPlanWatch = true
    try {
      const data = await planning.logPrefill(props.farmerId, { event_id, prescription_item_id, plan_id, recommendation_id })
      prefill.value = data
      if (data.kind) form.kind = data.kind
      form.plan_id = data.plan_id ?? ''
      form.field_text = data.field_text
      form.crop_text = data.crop_text
      form.area_text = data.area_text
      form.title = data.title
      form.material_name = data.material_name
      form.dilution = data.dilution
      form.pest_text = data.pest_text
      if (data.amount_value) form.amount_value = data.amount_value
      if (data.amount_unit) form.amount_unit = data.amount_unit
      cropId.value = data.crop_id
      if (data.plan_archived && data.event_id && data.plan_id) archivedPlan.value = data.plan_id
    } catch (e) {
      error.value = (e as Error).message
    } finally {
      loading.value = false
      await nextTick()
      suppressPlanWatch = false
    }
  } else if (editing?.plan_id) void resolveCrop(editing.plan_id)
  else if (form.plan_id) applyPlan(activePlans.value.find((plan) => plan.id === form.plan_id))
})

function values(): LogFields {
  const kind = form.kind as LogKind
  const visible = SECTIONS[kind]
  const text = (value: string, shown = true) => (shown ? value.trim() : '')
  return {
    done_on: form.done_on,
    done_time: form.done_time || null,
    kind,
    field_text: text(form.field_text),
    crop_text: text(form.crop_text),
    title: text(form.title),
    material_name: text(form.material_name, visible.material),
    material_supplier: text(form.material_supplier, visible.material),
    material_lot: text(form.material_lot, visible.material),
    dilution: text(form.dilution, visible.dilution),
    amount_value: text(form.amount_value, visible.amount),
    amount_unit: text(form.amount_unit, visible.amount),
    area_text: text(form.area_text),
    yield_value: text(form.yield_value, visible.yield),
    yield_unit: text(form.yield_unit, visible.yield),
    yield_destination: text(form.yield_destination, visible.yield),
    labor_text: text(form.labor_text),
    pest_id: visible.pest ? form.pest_id : null,
    pest_text: text(form.pest_text, visible.pest),
    note: form.note.trim(),
  }
}

function setBusy(value: boolean) {
  busy.value = value
  emit('busy', value)
}

async function save() {
  if (savedLog.value) return
  if (!locked && !form.kind) {
    error.value = '請先選擇作業類型'
    return
  }
  if (!locked && form.done_on > todayIso) {
    error.value = '作業日期不能晚於今天；還沒做的事請記在規劃'
    return
  }
  setBusy(true)
  error.value = ''
  try {
    let log: WorkLog
    if (editing && locked) {
      const patch: LogPatch = {}
      if (form.note.trim() !== editing.note) patch.note = form.note.trim()
      if (registration.value && !editing.registration_snapshot) patch.registration_id = registration.value.id
      log = Object.keys(patch).length ? await planning.patchLog(editing.id, patch) : editing
    } else if (editing) {
      const patch: LogPatch = values()
      const next = form.kind === 'spray' && !hasPrescription.value ? registration.value?.id ?? null : null
      if (next !== originalRegistrationId) patch.registration_id = next
      log = await planning.patchLog(editing.id, patch)
    } else {
      log = await planning.createLog(props.farmerId, {
        ...values(),
        plan_id: form.plan_id || null,
        event_id: prefill.value?.event_id ?? null,
        prescription_item_id: prefill.value?.prescription_item_id ?? null,
        recommendation_id: prefill.value?.recommendation_id ?? null,
        registration_id: form.kind === 'spray' && !hasPrescription.value ? registration.value?.id ?? null : null,
      })
    }
    savedLog.value = log
    emit('saved', log)
    await uploadPhotos(log)
  } catch (e) {
    error.value = (e as Error).message
    if (e instanceof PlanningError && e.status === 409 && error.value.includes('封存'))
      archivedPlan.value = prefill.value?.plan_id ?? form.plan_id
  } finally {
    setBusy(false)
  }
}

async function uploadPhotos(log: WorkLog) {
  const result: { uploaded: LogPhoto[]; failed: number } = uploader.value
    ? await uploader.value.uploadAll(log.id)
    : { uploaded: [], failed: 0 }
  if (result.uploaded.length) {
    const current = savedLog.value ?? log
    const updated: WorkLog = { ...current, plan: undefined, photos: [...current.photos, ...result.uploaded] }
    savedLog.value = updated
    emit('saved', updated)
  }
  uploadFailed.value = result.failed
  if (!result.failed) emit('done')
}

async function retry() {
  if (!savedLog.value) return
  setBusy(true)
  try {
    await uploadPhotos(savedLog.value)
  } finally {
    setBusy(false)
  }
}
</script>

<template>
  <form class="farm-form wlf" @submit.prevent="save">
    <p v-if="loading" class="quiet-state">正在帶入資料⋯</p>
    <template v-else>
      <div v-if="banner" class="wlf-prefill"><Icon name="info" :size="16" /><span>{{ banner }}</span></div>
      <div v-if="archivedPlan" class="wlf-archived" role="alert">
        <Icon name="warn" :size="16" />
        <span>這一期規劃已封存，封存的規劃不能再記錄事項，避免已結束的一期被改動。恢復規劃後就能記錄。</span>
        <button type="button" class="btn sm" @click="emit('restore-plan', archivedPlan)">恢復規劃</button>
      </div>

      <fieldset class="wlf-fields" :disabled="busy || !!savedLog || !!archivedPlan">
        <template v-if="editing && locked">
          <div class="source-note">
            這筆紀錄已超過 24 小時，只能修改備註、補照片{{ backfillable ? '或補選藥劑登記' : '' }}。內容要更正請寫在備註，原紀錄會保留給產銷履歷與查核。
          </div>
          <div class="wlf-locked">
            <b>{{ editing.title }}</b>
            <small>{{ editing.done_on }} · {{ editing.field_text }}</small>
          </div>
        </template>
        <template v-else>
          <div class="wlf-kinds" role="radiogroup" aria-label="作業類型">
            <button
              v-for="kind in LOG_KINDS"
              :key="kind.key"
              type="button"
              role="radio"
              class="wlf-kind"
              :class="[kind.tone, { on: form.kind === kind.key }]"
              :aria-checked="form.kind === kind.key"
              :disabled="(hasPrescription && kind.key !== 'spray') || (!!recommendationKind && kind.key !== recommendationKind)"
              @click="form.kind = kind.key"
            >
              <span class="i"><Icon :name="icon(kind.icon)" :size="20" /></span>{{ kind.name }}
            </button>
          </div>
          <div class="form-grid">
            <label>實際日期<input v-model="form.done_on" type="date" required min="2000-01-01" :max="todayIso" /></label>
            <label>時間（選填）<input v-model="form.done_time" type="time" /></label>
          </div>

          <div class="wlf-group">
            <span class="wlf-label">田區</span>
            <div v-if="fieldOptions.length && !fromEvent" class="wlf-chips">
              <button
                v-for="name in fieldOptions"
                :key="name"
                type="button"
                class="chip"
                :class="{ on: form.field_text === name }"
                :aria-pressed="form.field_text === name"
                @click="chooseField(name)"
              >
                {{ name }}
              </button>
            </div>
            <div class="form-grid">
              <label>田區名稱<input v-model="form.field_text" required maxlength="160" :readonly="fromEvent" placeholder="例如：東側一區" /></label>
              <label>作物<input v-model="form.crop_text" maxlength="80" :required="form.kind === 'harvest'" placeholder="例如：芒果" /></label>
            </div>
          </div>
          <label v-if="!editing && !fromEvent && activePlans.length"
            >屬於哪一期規劃（選填）<select v-model="form.plan_id">
              <option value="">不指定</option>
              <option v-for="plan in activePlans" :key="plan.id" :value="plan.id">{{ plan.field_name }} · {{ plan.crop_name }}</option>
            </select>
            <small>指定後帶入作物與面積，藥劑登記候選也會限定這個作物。</small>
          </label>
          <label>做了什麼<input v-model="form.title" maxlength="200" placeholder="留空會依類型與資材自動產生" /></label>
        </template>

        <section v-if="showDrug" class="wlf-drug">
          <div v-if="hasPrescription" class="wlf-source">
            <Icon name="file" :size="16" />
            <div>
              <b>處方 {{ rxCaseNo }}</b>
              <span v-if="rx?.moa_codes.length" class="wlf-tags"><span v-for="code in rx.moa_codes" :key="code" class="tag moa">{{ code }}</span></span>
              <small>作用機制與安全採收期取自處方快照{{ rx && rx.phi_days !== null ? `；安全採收期 ${rx.phi_days} 天` : '' }}</small>
              <small v-if="rx?.dose">處方每公頃用量 {{ rx.dose }}；實際用量請填在下方。</small>
            </div>
          </div>
          <div v-else-if="registration && !picking" class="wlf-source" :class="{ crit: registration.banned }">
            <Icon name="flask" :size="16" />
            <div>
              <b>{{ registration.label }}<template v-if="registration.pest"> → {{ registration.pest }}</template></b>
              <span class="wlf-tags">
                <span v-for="code in registration.moaCodes" :key="code" class="tag moa">{{ code }}</span>
                <span v-if="!registration.moaCodes.length" class="tag warn">作用機制未載明</span>
              </span>
              <small
                >安全採收期 {{ registration.phiDays === null ? '未載明' : `${registration.phiDays} 天` }}<template v-if="harvestDate">
                  → {{ shortDay(harvestDate) }} 起可採</template
                ></small
              >
              <small v-if="registration.banned" class="crit-text">此登記含禁用成分。仍會照實記錄，請與植物醫生確認。</small>
              <small v-if="registration.cropMatches === false" class="warn-text">此登記不是這個作物的核准用法。</small>
            </div>
            <span v-if="canChangeRegistration" class="wlf-source-actions">
              <button type="button" class="text-btn" @click="picking = true">更換</button>
              <button v-if="!locked" type="button" class="text-btn" @click="registration = null">改為手動輸入</button>
            </span>
          </div>
          <RegistrationPicker
            v-if="picking && canChangeRegistration"
            :crop-id="cropId"
            :crop-name="form.crop_text"
            @pick="pick"
            @cancel="picking = false"
          />
          <button
            v-else-if="!registration && !hasPrescription && canChangeRegistration"
            type="button"
            class="btn wlf-pick"
            @click="picking = true"
          >
            <Icon name="flask" :size="16" />從藥劑登記挑選<small>帶入作用機制與安全採收期</small>
          </button>
        </section>

        <template v-if="!locked">
          <div v-if="section.material" class="form-grid">
            <label
              >{{ form.kind === 'spray' ? '藥劑名稱' : form.kind === 'fertilize' ? '資材品名' : '資材（選填）'
              }}<input v-model="form.material_name" maxlength="200" :required="form.kind === 'spray'"
            /></label>
            <label v-if="section.dilution">稀釋倍數<input v-model="form.dilution" maxlength="40" inputmode="decimal" placeholder="例如：1000" /></label>
          </div>
          <p v-if="moa === 'missing' && form.material_name.trim()" class="wlf-warn">
            <Icon name="warn" :size="14" />{{ MOA_MISSING }}
          </p>
          <div v-if="section.amount" class="form-grid">
            <label>{{ form.kind === 'irrigate' ? '用水量' : '用量' }}<input v-model="form.amount_value" maxlength="40" inputmode="decimal" /></label>
            <label>單位<input v-model="form.amount_unit" maxlength="20" :placeholder="unitHint" /></label>
          </div>
          <div v-if="section.material" class="form-grid">
            <label>供應者<input v-model="form.material_supplier" maxlength="120" placeholder="購買的店家或廠商" /></label>
            <label>批號<input v-model="form.material_lot" maxlength="80" placeholder="包裝上的批號" /></label>
          </div>
          <div v-if="section.yield" class="form-grid three">
            <label>產量<input v-model="form.yield_value" maxlength="40" inputmode="decimal" /></label>
            <label>單位<input v-model="form.yield_unit" maxlength="20" placeholder="公斤" /></label>
            <label>去向<input v-model="form.yield_destination" maxlength="120" placeholder="例如：果菜市場" /></label>
          </div>
          <label v-if="section.pest"
            >{{ form.kind === 'scout' ? '觀察到的病蟲害' : '針對的病蟲害'
            }}<input v-model="form.pest_text" maxlength="160" @input="form.pest_id = null"
          /></label>
          <div class="form-grid">
            <label>面積<input v-model="form.area_text" maxlength="60" placeholder="例如：0.5 公頃" /></label>
            <label>人力／工時<input v-model="form.labor_text" maxlength="120" placeholder="例如：2 人 · 1 小時" /></label>
          </div>
        </template>
        <label>備註<textarea v-model="form.note" rows="3" maxlength="4000" :placeholder="notePlaceholder" /></label>
        <div class="wlf-group">
          <span class="wlf-label">照片（拍攝時間與位置會自動讀取）</span>
          <PhotoUploader ref="uploader" />
        </div>
      </fieldset>

      <p v-if="!locked && missing.length" class="wlf-hint">
        <Icon name="info" :size="14" />產銷履歷還缺：{{ missingText(missing) }}。可以先存，之後在日誌補齊。
      </p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div v-if="savedLog && uploadFailed" class="wlf-upload-failed" role="alert">
        <span>紀錄已儲存；{{ uploadFailed }} 張照片上傳失敗。可以重試，或之後在日誌卡上補照片。</span>
        <div>
          <button type="button" class="btn sm primary" :disabled="busy" @click="retry">{{ busy ? '上傳中⋯' : '重試上傳' }}</button>
          <button type="button" class="btn sm" :disabled="busy" @click="emit('done')">稍後再補</button>
        </div>
      </div>
      <div v-else class="wlf-actions">
        <button class="btn primary wlf-save" :disabled="busy || !!savedLog || !!archivedPlan || (!locked && !form.kind)">
          <Icon name="check" :size="16" />{{ busy ? (savedLog ? '上傳照片中⋯' : '儲存中⋯') : label }}
        </button>
        <button type="button" class="btn ghost" :disabled="busy" @click="emit('cancel')">取消</button>
        <span v-if="fromEvent" class="wlf-foot">存檔後這條事項變淺色並帶「已記錄」標籤，日誌頁可再補照片。</span>
        <span v-else-if="form.kind === 'spray' && !editing" class="wlf-foot">施藥紀錄會提供給診療開單的抗藥性輪替檢查。</span>
      </div>
    </template>
  </form>
</template>

<style scoped>
.wlf { gap: 14px; }
.wlf-fields { border: 0; margin: 0; padding: 0; min-width: 0; display: flex; flex-direction: column; gap: 14px; }
.wlf-prefill { display: flex; align-items: flex-start; gap: 8px; padding: 10px 12px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); font-size: 13px; line-height: 1.55; }
.wlf-prefill .ico { margin-top: 2px; }
.wlf-archived { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 10px; padding: 10px 12px; border-radius: 10px; background: var(--warn-soft); color: #6d4506; font-size: 13px; }
.wlf-archived span { flex: 1; min-width: 200px; }
.wlf-kinds { display: grid; grid-template-columns: repeat(8, minmax(0, 1fr)); gap: 6px; }
.wlf-kind { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 8px 2px; border: 1px solid var(--line); border-radius: 10px; background: #fff; color: var(--ink-2); font-size: 12px; white-space: nowrap; }
.wlf-kind .i { width: 34px; height: 34px; border-radius: 9px; display: flex; align-items: center; justify-content: center; }
.wlf-kind.on { border-color: var(--brand); box-shadow: inset 0 0 0 1px var(--brand); color: var(--ink); font-weight: 600; background: #fbfdfb; }
.spray .i { background: #fbebe9; color: #b23b2d; }
.sack .i { background: #fdf3df; color: #9a6208; }
.drop .i { background: #e9f0f9; color: #24578f; }
.work .i { background: #e7f1ea; color: var(--brand); }
.harvest .i { background: #efe9f6; color: #5b3d8a; }
.soil .i { background: #f3ece4; color: #7a5a3a; }
.eye .i { background: #eef0f2; color: #4f5d6b; }
.other .i { background: #f1f3f0; color: #5d6a62; }
.wlf-group { display: flex; flex-direction: column; gap: 8px; }
.wlf-label { font-size: 13px; font-weight: 600; color: var(--ink-2); }
.wlf-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.wlf-chips .chip { font: inherit; font-size: 14px; }
.wlf-drug { display: flex; flex-direction: column; gap: 8px; }
.wlf-source { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: #fafbf9; font-size: 13px; }
.wlf-source > .ico { margin-top: 3px; color: var(--brand); }
.wlf-source > div { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.wlf-source small { color: var(--muted); font-size: 12px; }
.wlf-source.crit { border-color: #efb8b2; background: var(--crit-soft); }
.wlf-source-actions { display: flex; flex-direction: column; align-items: flex-end; }
.wlf-source-actions .text-btn { padding: 2px 0; font-size: 12px; white-space: nowrap; }
.wlf-tags { display: flex; flex-wrap: wrap; gap: 4px; }
.crit-text { color: var(--crit) !important; }
.warn-text { color: var(--warn) !important; }
.wlf-pick { justify-content: flex-start; flex-wrap: wrap; height: auto; min-height: 44px; padding: 8px 14px; border-style: dashed; }
.wlf-pick small { color: var(--muted); font-weight: 400; font-size: 12px; }
.wlf-warn { display: flex; align-items: center; gap: 6px; margin: -6px 0 0; font-size: 12px; color: var(--warn); }
.wlf-hint { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 12px; color: var(--muted); }
.wlf-locked { display: flex; flex-direction: column; font-size: 14px; }
.wlf-locked small { color: var(--muted); font-size: 12px; }
.three { grid-template-columns: repeat(3, minmax(0, 1fr)); }
.wlf-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.wlf-foot { margin-left: auto; font-size: 12px; color: var(--muted); }
.wlf-upload-failed { display: flex; flex-direction: column; gap: 8px; padding: 12px; border-radius: 10px; background: var(--crit-soft); color: #7a2119; font-size: 13px; }
.wlf-upload-failed div { display: flex; gap: 8px; }
@media (max-width: 760px) {
  .wlf-kinds { grid-template-columns: repeat(4, minmax(0, 1fr)); }
}
@media (max-width: 660px) {
  .wlf-kind { padding: 10px 2px; font-size: 13px; }
  .wlf-kind .i { width: 40px; height: 40px; }
  .wlf-save { width: 100%; height: 52px; font-size: 16px; }
  .wlf-actions .ghost { width: 100%; }
  .wlf-foot { margin-left: 0; width: 100%; text-align: center; }
  .three { grid-template-columns: 1fr 1fr; }
}
</style>
