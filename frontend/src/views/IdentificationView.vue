<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import Icon from '@/components/Icon.vue'
import { toast } from '@/composables/toast'
import { demoCandidates, decisionLabels, reviewProblems, type CandidateReview } from '@/composables/identificationDemo'
import { role as sessionRole } from '@/stores/session'

interface Photo { id: string; url: string; name: string; organ: string; note: string; example: boolean }
/* 這一頁看到什麼由登入身分決定，不是頁面裡的開關。
 * 舊版是 `ref('doctor')` 加一個誰都能按的切換器——農民按一下就進到「醫師判讀紀錄」，
 * 可以填判讀理由、匯出成 JSON，看起來像是專業判讀。判讀是誰做的必須跟帳號一致。 */
const preview = ref<'doctor' | 'farmer' | null>(null)
const isAdmin = computed(() => sessionRole.value === 'admin')
const role = computed<'doctor' | 'farmer'>(() => {
  if (isAdmin.value && preview.value) return preview.value
  // 未登入的訪客不是植物醫生；機關看得到醫師視角（他們要讀得懂判讀），但存不了。
  return sessionRole.value === 'farmer' || sessionRole.value === null ? 'farmer' : 'doctor'
})
/** 機關與管理者預覽可以看，但不該留下掛在別人名下的判讀紀錄。 */
const canSaveReview = computed(() => role.value === 'doctor' && sessionRole.value === 'doctor')
const step = ref(1)
const crop = ref('')
const description = ref('')
const field = ref('')
const photos = ref<Photo[]>([])
const activePhotoId = ref('')
const activePhoto = computed(() => photos.value.find(p => p.id === activePhotoId.value) ?? photos.value[0])
const candidates = ref<CandidateReview[]>([])
const selectedCandidate = ref('')
const selected = computed(() => candidates.value.find(c => c.id === selectedCandidate.value))
const extraName = ref('')
const conclusion = ref('')
const followUp = ref('')
const consent = ref(false)
const savedAt = ref('')
const records = ref<{ id: string; at: string; crop: string; diagnoses: string[]; data: object }[]>([])
const modal = ref<'contact' | 'prescription' | null>(null)
const requestDrafted = ref(false)
const attempted = ref(false)
const problems = computed(() => reviewProblems(candidates.value))
const confirmed = computed(() => candidates.value.filter(c => c.decision === 'confirmed'))
const chosenDrugs = reactive<Record<string, boolean>>({})
const reviewStamp = computed(() => JSON.stringify([candidates.value, conclusion.value, followUp.value, consent.value, chosenDrugs]))
watch(reviewStamp, () => { savedAt.value = '' })
watch(() => JSON.stringify([crop.value, field.value, description.value, photos.value.map(p => [p.id,p.organ,p.note])]), () => {
  if (step.value === 1 && candidates.value.length) clearResults()
})

