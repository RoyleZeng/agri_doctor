<script setup lang="ts">
import { computed } from 'vue'
import type { LogSummary } from '@/api/planning'
import { missingText, shortDay } from '@/composables/farmLog'

const props = defineProps<{ total: number; compliance: LogSummary['compliance'] }>()
const emit = defineEmits<{ focus: [logId: string] }>()
const percent = computed(() => (props.total ? Math.round((props.compliance.complete / props.total) * 100) : 0))
</script>

<template>
  <div class="compliance">
    <div class="c-title">產銷履歷欄位完整度</div>
    <div class="meter" role="meter" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100" aria-label="產銷履歷欄位完整度">
      <i :style="{ width: `${percent}%` }" />
    </div>
    <p v-if="!total" class="c-note">本月尚無紀錄。</p>
    <p v-else class="c-note">{{ compliance.complete }} / {{ total }} 筆欄位齊全<span v-if="compliance.incomplete.length"> · 以下紀錄可補齊：</span></p>
    <ul v-if="compliance.incomplete.length" class="c-list">
      <li v-for="item in compliance.incomplete.slice(0, 6)" :key="item.id">
        <button type="button" @click="emit('focus', item.id)">
          <b>{{ shortDay(item.done_on) }}</b> {{ item.title }}<small>缺{{ missingText(item.missing) }}</small>
        </button>
      </li>
      <li v-if="compliance.incomplete.length > 6" class="c-more">另有 {{ compliance.incomplete.length - 6 }} 筆</li>
    </ul>
    <p class="c-foot">依存檔當時的規則判定；只影響匯出提醒，不擋存檔。</p>
  </div>
</template>

<style scoped>
.c-title { font-size: 13px; font-weight: 600; }
.meter { height: 8px; border-radius: 999px; background: var(--line); overflow: hidden; margin: 8px 0 6px; }
.meter i { display: block; height: 100%; background: var(--kdais); transition: width .3s; }
.c-note, .c-foot { margin: 0; font-size: 12px; color: var(--muted); }
.c-foot { margin-top: 8px; }
.c-list { list-style: none; margin: 8px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.c-list button { width: 100%; border: 0; background: var(--warn-soft); border-radius: 8px; padding: 6px 10px; text-align: left; font-size: 12px; color: var(--ink-2); }
.c-list button:hover { filter: brightness(.97); }
.c-list small { display: block; color: var(--warn); font-size: 11px; }
.c-more { font-size: 12px; color: var(--muted); padding-left: 10px; }
</style>
