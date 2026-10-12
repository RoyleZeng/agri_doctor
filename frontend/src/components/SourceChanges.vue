<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '@/api/client'
import type { UpdateFeed, SourceUpdate } from '@/api/types'
const feed = ref<UpdateFeed | null>(null)
const error = ref('')
const busy = ref(false)
async function load(more = false) {
  busy.value = true
  try { const data = await api.updates(more ? feed.value?.items.length : 0)
    feed.value = more && feed.value ? { ...data, items: [...feed.value.items, ...data.items] } : data
    error.value = ''
  } catch { error.value = '無法讀取變更紀錄，請稍後重試；目前不能確認是否有更新。' }
  finally { busy.value = false }
}
const names: Record<string,string> = { ppm:'植保系統', fda:'食藥署', kdais:'高改場' }
const labels: Record<string,string> = { name:'名稱', english_name:'英文名', common_name:'成分', international_name:'國際名稱', crop_category:'作物類別', limit_text:'MRL (ppm)', note:'備註', banned_manufacture_date:'禁止製造／輸入', banned_sale_date:'禁止銷售／使用', revision_date:'修訂日期', sha256:'檔案 SHA-256', row_count:'收錄筆數', complete:'完整性驗證', title:'文件', pdf_url:'文件連結' }
function display(value: unknown, fallback: string) { return value === true ? '已驗證' : value === false ? '尚未驗證' : value ?? fallback }
function entries(r: SourceUpdate) { return [...new Set([...Object.keys(r.before || {}), ...Object.keys(r.after || {})])].filter(k => labels[k]).map(k => ({ label:labels[k], before:display(r.before?.[k], '未收錄／未提供'), after:display(r.after?.[k], '未再列出／未提供') })) }
onMounted(() => load())
</script>
<template>
  <section class="card changes-panel">
    <div class="card-h"><div><strong>本週變更</strong><span class="small muted">　最近7天 · 三源快照差異</span></div><button class="btn sm" :disabled="busy" @click="load()">重新整理</button></div>
    <div class="card-b">
      <p v-if="error" role="alert">{{ error }}</p>
      <template v-else-if="feed">
        <p class="small muted">{{ feed.notice }}</p>
        <p v-if="!feed.items.length" class="no-changes">目前沒有可比較的變更紀錄。完成來源更新後，這裡會列出禁用名單、MRL 與摘要表的差異。</p>
        <div class="source-health"><span v-for="s in feed.sources" :key="s.source">{{ names[s.source] }}：{{ s.status === 'publication_failed' ? '發布失敗，保留舊資料' : s.status === 'failed' ? '爬取失敗，保留舊資料' : s.status === 'running' ? '更新中' : s.complete && s.published ? '已核驗並發布' : '尚無完整更新驗證' }}</span></div>
        <details v-for="r in feed.items" :key="r.id" class="change-row">
          <summary><span class="tag" :class="r.kind.startsWith('fda_banned') ? 'crit' : 'info'">{{ names[r.source] }}</span><strong>{{ r.title }}</strong><time>{{ new Date(r.detected_at).toLocaleDateString('zh-TW') }}</time></summary>
          <p v-if="r.kind.startsWith('fda_banned')" class="notice">禁止銷售／使用日期（原文）：{{ r.effective_date || '來源未提供，須人工確認' }}。未將製造／輸入日期當成使用禁令生效日。</p>
          <div class="table-scroll"><table class="table"><thead><tr><th>欄位</th><th>前次快照 #{{ r.previous_snapshot_id ?? '—' }}</th><th>本次快照 #{{ r.snapshot_id }}</th></tr></thead><tbody><tr v-for="e in entries(r)" :key="e.label"><td>{{ e.label }}</td><td>{{ e.before }}</td><td>{{ e.after }}</td></tr></tbody></table></div>
          <a v-if="r.source_url" :href="r.source_url" target="_blank" rel="noopener">查看官方來源 ↗</a>
        </details>
        <button v-if="feed.total > feed.items.length" class="btn sm" :disabled="busy" @click="load(true)">再看10筆（共 {{ feed.total }} 筆）</button>
      </template>
      <p v-else>讀取變更紀錄…</p>
    </div>
  </section>
</template>
<style scoped>
.changes-panel { margin:22px 0; }.card-h { gap:12px; flex-wrap:wrap; }.source-health { display:flex; gap:10px 20px; flex-wrap:wrap; color:var(--ink-2); font-size:12px; margin:15px 0; }.no-changes { padding:20px 0; }.change-row { border-top:1px solid var(--line); padding:14px 0; }.change-row summary { cursor:pointer; display:flex; gap:12px; align-items:baseline; }.change-row summary strong { flex:1; font-size:14px; }.change-row time { white-space:nowrap; font-size:12px; color:var(--ink-2); }.change-row td { max-width:320px; overflow-wrap:anywhere; }.notice { margin:12px 0; font-size:13px; }.change-row a { display:block; margin-top:12px; }@media(max-width:600px){.change-row summary { flex-wrap:wrap; }.change-row summary strong { flex-basis:70%; }}
</style>
