<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { planning, type ExportFormat, type ExportPreview, type Farmer } from '@/api/planning'
import { exportFileName, exportRangeError, monthRange, shortDay } from '@/composables/farmLog'
import { toast } from '@/composables/toast'
import FarmModal from './FarmModal.vue'
import Icon from './Icon.vue'

/**
 * 匯出農事紀錄表。先用預覽端點把缺漏欄位與安全採收期問題列出來，再產檔；
 * 這些提醒只告知、不擋匯出——紀錄表要照實呈現日誌內容。
 */
const props = defineProps<{ farmer: Farmer; month: string; fields: string[]; field: string }>()
const emit = defineEmits<{ close: []; focus: [item: { id: string; done_on: string }] }>()

const range = monthRange(props.month)
const from = ref(range.from)
const to = ref(range.to)
const field = ref(props.field)
const format = ref<ExportFormat>('pdf')
const photos = ref(true)
const gps = ref(true)
const phi = ref(true)
const preview = ref<ExportPreview | null>(null)
const loading = ref(false)
const previewError = ref('')
const busy = ref(false)
const error = ref('')

const rangeError = computed(() => exportRangeError(from.value, to.value))
// 照片全部內嵌進 PDF，超過上限伺服器會拒絕；Excel 只列清單不受影響。
const photoBlocked = computed(() => format.value === 'pdf' && photos.value && !!preview.value?.too_many_photos)

let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0
watch(
  [from, to, field],
  () => {
    clearTimeout(timer)
    sequence++
    preview.value = null
    previewError.value = ''
    loading.value = !rangeError.value
    if (rangeError.value) return
    timer = setTimeout(() => void loadPreview(), 300)
  },
  { immediate: true },
)

async function loadPreview() {
  const mine = ++sequence
  try {
    const data = await planning.exportPreview(props.farmer.id, { from: from.value, to: to.value, field: field.value || undefined })
    if (mine === sequence) preview.value = data
  } catch (e) {
    if (mine === sequence) previewError.value = (e as Error).message
  } finally {
    if (mine === sequence) loading.value = false
  }
}

