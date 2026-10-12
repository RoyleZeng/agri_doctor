<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { SourceInfo, SourceKey } from '@/api/types'
import Icon from '@/components/Icon.vue'
import SearchBox from '@/components/SearchBox.vue'
import CoveragePanel from '@/components/CoveragePanel.vue'
import SourceChanges from '@/components/SourceChanges.vue'
import { recentQueries } from '@/composables/recent'

const box = ref<InstanceType<typeof SearchBox> | null>(null)
const sources = ref<Record<string, SourceInfo>>({})

const EXAMPLES = ['芒果', '炭疽病', '介殼蟲', '水稻', '白葉枯病']

const SOURCE_META: Record<SourceKey, { title: string; note: (s: SourceInfo) => string }> = {
  ppm: {
    title: '植物保護資訊系統（藥毒所）',
    note: () => '網頁無版本號',
  },
  fda: {
    title: '食藥署 殘留容許量／禁用名單',
    note: () => '網頁無版本號',
  },
  kdais: {
    title: '高雄區農業改良場 用藥摘要表',
    note: (s) => (s.documents?.length ? `${s.documents.length} 份 PDF，各份有修訂日期` : '各份有修訂日期'),
  },
}

const ORDER: SourceKey[] = ['ppm', 'fda', 'kdais']

const cards = computed(() =>
  ORDER.map((key) => {
    const s = sources.value[key]
    return {
      key,
      title: SOURCE_META[key].title,
      value: s ? `${s.row_count.toLocaleString()} 筆` : '尚未取得',
      note: s
        ? `${SOURCE_META[key].note(s)} · 本站抓取 ${fmt(s.fetched_at)}`
        : '尚未抓取',
      absent: !s || s.row_count === 0,
    }
  }),
)

function fmt(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(
    d.getMinutes(),
  ).padStart(2, '0')}`
}

onMounted(async () => {
  try {
    sources.value = await api.sourceStatus()
  } catch {
    /* 後端沒起來時首頁仍要能用 */
  }
})
</script>

<template>
  <div class="wrap">
    <section class="hero">
      <h1>一次查完三個網站</h1>
      <p>輸入作物、病名或害蟲名都可以，系統會同時比對植保系統、食藥署與高改場。</p>

      <SearchBox ref="box" autofocus />

      <div class="examples">
        <span>試試：</span>
        <a v-for="e in EXAMPLES" :key="e" href="#" @click.prevent="box?.fill(e)">
          <Icon name="search" :size="13" />{{ e }}
        </a>
      </div>
    </section>

    <CoveragePanel />
    <SourceChanges />
    <div class="grid2">
      <section class="card">
        <div class="card-h">
          <span class="title">最近查詢</span>
          <span class="small muted">點一下重新開啟</span>
        </div>
        <div v-if="recentQueries.items.length" class="recent">
          <router-link
            v-for="r in recentQueries.items"
            :key="r.cropId + '-' + r.pestId"
            :to="{ name: 'result', query: { crop: r.cropId, pest: r.pestId } }"
          >
            <span class="q">{{ r.cropName }} × {{ r.pestName }}</span>
            <span class="pills">
              <span class="pill ppm">植保 {{ r.ppm }}</span>
              <span class="pill" :class="r.fda ? 'fda' : 'absent'">
                食藥 {{ r.fda || '無' }}
              </span>
              <span class="pill" :class="r.kdais ? 'kdais' : 'absent'">
                高改 {{ r.kdais || '無' }}
              </span>
            </span>
          </router-link>
        </div>
        <div v-else class="card-b muted small">
          還沒有查詢紀錄。查過的組合會留在這裡，方便回頭比對。
        </div>
      </section>

      <section class="card">
        <div class="card-h">
          <span class="title">三源資料狀態</span>
          <router-link to="/admin/sources" class="small">資料來源頁</router-link>
        </div>
        <div class="srcstat">
          <div v-for="c in cards" :key="c.key" class="src" :class="[c.key, { absent: c.absent }]">
            <span class="bar" />
            <div>
              <div class="k">{{ c.title }}</div>
              <div class="v">{{ c.value }}</div>
              <div class="sub">{{ c.note }}</div>
            </div>
          </div>
        </div>
      </section>
    </div>
    <div class="farm-entry"><Icon name="calendar" :size="24" /><div><strong>從用藥查詢，到每一期農場規劃</strong><p>建立自己的作物時程，一次掌握多種作物的農事與病蟲害巡查。</p></div><router-link to="/farm" class="btn primary">前往農場規劃 →</router-link></div>
  </div>
</template>

<style scoped>
.examples a { cursor: pointer; }
.farm-entry { max-width: 1100px; margin: 24px auto 0; display: flex; align-items: center; gap: 18px; border: 1px solid #d5e1ce; border-radius: 14px; padding: 20px; background: #eaf1e4; color: var(--brand); }
.farm-entry div { flex: 1; }.farm-entry p { font-size: 12px; margin-top: 4px; color: var(--ink-2); }
@media (max-width: 760px) { .farm-entry { flex-wrap: wrap; }.farm-entry div { min-width: 200px; }.farm-entry .btn { margin-left: 42px; } }
</style>
