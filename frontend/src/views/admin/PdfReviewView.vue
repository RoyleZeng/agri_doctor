<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { KdaisPendingRow } from '@/api/types'
import Icon from '@/components/Icon.vue'
import { toast } from '@/composables/toast'

const rows = ref<KdaisPendingRow[]>([])
const total = ref(0)
const loading = ref(false)
const current = ref<KdaisPendingRow | null>(null)
const verifier = ref(localStorage.getItem('agri-doctor.verifier') ?? '')
const done = ref(0)

const form = ref({
  crop_text: '', pest_text: '', product_text: '',
  dilution_text: '', phi_text: '', tolerance_text: '', remark: '',
})

/** 佇列依來源文件分組，一份一份校對比跳來跳去省力。 */
const groups = computed(() => {
  const map = new Map<string, KdaisPendingRow[]>()
  for (const r of rows.value) {
    const key = r.document_title ?? `${r.crop_text ?? '未知作物'}摘要表`
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(r)
  }
  return [...map.entries()].map(([title, items]) => ({ title, items }))
})

/** 這一列為什麼信心不足 —— 講出原因，校對的人才知道要看哪裡。 */
function why(r: KdaisPendingRow): string {
  const missing: string[] = []
  if (!r.pest_text) missing.push('病蟲害欄空白')
  if (!r.dilution_text) missing.push('稀釋倍數欄空白')
  if (!r.product_text) missing.push('藥劑名欄空白')
  const cells = Object.keys(r.raw_row ?? {}).length
  if (cells && cells < 8) missing.push(`欄數對不上（${cells} 欄，預期 8 欄）`)
  return missing.join('、') || '解析結果需人工確認'
}

