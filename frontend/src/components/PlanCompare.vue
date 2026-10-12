<script setup lang="ts">
import type { LogSummary, UnloggedEvent } from '@/api/planning'
import { shortDay } from '@/composables/farmLog'

defineProps<{ compare: LogSummary['plan_compare']; canRecord: boolean }>()
const emit = defineEmits<{ record: [item: UnloggedEvent] }>()
</script>

<template>
  <div class="compare">
    <div class="cmp">
      <div class="c"><b>{{ compare.total }}</b><span>規劃事項</span></div>
      <div class="c"><b class="ok">{{ compare.logged }}</b><span>已記錄</span></div>
      <div class="c"><b class="warn">{{ compare.unlogged.length }}</b><span>未記錄</span></div>
    </div>
    <p class="hint">只算本月已經開始的農事與肥培事項；生育時期與巡查窗口不是作業，不列入。</p>
    <ul v-if="compare.unlogged.length" class="list">
      <li v-for="item in compare.unlogged" :key="item.event_id">
        <span class="text">
          <b>{{ shortDay(item.start_on) }} {{ item.title }}</b>
          <small>{{ item.crop_name }} · {{ item.field_name }}<template v-if="item.completed"> · 已標完成</template></small>
        </span>
        <button v-if="canRecord" type="button" class="btn sm" @click="emit('record', item)">補記</button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.cmp { display: flex; gap: 8px; }
.c { flex: 1; text-align: center; padding: 10px 6px; border-radius: 10px; background: #fafbf9; border: 1px solid var(--line); }
.c b { display: block; font-size: 20px; line-height: 1.3; }
.c b.ok { color: var(--kdais-ink); }
.c b.warn { color: var(--warn); }
.c span { font-size: 12px; color: var(--muted); }
.hint { margin: 10px 0 0; font-size: 12px; color: var(--muted); }
.list { list-style: none; margin: 10px 0 0; padding: 0; display: flex; flex-direction: column; }
.list li { display: flex; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); }
.text { flex: 1; min-width: 0; display: flex; flex-direction: column; font-size: 13px; }
.text small { color: var(--muted); font-size: 12px; }
</style>
