<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

import type { LogFormMode, Plan, WorkLog } from '@/api/planning'
import Icon from '@/components/Icon.vue'
import WorkLogForm from '@/components/WorkLogForm.vue'
import { kindOf } from '@/composables/farmLog'

/**
 * 就地記錄抽屜。第四輪回饋「點色塊跳到日誌頁很煩」的修法。
 *
 * 舊版每個入口都是 `router.push('/farm/log')`：離開現在看的這張圖、載入另一頁、
 * 記完再自己走回來，而且回來時捲動位置與已選作物都重來一次。
 * 抽屜讓「看圖」與「記一筆」在同一個畫面裡，存完圖就地更新。
 *
 * 表單本身沿用 `WorkLogForm`，不重寫——欄位、產銷履歷完整度、安全採收期快照的規則都在那裡。
 */
const props = defineProps<{
  farmerId: string
  plans: Plan[]
  /** `view` 是唯讀檢視既有紀錄；其餘直接交給 WorkLogForm。 */
  mode: LogFormMode | { type: 'view'; log: WorkLog }
  title: string
  /** 「在農事日誌中開啟」的目的地；只有這個連結會離開本頁。 */
  logHref?: string
}>()
const emit = defineEmits<{
  close: []
  saved: [log: WorkLog]
  'restore-plan': [planId: string]
}>()

const busy = ref(false)
const viewing = computed(() => (props.mode.type === 'view' ? props.mode.log : null))
/** 唯讀模式按「編輯」之後切成表單，不用關掉抽屜再開一次。 */
const editing = ref(false)
const formMode = computed<LogFormMode>(() =>
  viewing.value ? { type: 'edit', log: viewing.value } : (props.mode as LogFormMode))

const kindLabel = computed(() => (viewing.value ? kindOf(viewing.value.kind).name : ''))
const facts = computed(() => {
  const log = viewing.value
  if (!log) return []
  return [
    ['實際日期', log.done_on],
    ['田區', log.field_text || '未填'],
    ['作物', log.crop_text || '未填'],
    ['資材', log.material_name || '—'],
    ['用量', [log.amount_value, log.amount_unit].filter(Boolean).join(' ') || '—'],
    ['人力', log.labor_text || '—'],
    ['照片', `${log.photos.length} 張`],
  ].filter(([, value]) => value !== '—' || true) as [string, string][]
})

function close() {
  if (!busy.value) emit('close')
}
function onKey(event: KeyboardEvent) {
  if (event.key === 'Escape') close()
}
onMounted(() => document.addEventListener('keydown', onKey))
onBeforeUnmount(() => document.removeEventListener('keydown', onKey))
</script>

<template>
  <div class="drawer-scrim" @click.self="close" />
  <aside class="record-drawer" role="dialog" aria-modal="true" :aria-label="title">
    <header class="rd-head">
      <b>{{ title }}</b>
      <button class="rd-close" type="button" :disabled="busy" aria-label="關閉" @click="close">
        <Icon name="close" :size="18" />
      </button>
    </header>

    <div class="rd-body">
      <!-- 唯讀：先給事實，要改再按「編輯」。直接開成可編輯表單的話，
           點進來只想確認一下的人很容易改到東西。 -->
      <template v-if="viewing && !editing">
        <div class="rd-kind">
          <span class="rd-dot" :class="viewing.kind" />{{ kindLabel }}
          <span v-if="viewing.title" class="rd-title">{{ viewing.title }}</span>
        </div>
        <dl class="rd-facts">
          <template v-for="[label, value] in facts" :key="label">
            <dt>{{ label }}</dt>
            <dd>{{ value }}</dd>
          </template>
        </dl>
        <p v-if="viewing.note" class="rd-note">{{ viewing.note }}</p>
        <p v-if="viewing.locked" class="rd-locked">
          已超過可修改期限，內容不再變動；需要更正請在農事日誌補備註。
        </p>
        <div class="rd-actions">
          <button v-if="!viewing.locked" class="btn primary" type="button" @click="editing = true">
            編輯
          </button>
          <a v-if="logHref" class="btn" :href="logHref">在農事日誌中開啟</a>
        </div>
      </template>

      <WorkLogForm
        v-else
        :farmer-id="farmerId"
        :plans="plans"
        :mode="formMode"
        @busy="busy = $event"
        @saved="emit('saved', $event)"
        @done="emit('close')"
        @cancel="viewing ? (editing = false) : emit('close')"
        @restore-plan="emit('restore-plan', $event)"
      />
    </div>
  </aside>
</template>

<style scoped>
.drawer-scrim {
  position: fixed;
  inset: 0;
  background: rgba(20, 30, 25, 0.28);
  z-index: 70;
}
.record-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 380px;
  max-width: 100vw;
  background: #fff;
  border-left: 1px solid var(--line);
  box-shadow: -12px 0 32px rgba(20, 30, 25, 0.16);
  z-index: 71;
  display: flex;
  flex-direction: column;
}
.rd-head {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 56px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
}
.rd-head b { font-size: 15px; flex: 1; min-width: 0; }
.rd-close {
  border: 0;
  background: none;
  color: var(--muted);
  cursor: pointer;
  display: flex;
  align-items: center;
}
.rd-body { flex: 1; overflow-y: auto; padding: 16px; }

.rd-kind { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; }
.rd-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); flex: none; }
.rd-dot.spray { background: #b23b2d; }
.rd-dot.fertilize { background: #c07a16; }
.rd-dot.irrigate { background: #2f6fb5; }
.rd-dot.fieldwork, .rd-dot.tillage { background: #2f8f5b; }
.rd-dot.harvest { background: #7a5230; }
.rd-dot.scout { background: #865e20; }
.rd-title { font-weight: 400; color: var(--ink-2); }

.rd-facts {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: 6px 10px;
  margin: 14px 0;
  font-size: 13px;
}
.rd-facts dt { color: var(--muted); }
.rd-facts dd { margin: 0; }
.rd-note {
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  background: var(--bg);
  border-radius: 8px;
  padding: 10px 12px;
}
.rd-locked { font-size: 12px; color: var(--muted); margin-top: 10px; line-height: 1.6; }
.rd-actions { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }

/* 手機：抽屜改成從下方推上來的 sheet，右側 380px 在小螢幕等於整頁又不像整頁。 */
@media (max-width: 720px) {
  .record-drawer {
    top: auto;
    left: 0;
    width: 100%;
    max-height: 88vh;
    border-left: 0;
    border-top: 1px solid var(--line);
    border-radius: 16px 16px 0 0;
    box-shadow: 0 -12px 32px rgba(20, 30, 25, 0.18);
  }
}
</style>
