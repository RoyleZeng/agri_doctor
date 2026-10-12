<script setup lang="ts">
import { computed } from 'vue'

import type { BannerTone } from '@/composables/banners'
import type { IconName } from '@/components/icons'
import Icon from './Icon.vue'

/**
 * 頁面層級的橫幅。位置固定在 PageBar 下方、內容上方。
 *
 * 為什麼要有這個元件：第四輪之前只有精準模式有自己的 `DemoBanner`，
 * 抗藥性輪替的提醒則散在每一個處方項目裡——三項各自說一句「建議評估更換組別」，
 * 沒有人把它們加起來看成「這張處方連續第三次同機制」。把「整頁要先講的一句話」
 * 變成同一個元件，才有地方放那句話。
 *
 * 行為（可否關閉、幾天後再出現、最多兩條）由 `composables/banners.ts` 決定，
 * 這裡只負責畫；那些規則離線可測（`npm run test:banners`）。
 */
const props = defineProps<{
  tone: BannerTone
  title: string
  body?: string
  /** 可關閉才畫叉叉。不可關閉的（輪替、示範資料）不給關，因為那是前提不是提示。 */
  dismissible?: boolean
}>()
const emit = defineEmits<{ dismiss: [] }>()

const ICONS: Record<BannerTone, IconName> = {
  crit: 'warn',
  warn: 'bug',
  info: 'refresh',
  demo: 'thermometer',
  ok: 'check',
}
const icon = computed(() => ICONS[props.tone])
const ROLES: Partial<Record<BannerTone, 'alert' | 'status'>> = { crit: 'alert', warn: 'status', ok: 'status' }
</script>

<template>
  <div class="bn" :class="tone" :role="ROLES[tone]">
    <span class="i"><Icon :name="icon" :size="20" /></span>
    <div class="bn-text">
      <b>{{ title }}</b>
      <p v-if="body">{{ body }}</p>
      <slot name="detail" />
    </div>
    <div class="acts">
      <slot />
      <button
        v-if="dismissible"
        class="x"
        type="button"
        aria-label="關閉這則提示"
        @click="emit('dismiss')"
      >
        <Icon name="close" :size="16" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.bn {
  display: grid;
  grid-template-columns: 40px minmax(0, 1fr) auto;
  gap: 12px;
  align-items: center;
  padding: 12px 16px;
  border-radius: 12px;
  border: 1px solid;
  margin-bottom: 12px;
}
.bn .i {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex: none;
}
.bn-text b {
  display: block;
  font-size: 15px;
}
.bn-text p {
  margin: 2px 0 0;
  font-size: 13px;
  color: var(--ink-2);
  line-height: 1.55;
}
.acts {
  display: flex;
  gap: 6px;
  align-items: center;
}
.acts :deep(.btn) {
  height: 32px;
  font-size: 13px;
}
.x {
  color: var(--muted);
  margin-left: 4px;
  border: 0;
  background: none;
  cursor: pointer;
  display: flex;
  align-items: center;
}

.bn.crit { background: #fdecea; border-color: #f3cfc9; }
.bn.crit .i { background: #b23b2d; color: #fff; }
.bn.warn { background: #fdf4e3; border-color: #f2dfb3; }
.bn.warn .i { background: var(--warn); color: #fff; }
.bn.info { background: #eef2f5; border-color: #dfe4e8; }
.bn.info .i { background: #5b7e9e; color: #fff; }
/* 示範資料用紫色，跟其他四種一眼分開：它講的不是「出了什麼事」而是「這頁的數字是合成的」。 */
.bn.demo { background: #efe9f6; border-color: #dccfee; }
.bn.demo .i { background: #5b3d8a; color: #fff; }
.bn.ok { background: var(--kdais-soft, #e8f2ea); border-color: #bfe0cc; }
.bn.ok .i { background: var(--kdais, #2f8f5b); color: #fff; }

@media (max-width: 720px) {
  .bn {
    grid-template-columns: 32px minmax(0, 1fr);
    align-items: start;
  }
  .bn .i { width: 32px; height: 32px; }
  .acts { grid-column: 1 / -1; flex-wrap: wrap; }
}
</style>