function clearResults() { step.value = 1; candidates.value = []; savedAt.value = ''; attempted.value = false; conclusion.value = ''; followUp.value = ''; consent.value = false; requestDrafted.value = false; Object.keys(chosenDrugs).forEach(k => delete chosenDrugs[k]) }
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  for (const file of Array.from(input.files ?? [])) {
    if (photos.value.length >= 6) { toast.warn('最多加入 6 張照片。'); break }
    if (!['image/jpeg','image/png','image/webp'].includes(file.type) || file.size > 10 * 1024 * 1024) { toast.warn(`${file.name}：請使用 10 MB 以內的 JPG、PNG 或 WebP。`); continue }
    const url = URL.createObjectURL(file)
    const img = new Image(); img.src = url
    try { await img.decode() } catch { URL.revokeObjectURL(url); toast.warn('無法讀取這張圖片，請換一張。'); continue }
    const id = crypto.randomUUID()
    photos.value.push({ id, url, name: file.name, organ: '葉片', note: '', example: false })
    activePhotoId.value = id
  }
  clearResults(); input.value = ''
}
function useExample() {
  releasePhotos()
  photos.value = [{ id: 'example-1', url: '/demo/field-example.svg', name: '田間症候示意圖', organ: '葉片', note: '示例：葉面斑點與葉背痕跡可能並存。', example: true }]
  activePhotoId.value = 'example-1'; crop.value = '示範作物'; field.value = '示範田區'; description.value = ''
  clearResults()
}
function removePhoto(id: string) {
  const item = photos.value.find(p => p.id === id)
  if (item && !item.example) URL.revokeObjectURL(item.url)
  photos.value = photos.value.filter(p => p.id !== id); clearResults()
}
function releasePhotos() { photos.value.filter(p => !p.example).forEach(p => URL.revokeObjectURL(p.url)) }
function analyze() {
  if (!photos.value.length || !crop.value.trim()) { toast.warn('請加入照片並填寫作物。田間敘述可留白。'); return }
  candidates.value = demoCandidates(photos.value.map(p => p.id)); selectedCandidate.value = candidates.value[0]!.id
  step.value = 2; attempted.value = false; savedAt.value = ''
}
function addCandidate() {
  if (!extraName.value.trim()) return
  const id = crypto.randomUUID()
  candidates.value.push({ id, name: extraName.value.trim(), kind: '醫師補充', observation: '醫師新增的候選判斷', nextStep: '記錄支持或排除的證據。', decision: 'pending', reason: '', photoIds: [], origin: 'doctor' })
  extraName.value = ''; selectedCandidate.value = id
}
function recordData() {
  return { schema_version: 'identification-demo-v1', origin: 'frontend_demo', model: null, training_eligible: false,
    role: role.value, recorded_at: new Date().toISOString(), crop: crop.value, field: field.value, description: description.value,
    photos: photos.value.map(({url,...p}) => p), proposals: candidates.value.map(c => ({ ...c, photoIds: [...c.photoIds] })),
    conclusion: conclusion.value, follow_up: followUp.value, training_consent_intent: consent.value,
    treatment_demo_candidate_ids: Object.keys(chosenDrugs).filter(id => chosenDrugs[id]),
    notice: '展示紀錄，非真實模型結果；不含照片檔，未上傳或送入訓練。' }
}
function saveReview() {
  if (!canSaveReview.value) { toast.warn('只有植物醫生帳號能保存判讀紀錄。'); return }
  attempted.value = true
  if (problems.value.length) { toast.warn('請完成各候選的判讀與理由後保存。'); return }
  const data = recordData()
  records.value.unshift({ id: crypto.randomUUID(), at: data.recorded_at, crop: crop.value, diagnoses: confirmed.value.map(c => c.name), data })
  savedAt.value = new Date().toLocaleTimeString('zh-TW'); toast.ok('展示紀錄已保留於本頁，可匯出 JSON；尚未上傳。')
}
function downloadRecord(data: object) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'}))
  const a = document.createElement('a'); a.href = url; a.download = 'field-review-demo.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
function prepareContact() { requestDrafted.value = true; toast.info('諮詢摘要已整理於本頁，尚未送給任何醫師。') }
onBeforeUnmount(releasePhotos)
</script>

