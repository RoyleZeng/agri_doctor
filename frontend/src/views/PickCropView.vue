<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import type { PestCrops } from '@/api/types'
import Icon from '@/components/Icon.vue'
import SearchBox from '@/components/SearchBox.vue'
import { toast } from '@/composables/toast'

const route = useRoute()
const data = ref<PestCrops | null>(null)
const loading = ref(false)
const filter = ref<'all' | 'kdais' | 'issue'>('all')

const KIND_LABEL: Record<string, string> = {
  disease: '病害',
  insect: '蟲害',
  weed: '雜草',
  other: '其他',
}

const crops = computed(() => {
  const all = data.value?.crops ?? []
  if (filter.value === 'kdais') return all.filter((c) => c.kdais_count > 0)
  if (filter.value === 'issue') return all.filter((c) => c.critical + c.warning > 0)
  return all
})

const counts = computed(() => {
  const all = data.value?.crops ?? []
  return {
    all: all.length,
    kdais: all.filter((c) => c.kdais_count > 0).length,
    issue: all.filter((c) => c.critical + c.warning > 0).length,
  }
})

/** 卡片底部那一行：先講最嚴重的，沒有問題才說三源一致。 */
function flag(c: PestCrops['crops'][number]): { text: string; tone: 'crit' | 'warn' | 'ok' } {
  if (c.critical) return { text: `${c.critical} 項需要立即確認`, tone: 'crit' }
  if (c.coverage !== 'covered' && c.coverage !== 'verified_empty') return { text: '植保收錄完整性待確認', tone: 'warn' }
  if (c.warning) return { text: `${c.warning} 項三源對不起來`, tone: 'warn' }
  // 高改場只有 35 種作物有摘要表，沒有不代表有問題，要分開講清楚
  if (!c.kdais_has_document) return { text: '未對上高改場摘要表用藥', tone: 'ok' }
  return { text: '目前已收錄資料未偵測到衝突', tone: 'ok' }
}

async function load(pestId: number) {
  loading.value = true
  try {
    data.value = await api.cropsForPest(pestId)
    if (!data.value.crops.length) {
      toast.warn('本站尚未收錄這個病蟲害的作物組合，不能判定是否有核准用藥。')
    }
  } catch (e) {
    toast.error((e as Error).message)
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => route.params.pestId, (id) => id && load(Number(id)))
onMounted(() => load(Number(route.params.pestId)))
</script>

<template>
  <div class="wrap">
    <div class="qbar">
      <SearchBox size="md" placeholder="改查其他作物或病蟲害⋯" />
      <span class="small muted">已選病蟲害，接著選作物</span>
    </div>

    <template v-if="data">
      <div class="head">
        <div>
          <h1>
            {{ data.pest.name }}
            <span class="tag" :class="data.pest.kind === 'disease' ? 'crit' : 'warn'">
              {{ KIND_LABEL[data.pest.kind] ?? data.pest.kind }}
            </span>
          </h1>
          <p>
            <span v-if="data.pest.scientific_name" class="sci">{{ data.pest.scientific_name }}</span>
            <template v-if="data.pest.scientific_name"> · </template>
            {{ data.crops.length }} 種作物有收錄資料，請選擇作物
          </p>
        </div>

        <div class="chips">
          <button class="chip" :class="{ on: filter === 'all' }" @click="filter = 'all'">
            全部 <span class="cnt">{{ counts.all }}</span>
          </button>
          <button class="chip" :class="{ on: filter === 'kdais' }" @click="filter = 'kdais'">
            高改場有列 <span class="cnt">{{ counts.kdais }}</span>
          </button>
          <button class="chip" :class="{ on: filter === 'issue' }" @click="filter = 'issue'">
            有衝突 <span class="cnt">{{ counts.issue }}</span>
          </button>
        </div>
      </div>

      <div v-if="crops.length" class="crops">
        <router-link
          v-for="c in crops"
          :key="c.id"
          class="cropc"
          :to="{ name: 'result', query: { crop: c.id, pest: data.pest.id } }"
        >
          <div class="n">
            {{ c.name }}
            <span v-if="c.aliases.length" class="alias">也叫「{{ c.aliases.join('、') }}」</span>
          </div>
          <div class="cnt">
            <span class="c ppm">植保<b>{{ c.ppm_count }}</b></span>
            <span class="c" :class="c.fda_count ? 'fda' : 'absent'">
              食藥<b>{{ c.fda_count || '—' }}</b>
            </span>
            <span class="c" :class="c.kdais_count ? 'kdais' : 'absent'">
              高改<b>{{ c.kdais_count || '—' }}</b>
            </span>
          </div>
          <div class="flag" :class="flag(c).tone">
            <Icon :name="flag(c).tone === 'ok' ? 'check' : 'warn'" :size="13" />
            {{ flag(c).text }}
          </div>
        </router-link>
      </div>

      <p v-else class="muted empty">這個篩選條件下沒有作物。</p>
    </template>

    <p v-else-if="loading" class="muted empty">載入中⋯</p>
  </div>
</template>

<style scoped>
.qbar { margin-bottom: 4px; }
.qbar :deep(.searchbox) { flex: 1; max-width: 640px; }
.cropc .flag.warn { color: var(--warn); }
.empty { padding: 40px 0; text-align: center; }
.chip { font-family: inherit; cursor: pointer; }
</style>
