<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import type { SourceInfo, SourceKey } from '@/api/types'
import Icon from '@/components/Icon.vue'
import Sidebar from '@/components/Sidebar.vue'
import { dismiss, toasts } from '@/composables/toast'
import { ensureSession } from '@/stores/session'

const route = useRoute()
const sources = ref<Record<string, SourceInfo>>({})

const SOURCE_ORDER: SourceKey[] = ['ppm', 'fda', 'kdais']
const SHORT: Record<SourceKey, string> = { ppm: '植保', fda: '食藥', kdais: '高改' }

/** 頁首的更新時間只給到日期，細節在資料來源頁；這裡是「資料多新」的一眼版本。 */
const freshness = computed(() =>
  SOURCE_ORDER.map((key) => {
    const iso = sources.value[key]?.fetched_at ?? null
    const d = iso ? new Date(iso) : null
    return {
      key,
      label: SHORT[key],
      when: d ? `${d.getMonth() + 1}/${d.getDate()}` : '—',
    }
  }),
)

onMounted(async () => {
  // 登入狀態在這裡抓一次就好；各頁不再自己打 me/catalog/links（第三輪「切頁整頁閃」的來源）。
  void ensureSession()
  try {
    const snaps = await api.snapshots()
    const out: Record<string, SourceInfo> = {}
    for (const key of SOURCE_ORDER) {
      const active = snaps.find((s) => s.source === key && s.is_active)
      out[key] = {
        label: key,
        fetched_at: active?.finished_at ?? null,
        row_count: active?.row_count ?? 0,
        status: active?.status ?? 'never',
      }
    }
    sources.value = out
  } catch {
    /* 後端還沒起來時不要擋住整個介面 */
  }
})
</script>

<template>
  <div class="shell">
    <Sidebar :freshness="freshness" />

    <div class="content">
      <main class="main">
        <router-view />
      </main>

      <footer class="foot">
        本平台彙整植物保護資訊系統、食藥署、高雄區農業改良場三個公開來源供專業判讀，
        不取代植物醫生的臨場判斷；用藥前請確認官方最新公告。
        <span v-if="route.path.startsWith('/farm')">農場規劃另參考農業部及農業改良場的栽培資料，日期為規劃估計。</span>
      </footer>
    </div>

    <div class="toasts" role="status" aria-live="polite">
      <div v-for="t in toasts.items" :key="t.id" class="toast" :class="t.kind" @click="dismiss(t.id)">
        <Icon :name="t.kind === 'ok' ? 'check' : t.kind === 'info' ? 'info' : 'warn'" :size="16" />
        <span>{{ t.text }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
/* 外殼：左側邊欄（sticky、自己捲）＋右內容欄。原本的頂列導覽已移進 Sidebar.vue。 */
.shell { display: flex; min-height: 100vh; }
.content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.content .main { flex: 1; padding: 24px 28px 40px; }

.foot {
  padding: 18px 28px 26px;
  border-top: 1px solid var(--line);
  background: #fff;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}

@media (max-width: 1023px) {
  /* 側邊欄變成抽屜，內容佔滿；左上角的抽屜按鈕由 Sidebar 自己畫。 */
  .content .main { padding: 64px 16px 32px; }
}

.toasts {
  position: fixed;
  right: 20px;
  bottom: 20px;
  z-index: 100;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-width: min(420px, calc(100vw - 40px));
}
.toast {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 11px 14px;
  border-radius: 10px;
  font-size: 14px;
  line-height: 1.5;
  background: #fff;
  border: 1px solid var(--line-strong);
  box-shadow: 0 8px 24px rgba(20, 30, 25, 0.14);
  cursor: pointer;
}
.toast.ok { background: var(--kdais-soft); border-color: #bfe0cc; color: var(--kdais-ink); }
.toast.warn { background: var(--warn-soft); border-color: #f2dfb3; color: #6d4506; }
.toast.crit { background: var(--crit-soft); border-color: #f3cfc9; color: #7a2119; }
</style>
