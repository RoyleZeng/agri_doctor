<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { planning, type Workspace } from '@/api/planning'
import FarmDashboard from '@/components/FarmDashboard.vue'
import Icon from '@/components/Icon.vue'
import PageBar from '@/components/PageBar.vue'
import WorkspaceSkeleton from '@/components/WorkspaceSkeleton.vue'
import { syncSelection, workspaceView } from '@/composables/workspaceView'
import { ensureSession, session } from '@/stores/session'

/**
 * 植物醫生看某位農友的工作區（/farmers/:id）。
 *
 * 切換農民時**不清畫面**：舊內容留著變淡、頂端跑一條細進度條，資料回來才換。
 * 這是第三輪回饋「切農民卡卡、整頁閃一下」的修法——舊版每次都 `workspace=null` 再轉圈。
 */
const route = useRoute()
const router = useRouter()

const workspace = ref<Workspace | null>(null)
const error = ref('')
const stale = ref(false)

const farmerId = computed(() => (typeof route.params.farmerId === 'string' ? route.params.farmerId : ''))
const links = computed(() => session.links)

/** 分頁在路徑裡，換分頁**不重抓 workspace**（watch 只看 farmerId）——這是第三輪「切頁整頁閃」的修法。 */
const TABS = [
  { key: 'plan', label: '複合圖', suffix: '' },
  { key: 'log', label: '日誌', suffix: '/log' },
  { key: 'precision', label: '精準', suffix: '/precision' },
] as const
const tab = computed(() => {
  if (route.path.endsWith('/log')) return 'log' as const
  if (route.path.endsWith('/precision')) return 'precision' as const
  return 'plan' as const
})
const TITLES = { plan: '農友工作區', log: '農友農事日誌', precision: '農友精準模式' } as const

function goTab(suffix: string) {
  const next = `/farmers/${farmerId.value}${suffix}`
  if (next !== route.path) void router.push(next)
}

let sequence = 0
async function load() {
  const token = ++sequence
  error.value = ''
  stale.value = !!workspace.value // 已經有內容 → 不給骨架，改用淡化
  try {
    await ensureSession()
    if (token !== sequence) return
    const result = await planning.workspace(farmerId.value)
    if (token !== sequence) return
    workspace.value = result
    syncSelection(result.plans.map((p) => p.id))
  } catch (e) {
    if (token === sequence) error.value = (e as Error).message
  } finally {
    if (token === sequence) stale.value = false
  }
}

watch(farmerId, () => load(), { immediate: true })

function switchFarmer(id: string) {
  // 換農友時停在同一個分頁：植物醫生通常是「一個個看日誌」，每次被踢回複合圖很煩。
  if (id !== farmerId.value) void router.push(`/farmers/${id}${TABS.find((t) => t.key === tab.value)?.suffix ?? ''}`)
}
</script>

<template>
  <PageBar :title="workspace?.farmer.farm_name || TITLES[tab]" :note="workspace?.farmer.name">
    <template #lead>
      <router-link to="/farmers" class="text-btn back"><Icon name="right" :size="14" class="flip" />農民管理</router-link>
    </template>
    <!-- 切換器留在頁頂：換農民時側邊欄項目不變，只有這裡跟著內容換。 -->
    <label v-if="links.length > 1" class="switcher">
      切換農友
      <select :value="farmerId" @change="switchFarmer(($event.target as HTMLSelectElement).value)">
        <option v-for="f in links" :key="f.id" :value="f.id">{{ f.name }} · {{ f.farm_name }}</option>
      </select>
    </label>
    <button v-if="session.me?.role === 'admin' && tab === 'plan'" class="btn sm primary" @click="workspaceView.planFormOpen = true">新增作物規劃</button>
    <div class="seg" role="tablist" aria-label="農友工作區分頁">
      <button
        v-for="t in TABS"
        :key="t.key"
        type="button"
        role="tab"
        :class="{ on: tab === t.key }"
        :aria-selected="tab === t.key"
        @click="goTab(t.suffix)"
      >
        {{ t.label }}
      </button>
    </div>
  </PageBar>

  <div class="wrap farm-page">
    <section v-if="error" class="card farm-empty" role="alert">
      <Icon name="warn" :size="32" />
      <h2>載入農友資料時發生問題</h2>
      <p>{{ error }}</p>
      <button class="btn primary" @click="load">重新載入</button>
    </section>

    <WorkspaceSkeleton v-else-if="!workspace" />

    <div v-else :class="{ stale }">
      <div v-if="stale" class="stale-bar" role="status" aria-label="載入中" />
      <FarmDashboard
        :workspace="workspace"
        :user="session.me"
        :catalog="session.catalog"
        :demo="false"
        :owner="session.me?.role === 'admin'"
        :tab="tab"
        @reload="load"
      />
    </div>
  </div>
</template>

<style scoped>
.back { display: inline-flex; align-items: center; gap: 4px; }
.back .flip { transform: rotate(180deg); }
.switcher { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--ink-2); }
.switcher select { height: 32px; border: 1px solid var(--line-strong); border-radius: var(--r-ctl); padding: 0 8px; font-size: 13px; }
.seg { display: inline-flex; border: 1px solid var(--line-strong); border-radius: var(--r-ctl); overflow: hidden; }
.seg button { height: 32px; padding: 0 14px; border: 0; background: #fff; color: var(--ink-2); font-size: 13px; cursor: pointer; }
.seg button + button { border-left: 1px solid var(--line); }
.seg button.on { background: var(--brand); color: #fff; font-weight: 600; }
.stale { opacity: 0.55; transition: opacity 0.15s ease; pointer-events: none; }
.stale-bar {
  position: fixed;
  left: 0;
  right: 0;
  top: 0;
  height: 3px;
  background: linear-gradient(90deg, transparent, var(--brand), transparent);
  background-size: 50% 100%;
  animation: slide 1s linear infinite;
  z-index: 70;
}
@keyframes slide { 0% { background-position: -50% 0; } 100% { background-position: 150% 0; } }
</style>