async function generate() {
  if (rangeError.value || busy.value || photoBlocked.value) return
  busy.value = true
  error.value = ''
  try {
    const blob = await planning.exportLogs(props.farmer.id, {
      from: from.value,
      to: to.value,
      field: field.value || undefined,
      format: format.value,
      photos: photos.value,
      gps: gps.value,
      phi: phi.value,
    })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exportFileName(props.farmer.farm_name, from.value, to.value, format.value)
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
    toast.ok(format.value === 'pdf' ? '已產出農事紀錄表 PDF' : '已產出農事紀錄 Excel')
    emit('close')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <FarmModal title="匯出農事紀錄" wide sheet @close="!busy && emit('close')">
    <form class="farm-form" @submit.prevent="generate">
      <div class="ex-grid">
        <label>起始日期<input v-model="from" type="date" required /></label>
        <label>結束日期<input v-model="to" type="date" required :min="from" /></label>
        <label
          >田區<select v-model="field">
            <option value="">全部田區</option>
            <option v-for="name in fields" :key="name" :value="name">{{ name }}</option>
          </select></label
        >
      </div>
      <p v-if="rangeError" class="form-error" role="alert">{{ rangeError }}</p>

      <div class="ex-formats" role="radiogroup" aria-label="匯出格式">
        <button type="button" role="radio" :aria-checked="format === 'pdf'" :class="{ on: format === 'pdf' }" @click="format = 'pdf'">
          <b>PDF 紀錄表</b><small>對齊產銷履歷紀錄表，可列印、簽名後送查核</small>
        </button>
        <button type="button" role="radio" :aria-checked="format === 'xlsx'" :class="{ on: format === 'xlsx' }" @click="format = 'xlsx'">
          <b>Excel</b><small>一列一筆，另有照片張數與缺漏欄位，方便整理</small>
        </button>
      </div>

      <div class="ex-options">
        <label class="check-label"
          ><input v-model="photos" type="checkbox" />{{ format === 'pdf' ? '附照片附錄' : '附照片清單' }}<span
            v-if="preview"
            class="ex-muted"
            >（{{ preview.photo_count }} 張）</span
          ></label
        >
        <label class="check-label" :class="{ disabled: !photos }"
          ><input v-model="gps" type="checkbox" :disabled="!photos" />印出照片 GPS 座標</label
        >
        <p class="form-hint ex-indent">補助查核常用座標證明地點；要把檔案交給其他人時可以取消。</p>
        <label class="check-label"><input v-model="phi" type="checkbox" />附施藥安全採收期核對</label>
      </div>

      <section class="ex-preview" aria-live="polite">
        <p v-if="loading" class="quiet-state">正在檢查這段期間的紀錄⋯</p>
        <p v-else-if="previewError" class="form-error" role="alert">{{ previewError }}</p>
        <template v-else-if="preview">
          <p class="ex-count">
            <b>{{ preview.total }}</b> 筆紀錄 · <b>{{ preview.photo_count }}</b> 張照片<template v-if="phi">
              · 核對 <b>{{ preview.phi.checked }}</b> 次採收</template
            >
          </p>
          <p v-if="!preview.total" class="form-hint">這段期間沒有紀錄，匯出的表格會是空白的。</p>
          <div v-if="photoBlocked" class="ex-alert crit">
            照片 {{ preview.photo_count }} 張，超過一次附在 PDF 的上限 {{ preview.max_photos }} 張。請分期匯出（縮短期間）、選擇單一田區，或取消照片附錄。
          </div>
          <div v-if="phi && preview.phi.violations.length" class="ex-alert crit">
            <b><Icon name="warn" :size="14" />未達安全採收期</b>
            <ul>
              <li v-for="item in preview.phi.violations" :key="item.harvest_id">
                {{ shortDay(item.harvest_on) }} {{ item.field_text }} 採收：{{ item.message }}
              </li>
            </ul>
          </div>
          <div v-if="phi && preview.phi.unknown.length" class="ex-alert warn">
            <b>無法判斷安全採收期</b>
            <ul>
              <li v-for="item in preview.phi.unknown" :key="item.harvest_id">
                {{ shortDay(item.harvest_on) }} {{ item.field_text }} 採收：{{ item.message }}
              </li>
            </ul>
            <small>施藥時帶入處方或從藥劑登記挑選，才有安全採收期可以核對；已記錄的施藥可在日誌卡「補選登記」。</small>
          </div>
          <div v-if="preview.incomplete.length" class="ex-alert warn">
            <b>{{ preview.incomplete.length }} 筆產銷履歷欄位不齊</b>（紀錄表以黃底標示）
            <ul>
              <li v-for="item in preview.incomplete.slice(0, 8)" :key="item.id">
                <button type="button" class="text-btn" @click="emit('focus', item)">
                  {{ shortDay(item.done_on) }} {{ item.title }}
                </button>
                · 缺{{ item.missing.join('、') }}
              </li>
            </ul>
            <small v-if="preview.incomplete.length > 8">另有 {{ preview.incomplete.length - 8 }} 筆</small>
          </div>
          <p v-if="preview.rule_changed" class="form-hint">
            {{ preview.rule_changed }} 筆紀錄存檔時的欄位規則與目前版本不同；上面的缺漏依目前規則（{{ preview.rule_version }}）重新檢查，不會改動原紀錄。
          </p>
          <p class="form-hint">以上提醒不會擋下匯出；紀錄表照實呈現日誌內容。</p>
        </template>
      </section>

      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="form-actions">
        <button type="button" class="btn" :disabled="busy" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="busy || !!rangeError || photoBlocked">
          <Icon name="download" :size="16" />{{ busy ? '產出中⋯' : format === 'pdf' ? '產出 PDF 紀錄表' : '產出 Excel' }}
        </button>
      </div>
      <p v-if="busy && format === 'pdf' && photos" class="form-hint">照片較多時需要數十秒，請不要關閉視窗。</p>
    </form>
  </FarmModal>
</template>

<style scoped>
.ex-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.ex-formats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.ex-formats button { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; padding: 10px 12px; border: 1px solid var(--line-strong); border-radius: 10px; background: #fff; color: var(--ink); text-align: left; font: inherit; }
.ex-formats button.on { border-color: var(--brand); box-shadow: inset 0 0 0 1px var(--brand); background: var(--brand-soft); }
.ex-formats small { color: var(--muted); font-size: 12px; }
.ex-options { display: flex; flex-direction: column; gap: 6px; }
.ex-options .disabled { color: var(--muted); }
.ex-indent { margin: -4px 0 2px 26px; }
.ex-muted { color: var(--muted); font-weight: 400; }
.ex-preview { display: flex; flex-direction: column; gap: 8px; padding: 12px; border: 1px solid var(--line); border-radius: 12px; background: #fafbf9; }
.ex-count { margin: 0; font-size: 14px; }
.ex-alert { padding: 10px 12px; border-radius: 10px; font-size: 13px; line-height: 1.6; }
.ex-alert b { display: inline-flex; align-items: center; gap: 5px; }
.ex-alert ul { margin: 4px 0; padding-left: 18px; }
.ex-alert small { display: block; font-size: 12px; }
.ex-alert .text-btn { padding: 0; font-size: 13px; }
.ex-alert.crit { background: var(--crit-soft); color: #7a2119; }
.ex-alert.warn { background: var(--warn-soft); color: #6d4506; }
@media (max-width: 660px) {
  .ex-grid, .ex-formats { grid-template-columns: 1fr; }
  .form-actions .primary { width: 100%; height: 52px; font-size: 16px; }
}
</style>
