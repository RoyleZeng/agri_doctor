<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { Catalog, Plan, PlanInput } from '@/api/planning'
import { planning } from '@/api/planning'
import { day, plus, today } from '@/composables/farmDates'
import FarmModal from './FarmModal.vue'
const props = defineProps<{ catalog: Catalog; farmerId?: string }>()
const emit = defineEmits<{ close: []; saved: [Plan] }>()
const step = ref(1)
const search = ref('')
const key = ref<string | null>('rice')
const busy = ref(false)
const error = ref('')
const data = reactive<PlanInput>({
  crop_key: 'rice',
  crop_name: '水稻',
  field_name: '',
  variety: '',
  area: '',
  planted_on: today(),
  end_on: plus(today(), 120),
  notes: '',
})
const template = computed(() =>
  props.catalog.crops.find((c) => c.key === key.value),
)
const matches = computed(() =>
  props.catalog.crops.filter((c) =>
    [c.name, ...c.aliases].some((n) => n.includes(search.value.trim())),
  ),
)
watch(key, () => {
  data.crop_key = key.value
  data.crop_name = template.value?.name || search.value
  data.end_on = plus(data.planted_on, template.value?.days || 90)
})
watch(search, () => {
  const exact = props.catalog.crops.find((c) =>
    [c.name, ...c.aliases].includes(search.value.trim()),
  )
  if (exact) key.value = exact.key
})
watch(
  () => data.planted_on,
  () => {
    if (data.planted_on)
      data.end_on = plus(data.planted_on, template.value?.days || 90)
  },
)
function next() {
  error.value = ''
  if (key.value && !matches.value.some((c) => c.key === key.value)) {
    error.value = '請點選搜尋結果，或選擇自訂其他作物'
    return
  }
  if (!data.crop_name.trim()) {
    error.value = '請輸入作物名稱'
    return
  }
  step.value = 2
}
async function save() {
  error.value = ''
  if (
    day(data.end_on) - day(data.planted_on) < 14 ||
    day(data.end_on) - day(data.planted_on) > 730
  ) {
    error.value = '規劃長度請設定為14～730天'
    return
  }
  busy.value = true
  try {
    emit('saved', await planning.createPlan({ ...data, farmer_id: props.farmerId }))
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>
<template>
  <FarmModal title="新增作物規劃" wide @close="!busy && emit('close')">
    <div class="form-steps">
      <span :class="{ active: step === 1 }">1 選擇作物</span><span>→</span
      ><span :class="{ active: step === 2 }">2 填寫種植資料</span>
    </div>
    <form v-if="step === 1" class="farm-form" @submit.prevent="next">
      <div>
        <h3>這一期，想種什麼？</h3>
        <p class="form-hint">點選作物，或輸入您習慣的名稱，例如「高麗菜」。</p>
      </div>
      <label
        >搜尋作物<input
          v-model="search"
          type="search"
          placeholder="搜尋作物或別名"
          autofocus
          maxlength="80"
      /></label>
      <div class="crop-options">
        <button
          v-for="c in matches"
          :key="c.key"
          type="button"
          class="crop-option"
          :class="{ selected: key === c.key }"
          :aria-pressed="key === c.key"
          @click="key = c.key"
        >
          <span class="crop-emoji">{{ c.emoji }}</span
          ><strong>{{ c.name }}</strong
          ><small>{{ c.aliases[0] }} · 參考範本</small
          ><span class="option-check">{{
            key === c.key ? '✓ 已選擇' : '選擇'
          }}</span>
        </button>
      </div>
      <p v-if="!matches.length" class="form-hint">
        目前沒有相符範本，可以先建立自訂作物。
      </p>
      <button
        class="custom-option"
        type="button"
        :class="{ selected: key === null }"
        :aria-pressed="key === null"
        @click="key = null"
      >
        ＋ 自訂其他作物 <span>自行新增事項，也可請植物醫生協助規劃</span>
      </button>
      <label v-if="key === null"
        >作物名稱<input
          v-model="data.crop_name"
          required
          maxlength="80"
          placeholder="例如：芭樂"
      /></label>
      <p v-if="key === null" class="form-hint">
        此作物尚無經整理的範本，系統會建立空白時程，不自動推測病蟲害。
      </p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="form-actions">
        <button class="btn" type="button" @click="emit('close')">取消</button
        ><button class="btn primary" type="submit">下一步：種植資料 →</button>
      </div>
    </form>
    <form v-else class="farm-form" @submit.prevent="save">
      <div class="chosen-crop">
        <span>{{ template?.emoji || '🌱' }}</span>
        <div>
          <strong>{{ data.crop_name }}</strong>
          <p>{{ template?.scope || '自訂作物 · 空白規劃' }}</p>
        </div>
        <button class="btn ghost sm" type="button" @click="step = 1">
          更換
        </button>
      </div>
      <div class="form-grid">
        <label
          >田區名稱 <span class="required">必填</span
          ><input
            v-model="data.field_name"
            required
            maxlength="100"
            placeholder="例如：溪畔一區、溫室A" /></label
        ><label
          >品種 <small>選填</small
          ><input
            v-model="data.variety"
            maxlength="80"
            placeholder="例如：台農71號"
        /></label>
      </div>
      <div class="form-grid">
        <label
          >{{ template?.anchor || '種植日' }} <span class="required">必填</span
          ><input
            v-model="data.planted_on"
            type="date"
            required
            min="2000-01-01"
            max="2098-01-01" /></label
        ><label
          >預計本期結束日 <span class="required">必填</span
          ><input
            v-model="data.end_on"
            type="date"
            required
            :min="plus(data.planted_on, 14)"
            :max="plus(data.planted_on, 730)"
        /></label>
      </div>
      <p class="form-hint">
        以{{
          template?.anchor || '種植日'
        }}為第0天。整段時程會依起訖日期等比例估算；建立後可逐項調整日期。番茄可將本期結束日設為最後一批採收日。
      </p>
      <label
        >面積 <small>選填</small
        ><input
          v-model="data.area"
          maxlength="60"
          placeholder="例如：0.5 公頃、3 分地"
      /></label>
      <label
        >栽培備註 <small>選填</small
        ><textarea
          v-model="data.notes"
          maxlength="2000"
          rows="2"
          placeholder="例如：露天栽培、排水較慢、前一期種植情況"
        />
      </label>
      <div class="source-note">
        {{
          template
            ? '將產生生育時期、農事、肥培與病蟲害巡查。內容附官方來源；日期為待確認的規劃估計。'
            : '將建立作物與田區，再由您或已連結的植物醫生新增事項。'
        }}
      </div>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="form-actions">
        <button class="btn" type="button" :disabled="busy" @click="step = 1">
          上一步</button
        ><button class="btn primary" :disabled="busy" type="submit">
          {{ busy ? '正在儲存…' : '建立我的作物規劃' }}
        </button>
      </div>
    </form>
  </FarmModal>
</template>
<style scoped>
.form-steps {
  display: flex;
  gap: 12px;
  color: var(--muted);
  font-size: 13px;
  padding-bottom: 24px;
}
.form-steps .active {
  color: var(--brand);
  font-weight: 600;
}
.crop-options {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 10px;
}
.crop-option {
  display: flex;
  align-items: center;
  flex-direction: column;
  gap: 5px;
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 18px 8px;
  background: white;
  color: var(--ink);
  cursor: pointer;
}
.crop-option.selected,
.custom-option.selected {
  border-color: var(--brand);
  background: #f1f7ef;
  box-shadow: 0 0 0 1px var(--brand);
}
.crop-emoji {
  font-size: 32px;
}
.crop-option strong {
  font-size: 16px;
}
.crop-option small {
  color: var(--ink-2);
  font-size: 11px;
}
.option-check {
  color: var(--brand);
  margin-top: 8px;
  font-size: 12px;
}
.custom-option {
  border: 1px dashed var(--line-strong);
  padding: 14px;
  background: #fafbf9;
  text-align: left;
  border-radius: 10px;
  color: var(--brand);
  cursor: pointer;
}
.custom-option span {
  display: block;
  font-size: 12px;
  color: var(--ink-2);
  margin-top: 4px;
}
.chosen-crop {
  display: flex;
  gap: 14px;
  align-items: center;
  background: var(--brand-soft);
  border-radius: 12px;
  padding: 12px;
}
.chosen-crop > span {
  font-size: 30px;
}
.chosen-crop p {
  font-size: 12px;
  color: var(--ink-2);
}
.chosen-crop button {
  margin-left: auto;
}
</style>
