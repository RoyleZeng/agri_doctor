<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { contentRequest, type ContentRow, type ContentEvent } from '@/api/farmContent'
import { planning } from '@/api/planning'
import { session } from '@/stores/session'
import { kinds } from '@/composables/farmDates'
import PageBar from '@/components/PageBar.vue'
import GuidanceMedia from '@/components/GuidanceMedia.vue'
import { toast } from '@/composables/toast'
const rows = ref<ContentRow[]>([])
const selected = ref<ContentRow | null>(null)
const selectedEvent = ref(0)
const current = computed(() => selected.value?.draft.events[selectedEvent.value])
const busy = ref(false)
const error = ref('')
const savedJson = ref('')
const dirty = computed(() => !!selected.value && JSON.stringify(selected.value.draft) !== savedJson.value)
const history = ref<{revision: number; published_by: string; published_at: string}[]>([])
const publishing = ref(false)
function choose(row: ContentRow) {
  selected.value = JSON.parse(JSON.stringify(row)) as ContentRow
  selected.value.draft.events.forEach(e => { e.media ||= [] })
  selectedEvent.value = 0
  savedJson.value = JSON.stringify(selected.value.draft)
  history.value = []
  publishing.value = false
  error.value = ''
}
async function load() {
  busy.value = true
  try { rows.value = await contentRequest<ContentRow[]>(); if (rows.value[0]) choose(rows.value[0]) }
  catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
function newCrop() {
  choose({ key: '', revision: 0, published_version: null, draft: { key: '', name: '', emoji: '🌱', days: 90, anchor: '種植日', scope: '', aliases: [], sources: [], events: [] } })
  addEvent()
}
function addEvent() {
  if (!selected.value) return
  selected.value.draft.events.push({kind: 'care', title: '', start: 0, end: 7, detail: '', source_ids: [], media: []})
  selectedEvent.value = selected.value.draft.events.length - 1
}
function removeEvent() {
  selected.value?.draft.events.splice(selectedEvent.value, 1)
  selectedEvent.value = 0
}
function eventStyle(e: ContentEvent) {
  const days = (selected.value?.draft.days || 90) + 1
  return { marginLeft: `${Math.max(0, e.start) / days * 100}%`, width: `${Math.max(1, e.end - e.start + 1) / days * 100}%` }
}
async function save() {
  if (!selected.value) return
  busy.value = true; error.value = ''
  try {
    const row = await contentRequest<ContentRow>(`/${encodeURIComponent(selected.value.draft.key)}`, 'PUT', { revision: selected.value.revision, content: selected.value.draft })
    const index = rows.value.findIndex(r => r.key === row.key)
    if (index < 0) rows.value.push(row); else rows.value[index] = row
    const indexEvent = selectedEvent.value
    choose(row); selectedEvent.value = indexEvent
    toast.ok('草稿已儲存，發布後才供新規劃使用')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function publish() {
  if (!selected.value || dirty.value) return
  busy.value = true; error.value = ''
  try {
    const row = await contentRequest<ContentRow>(`/${encodeURIComponent(selected.value.key)}/publish`, 'POST', { revision: selected.value.revision })
    rows.value[rows.value.findIndex(r => r.key === row.key)] = row
    choose(row)
    session.catalog = await planning.catalog()
    toast.ok('已發布；既有農場規劃仍保留原版本')
  } catch (e) { error.value = (e as Error).message }
  finally { busy.value = false }
}
async function releases() {
  if (!selected.value?.key) return
  try { history.value = await contentRequest(`/${encodeURIComponent(selected.value.key)}/releases`); if (!history.value.length) toast.info('尚無後台發布紀錄') }
  catch (e) { error.value = (e as Error).message }
}
onMounted(load)
</script>
<template>
  <PageBar title="作物內容管理" note="生育時期、規劃事項、巡查與防治建議" />
  <div class="wrap content-admin">
    <p class="source-note">先編輯草稿、預覽複合圖，再發布。發布只影響之後新增的規劃；歷史內容、日誌與處方保持原快照。教學內容不能代替田間診斷或核准用藥查核。</p>
    <div class="toolbar">
      <label>作物範本 <select :value="selected?.key" :disabled="busy || dirty" @change="choose(rows.find(r => r.key === ($event.target as HTMLSelectElement).value)!)"><option v-for="r in rows" :key="r.key" :value="r.key">{{ r.draft.name }}</option></select></label>
      <button class="btn" :disabled="busy || dirty" @click="newCrop">＋ 新增作物</button>
      <button v-if="dirty" class="btn" @click="load">放棄未儲存修改</button>
      <span class="muted">{{ dirty ? '有未儲存修改' : selected?.published_version ? `目前發布 ${selected.published_version}` : '尚未發布' }}</span>
    </div>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <form v-if="selected" class="farm-form" @submit.prevent="save">
      <fieldset :disabled="busy">
        <section class="card card-b">
          <h2>作物與規劃基準</h2>
          <div class="form-grid">
            <label>作物名稱<input v-model="selected.draft.name" required maxlength="80" /></label>
            <label>作物代碼<input v-model="selected.draft.key" required pattern="[a-z][a-z0-9_-]{0,39}" :readonly="!!selected.key" placeholder="例如 sweet-potato" /></label>
            <label>標準天數<input v-model.number="selected.draft.days" type="number" min="14" max="730" required /></label>
            <label>起算基準<input v-model="selected.draft.anchor" required maxlength="30" /></label>
          </div>
          <label>別名（用逗號分隔）<input :value="selected.draft.aliases.join('、')" @input="selected.draft.aliases = ($event.target as HTMLInputElement).value.split(/[,，、]/).map(s => s.trim()).filter(Boolean)" /></label>
          <label>適用範圍與限制<textarea v-model="selected.draft.scope" required maxlength="1000" rows="2" /></label>
        </section>
        <section class="card card-b preview">
          <h2>複合圖預覽 <small>第 0 天 → 第 {{ selected.draft.days }} 天</small></h2>
          <div v-for="kind in kinds" :key="kind.key" class="preview-lane"><b>{{ kind.name }}</b><div class="preview-track"><div v-for="(e, i) in selected.draft.events" :key="i"><button v-if="e.kind === kind.key" type="button" :class="e.kind" :style="eventStyle(e)" @click="selectedEvent = i">{{ e.title || '尚未命名' }}</button></div></div></div>
        </section>
        <div class="editor-grid">
          <section class="card card-b event-picker">
            <h2>建議事項</h2><button type="button" class="btn" @click="addEvent">＋ 新增事項</button>
            <button v-for="(e, i) in selected.draft.events" :key="i" type="button" :class="['event-choice', {on: selectedEvent === i}]" @click="selectedEvent = i">{{ e.title || '尚未命名' }}<small>{{ kinds.find(k => k.key === e.kind)?.name }} · 第 {{ e.start }}–{{ e.end }} 天</small></button>
          </section>
          <section v-if="current" class="card card-b detail-editor">
            <div class="toolbar"><h2>內容與教學素材</h2><button type="button" class="text-btn" @click="removeEvent">移除這項草稿事項</button></div>
            <label>事項名稱<input v-model="current.title" required maxlength="120" /></label>
            <div class="form-grid"><label>類別<select v-model="current.kind"><option v-for="k in kinds" :value="k.key" :key="k.key">{{ k.name }}</option></select></label><label>開始日（種植日為 0）<input v-model.number="current.start" type="number" min="0" :max="selected.draft.days" required /></label><label>結束日<input v-model.number="current.end" type="number" :min="current.start" :max="selected.draft.days" required /></label></div>
            <label>建議事項／觀察與防治詳細內容<textarea v-model="current.detail" rows="7" required maxlength="3000" placeholder="說明觀察部位、判斷依據、管理方式、何時尋求植物醫生協助；不要將觀察窗口寫成已發病。" /></label>
            <div><b>這項建議的參考來源</b><label v-for="s in selected.draft.sources" :key="s.id" class="check-label"><input v-model="current.source_ids" type="checkbox" :value="s.id" />{{ s.title }}</label></div>
            <h3>圖片與影片</h3><p class="form-hint">使用有授權的 HTTPS 圖片網址或影片頁面網址，並填入來源。影片另開原站播放；外部素材失效時仍保留建議文字與來源。</p>
            <div v-for="(m, i) in current.media" :key="i" class="media-editor">
              <label>素材類型<select v-model="m.kind"><option value="image">圖片</option><option value="video">影片</option></select></label>
              <label>素材網址<input v-model="m.url" type="text" required maxlength="2000" /></label>
              <label>圖片替代文字／影片說明<input v-model="m.caption" required maxlength="300" /></label>
              <label>素材來源網址<input v-model="m.source_url" type="url" required maxlength="2000" /></label>
              <button type="button" class="text-btn" @click="current.media.splice(i, 1)">移除素材</button>
            </div>
            <button type="button" class="btn" :disabled="current.media.length >= 12" @click="current.media.push({kind:'image',url:'',caption:'',source_url:''})">＋ 圖片／影片</button>
            <GuidanceMedia :items="current.media.filter(m => m.url.startsWith('https://') || m.url.startsWith('/guidance-demo/'))" />
          </section>
        </div>
        <section class="card card-b references"><h2>範本參考來源</h2>
          <div v-for="(s, i) in selected.draft.sources" :key="i" class="form-grid reference">
            <label>來源代碼<input v-model="s.id" required maxlength="80" /></label><label>名稱<input v-model="s.title" required maxlength="300" /></label><label>發布單位<input v-model="s.publisher" required maxlength="160" /></label><label>原文網址<input v-model="s.url" type="url" required maxlength="2000" /></label>
            <button type="button" class="text-btn" @click="selected.draft.sources.splice(i, 1)">移除來源</button>
          </div>
          <button type="button" class="btn" @click="selected.draft.sources.push({id:'',title:'',publisher:'',url:'',fetched_at:new Date().toISOString(),publication_date:null,sha256:'',status:'ok'})">＋ 參考來源</button>
        </section>
        <div class="actions"><button type="button" class="btn" @click="releases">發布紀錄</button><button class="btn primary" :disabled="!dirty">{{ busy ? '儲存中…' : '儲存草稿' }}</button><button type="button" class="btn" :disabled="dirty || !selected.revision" @click="publishing = true">發布新版…</button></div>
        <div v-if="publishing" class="source-note">即將發布 {{ selected.draft.name }}，共 {{ selected.draft.events.length }} 項建議。請確認內容、來源與素材授權。<button type="button" class="btn primary" @click="publish">確認發布</button><button type="button" class="btn" @click="publishing = false">取消</button></div>
        <p v-for="h in history" :key="h.revision" class="source-note">r{{ h.revision }} · {{ h.published_at }} · 管理者 {{ h.published_by }}</p>
      </fieldset>
    </form>
  </div>
</template>
<style scoped>
.content-admin { display: grid; gap: 18px; }
fieldset { border: 0; padding: 0; margin: 0; min-width: 0; display: grid; gap: 20px; }
.card-b { padding: 24px; display: grid; gap: 16px; }
h2, h3 { margin: 0; } h2 { font-size: 19px; } h2 small { font-size: 12px; color: var(--muted); font-weight: 400; }
.toolbar, .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 12px; }
.toolbar label { display: flex; gap: 12px; align-items: center; }
.toolbar .muted { margin-left: auto; font-size: 13px; }
.editor-grid { display: grid; grid-template-columns: 260px minmax(0, 1fr); gap: 20px; align-items: start; }
.event-picker { gap: 10px; }
.event-choice { border: 1px solid var(--line); background: white; color: var(--ink); border-radius: 9px; padding: 12px; text-align: left; }
.event-choice.on { background: var(--brand-soft); border-color: var(--brand); }
.event-choice small { display: block; color: var(--muted); margin-top: 5px; }
.preview-lane { display: grid; grid-template-columns: 95px minmax(0, 1fr); gap: 14px; align-items: center; font-size: 13px; }
.preview-track { background: var(--bg); border-radius: 8px; overflow: hidden; padding: 6px 0; }
.preview-track button { display: block; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; text-align: left; border: 0; border-left: 3px solid var(--brand); color: var(--ink); background: var(--brand-soft); padding: 8px; min-width: 24px; margin-bottom: 3px; }
.preview-track .pest { background: var(--warn-soft); border-color: var(--warn); }
.preview-track .nutrition { background: #ecf2fa; border-color: #779abe; }
.media-editor, .reference { border: 1px solid var(--line); border-radius: 10px; padding: 16px; display: grid; gap: 12px; }
.actions { position: sticky; bottom: 0; background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 16px; justify-content: flex-end; }
@media(max-width: 800px) { .editor-grid { grid-template-columns: 1fr; } .card-b { padding: 16px; } .preview-lane { grid-template-columns: 70px minmax(0,1fr); } }
</style>
