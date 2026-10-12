<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { ConsultCase } from '@/api/types'
import { toast } from '@/composables/toast'

const props = defineProps<{ caseData: ConsultCase; itemDirty: boolean; busy: boolean }>()
const emit = defineEmits<{ updated: [value: ConsultCase]; working: [value: boolean] }>()
const fields = ref({ symptoms: '', diagnosis_text: '', cultural_control: '', examined_by: [] as string[], diagnosis_basis: [] as string[] })
const baseline = ref('')
const working = ref(false)
const confirmIssue = ref(false)
const preview = ref('')
const locked = computed(() => props.caseData.status === 'issued')
const dirty = computed(() => JSON.stringify(fields.value) !== baseline.value)
const hasBlock = computed(() => props.caseData.items.some(item => item.checks.some(check => check.level === 'block')))
watch(() => props.caseData.id, () => {
  const c = props.caseData
  fields.value = { symptoms: c.symptoms, diagnosis_text: c.diagnosis_text, cultural_control: c.cultural_control, examined_by: [...c.examined_by], diagnosis_basis: [...c.diagnosis_basis] }
  baseline.value = JSON.stringify(fields.value)
  closePreview(); confirmIssue.value = false
}, { immediate: true })
async function save() {
  working.value = true; emit('working', true)
  try {
    const value = await api.patchConsultCase(props.caseData.id, { ...fields.value, version: props.caseData.version })
    emit('updated', value); baseline.value = JSON.stringify(fields.value); toast.ok('診察紀錄已儲存。')
  } catch (e) { toast.error((e as Error).message) }
  finally { working.value = false; emit('working', false) }
}
async function showPdf() {
  working.value = true; emit('working', true)
  try { closePreview(); preview.value = URL.createObjectURL(await api.consultReport(props.caseData.id)) }
  catch (e) { toast.error((e as Error).message) }
  finally { working.value = false; emit('working', false) }
}
async function issue() {
  if (dirty.value || props.itemDirty || hasBlock.value || locked.value) return
  working.value = true; emit('working', true)
  try { emit('updated', await api.issueConsultCase(props.caseData.id, props.caseData.version)); confirmIssue.value = false; toast.ok('已開立並鎖定案件，可下載或重印診斷書。') }
  catch (e) { toast.error((e as Error).message) }
  finally { working.value = false; emit('working', false) }
}
function closePreview() { if (preview.value) URL.revokeObjectURL(preview.value); preview.value = '' }
onBeforeUnmount(closePreview)
</script>
<template>
  <section class="report-panel">
    <h3>診察紀錄與診斷書 <span v-if="locked" class="tag brand">已開立 · 已鎖定</span></h3>
    <fieldset :disabled="locked || working || busy">
      <label>田間狀況與病徵<textarea v-model="fields.symptoms" rows="3" maxlength="10000" /></label>
      <label>診斷結果（可並列多種病蟲害）<textarea v-model="fields.diagnosis_text" rows="2" maxlength="160" /></label>
      <div class="options"><span>診察方式</span><label v-for="[key,name] in [['on_site','現場勘查'],['sample','樣本送診'],['photo','照片研判'],['other','其他']]" :key="key"><input v-model="fields.examined_by" type="checkbox" :value="key">{{ name }}</label></div>
      <div class="options"><span>診斷依據</span><label v-for="[key,name] in [['visual','目視'],['microscopy','鏡檢'],['culture','分離培養'],['lab','送鑑定'],['other','其他']]" :key="key"><input v-model="fields.diagnosis_basis" type="checkbox" :value="key">{{ name }}</label></div>
      <label>非藥劑防治建議<textarea v-model="fields.cultural_control" rows="2" maxlength="10000" /></label>
      <button v-if="!locked" class="btn sm" :disabled="!dirty" @click="save">儲存診察紀錄</button>
    </fieldset>
    <p v-if="dirty || itemDirty" class="small warning">尚有未儲存的編輯，請先儲存後預覽或開立。</p>
    <p v-if="hasBlock" class="small warning">處方含不可開立的檢查結果，請先處理。</p>
    <div class="actions"><button class="btn" :disabled="working || busy || dirty || itemDirty" @click="showPdf">{{ locked ? '重印診斷書' : '預覽診斷書' }}</button><button v-if="!locked" class="btn primary" :disabled="working || busy || dirty || itemDirty || hasBlock" @click="confirmIssue = true">開立並鎖定</button></div>
    <div v-if="confirmIssue" class="confirm" role="alert"><b>確認開立案件 {{ caseData.case_no }}？</b><p>開立後不可修改或刪除，重印會使用目前已儲存的處方與檢查。</p><button class="btn sm primary" :disabled="working || busy" @click="issue">確認開立</button> <button class="btn sm" :disabled="working" @click="confirmIssue = false">返回核對</button></div>
    <div v-if="preview" class="pdf-overlay" role="dialog" aria-modal="true" aria-label="診斷書預覽"><div class="pdf-dialog"><div class="actions"><b>{{ locked ? '診斷書重印' : '草稿預覽' }} · 暫定版型</b><a :href="preview" :download="`diagnosis-${caseData.case_no}.pdf`" class="btn sm">下載 PDF</a><button class="btn sm" @click="closePreview">關閉預覽</button></div><iframe :src="preview + '#view=FitH&navpanes=0'" title="診斷書 PDF" /><p class="small">如裝置不支援內嵌預覽，請下載 PDF 開啟。</p></div></div>
  </section>
</template>
<style scoped>
.report-panel { padding: 18px; border-top: 1px solid var(--line); } h3 { font-size: 16px; margin-bottom: 12px; } fieldset { border: 0; margin: 0; padding: 0; display: grid; gap: 10px; } label { display: grid; gap: 4px; font-size: 12px; color: var(--ink-2); } textarea { width: 100%; font: inherit; border: 1px solid var(--line-strong); border-radius: 8px; padding: 8px; resize: vertical; } .options { display: flex; flex-wrap: wrap; gap: 8px; font-size: 12px; }.options label { display: flex; align-items: center; }.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; align-items: center; }.warning { color: var(--warn); margin-top: 10px; }.confirm { background: var(--warn-soft); border-radius: 10px; padding: 12px; margin-top: 12px; font-size: 13px; }.confirm p { margin: 8px 0; }.pdf-overlay { position: fixed; inset: 0; z-index: 90; background: #1b2620aa; display: grid; place-items: center; padding: 20px; }.pdf-dialog { background: var(--bg); border-radius: 14px; padding: 16px; width: min(950px,100%); height: 94vh; display: flex; flex-direction: column; gap: 10px; }.pdf-dialog .actions { margin: 0; }.pdf-dialog b { margin-right: auto; } iframe { width: 100%; flex: 1; border: 0; }button:disabled { opacity: .55; cursor: not-allowed; }
</style>