<template>
<div class="wrap identify">
  <div class="heading"><div><p class="eyebrow">FIELD NOTES / 田間協作</p><h1>病蟲害輔助辨識</h1><p>讓複合問題被看見，也讓每一次專業判讀留下依據。</p></div><div v-if="isAdmin" class="role-switch" aria-label="管理者預覽視角"><span class="preview-tag">管理者預覽</span><button :class="{active:role === 'doctor'}" @click="preview = 'doctor'">植物醫生視角</button><button :class="{active:role === 'farmer'}" @click="preview = 'farmer'">農民視角</button></div></div>
  <div class="demo-notice"><Icon name="info" :size="18"/><div><b>互動展示 · 尚未連接辨識模型</b><span>照片只在本頁預覽；下方候選與用藥卡為固定展示內容，不是上傳照片的辨識結果。</span></div><a href="/demo/diagnosis-report.pdf" target="_blank" rel="noopener">診斷書版型 ↗</a></div>
  <nav class="steps" aria-label="辨識流程"><button :class="{current:step === 1}" @click="step = 1"><span>01</span>照片與觀察</button><button :disabled="!candidates.length" :class="{current:step === 2}" @click="step = 2"><span>02</span>複合候選與用藥</button><button :disabled="!candidates.length" :class="{current:step === 3}" @click="step = 3"><span>03</span>{{ role === 'doctor' ? '專業判讀與留存' : '聯繫植物醫生' }}</button></nav>

  <div class="identify-grid">
    <aside class="evidence card">
      <div class="card-h"><h2>田間影像</h2><span class="small muted">{{ photos.length }} / 6 張</span></div>
      <template v-if="activePhoto"><div class="photo-stage"><img :src="activePhoto.url" :alt="activePhoto.name"><span>{{ activePhoto.example ? '操作示意圖' : '本機照片 · 未上傳' }}</span></div><div class="thumbnails"><button v-for="(photo,i) in photos" :key="photo.id" :class="{chosen:activePhoto?.id === photo.id}" @click="activePhotoId = photo.id"><img :src="photo.url" :alt="`照片 ${i+1}`"><span>{{ i+1 }}</span></button></div><div class="photo-meta"><b>{{ activePhoto.name }}</b><label>拍攝部位<select v-model="activePhoto.organ" :disabled="step !== 1"><option>葉片</option><option>葉背</option><option>果實</option><option>莖部</option><option>根部</option><option>全株／田區</option></select></label><label>這張照片的觀察（選填）<textarea v-model="activePhoto.note" :disabled="step !== 1" rows="2" placeholder="例如：葉背有聚集痕跡" /></label><button v-if="step === 1" class="text-button" @click="removePhoto(activePhoto.id)">移除此張</button></div></template>
      <div v-else class="photo-empty"><Icon name="leaf" :size="42"/><h3>從一張田間照片開始</h3><p>全株、病徵近照、葉背與健康對照<br>多個角度，保留更多判讀線索。</p></div>
      <div v-if="step === 1" class="upload-actions"><label class="btn primary upload"><Icon name="plus" :size="17"/>加入照片<input type="file" accept="image/jpeg,image/png,image/webp" multiple @change="upload"></label><button class="btn" @click="useExample">載入展示案例</button><p>JPG / PNG / WebP，每張 10 MB 以內。敘述可留白。</p></div>
      <div v-if="step > 1" class="evidence-summary"><b>{{ crop }}</b><span>{{ field || '田區未填' }}</span><p>{{ description || '未提供田間敘述，待醫師問診補充。' }}</p><button class="text-button" @click="step = 1">返回補充照片與敘述</button></div>
    </aside>

    <main class="workspace">
      <section v-if="step === 1" class="card observation"><div class="card-h"><div><p class="eyebrow">01 / 記錄觀察</p><h2>先保留現場，再一起判讀</h2></div><Icon name="note" :size="23"/></div><div class="card-b"><div class="field-pair"><label>作物 <span class="required">必填</span><input v-model="crop" maxlength="80" placeholder="輸入作物名稱"></label><label>田區（選填）<input v-model="field" maxlength="160" placeholder="例如：北側果園"></label></div><label>田間敘述 <span class="muted">選填</span><textarea v-model="description" rows="5" maxlength="10000" placeholder="可以描述何時開始、哪些部位受影響、是否擴散，或最近的天氣與施作。沒有敘述也可以繼續。" /></label><div class="observation-tip"><Icon name="people" :size="21"/><div><b>一塊田，可能同時有好幾種問題</b><p>保留不同植株、部位及症狀的照片。後續可把每個病害、害蟲或逆境候選分別記錄，不必只選一個答案。</p></div></div><div class="next"><span class="small muted">展示流程不會上傳照片</span><button class="btn primary" :disabled="!photos.length || !crop.trim()" @click="analyze">查看展示候選 <Icon name="right" :size="16"/></button></div></div></section>

      <template v-if="step === 2">
        <section class="card"><div class="card-h"><div><p class="eyebrow">02 / 多重候選</p><h2>可能並存的問題</h2></div><span class="tag brand">{{ candidates.length }} 個候選</span></div><p class="candidate-intro">各候選可同時成立，也可能互相混淆。示範內容沒有模型機率，不代表診斷信心。</p><button v-for="candidate in candidates" :key="candidate.id" class="candidate" :class="{selected:selectedCandidate === candidate.id}" @click="selectedCandidate = candidate.id"><span class="candidate-icon"><Icon :name="candidate.kind === '蟲害' ? 'bug' : candidate.kind === '病害' ? 'disease' : 'leaf'" :size="22"/></span><span><b>{{ candidate.name }}</b><small>{{ candidate.kind }} · {{ candidate.origin === 'demo' ? '展示候選' : '醫師新增' }}</small></span><span class="tag" :class="candidate.decision === 'confirmed' ? 'ok' : 'info'">{{ decisionLabels[candidate.decision] }}</span><Icon name="right" :size="15"/></button></section>
        <section v-if="selected" class="card candidate-detail"><div class="card-h"><h2>{{ selected.name }}</h2><span class="small muted">觀察依據</span></div><div class="card-b"><p>{{ selected.observation }}</p><div class="next-evidence"><Icon name="search" :size="18"/><div><b>下一步需要的證據</b><p>{{ selected.nextStep }}</p></div></div><h3>推薦用藥區 · 互動示意</h3><p class="small muted">實際用藥需先確認作物與病蟲害，再查核三源、禁用及安全採收期。</p><div class="demo-drug"><span class="pill absent">核准登記待查核</span><h3>{{ selected.kind === '其他原因' ? '先排除非生物性因素' : '示範藥劑候選（非實際農藥）' }}</h3><p>稀釋、每公頃量、安全採收期：未載明</p><div class="pills"><span class="pill absent">植保 待查</span><span class="pill absent">食藥 待查</span><span class="pill absent">高改 待查</span></div><label v-if="role === 'doctor' && selected.kind !== '其他原因'" class="inline-check"><input v-model="chosenDrugs[selected.id]" type="checkbox">加入展示處方草稿（不寫入正式案件）</label><p v-else class="small">{{ role === 'farmer' ? '請交由植物醫生確認後再決定處置。' : '未確認病蟲害前，不以農藥作為預設處置。' }}</p></div><router-link to="/search" class="text-button">開啟正式三源用藥查詢 ↗</router-link></div></section>
        <div class="next"><span class="small muted">候選不是最終判定</span><button class="btn primary" @click="step = 3">{{ role === 'doctor' ? '進行專業判讀' : '整理諮詢摘要' }} <Icon name="right" :size="16"/></button></div>
      </template>

      <template v-if="step === 3 && role === 'doctor'">
        <section class="card review"><div class="card-h"><div><p class="eyebrow">03 / 專業回饋</p><h2>醫師判讀紀錄</h2></div><span class="small muted">各候選獨立標記</span></div><div class="card-b"><p class="small muted">確認、排除與尚不確定，都值得留下。請將理由連結到照片，保留與展示候選不同的判斷。</p><article v-for="candidate in candidates" :key="candidate.id" class="review-item"><div class="review-title"><b>{{ candidate.name }}</b><span class="small muted">{{ candidate.origin === 'doctor' ? '醫師新增' : '原始展示候選' }}</span></div><label>判讀結果<select v-model="candidate.decision"><option v-for="(label,key) in decisionLabels" :key="key" :value="key">{{ label }}</option></select></label><div class="photo-evidence"><span>佐證照片</span><label v-for="(photo,i) in photos" :key="photo.id"><input v-model="candidate.photoIds" type="checkbox" :value="photo.id">照片 {{ i+1 }} · {{ photo.organ }}</label></div><label>判讀理由<textarea v-model="candidate.reason" rows="2" maxlength="10000" placeholder="支持／排除的特徵、與其他原因的差異，或還缺少什麼證據" /></label></article><div class="add-candidate"><input v-model="extraName" maxlength="160" aria-label="補充候選名稱" placeholder="補充未列出的病蟲害或其他原因" @keydown.enter.prevent="addCandidate"><button class="btn sm" @click="addCandidate">新增候選</button></div><label>綜合判斷（選填）<textarea v-model="conclusion" rows="3" placeholder="多種問題的關係、主要與伴隨問題，或目前無法確認的部分" /></label><label>追蹤與驗證（選填）<textarea v-model="followUp" rows="2" placeholder="送驗項目、預計複診、需補拍的照片或施作後觀察" /></label><label class="inline-check consent"><input v-model="consent" type="checkbox">願意提供本案例供後續研究／模型改善（展示意向，尚未正式授權或上傳）</label><p v-if="!canSaveReview" class="small muted">目前以{{ sessionRole === 'agency' ? '主管機關' : '管理者預覽' }}身分檢視，可以閱讀判讀流程但不能保存紀錄。</p><p class="small muted">系統應分開保留原始候選與醫師回饋；展示資料不會被標記為可供訓練的真實案例。</p><ul v-if="attempted && problems.length" class="problems" role="alert"><li v-for="problem in problems" :key="problem">{{ problem }}</li></ul><div class="review-actions"><button class="btn primary" :disabled="!canSaveReview" :title="canSaveReview ? undefined : '只有植物醫生帳號能保存判讀紀錄'" @click="saveReview">保存展示紀錄</button><button class="btn" :disabled="!savedAt" @click="downloadRecord(recordData())">匯出回饋 JSON</button><button class="btn" :disabled="!savedAt || !confirmed.length" @click="modal = 'prescription'">預覽展示處方簽</button></div><p v-if="savedAt" role="status" class="saved">{{ savedAt }} 已保存在本頁記憶體；重新整理會清除，請先匯出。</p></div></section>
      </template>
      <section v-if="step === 3 && role === 'farmer'" class="card contact"><div class="card-h"><div><p class="eyebrow">03 / 尋求專業協助</p><h2>讓植物醫生一起看現場</h2></div><Icon name="people" :size="24"/></div><div class="card-b"><p>將照片、田間描述和多個待確認候選一起整理，避免只討論其中一種問題。</p><div class="summary"><b>{{ crop }} · {{ field || '未填田區' }}</b><p>{{ photos.length }} 張影像 · {{ description ? '已填田間敘述' : '敘述未填，可由醫師問診補充' }}</p><p v-for="c in candidates" :key="c.id">待確認：{{ c.name }}</p></div><button class="btn primary" @click="modal = 'contact'">聯繫植物醫生 · 展示</button><p class="small muted">下一步預覽諮詢摘要；本展示不會送出訊息或通知醫師。</p></div></section>
      <section v-if="records.length" class="card records"><div class="card-h"><h2>本次展示留下的紀錄</h2><span class="small muted">{{ records.length }} 筆 · 本頁暫存</span></div><div v-for="r in records" :key="r.id" class="record"><div><b>{{ r.crop }}</b><p>{{ r.diagnoses.join('、') || '尚無確認病蟲害／需進一步鑑定' }}</p></div><button class="btn sm" @click="downloadRecord(r.data)">匯出此版</button></div></section>
    </main>
  </div>
  <div v-if="modal" class="modal-overlay" @click.self="modal = null"><section class="modal-card" role="dialog" aria-modal="true" :aria-label="modal === 'contact' ? '諮詢摘要預覽' : '展示處方簽'"><div class="card-h"><h2>{{ modal === 'contact' ? '諮詢摘要預覽' : '展示處方簽' }}</h2><button class="btn sm" @click="modal = null">關閉</button></div><div class="card-b"><span class="tag warn">展示用途 · 不會正式送出或開立</span><h3>{{ crop }} · {{ field || '田區未填' }}</h3><p>{{ description || '田間敘述未填' }}</p><template v-if="modal === 'contact'"><h3>希望醫師協助確認</h3><p v-for="c in candidates" :key="c.id">{{ c.name }}（展示候選，未經辨識）</p><label>希望醫師協助的問題<textarea rows="3" placeholder="例如：哪些問題可能同時發生？還需要補哪些照片？" /></label><button class="btn primary" @click="prepareContact">整理諮詢草稿</button><p v-if="requestDrafted" role="status" class="saved">草稿已整理。尚未串接醫師名單、聯絡或通知服務。</p></template><template v-else><h3>醫師確認的問題</h3><p v-for="c in confirmed" :key="c.id">{{ c.name }} — {{ c.reason }}</p><h3>展示用藥草稿</h3><p v-for="c in candidates.filter(c => chosenDrugs[c.id])" :key="c.id">{{ c.name }}：示範藥劑候選（非實際農藥）；所有劑量與 PHI 待查核。</p><p v-if="!Object.values(chosenDrugs).some(Boolean)" class="muted">未加入展示藥劑；可保留非藥劑防治與追蹤建議。</p><p>{{ followUp || '追蹤計畫未填' }}</p><router-link class="btn primary" to="/consult">前往正式診療開單</router-link><p class="small muted">展示內容不會自動寫入正式案件，請依真實診察紀錄重新建立。</p></template></div></section></div>
