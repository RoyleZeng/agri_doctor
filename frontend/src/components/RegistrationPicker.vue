<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '@/api/client'
import type { RegistrationSearchItem } from '@/api/types'
import { splitByMoa, usesExcludedMoa } from '@/composables/banners'
import Icon from './Icon.vue'

/** 施藥紀錄挑選藥劑登記。作用機制與安全採收期只從這裡（或處方）來，手打的藥名不反查。 */
const props = defineProps<{
  cropId: number | null
  cropName: string
  /** 要避開的作用機制（正規化後，如 `FRAC 3`）；抗藥性輪替橫幅的「換一支不同機制」會帶進來。 */
  excludeGroups?: string[]
}>()
const emit = defineEmits<{ pick: [item: RegistrationSearchItem]; cancel: [] }>()

const query = ref('')
const loading = ref(false)
const searched = ref(false)
const error = ref('')
const items = ref<RegistrationSearchItem[]>([])
let timer: ReturnType<typeof setTimeout> | undefined
let sequence = 0

watch(query, (value) => {
  clearTimeout(timer)
  error.value = ''
  const term = value.trim()
  if (term.length < 2) {
    sequence++
    items.value = []
    searched.value = false
    loading.value = false
    return
  }
  loading.value = true
  // 每筆候選都要跑三源比對，逐鍵查會把伺服器打滿。
  timer = setTimeout(() => void run(term), 420)
})

async function run(term: string) {
  const mine = ++sequence
  try {
    const result = await api.searchRegistrations(term, props.cropId ?? undefined)
    if (mine !== sequence) return
    items.value = result.items
    searched.value = true
  } catch (e) {
    if (mine === sequence) error.value = `搜尋失敗：${(e as Error).message}`
  } finally {
    if (mine === sequence) loading.value = false
  }
}

const productText = (item: RegistrationSearchItem) =>
  [item.concentration, item.product_name, item.formulation].filter(Boolean).join(' ')

/* 依作用機制分堆。**同機制的不刪掉**，只收起來——平台的立場是把對不起來的地方標出來，
   不是替植物醫生做決定；有時候就是只剩那一支藥可用，他得看得到並自己承擔判斷。 */
const split = computed(() => splitByMoa(items.value, (i) => i.moa_codes, props.excludeGroups ?? []))
const showExcluded = ref(false)
const excluding = computed(() => (props.excludeGroups?.length ?? 0) > 0)
const visible = computed(() =>
  excluding.value && !showExcluded.value
    ? [...split.value.allowed, ...split.value.unknown]
    : items.value)
