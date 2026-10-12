<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { planning, type Farmer, type Workspace } from '@/api/planning'
import FarmLocationForm from '@/components/FarmLocationForm.vue'
import FarmDashboard from '@/components/FarmDashboard.vue'
import FarmShareModal from '@/components/FarmShareModal.vue'
import Icon from '@/components/Icon.vue'
import PageBar from '@/components/PageBar.vue'
import WorkspaceSkeleton from '@/components/WorkspaceSkeleton.vue'
import { syncSelection, workspaceView } from '@/composables/workspaceView'
import { ensureSession, session } from '@/stores/session'

/** 農民自己的工作區（/farm、/farm/log、/farm/precision）與「操作範例」（?demo=1）。 */
const route = useRoute()
const router = useRouter()
const locationOpen = computed(() => route.query.settings === 'location')
function closeLocation() {
  const { settings, return: back, ...query } = route.query
  void router.replace({ query })
}
function locationSaved(farmer: Farmer) {
  if (workspace.value) workspace.value.farmer = farmer
  if (route.query.return === 'region') void router.replace('/region')
  else { closeLocation(); void load() }
}

const workspace = ref<Workspace | null>(null)
const error = ref('')
/** 已經有內容時不清畫面，只把它變淡＋頂端跑進度條；只有第一次載入才給骨架。 */
const stale = ref(false)
const shareOpen = ref(false)

const demo = computed(() => route.query.demo === '1')
const tab = computed(() => {
  if (demo.value) return 'plan' as const
  if (route.path === '/farm/log') return 'log' as const
  if (route.path === '/farm/precision') return 'precision' as const
  return 'plan' as const
})

const TITLES = { plan: '我的農場', log: '農事日誌', precision: '精準模式' } as const

let sequence = 0
async function load() {
  const token = ++sequence
  error.value = ''
  stale.value = !!workspace.value
  try {
    await ensureSession()
    if (token !== sequence) return
    const result = demo.value
      ? await planning.example()
      : session.me
        ? await planning.workspace(session.me.id)
        : null
    if (token !== sequence) return
    workspace.value = result
    if (result) syncSelection(result.plans.map((p) => p.id))
  } catch (e) {
    if (token === sequence) error.value = (e as Error).message
  } finally {
    if (token === sequence) stale.value = false
  }
}

// 只有「看哪個工作區」變了才重載；切分頁（/farm ↔ /farm/log）不重抓。
watch(() => (demo.value ? 'demo' : (session.me?.id ?? 'anon')), () => load(), { immediate: true })
</script>

<template>
  <PageBar :title="TITLES[tab]" :note="workspace?.farmer.farm_name">
    <button v-if="!demo && session.me" class="btn sm" @click="router.replace({ query: { ...route.query, settings: 'location' } })">農場設定</button>
    <button v-if="!demo && session.me" class="btn sm" @click="shareOpen = true">
      <Icon name="link" :size="14" />與植物醫生協作</button
    ><button
      v-if="tab === 'plan' && !demo && session.me"
      class="btn sm primary"
      @click="workspaceView.planFormOpen = true"
    >
      <Icon name="plus" :size="14" />新增作物規劃
    </button>
  </PageBar>

  <div class="wrap farm-page">
    <section v-if="error" class="card farm-empty" role="alert">
      <Icon name="warn" :size="32" />
      <h2>載入農場資料時發生問題</h2>
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
        :demo="demo"
        :owner="!demo"
        :tab="tab"
        @reload="load"
      />
    </div>

    <FarmLocationForm v-if="locationOpen && !demo && session.me" :farmer="session.me" @close="closeLocation" @saved="locationSaved" />
    <FarmShareModal v-if="shareOpen" :links="session.links" @close="shareOpen = false" />
  </div>
</template>

<style scoped>
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
