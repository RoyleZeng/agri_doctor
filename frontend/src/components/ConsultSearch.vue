<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { RegistrationSearchItem, SearchItem, SearchResult } from '@/api/types'
import Icon from '@/components/Icon.vue'
import { toast } from '@/composables/toast'

const props = defineProps<{ cropId: number; cropName: string }>()
const emit = defineEmits<{
  pest: [item: SearchItem]
  registration: [item: RegistrationSearchItem]
}>()

const query = ref('')
const open = ref(false)
const loading = ref(false)
const pestResult = ref<SearchResult | null>(null)
const registrations = ref<RegistrationSearchItem[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0

const pests = computed(() =>
  (pestResult.value?.groups ?? [])
    .filter((group) => group.key !== 'crop')
    .flatMap((group) => group.items),
)

watch(query, (value) => {
  clearTimeout(timer)
  const term = value.trim()
  if (!term) {
    pestResult.value = null
    registrations.value = []
    open.value = false
    return
  }
  open.value = true
  // 藥名搜尋最多會帶出 30 筆三源比對，統一延後查詢避免逐鍵請求。
  timer = setTimeout(() => void run(term), 420)
})

async function run(term: string) {
  const mine = ++sequence
  loading.value = true
  try {
    const [pestsData, registrationData] = await Promise.all([
      api.search(term),
      term.length >= 2 ? api.searchRegistrations(term, props.cropId) : Promise.resolve(null),
    ])
    if (mine !== sequence) return
    pestResult.value = pestsData
    registrations.value = registrationData?.items ?? []
  } catch (error) {
    if (mine === sequence) toast.error(`搜尋失敗：${(error as Error).message}`)
  } finally {
    if (mine === sequence) loading.value = false
  }
}

function choosePest(item: SearchItem) {
  emit('pest', item)
  close()
}

function chooseRegistration(item: RegistrationSearchItem) {
  if (!item.can_add) return
  emit('registration', item)
}

function close() {
  open.value = false
  query.value = ''
  pestResult.value = null
  registrations.value = []
}

function onBlur() {
  setTimeout(() => (open.value = false), 140)
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="consult-search">
    <div class="input" :class="{ focused: open }">
      <Icon name="search" :size="18" />
      <input
        v-model="query"
        type="search"
        placeholder="搜尋病蟲害、藥劑或成分名⋯"
        autocomplete="off"
        @focus="query && (open = true)"
        @blur="onBlur"
        @keydown.esc="close"
      />
      <span v-if="loading" class="xs muted">搜尋中⋯</span>
      <button v-else-if="query" class="clear" aria-label="清除搜尋" @mousedown.prevent="close">
        <Icon name="close" :size="16" />
      </button>
    </div>

    <div v-if="open && (loading || pestResult)" class="dd">
      <section v-if="registrations.length" class="group">
        <p class="group-title"><Icon name="flask" :size="14" /> 藥劑 · 已限定作物「{{ cropName }}」</p>
        <button
          v-for="item in registrations"
          :key="`registration-${item.registration_id}`"
          class="result-row drug-row"
          :class="{ unavailable: !item.can_add }"
          :disabled="!item.can_add"
          @mousedown.prevent="chooseRegistration(item)"
        >
          <span class="result-main">
            <b>{{ item.product_name }} → {{ item.pest.name }}</b>
            <small>{{ item.english_composition || item.pest.kind }} · {{ item.moa_codes.join('、') || '作用機制待確認' }}</small>
          </span>
          <span class="facts">
            <span>稀釋<b class="num">{{ item.dilution_text || '—' }}</b></span>
            <span>採收期<b class="num">{{ item.phi_days == null ? '待確認' : `${item.phi_days} 天` }}</b></span>
          </span>
          <span class="action">{{ item.can_add ? '＋ 加入處方' : item.applicable === false ? '不適用本作物' : '不可開立' }}</span>
        </button>
      </section>

      <section v-if="pests.length" class="group">
        <p class="group-title"><Icon name="disease" :size="14" /> 病蟲害 · 查看核准用藥</p>
        <button
          v-for="item in pests"
          :key="`pest-${item.kind}-${item.id}`"
          class="result-row pest-row"
          @mousedown.prevent="choosePest(item)"
        >
          <span class="pest-icon" :class="item.kind === 'disease' ? 'disease' : 'insect'">
            <Icon :name="item.kind === 'disease' ? 'disease' : 'bug'" :size="17" />
          </span>
          <span class="result-main">
            <b>{{ item.name }}</b>
            <small>{{ item.scientific_name || item.alias || item.count_label }}</small>
          </span>
          <span class="action">查看候選 <Icon name="right" :size="14" /></span>
        </button>
      </section>

      <p v-if="query.trim().length === 1" class="tip">藥名與成分名輸入兩個字後開始搜尋；病蟲害可直接選取。</p>
      <p v-else-if="!loading && !registrations.length && !pests.length" class="tip">找不到符合「{{ query }}」的病蟲害或藥劑。</p>
      <p v-else class="tip">藥劑候選以目前案件作物篩選；加入時仍會檢查禁用、採收期與輪替紀錄。</p>
    </div>
  </div>
</template>

<style scoped>
.consult-search { position: relative; z-index: 5; max-width: 860px; }
.input { color: var(--ink); height: 46px; border-color: var(--brand); box-shadow: 0 0 0 4px rgba(36, 92, 69, .1); }
.input.focused { box-shadow: 0 0 0 4px rgba(36, 92, 69, .16), var(--shadow); }
.input input { flex: 1; min-width: 0; border: 0; outline: 0; font: inherit; background: transparent; color: var(--ink); }
.input input::placeholder { color: var(--muted); }
.clear { display: grid; place-items: center; border: 0; background: transparent; color: var(--muted); cursor: pointer; padding: 4px; }
.dd { position: absolute; inset: 54px 0 auto; max-height: min(62vh, 570px); overflow: auto; background: #fff; border: 1px solid var(--line); border-radius: 14px; box-shadow: 0 12px 32px rgba(20, 30, 25, .12); }
.group { padding: 8px 0; }
.group + .group { border-top: 1px solid var(--line); }
.group-title { display: flex; align-items: center; gap: 7px; padding: 3px 18px 7px; font-size: 12px; font-weight: 600; color: var(--muted); }
.result-row { width: 100%; border: 0; background: #fff; text-align: left; cursor: pointer; display: grid; grid-template-columns: minmax(0, 1fr) 210px 112px; align-items: center; gap: 14px; padding: 10px 18px; color: var(--ink); font: inherit; }
.result-row:hover { background: var(--brand-soft); }
.result-row.unavailable { opacity: .62; cursor: not-allowed; }
.result-row.unavailable:hover { background: #fff; }
.result-main { min-width: 0; display: flex; flex-direction: column; }
.result-main b { font-size: 14px; line-height: 1.45; }
.result-main small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--muted); font-size: 12px; }
.facts { display: flex; gap: 12px; color: var(--ink-2); font-size: 11px; }
.facts b { display: block; color: var(--ink); font-size: 13px; font-weight: 600; }
.action { justify-self: end; display: inline-flex; align-items: center; gap: 4px; color: var(--brand); font-size: 13px; font-weight: 600; white-space: nowrap; }
.unavailable .action { color: var(--muted); }
.pest-row { grid-template-columns: 34px minmax(0, 1fr) 112px; }
.pest-icon { display: grid; place-items: center; width: 34px; height: 34px; border-radius: 9px; }
.pest-icon.disease { background: var(--crit-soft); color: var(--crit); }
.pest-icon.insect { background: var(--warn-soft); color: var(--warn); }
.tip { margin: 0; padding: 10px 18px; border-top: 1px solid var(--line); background: #fafbf9; color: var(--muted); font-size: 12px; }
@media (max-width: 720px) {
  .result-row { grid-template-columns: minmax(0, 1fr) auto; }
  .facts { display: none; }
  .pest-row { grid-template-columns: 34px minmax(0, 1fr) auto; }
}
</style>