const isExcluded = (item: RegistrationSearchItem) =>
  excluding.value && usesExcludedMoa(item.moa_codes, props.excludeGroups ?? [])

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="reg-picker">
    <div class="reg-input">
      <Icon name="search" :size="16" />
      <input
        v-model="query"
        type="search"
        autocomplete="off"
        placeholder="輸入藥名或成分名，至少兩個字"
        aria-label="搜尋藥劑登記"
        @keydown.esc.prevent="emit('cancel')"
        @keydown.enter.prevent
      />
      <span v-if="loading" class="reg-status">搜尋中⋯</span>
      <button type="button" class="text-btn" @click="emit('cancel')">取消</button>
    </div>
    <p class="reg-tip">
      {{ cropId ? `候選已限定作物「${cropName}」` : '這筆紀錄的作物沒有對應到標準作物，候選不限作物，請核對登記作物。' }}
    </p>
    <p v-if="excluding" class="reg-tip reg-exclude">
      <Icon name="info" :size="14" />
      已避開 {{ (excludeGroups ?? []).join('、') }}；作用機制未載明的仍會列出，但不代表它是不同機制。
      <button
        v-if="split.excluded.length"
        type="button"
        class="text-btn"
        @click="showExcluded = !showExcluded"
      >
        {{ showExcluded ? '收起同機制的' : `仍要顯示同機制的 ${split.excluded.length} 筆` }}
      </button>
    </p>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <div v-if="visible.length" class="reg-list">
      <button
        v-for="item in visible"
        :key="item.registration_id"
        type="button"
        class="reg-row"
        @click="emit('pick', item)"
      >
        <span class="reg-main">
          <b>{{ productText(item) }} → {{ item.pest.name }}</b>
          <small>{{ item.english_composition || '成分資料待確認' }} · {{ item.moa_codes.join('、') || '作用機制未載明' }}</small>
          <span v-if="item.banned || item.applicable === false || isExcluded(item) || !item.moa_codes.length" class="reg-flags">
            <span v-if="item.banned" class="tag crit">含禁用成分</span>
            <span v-if="item.applicable === false" class="tag warn">非本作物登記</span>
            <span v-if="isExcluded(item)" class="tag crit">與近期同機制</span>
            <span v-else-if="excluding && !item.moa_codes.length" class="tag warn">機制未載明</span>
          </span>
        </span>
        <span class="reg-facts">
          <span>稀釋<b class="num">{{ item.dilution_text || '—' }}</b></span>
          <span>採收期<b class="num">{{ item.phi_days == null ? '未載明' : `${item.phi_days} 天` }}</b></span>
        </span>
        <span class="reg-action">選用</span>
      </button>
    </div>
    <p v-else-if="searched && !loading && excluding && split.excluded.length" class="reg-tip">
      符合「{{ query.trim() }}」的登記全部都是 {{ (excludeGroups ?? []).join('、') }}。
      <button type="button" class="text-btn" @click="showExcluded = true">仍要顯示這 {{ split.excluded.length }} 筆</button>
    </p>
    <p v-else-if="searched && !loading" class="reg-tip">找不到符合「{{ query.trim() }}」的登記。仍可直接填寫藥名，但輪替檢查將無法判斷作用機制。</p>
  </div>
</template>

<style scoped>
.reg-picker { border: 1px solid var(--line-strong); border-radius: 12px; background: #fff; overflow: hidden; }
.reg-input { display: flex; align-items: center; gap: 8px; padding: 6px 10px 6px 12px; border-bottom: 1px solid var(--line); color: var(--muted); }
.reg-input input { flex: 1; min-width: 0; border: 0 !important; outline: 0; padding: 6px 0 !important; font: inherit; color: var(--ink); background: transparent; box-shadow: none !important; }
.reg-status { font-size: 12px; white-space: nowrap; }
.reg-tip { margin: 0; padding: 8px 12px; font-size: 12px; color: var(--muted); background: #fafbf9; }
.reg-list { max-height: 300px; overflow: auto; border-top: 1px solid var(--line); }
.reg-row { width: 100%; display: grid; grid-template-columns: minmax(0, 1fr) 150px 52px; gap: 12px; align-items: center; padding: 10px 12px; border: 0; border-bottom: 1px solid var(--line); background: #fff; text-align: left; color: var(--ink); font: inherit; }
.reg-row:hover { background: var(--brand-soft); }
.reg-main { display: flex; flex-direction: column; min-width: 0; }
.reg-main b { font-size: 14px; line-height: 1.45; }
.reg-main small { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.reg-flags { display: flex; gap: 6px; margin-top: 4px; }
.reg-facts { display: flex; gap: 12px; font-size: 11px; color: var(--ink-2); }
.reg-facts b { display: block; font-size: 13px; color: var(--ink); font-weight: 600; }
.reg-action { justify-self: end; color: var(--brand); font-weight: 600; font-size: 13px; }
@media (max-width: 660px) {
  .reg-row { grid-template-columns: minmax(0, 1fr) auto; }
  .reg-facts { grid-column: 1; }
  .reg-action { grid-row: 1; grid-column: 2; }
}
.reg-exclude { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; color: var(--ink-2); }
</style>
