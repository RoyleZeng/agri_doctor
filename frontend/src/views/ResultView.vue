<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { Comparison, ComparisonRow, SourceKey } from '@/api/types'
import DrugCard from '@/components/DrugCard.vue'
import Icon from '@/components/Icon.vue'
import SearchBox from '@/components/SearchBox.vue'
import { remember } from '@/composables/recent'
import { toast } from '@/composables/toast'

const route = useRoute()
const router = useRouter()

const result = ref<Comparison | null>(null)
const loading = ref(false)
const error = ref('')
const filter = ref<'all' | 'three' | 'kdais' | 'issue'>('all')

const SOURCE_KEYS: SourceKey[] = ['ppm', 'fda', 'kdais']

const has = (r: ComparisonRow, k: SourceKey) => !!r.sources?.[k]?.present
const hasThree = (r: ComparisonRow) => SOURCE_KEYS.every((k) => has(r, k))
const hasIssue = (r: ComparisonRow) => r.banned || r.issues.length > 0

const counts = computed(() => {
  const rows = result.value?.rows ?? []
  return {
    all: rows.length,
    three: rows.filter(hasThree).length,
    kdais: rows.filter((r) => has(r, 'kdais')).length,
    issue: rows.filter(hasIssue).length,
  }
})

const rows = computed(() => {
  const all = [...(result.value?.rows ?? [])]
  const picked =
    filter.value === 'three'
      ? all.filter(hasThree)
      : filter.value === 'kdais'
        ? all.filter((r) => has(r, 'kdais'))
        : filter.value === 'issue'
          ? all.filter(hasIssue)
          : all

  // 安全採收期短的排前面（未載明沉底），禁用永遠置頂。
  picked.sort((a, b) => (a.phi_days ?? 9999) - (b.phi_days ?? 9999))
  picked.sort((a, b) => Number(b.banned) - Number(a.banned))
  return picked
})

/** 標題卡的三源摘要：各自回答「收了幾支、有什麼要注意、多新」。 */
const summary = computed(() => {
  const all = result.value?.rows ?? []
  const s = result.value?.sources ?? {}
  const fdaOk = all.filter((r) => has(r, 'fda')).length
  const banned = all.filter((r) => r.banned).length
  const kdais = all.filter((r) => has(r, 'kdais')).length

  const revisions = [
    ...new Set(
      all.flatMap((r) => r.kdais_entries.map((k) => k.revision_date).filter(Boolean)),
    ),
  ]

  return [
    {
      key: 'ppm' as SourceKey,
      label: '植保系統 · 法定核准',
      value: all.length ? `已收錄 ${all.length} 筆用法` : (result.value?.coverage?.label || '收錄狀態待確認'),
      sub: `無版本號 · 抓取 ${fmt(s.ppm?.fetched_at)}`,
      absent: !all.length,
    },
    {
      key: 'fda' as SourceKey,
      label: '食藥署 · 殘留容許量',
      value: all.length ? `${fdaOk} / ${all.length} 筆有容許量對應` : '無已收錄藥劑可比對',
      sub:
        [banned ? `${banned} 筆涉及禁用名單` : null, all.length - fdaOk ? `${all.length - fdaOk} 筆尚未對上` : null]
          .filter(Boolean)
          .join(' · ') + ` · 抓取 ${fmt(s.fda?.fetched_at)}`,
      absent: fdaOk === 0,
    },
    {
      key: 'kdais' as SourceKey,
      label: '高改場 · 實務推薦',
      value: kdais ? `摘要表對應 ${kdais} 筆` : '未對上此組合的摘要表用藥',
      sub: revisions.length
        ? `${revisions.join('、')} 修訂 · 抓取 ${fmt(s.kdais?.fetched_at)}`
        : `抓取 ${fmt(s.kdais?.fetched_at)}`,
      absent: kdais === 0,
    },
  ]
})

