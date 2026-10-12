<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api/client'
import type { CoverageOverview } from '@/api/types'
const data = ref<CoverageOverview | null>(null)
const error = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const statuses: Record<string,string> = { running:'爬取進行中，約每30秒更新', failed:'爬取失敗，保留原本正式資料', schema_drift:'來源結構或筆數需人工檢查，未發布', stalled:'爬取心跳逾時，請檢查執行狀態', publication_failed:'發布檢查失敗，保留原本正式資料' }
async function load() {
  try { data.value = await api.coverage(); error.value = '' }
  catch { error.value = '暫時無法確認覆蓋狀態，請核對官方來源。' }
}
onMounted(() => { void load(); timer = setInterval(load, 30000) })
onUnmounted(() => clearInterval(timer))
</script>
<template>
  <section class="card coverage-panel" aria-label="植保資料覆蓋率">
    <div class="card-h"><strong>先確認，資料收錄到哪裡</strong><span class="tag" :class="data?.active.complete ? 'ok' : 'warn'">{{ data?.active.complete ? '本次目錄已完整收錄' : '覆蓋尚未完整驗證' }}</span></div>
    <div class="card-b">
      <p v-if="error" role="status">{{ error }}</p>
      <template v-else-if="data">
        <div class="coverage-stats">
          <div><span>正式快照收錄</span><strong>{{ data.active.stored.toLocaleString() }} <small>個防治對象</small></strong></div>
          <div><span>最近一輪目錄發現</span><strong>{{ data.latest.discovered?.toLocaleString() ?? '未確認' }} <small>個連結</small></strong></div>
          <div><span>{{ data.latest.limited_run ? '測試輪次・未發布' : data.latest.is_active ? '已發布' : '最近爬取・尚未發布' }}</span><strong>{{ data.latest.verified.toLocaleString() }} <small>已驗證</small></strong></div>
        </div>
        <template v-if="data.latest.discovered">
          <progress :value="data.latest.verified" :max="data.latest.discovered" aria-label="最近爬取完成比例" />
          <p class="small muted">{{ data.latest.percent }}% · 待處理 {{ data.latest.pending }} · 失敗 {{ data.latest.failed }} · {{ statuses[data.latest.status || 'never'] || (data.latest.is_active ? '已完成發布' : '完整性檢查通過後才發布') }}</p>
        </template>
        <p class="coverage-note">{{ data.message }} <a :href="data.source_url" target="_blank" rel="noopener">核對官方植保系統 ↗</a></p>
        <p class="small muted">比例只對最近解析的目錄計算，不混用不同快照；來源抓取日期不等於官方更新日。</p>
      </template>
      <p v-else class="muted">正在確認收錄狀態…</p>
    </div>
  </section>
</template>
<style scoped>
.coverage-panel { margin: 22px 0; }.card-h { flex-wrap:wrap; gap:10px; }.coverage-stats { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:18px; }.coverage-stats span { display:block; color:var(--ink-2); font-size:12px; }.coverage-stats strong { display:block; font-size:25px; margin-top:6px; color:var(--brand); }.coverage-stats small { font-size:12px; font-weight:400; }.coverage-note { margin:12px 0 6px; font-size:14px; }progress { width:100%; height:8px; accent-color:var(--brand); margin:16px 0 6px; }@media(max-width:600px){.coverage-stats { grid-template-columns:1fr; gap:12px; }.coverage-stats div { display:flex; justify-content:space-between; align-items:center; gap:10px; }.coverage-stats strong { font-size:21px; margin:0; }}
</style>