async function load() {
  loading.value = true
  try {
    const data = await api.kdaisPending(0)
    rows.value = data.items
    total.value = data.total
    select(rows.value[0] ?? null)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function select(row: KdaisPendingRow | null) {
  current.value = row
  if (!row) return
  form.value = {
    crop_text: row.crop_text ?? '',
    pest_text: row.pest_text ?? '',
    product_text: row.product_text ?? '',
    dilution_text: row.dilution_text ?? '',
    phi_text: row.phi_text ?? '',
    tolerance_text: row.tolerance_text ?? '',
    remark: row.remark ?? '',
  }
}

function drop(id: number) {
  rows.value = rows.value.filter((r) => r.id !== id)
  total.value -= 1
  done.value += 1
  select(rows.value[0] ?? null)
}

async function confirm() {
  if (!current.value) return
  if (!verifier.value.trim()) return toast.warn('請先填寫校對人姓名')
  localStorage.setItem('agri-doctor.verifier', verifier.value)
  try {
    await api.verifyKdaisRow(current.value.id, { ...form.value, verified_by: verifier.value })
    toast.ok('已標記為人工確認，這列會納入推薦比對')
    drop(current.value.id)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function discard() {
  if (!current.value) return
  if (!window.confirm('確定這一列不是用藥資料（例如是 PDF 的區段標題）？刪除後不會再出現。')) return
  try {
    await api.verifyKdaisRow(current.value.id, {
      verified_by: verifier.value || 'unknown',
      discard: true,
    })
    toast.info('已捨棄這一列')
    drop(current.value.id)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

onMounted(load)
</script>

<template>
  <div class="wrap">
    <div class="ph">
      <div>
        <h1>PDF 校對</h1>
        <p>
          高改場摘要表由 PDF 自動解析；信心不足的列在這裡人工確認，
          <b>確認前不會進推薦表</b>。人工校對過的列，重跑爬蟲不會被覆蓋。
        </p>
      </div>
      <div class="who">
        <span class="small muted">待校對 {{ total }} 列 · 本次已校對 {{ done }} 列</span>
        <div class="input"><input v-model="verifier" placeholder="校對人姓名" /></div>
      </div>
    </div>

    <div v-if="!total && !loading" class="card card-b muted" style="text-align: center; padding: 48px">
      目前沒有待校對的列。
    </div>

    <div v-else class="split">
      <section class="card queue">
        <div class="card-h"><span class="title">待校對佇列</span><span class="small muted">{{ total }}</span></div>
        <div class="qbody">
          <template v-for="g in groups" :key="g.title">
            <div class="qg">{{ g.title }} · {{ g.items.length }}</div>
            <button
              v-for="r in g.items"
              :key="r.id"
              class="qi"
              :class="{ on: current?.id === r.id }"
              @click="select(r)"
            >
              <span class="t">
                <span>{{ r.pest_text ?? '（無病蟲害）' }} · 第 {{ r.pdf_page }} 頁</span>
                <span class="tag warn">信心低</span>
              </span>
              <span class="s">{{ r.product_text || '（無藥劑名）' }}</span>
              <span class="s">{{ why(r) }}</span>
            </button>
          </template>
        </div>
      </section>

      <section v-if="current" class="card" style="overflow: hidden">
        <div class="card-h">
          <div>
            <span class="title">
              {{ current.document_title ?? '摘要表' }} · 第 {{ current.pdf_page }} 頁
            </span>
            <div class="xs muted">
              {{ current.revision_date }} 修訂 · 信心「{{ current.confidence }}」 · {{ why(current) }}
            </div>
          </div>
          <a v-if="current.pdf_url" class="btn sm" :href="current.pdf_url" target="_blank" rel="noopener">
            開啟 PDF 第 {{ current.pdf_page }} 頁 <Icon name="external" :size="13" />
          </a>
        </div>

        <div class="form">
          <div class="fields">
            <div class="f"><label>作物</label><div class="input"><input v-model="form.crop_text" /></div></div>
            <div class="f">
              <label>病蟲害</label>
              <div class="input" :class="{ bad: !form.pest_text }"><input v-model="form.pest_text" /></div>
              <div v-if="!form.pest_text" class="hint">
                <Icon name="warn" :size="13" />病蟲害欄空白，這列無法對應到任何比對組合。
              </div>
            </div>
            <div class="f"><label>藥劑</label><div class="input" :class="{ bad: !form.product_text }"><input v-model="form.product_text" /></div></div>
            <div class="f">
              <label>稀釋倍數</label>
              <div class="input" :class="{ bad: !form.dilution_text }"><input v-model="form.dilution_text" /></div>
            </div>
            <div class="f"><label>安全採收期</label><div class="input"><input v-model="form.phi_text" /></div></div>
            <div class="f"><label>安全容許量</label><div class="input"><input v-model="form.tolerance_text" /></div></div>
            <div class="f"><label>備註</label><div class="input ta"><textarea v-model="form.remark" rows="2" /></div></div>
            <div class="f actions">
              <button class="btn primary" @click="confirm">確認並納入比對</button>
              <button class="btn" @click="discard">這列不是資料</button>
            </div>
          </div>

          <div class="raw">
            <h4>
              <span>PDF 原始儲存格</span>
              <span class="xs muted">解析前的內容，逐格對照</span>
            </h4>
            <dl class="rawrow">
              <template v-for="(v, k) in current.raw_row ?? {}" :key="k">
                <dt>第 {{ Number(k) + 1 }} 欄</dt>
                <dd>{{ v || '—' }}</dd>
              </template>
            </dl>
            <p v-if="!current.raw_row" class="muted small">這一列沒有保留原始儲存格。</p>
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ph { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 18px; flex-wrap: wrap; }
.ph h1 { font-size: 24px; font-weight: 700; }
.ph p { color: var(--muted); margin-top: 4px; font-size: 14px; max-width: 720px; line-height: 1.6; }
.ph p b { color: var(--ink-2); }
.who { display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.who .input { width: 190px; height: 38px; }

.split { display: grid; grid-template-columns: 340px minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 1000px) { .split { grid-template-columns: minmax(0, 1fr); } }

.queue .card-h { padding: 14px 16px; }
.qbody { max-height: 66vh; overflow-y: auto; }
.qg { padding: 8px 16px 4px; font-size: 12px; font-weight: 600; color: var(--muted); background: #fafbf9; border-bottom: 1px solid var(--line); }
.qi {
  display: flex; flex-direction: column; gap: 3px; padding: 12px 16px; width: 100%;
  border: none; border-bottom: 1px solid var(--line); background: none;
  text-align: left; font-family: inherit; cursor: pointer;
}
.qi:last-child { border-bottom: none; }
.qi.on { background: var(--brand-soft); box-shadow: inset 3px 0 0 var(--brand); }
.qi .t { font-weight: 600; font-size: 14px; display: flex; justify-content: space-between; gap: 8px; align-items: center; }
.qi .t .tag { height: 20px; font-size: 11px; }
.qi .s { font-size: 12px; color: var(--muted); }

.form { display: grid; grid-template-columns: minmax(0, 1fr) 380px; }
@media (max-width: 1200px) { .form { grid-template-columns: minmax(0, 1fr); } }
.fields { padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; border-right: 1px solid var(--line); }
.f { display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; align-items: center; }
.f label { font-size: 13px; color: var(--muted); }
.f .input { height: 38px; color: var(--ink); font-size: 14px; }
.f .input.ta { height: auto; padding: 8px 12px; }
.f .input input, .f .input textarea {
  flex: 1; min-width: 0; border: none; outline: none; background: transparent;
  font: inherit; color: inherit; resize: vertical;
}
.f .input.bad { border-color: var(--warn); background: var(--warn-soft); }
.f .hint { grid-column: 2; font-size: 12px; color: var(--warn); display: flex; align-items: center; gap: 5px; margin-top: -6px; }
.f.actions { grid-template-columns: 96px minmax(0, 1fr); }
.f.actions > :last-child { display: flex; gap: 10px; flex-wrap: wrap; }

.raw { padding: 18px 20px; background: #fafbf9; }
.raw h4 { font-size: 13px; font-weight: 700; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.rawrow { border: 1px solid var(--line); border-radius: 10px; background: #fff; padding: 12px; font-size: 13px; display: grid; grid-template-columns: 74px 1fr; gap: 4px 10px; margin: 0; }
.rawrow dt { color: var(--muted); }
.rawrow dd { margin: 0; word-break: break-all; }
</style>
