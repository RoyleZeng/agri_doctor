<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { api } from '@/api/client'
import type { SearchGroup, SearchItem, SearchResult } from '@/api/types'
import Icon from '@/components/Icon.vue'
import type { IconName } from '@/components/icons'
import { toast } from '@/composables/toast'

const props = withDefaults(
  defineProps<{ size?: 'lg' | 'md'; autofocus?: boolean; placeholder?: string }>(),
  { size: 'lg', autofocus: false, placeholder: '輸入作物、病名或害蟲名⋯' },
)

const router = useRouter()

const q = ref('')
const open = ref(false)
const loading = ref(false)
const result = ref<SearchResult | null>(null)
const active = ref(0)
const inputEl = ref<HTMLInputElement | null>(null)

let timer: ReturnType<typeof setTimeout> | undefined
let seq = 0

/** 把分組結果攤平成一維，鍵盤上下鍵才好走。 */
const flat = computed<{ group: SearchGroup; item: SearchItem }[]>(() => {
  const out: { group: SearchGroup; item: SearchItem }[] = []
  for (const group of result.value?.groups ?? []) {
    for (const item of group.items) out.push({ group, item })
  }
  return out
})

const direct = computed(() => result.value?.direct ?? null)

const ICON: Record<string, IconName> = {
  crop: 'leaf',
  disease: 'disease',
  insect: 'bug',
  weed: 'leaf',
  other: 'flask',
}
const ICON_CLASS: Record<string, string> = {
  crop: 'crop',
  disease: 'dis',
  insect: 'pest',
  weed: 'pest',
  other: 'pest',
}

watch(q, (value) => {
  clearTimeout(timer)
  active.value = 0
  const term = value.trim()
  if (!term) {
    result.value = null
    open.value = false
    return
  }
  open.value = true
  timer = setTimeout(() => void run(term), 180)
})

async function run(term: string) {
  const mine = ++seq
  loading.value = true
  try {
    const data = await api.search(term)
    // 慢回來的舊請求不要覆蓋新結果
    if (mine !== seq) return
    result.value = data
  } catch (e) {
    if (mine === seq) toast.error(`搜尋失敗：${(e as Error).message}`)
  } finally {
    if (mine === seq) loading.value = false
  }
}

/** 命中詞用底線標出來，讓人確認為什麼這一筆會被找到。 */
function highlight(name: string): string {
  const term = result.value?.term
  const escaped = name.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[c]!)
  if (!term) return escaped
  const idx = escaped.indexOf(term)
  if (idx < 0) return escaped
  return (
    escaped.slice(0, idx) + `<mark>${term}</mark>` + escaped.slice(idx + term.length)
  )
}

function choose(item: SearchItem) {
  close()
  if (item.kind === 'crop') {
    router.push({ name: 'result', query: { crop: item.id } })
  } else {
    // 先選病蟲害的話，下一步是挑作物
    router.push({ name: 'pick-crop', params: { pestId: item.id } })
  }
}

function openDirect() {
  const d = direct.value
  if (!d) return
  close()
  router.push({ name: 'result', query: { crop: d.crop.id, pest: d.pest.id } })
}

function onEnter() {
  if (direct.value && active.value === -1) return openDirect()
  const hit = flat.value[active.value]
  if (hit) return choose(hit.item)
  if (direct.value) return openDirect()
}

function move(delta: number) {
  const min = direct.value ? -1 : 0
  const max = flat.value.length - 1
  if (max < min) return
  let next = active.value + delta
  if (next > max) next = min
  if (next < min) next = max
  active.value = next
}

function close() {
  open.value = false
  q.value = ''
  result.value = null
}

function onBlur() {
  // 延遲關閉，否則點選項目時 blur 會先觸發、click 就吃不到
  setTimeout(() => (open.value = false), 140)
}

function fill(term: string) {
  q.value = term
  void nextTick(() => inputEl.value?.focus())
}

defineExpose({ fill })
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="searchbox">
    <div class="input" :class="{ lg: size === 'lg', focused: open }">
      <Icon name="search" :size="size === 'lg' ? 20 : 17" style="color: var(--muted)" />
      <input
        ref="inputEl"
        v-model="q"
        type="text"
        :placeholder="placeholder"
        :autofocus="autofocus"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="onEnter"
        @keydown.esc="close"
        @focus="q && (open = true)"
        @blur="onBlur"
      />
      <span v-if="loading" class="xs muted">搜尋中⋯</span>
      <span v-else-if="!q" class="kbd">Enter</span>
    </div>

    <div v-if="open && (result || loading)" class="dd">
      <!-- 一次打兩個詞就能直接開結果頁，不用再點兩次 -->
      <div v-if="direct" class="g">
        <div class="gh">直接開啟</div>
        <div class="opt" :class="{ hl: active === -1 }" @mousedown.prevent="openDirect">
          <span class="icon crop"><Icon name="leaf" :size="18" /></span>
          <span class="t">
            <div class="n">{{ direct.crop.name }} × {{ direct.pest.name }}</div>
            <div class="s">已比對出這組有 {{ direct.registration_count }} 筆核准登記</div>
          </span>
          <span class="right">看三源比對 <Icon name="right" :size="14" /></span>
        </div>
      </div>

      <div v-for="group in result?.groups ?? []" :key="group.key" class="g">
        <div class="gh">{{ group.label }} · {{ group.items.length }}</div>
        <div
          v-for="item in group.items"
          :key="group.key + '-' + item.id"
          class="opt"
          :class="{ hl: flat[active]?.item === item }"
          @mousedown.prevent="choose(item)"
        >
          <span class="icon" :class="ICON_CLASS[group.key] ?? 'pest'">
            <Icon :name="ICON[group.key] ?? 'flask'" :size="18" />
          </span>
          <span class="t">
            <div class="n" v-html="highlight(item.name)" />
            <div v-if="item.scientific_name || item.alias" class="s">
              <em v-if="item.scientific_name">{{ item.scientific_name }}</em>
              <template v-if="item.scientific_name && item.alias"> · </template>
              <template v-if="item.alias">別名：{{ item.alias }}</template>
            </div>
          </span>
          <span class="right">{{ item.count_label }} <Icon name="right" :size="14" /></span>
        </div>
      </div>

      <div v-if="result && !result.total && !direct && !loading" class="g">
        <div class="opt empty">找不到名稱含「{{ result.term ?? q }}」的作物、病害或害蟲</div>
      </div>

      <div class="tip">
        <Icon name="info" :size="14" />
        也可以一次打兩個詞，例如「芒果 炭疽」直接指定作物與病蟲害 ·
        <kbd>↑</kbd><kbd>↓</kbd> 選擇 · <kbd>Enter</kbd> 開啟
      </div>
    </div>
  </div>
</template>

<style scoped>
.input { color: var(--ink); }
.input.focused {
  border-color: var(--brand);
  box-shadow: 0 0 0 4px rgba(36, 92, 69, 0.12), var(--shadow);
}
.input input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font: inherit;
  color: inherit;
}
.input input::placeholder { color: var(--muted); }

.kbd {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--muted);
  font-size: 12px;
}

.dd { max-height: min(60vh, 520px); overflow-y: auto; }
.opt { cursor: pointer; }
.opt .s em { font-style: italic; }
.opt.empty {
  display: block;
  color: var(--muted);
  font-size: 13px;
  padding: 12px 18px;
  cursor: default;
}
.dd .tip kbd + kbd { margin-left: 3px; }
</style>