function fmt(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function load(cropId: number, pestId: number) {
  loading.value = true
  error.value = ''
  try {
    const data = await api.recommend(cropId, pestId)
    result.value = data
    remember({
      cropId,
      pestId,
      cropName: data.crop.name,
      pestName: data.pest.name,
      ppm: data.rows.length,
      fda: data.rows.filter((r) => has(r, 'fda')).length,
      kdais: data.rows.filter((r) => has(r, 'kdais')).length,
    })
  } catch (e) {
    error.value = (e as Error).message
    result.value = null
  } finally {
    loading.value = false
  }
}

async function copyLink() {
  try {
    await navigator.clipboard.writeText(location.href)
    toast.ok('已複製此頁連結')
  } catch {
    toast.warn('瀏覽器不允許複製，請直接從網址列複製')
  }
}

function clearPest() {
  if (!result.value) return
  router.push({ name: 'pick-crop', params: { pestId: result.value.pest.id } })
}

watch(
  () => [route.query.crop, route.query.pest],
  ([crop, pest]) => {
    const c = Number(crop)
    const p = Number(pest)
    // 只有作物、還沒選病蟲害時，導去挑病蟲害
    if (c && !p) {
      router.replace({ name: 'crop-pests', params: { cropId: c } })
      return
    }
    if (c && p) void load(c, p)
  },
  { immediate: true },
)
</script>

<template>
  <div class="wrap">
    <div class="qbar">
      <div v-if="result" class="input picked">
        <span class="sel">{{ result.crop.name }}</span>
        <span class="sel pest">
          {{ result.pest.name }}
          <button class="x" title="改選作物" @click="clearPest"><Icon name="close" :size="13" /></button>
        </span>
        <span class="hint">用右邊的搜尋框可改查其他組合</span>
      </div>
      <SearchBox size="md" placeholder="改查其他作物或病蟲害⋯" />
      <button class="btn sm" @click="copyLink">
        <Icon name="link" :size="14" />複製此頁連結
      </button>
    </div>

    <div v-if="error" class="alert crit"><div>{{ error }}</div></div>

    <template v-if="result">
      <section class="card head">
        <div>
          <h1>
            {{ result.crop.name }}
            <span v-if="result.crop.scientific_name" class="sci">{{ result.crop.scientific_name }}</span>
            <span class="x">×</span>{{ result.pest.name }}
          </h1>
          <div class="meta">
            <span v-if="result.crop.aliases?.length">
              也叫 <b>{{ result.crop.aliases.join('、') }}</b>
            </span>
            <!-- 顯示真正在查容許量的類別；對不到才警告，否則會在明明查得到時嚇人 -->
            <span v-if="result.crop.fda_categories?.length">
              食藥署作物類別 <b>{{ result.crop.fda_categories.join('、') }}</b>
            </span>
            <span v-else class="tag warn">作物類別未對應，容許量可能查不到</span>
            <span v-if="result.pest.scientific_name">
              病原 <b class="sci">{{ result.pest.scientific_name }}</b>
            </span>
            <span v-if="result.pest.english_name">{{ result.pest.english_name }}</span>
          </div>
        </div>

        <div class="src-row">
          <div v-for="s in summary" :key="s.key" class="src" :class="[s.key, { absent: s.absent }]">
            <span class="bar" />
            <div>
              <div class="k">{{ s.label }}</div>
              <div class="v">{{ s.value }}</div>
              <div class="sub">{{ s.sub }}</div>
            </div>
          </div>
        </div>
      </section>

      <div v-if="result.coverage" class="alert" :class="result.coverage.state === 'covered' ? 'info' : 'warn'">
        <div><strong>{{ result.coverage.label }}</strong> · {{ result.coverage.message }}
          <a :href="result.coverage.source_url" target="_blank" rel="noopener">前往植保系統核對 ↗</a>
        </div>
      </div>

      <!-- 全域層級的衝突（例如高改場推薦了沒登記的藥）放最上面 -->
      <div v-if="result.issues.length" class="alerts">
        <div
          v-for="(i, idx) in result.issues"
          :key="idx"
          class="alert"
          :class="i.severity === 'critical' ? 'crit' : 'warn'"
        >
          <Icon name="warn" :size="18" style="margin-top: 2px" />
          <div>{{ i.summary }}</div>
        </div>
      </div>

      <div class="layout">
        <div>
          <div class="toolbar">
            <div class="chips">
              <button class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">
                全部 <span class="cnt">{{ counts.all }}</span>
              </button>
              <button class="chip" :class="{ on: filter === 'three' }" @click="filter = 'three'">
                三源都有 <span class="cnt">{{ counts.three }}</span>
              </button>
              <button class="chip" :class="{ on: filter === 'kdais' }" @click="filter = 'kdais'">
                高改場推薦 <span class="cnt">{{ counts.kdais }}</span>
              </button>
              <button class="chip" :class="{ on: filter === 'issue' }" @click="filter = 'issue'">
                有衝突 <span class="cnt">{{ counts.issue }}</span>
              </button>
            </div>
            <span class="sort">排序：禁用置頂 → 安全採收期短</span>
          </div>

          <div v-if="rows.length" class="list">
            <DrugCard
              v-for="r in rows"
              :key="r.registration_id"
              :row="r"
              :crop-name="result.crop.name"
            />
          </div>
          <p v-else class="muted empty">{{ result.rows.length ? '這個篩選條件下沒有藥劑，請切換「全部」。' : result.coverage?.message || '本站尚無可顯示的資料，請核對官方來源。' }}</p>
        </div>

        <aside class="side">
          <section v-if="result.profile?.symptoms || result.profile?.transmission" class="card">
            <div class="card-h">
              <span class="title">病徵與傳播</span>
              <span class="xs muted"><span class="dot ppm" /> 植保系統</span>
            </div>
            <div class="card-b">
              <div v-if="result.profile?.symptoms" class="blk">
                <div class="lbl">病徵</div>
                <p>{{ result.profile.symptoms }}</p>
              </div>
              <div v-if="result.profile?.transmission" class="blk">
                <div class="lbl">傳播途徑</div>
                <p>{{ result.profile.transmission }}</p>
              </div>
            </div>
          </section>

          <section v-if="result.profile?.cultural_control" class="card">
            <div class="card-h">
              <span class="title">非藥劑防治</span>
              <span class="xs muted">開藥前先看這裡</span>
            </div>
            <div class="card-b"><p class="pre">{{ result.profile.cultural_control }}</p></div>
          </section>

          <section class="card">
            <div class="card-h">
              <span class="title">抗藥性輪替</span>
              <span class="xs muted">同組＝同作用機制</span>
            </div>
            <div class="card-b">
              <p class="small muted" style="margin-bottom: 12px">{{ result.rotation.note }}</p>
              <div v-if="result.rotation.groups.length" class="rot">
                <div v-for="g in result.rotation.groups" :key="g.code" class="g">
                  <div class="code">{{ g.code }}<small>{{ g.label }}</small></div>
                  <div class="names"><span v-for="n in g.product_names" :key="n">{{ n }}</span></div>
                </div>
              </div>
              <p v-if="result.rotation.unknown_products.length" class="xs muted" style="margin-top: 10px">
                未標示作用機制：{{ result.rotation.unknown_products.join('、') }}
              </p>
            </div>
          </section>
        </aside>
      </div>
    </template>

    <p v-else-if="loading" class="muted empty">載入中⋯</p>
  </div>
</template>

<style scoped>
.qbar { flex-wrap: wrap; }
.qbar .input.picked { flex: none; width: auto; gap: 8px; }
.qbar :deep(.searchbox) { flex: 1; min-width: 240px; max-width: 420px; }
.qbar .sel.pest { background: #fbebe9; color: #b23b2d; }
.qbar .sel .x {
  border: none; background: none; padding: 0; cursor: pointer;
  color: inherit; opacity: 0.6; display: inline-flex;
}
.qbar .sel .x:hover { opacity: 1; }
.qbar .hint { font-size: 13px; color: var(--muted); }

.head h1 { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0 4px; }
.head h1 .sci { font-size: 15px; margin-left: 6px; }
.chip { font-family: inherit; cursor: pointer; }
.pre { white-space: pre-line; }
.empty { padding: 40px 0; text-align: center; }
</style>
