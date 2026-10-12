<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { IssueListItem } from '@/api/types'
import Icon from '@/components/Icon.vue'
import { toast } from '@/composables/toast'

/** 衝突類型的說明。這頁的價值在於讓人知道每一種不一致代表什麼、該不該緊張。 */
const TYPES: {
  key: string
  label: string
  tone: 'crit' | 'warn' | 'info'
  desc: string
}[] = [
  { key: 'BANNED_INGREDIENT', label: '禁用農藥', tone: 'crit', desc: '成分在食藥署禁用名單，其他兩源怎麼說都擋下' },
  { key: 'BAN_ANNOUNCED', label: '已公告・待生效禁用', tone: 'warn', desc: '依禁止銷售／使用日期判定，請核對過渡期規定' },
  { key: 'KDAIS_ONLY', label: '已收錄植保頁未對上', tone: 'crit', desc: '摘要表有列，已檢索植保頁未對上產品；仍須核對名稱、含量與劑型' },
  { key: 'PPM_COVERAGE_GAP', label: '植保覆蓋待確認', tone: 'warn', desc: '未完整收錄，不能判定是否有核准用藥' },
  { key: 'MRL_MISSING', label: '本站未找到容許量', tone: 'warn', desc: '需要確認食藥署來源完整性與作物類別對應' },
  { key: 'MRL_MISMATCH', label: '容許量不一致', tone: 'warn', desc: '植保系統頁上的值與食藥署現行值不同' },
  { key: 'DILUTION_MISMATCH', label: '稀釋倍數不一致', tone: 'warn', desc: '同一支藥，植保與高改場的倍數不同' },
  { key: 'PHI_MISMATCH', label: '安全採收期不一致', tone: 'warn', desc: '同一支藥，兩源的採收期天數不同' },
  { key: 'KDAIS_NOT_RECOMMENDED', label: '高改場未列入', tone: 'info', desc: '合法核准，但摘要表沒有推薦' },
  { key: 'KDAIS_PENDING_REVIEW', label: 'PDF 待校對', tone: 'info', desc: '解析信心不足，未確認前不進推薦表' },
]

