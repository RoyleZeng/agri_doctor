<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { api } from '@/api/client'
import Icon from '@/components/Icon.vue'
import CoveragePanel from '@/components/CoveragePanel.vue'
import SourceChanges from '@/components/SourceChanges.vue'
import { toast } from '@/composables/toast'

type Snapshot = {
  id: number; source: string; status: string; is_active: boolean
  row_count: number; started_at: string | null; finished_at: string | null
  error: string | null; meta: Record<string, unknown> | null
}
type KdaisDoc = {
  id: number; title: string; crop_text: string | null
  revision_date: string | null; pdf_url: string; file_size: string | null
  parsed_at: string | null; parse_note: string | null
}

const snapshots = ref<Snapshot[]>([])
const documents = ref<KdaisDoc[]>([])
const loading = ref(false)
const schedule = ref<Awaited<ReturnType<typeof api.updateSchedule>>>([])

const SOURCES = [
  { key: 'ppm', label: '植物保護資訊系統', note: '完整目錄逐頁節流核驗；耗時以實際進度為準' },
  { key: 'fda', label: '食藥署農藥殘留容許量', note: '四類資料依官網總筆數逐頁核驗' },
  { key: 'kdais', label: '高改場作物安全用藥資訊', note: '每輪核對 PDF 雜湊，保留未變動文件的完整資料' },
]

async function load() {
  loading.value = true
  try {
    const [snaps, docs, jobs] = await Promise.all([api.snapshots(), api.kdaisDocuments(), api.updateSchedule()])
    schedule.value = jobs
    snapshots.value = snaps
    documents.value = docs
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

async function crawl(source: string) {
  try {
    await api.triggerCrawl(source)
    toast.ok('已排入背景執行，稍後重新整理查看進度')
    setTimeout(load, 1500)
  } catch (e) {
    toast.error((e as Error).message)
  }
}

async function rebuild() {
  try {
    await api.triggerRebuild()
    toast.ok('已排入重新正規化與比對')
  } catch (e) {
    toast.error((e as Error).message)
  }
}

const tone = (s: string) =>
  s === 'success' ? 'ok' : s === 'running' ? 'info' : s === 'schema_drift' ? 'warn' : 'crit'

const fmt = (iso: string | null) =>
  iso ? new Date(iso).toLocaleString('zh-TW', { hour12: false }) : '—'

onMounted(load)
</script>

<template>
  <div class="wrap">
    <div class="ph">
      <div>
        <h1>資料來源</h1>
        <p>
          完整性驗證、正規化與差異比對通過後才發布。排程狀態以下方實際心跳為準。
        </p>
      </div>
    </div>

    <section class="card card-b" style="margin-bottom:20px">
      <strong>排程執行狀態</strong>
      <p v-if="!schedule.length" class="muted small">尚未啟動排程服務，不會自動更新。</p>
      <p v-for="job in schedule" :key="job.name" class="small">{{ job.schedule_label }} · {{ job.status === 'online' ? '執行中' : '離線／需確認' }} · 下次 {{ fmt(job.next_run_at) }}</p>
    </section>
    <CoveragePanel />
    <SourceChanges />

    <div class="actions">
      <div v-for="s in SOURCES" :key="s.key" class="card act">
        <div class="l"><span class="dot" :class="s.key" />{{ s.label }}</div>
        <div class="small muted">{{ s.note }}</div>
        <button class="btn sm" @click="crawl(s.key)">
          <Icon name="refresh" :size="14" />重新爬取
        </button>
      </div>
      <div class="card act">
        <div class="l">重建比對</div>
        <div class="small muted">不重新爬，只用現有 active 快照重跑正規化與衝突掃描</div>
        <button class="btn sm primary" @click="rebuild">重建</button>
      </div>
    </div>

    <!-- 三源裡只有高改場的來源文件本身帶版本號，逐份列出來才知道現在用的是哪一版 -->
    <div class="sec">
      <h3><span class="dot kdais" />高改場摘要表版本</h3>
      <span class="cnt">{{ documents.length }} 份 · 另外兩源的網頁沒有公告版本號，只能以抓取時間為準</span>
    </div>
    <section class="card">
      <div class="table-scroll" style="max-height: 420px; overflow-y: auto">
        <table class="table">
          <thead>
            <tr>
              <th>作物</th><th>文件</th><th>修訂日期</th><th>大小</th>
              <th>解析時間</th><th>解析警告</th><th>PDF</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in documents" :key="d.id">
              <td>{{ d.crop_text ?? '—' }}</td>
              <td>{{ d.title }}</td>
              <td class="num">{{ d.revision_date ?? '未標示' }}</td>
              <td class="small muted">{{ d.file_size }}</td>
              <td class="small num muted">{{ fmt(d.parsed_at) }}</td>
              <td class="small">
                <span v-if="d.parse_note" class="warn-text">{{ d.parse_note }}</span>
                <span v-else class="muted">—</span>
              </td>
              <td><a :href="d.pdf_url" target="_blank" rel="noopener">開啟<Icon name="external" :size="12" /></a></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <div class="sec" style="margin-top: 24px">
      <h3>爬取紀錄</h3>
      <span class="cnt">失敗不會覆蓋舊資料；資料量掉一半會標成 schema_drift 擋下來</span>
    </div>
    <section class="card">
      <div class="table-scroll">
        <table class="table">
          <thead>
            <tr>
              <th>#</th><th>來源</th><th>狀態</th><th>使用中</th>
              <th class="r">筆數</th><th>開始</th><th>結束</th><th>錯誤</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in snapshots" :key="s.id">
              <td class="muted">{{ s.id }}</td>
              <td><span class="dot" :class="s.source" />{{ s.source }}</td>
              <td><span class="tag" :class="tone(s.status)">{{ s.status }}</span></td>
              <td>
                <span v-if="s.is_active" class="tag ok">active</span>
                <span v-else class="muted">—</span>
              </td>
              <td class="r num">{{ s.row_count.toLocaleString() }}</td>
              <td class="small num muted">{{ fmt(s.started_at) }}</td>
              <td class="small num muted">{{ fmt(s.finished_at) }}</td>
              <td class="small">
                <span v-if="s.error" class="crit-text">{{ s.error }}</span>
                <span v-else class="muted">—</span>
              </td>
            </tr>
            <tr v-if="!snapshots.length && !loading">
              <td colspan="8" class="muted" style="text-align: center">還沒有爬取紀錄。</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped>
.ph { margin-bottom: 18px; }
.ph h1 { font-size: 24px; font-weight: 700; }
.ph p { color: var(--muted); margin-top: 4px; font-size: 14px; max-width: 760px; line-height: 1.6; }

.actions { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 26px; }
.act { width: 264px; padding: 16px 18px; display: flex; flex-direction: column; gap: 4px; }
.act .l { font-weight: 600; }
.act .btn { margin-top: 10px; align-self: flex-start; }

.sec h3 { display: flex; align-items: center; gap: 8px; }
.warn-text { color: var(--warn); }
.crit-text { color: var(--crit); }
.table a { display: inline-flex; align-items: center; gap: 4px; }
</style>