</div>
</template>

<style scoped>
.photo-stage { overflow: hidden; }
.photo-stage img { position: absolute; inset: 0; min-height: 0; min-width: 0; }
.identify { padding-bottom: 20px; } .heading { display: flex; justify-content: space-between; align-items: center; gap: 20px; margin: 8px 0 24px; }.heading h1 { font-size: 28px; margin: 5px 0 6px; }.heading p:not(.eyebrow) { color: var(--muted); }.preview-tag { align-self: center; font-size: 11px; color: var(--muted); padding: 0 8px; letter-spacing: .04em; }.role-switch { display: flex; padding: 4px; background: #e8ede7; border-radius: 12px; flex-shrink: 0; }.role-switch button { border: 0; background: transparent; padding: 9px 14px; border-radius: 9px; color: var(--ink-2); font: inherit; font-size: 13px; cursor: pointer; }.role-switch button.active { background: #fff; color: var(--brand); box-shadow: var(--shadow); font-weight: 600; }.demo-notice { display: flex; align-items: center; gap: 12px; background: #eef3ed; border: 1px solid #d6e2d3; border-radius: 12px; padding: 14px 18px; color: var(--brand); font-size: 13px; }.demo-notice span { display: block; margin-top: 2px; color: var(--ink-2); }.demo-notice a { margin-left: auto; white-space: nowrap; }.steps { display: flex; gap: 12px; margin: 26px 0; }.steps button { display: flex; align-items: center; gap: 10px; border: 0; border-bottom: 2px solid var(--line); background: transparent; color: var(--muted); font: inherit; padding: 0 22px 12px 0; cursor: pointer; }.steps button.current { color: var(--brand); border-color: var(--brand); font-weight: 600; }.steps span { font-size: 12px; display: grid; place-items: center; width: 29px; height: 29px; border-radius: 50%; background: #e8ede7; }
.identify-grid { display: grid; grid-template-columns: minmax(300px, .85fr) minmax(0, 1.45fr); gap: 24px; align-items: start; }.evidence { overflow: hidden; position: sticky; top: 20px; }.card-h h2 { font-size: 17px; }.workspace { display: flex; flex-direction: column; gap: 18px; min-width: 0; }.photo-stage { height: 285px; background: #e6ece2; position: relative; display: grid; place-items: center; }.photo-stage img { width: 100%; height: 100%; object-fit: contain; }.photo-stage > span { position: absolute; right: 12px; bottom: 12px; background: #ffffffdb; border-radius: 7px; padding: 4px 9px; color: var(--ink-2); font-size: 11px; }.thumbnails { display: flex; gap: 8px; padding: 12px 16px; overflow: auto; }.thumbnails button { position: relative; width: 62px; height: 50px; padding: 2px; flex-shrink: 0; background: #fff; border: 2px solid transparent; border-radius: 8px; cursor: pointer; }.thumbnails button.chosen { border-color: var(--brand); }.thumbnails img { width: 100%; height: 100%; object-fit: cover; border-radius: 5px; }.thumbnails span { position: absolute; bottom: 3px; left: 4px; font-size: 10px; background: white; border-radius: 3px; padding: 0 3px; }.photo-meta { padding: 0 18px 16px; display: grid; gap: 10px; }.photo-meta > b { font-size: 13px; overflow-wrap: anywhere; }.photo-empty { text-align: center; padding: 64px 18px 44px; color: var(--brand); background: linear-gradient(150deg,#eef3e8,#f9faf7); }.photo-empty h3 { margin-top: 16px; font-size: 18px; }.photo-empty p { margin-top: 8px; color: var(--muted); font-size: 13px; }.upload-actions { display: flex; flex-wrap: wrap; gap: 10px; padding: 18px; }.upload-actions p { font-size: 11px; color: var(--muted); }.upload { cursor: pointer; position: relative; }.upload input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }.evidence-summary { border-top: 1px solid var(--line); padding: 16px 18px; font-size: 13px; }.evidence-summary span { margin-left: 10px; color: var(--muted); }.evidence-summary p { margin: 8px 0; color: var(--ink-2); }
label { display: flex; flex-wrap: wrap; gap: 5px; font-size: 13px; color: var(--ink-2); } input:not([type=checkbox]):not([type=file]), textarea, select { width: 100%; border: 1px solid var(--line-strong); border-radius: 9px; padding: 10px 12px; background: #fff; font: inherit; color: var(--ink); outline-color: var(--brand); }textarea { resize: vertical; }input[type=checkbox] { accent-color: var(--brand); }.field-pair { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }.observation .card-b { display: grid; gap: 20px; padding: 24px; }.required { color: var(--brand); font-size: 11px; }.observation-tip { display: flex; gap: 12px; padding: 18px; border-radius: 11px; background: #f5f7f2; color: var(--brand); }.observation-tip p { font-size: 13px; margin-top: 5px; line-height: 1.8; color: var(--ink-2); }.next { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }.btn { cursor: pointer; }button:disabled { opacity: .48; cursor: not-allowed; }.text-button { border: 0; background: transparent; font: inherit; font-size: 13px; color: var(--brand); text-align: left; cursor: pointer; padding: 4px 0; }
.candidate-intro { padding: 16px 20px; color: var(--muted); font-size: 13px; }.candidate { display: flex; width: 100%; gap: 14px; align-items: center; text-align: left; padding: 17px 20px; border: 0; border-top: 1px solid var(--line); background: #fff; font: inherit; cursor: pointer; color: var(--ink); }.candidate:last-child { border-radius: 0 0 14px 14px; }.candidate.selected { background: var(--brand-soft); box-shadow: inset 3px 0 var(--brand); }.candidate-icon { padding: 9px; background: #f0f3ed; color: var(--brand); border-radius: 10px; }.candidate small { display: block; font-size: 12px; color: var(--muted); margin-top: 3px; }.candidate .tag { margin-left: auto; }.candidate-detail .card-b { display: grid; gap: 14px; }.candidate-detail h3 { font-size: 15px; }.next-evidence { display: flex; gap: 10px; padding: 14px; background: #f5f7f2; border-radius: 9px; font-size: 13px; }.next-evidence p { color: var(--ink-2); margin-top: 4px; }.demo-drug { border: 1px dashed var(--line-strong); border-radius: 11px; padding: 17px; display: grid; gap: 9px; }.demo-drug p { color: var(--muted); font-size: 13px; }.inline-check { align-items: start; flex-wrap: nowrap; gap: 8px; }.review .card-b { display: grid; gap: 18px; }.review-item { display: grid; gap: 12px; border: 1px solid var(--line); border-radius: 10px; padding: 16px; }.review-title { display: flex; justify-content: space-between; gap: 12px; }.photo-evidence { display: flex; flex-wrap: wrap; gap: 8px 14px; font-size: 12px; }.photo-evidence label { align-items: center; font-size: 12px; }.add-candidate { display: flex; align-items: center; gap: 8px; }.review-actions { display: flex; gap: 8px; flex-wrap: wrap; }.consent { padding: 14px; border-radius: 9px; background: #f4f6f3; }.saved { font-size: 13px; color: var(--brand); }.problems { margin: 0; padding: 12px 12px 12px 30px; color: #815316; background: var(--warn-soft); font-size: 13px; border-radius: 8px; }.contact .card-b { display: grid; gap: 18px; }.summary { padding: 18px; background: var(--bg); border-radius: 10px; font-size: 14px; }.summary p { margin-top: 6px; }.record { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 20px; border-bottom: 1px solid var(--line); }.record p { font-size: 12px; color: var(--muted); }
.modal-overlay { position: fixed; inset: 0; display: grid; place-items: center; z-index: 90; background: #14251da8; padding: 20px; }.modal-card { background: #fff; width: min(680px,100%); max-height: 90vh; overflow: auto; border-radius: 14px; box-shadow: 0 24px 80px #0004; }.modal-card .card-b { display: grid; gap: 16px; }.modal-card h3 { font-size: 16px; }
@media(max-width:1000px){ .identify-grid { grid-template-columns: minmax(260px,.8fr) minmax(0,1fr); gap: 16px; }.heading { align-items: start; flex-direction: column; }.candidate { flex-wrap: wrap; }.candidate .tag { margin-left: 0; }.photo-stage { height: 220px; } }
@media(max-width:720px){ .identify-grid { grid-template-columns: 1fr; }.evidence { position: static; }.heading h1 { font-size: 24px; }.demo-notice { align-items: start; flex-wrap: wrap; }.demo-notice a { margin-left: 30px; }.steps { gap: 8px; }.steps button { font-size: 12px; gap: 5px; padding-right: 0; flex: 1; align-items: start; }.steps span { width: 23px; height: 23px; flex-shrink: 0; }.field-pair { grid-template-columns: 1fr; }.candidate { gap: 10px; }.observation .card-b { padding: 18px; }.review-title { flex-direction: column; gap: 3px; }.role-switch { width: 100%; }.role-switch button { flex: 1; } }
</style>