const summary = ref<Record<string, number>>({})
const items = ref<IssueListItem[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const selected = ref<string>('')
const loading = ref(false)

const tiles = computed(() =>
  TYPES.map((t) => ({ ...t, count: summary.value[t.key] ?? 0 })),
)

const grandTotal = computed(() =>
  Object.values(summary.value).reduce((a, b) => a + b, 0),
)

const pages = computed(() => Math.max(1, Math.ceil(total.value / pageSize)))

const labelOf = (key: string) => TYPES.find((t) => t.key === key)?.label ?? key
const toneOf = (sev: string) =>
  sev === 'critical' ? 'crit' : sev === 'warning' ? 'warn' : 'info'

async function load() {
  loading.value = true
  try {
    const data = await api.issues({
      issue_type: selected.value || undefined,
      limit: pageSize,
      offset: (page.value - 1) * pageSize,
    })
    items.value = data.items
    total.value = data.total
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

function selectType(key: string) {
  selected.value = selected.value === key ? '' : key
  page.value = 1
  void load()
}

function go(delta: number) {
  const next = page.value + delta
  if (next < 1 || next > pages.value) return
  page.value = next
  void load()
}

/** detail 裡各來源說了什麼，攤成幾行給人直接比。 */
function versus(i: IssueListItem): { key: string; label: string; text: string }[] {
  const d = (i.detail ?? {}) as Record<string, unknown>
  const out: { key: string; label: string; text: string }[] = []
  const str = (v: unknown) => (v === null || v === undefined ? '' : String(v))

  if (d.ppm_text || d.ppm) out.push({ key: 'ppm', label: '植保', text: str(d.ppm_text ?? d.ppm) })
  if (d.fda_limit) {
    out.push({
      key: 'fda',
      label: '食藥',
      text: `${str(d.fda_limit)} ppm${d.fda_category ? `（${str(d.fda_category)}）` : ''}`,
    })
  }
  if (d.kdais) out.push({ key: 'kdais', label: '高改', text: str(d.kdais) })
  if (d.product_text) out.push({ key: 'kdais', label: '高改', text: str(d.product_text) })
  if (d.ingredient && !out.length) {
    out.push({ key: 'fda', label: '食藥', text: `成分「${str(d.ingredient)}」查無` })
  }
  return out
}

onMounted(async () => {
  try {
    const s = await api.issueSummary()
    const acc: Record<string, number> = {}
    for (const row of s.by_type) acc[row.issue_type] = (acc[row.issue_type] ?? 0) + row.count
    summary.value = acc
  } catch {
    /* 摘要拿不到不影響主清單 */
  }
  await load()
})
</script>

<template>
  <div class="wrap">
    <div class="ph">
      <div>
        <h1>三源衝突總覽</h1>
        <p>
          三個來源對不起來的地方。系統只攤開「誰說了什麼」，判斷留給植物醫生；
          每一筆都可以跳到該作物 × 病蟲害的比對頁。
        </p>
      </div>
      <span class="small muted">共 {{ grandTotal.toLocaleString() }} 筆</span>
    </div>

    <div class="tiles">
      <button
        v-for="t in tiles"
        :key="t.key"
        class="tile"
        :class="[t.tone, { on: selected === t.key }]"
        @click="selectType(t.key)"
      >
        <div class="t">
          {{ t.label }} <span class="tag" :class="t.tone">{{ t.tone === 'crit' ? 'critical' : t.tone === 'warn' ? 'warning' : 'info' }}</span>
        </div>
        <div class="v num">{{ t.count.toLocaleString() }}</div>
        <div class="d">{{ t.desc }}</div>
      </button>
    </div>

    <div class="fbar">
      <div class="chips">
        <button class="chip" :class="{ on: !selected }" @click="selectType(selected)">
          全部類型
        </button>
        <button v-if="selected" class="chip on">
          {{ labelOf(selected) }} <span class="cnt">{{ total.toLocaleString() }}</span>
        </button>
      </div>
      <span class="cnt">
        顯示 {{ total ? (page - 1) * pageSize + 1 : 0 }}–{{ Math.min(page * pageSize, total) }}
        / {{ total.toLocaleString() }} 筆
      </span>
    </div>

    <section class="card">
      <div class="rh">
        <span>嚴重度</span><span>類型</span><span>作物 × 病蟲害</span>
        <span>說明</span><span>三源各說什麼</span>
      </div>
      <div v-for="i in items" :key="i.id" class="row">
        <span><span class="tag" :class="toneOf(i.severity)">{{ i.severity }}</span></span>
        <span class="ty">{{ labelOf(i.issue_type) }}</span>
        <router-link
          v-if="i.crop_id && i.pest_id"
          class="cp"
          :to="{ name: 'result', query: { crop: i.crop_id, pest: i.pest_id } }"
        >
          {{ i.crop }} × {{ i.pest }}
          <small>看三源比對 <Icon name="right" :size="11" /></small>
        </router-link>
        <span v-else class="muted">—</span>
        <span class="sum">{{ i.summary }}</span>
        <span class="vs">
          <span v-for="(v, k) in versus(i)" :key="k">
            <span class="dot" :class="v.key" /><b>{{ v.label }}</b> {{ v.text }}
          </span>
          <span v-if="!versus(i).length" class="muted">見說明</span>
        </span>
      </div>
      <div v-if="!items.length && !loading" class="row empty">
        <span class="muted">這個類型目前沒有未處理的衝突。</span>
      </div>
    </section>

    <div class="pager">
      <button class="btn sm" :disabled="page <= 1" @click="go(-1)">上一頁</button>
      <span class="small muted">第 {{ page }} / {{ pages }} 頁</span>
      <button class="btn sm" :disabled="page >= pages" @click="go(1)">下一頁</button>
    </div>
  </div>
</template>

<style scoped>
.ph { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 18px; flex-wrap: wrap; }
.ph h1 { font-size: 24px; font-weight: 700; }
.ph p { color: var(--muted); margin-top: 4px; font-size: 14px; max-width: 720px; line-height: 1.6; }

.tiles { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 20px; }
@media (max-width: 1100px) { .tiles { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 620px) { .tiles { grid-template-columns: minmax(0, 1fr); } }
.tile {
  background: #fff; border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px;
  box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 4px;
  text-align: left; font-family: inherit; cursor: pointer;
}
.tile.on { outline: 2px solid var(--ink); }
.tile .t { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.tile .v { font-size: 26px; font-weight: 700; line-height: 1.1; }
.tile .d { font-size: 12px; color: var(--muted); line-height: 1.5; }
.tile.crit .v { color: var(--crit); }
.tile.warn .v { color: var(--warn); }
.tile.info .v { color: var(--info); }

.fbar { display: flex; align-items: center; gap: 10px; margin-bottom: 14px; flex-wrap: wrap; }
.fbar .cnt { margin-left: auto; font-size: 13px; color: var(--muted); }
.chip { font-family: inherit; cursor: pointer; }

.rh, .row {
  display: grid;
  grid-template-columns: 96px 130px 200px minmax(0, 1fr) 250px;
  gap: 14px; align-items: center;
}
.rh { padding: 10px 20px; font-size: 12px; font-weight: 600; color: var(--muted); background: #fafbf9; border-bottom: 1px solid var(--line); border-radius: var(--r-card) var(--r-card) 0 0; }
.row { padding: 14px 20px; border-bottom: 1px solid var(--line); font-size: 14px; }
.row:last-child { border-bottom: none; }
.row:hover { background: #fafbf9; }
.row.empty { display: block; padding: 40px 20px; text-align: center; }
.row .ty { font-size: 13px; }
.row .cp { font-weight: 600; color: var(--ink); }
.row .cp small { display: block; font-weight: 400; font-size: 12px; color: var(--brand); }
.row .sum { color: var(--ink-2); line-height: 1.5; }
.vs { display: flex; flex-direction: column; gap: 4px; font-size: 12px; }
.vs > span { display: flex; align-items: center; gap: 6px; }

@media (max-width: 1100px) {
  .rh { display: none; }
  .row { grid-template-columns: minmax(0, 1fr); gap: 6px; }
}

.pager { display: flex; align-items: center; justify-content: center; gap: 14px; margin-top: 18px; }
.btn[disabled] { opacity: 0.45; cursor: not-allowed; }
</style>
