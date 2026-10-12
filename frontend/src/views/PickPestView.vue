<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { api } from '@/api/client'
import type { CropPests } from '@/api/types'
import Icon from '@/components/Icon.vue'
import SearchBox from '@/components/SearchBox.vue'
import { toast } from '@/composables/toast'

const route = useRoute()
const data = ref<CropPests | null>(null)
const loading = ref(false)
const kind = ref<'all' | 'disease' | 'insect' | 'other'>('all')

const KIND_LABEL: Record<string, string> = {
  disease: '病害',
  insect: '蟲害',
  weed: '雜草',
  other: '其他',
}

const counts = computed(() => {
  const all = data.value?.pests ?? []
  return {
    all: all.length,
    disease: all.filter((p) => p.kind === 'disease').length,
    insect: all.filter((p) => p.kind === 'insect').length,
    other: all.filter((p) => p.kind !== 'disease' && p.kind !== 'insect').length,
  }
})

const pests = computed(() => {
  const all = data.value?.pests ?? []
  if (kind.value === 'all') return all
  if (kind.value === 'other') return all.filter((p) => p.kind !== 'disease' && p.kind !== 'insect')
  return all.filter((p) => p.kind === kind.value)
})

function flag(p: CropPests['pests'][number]): { text: string; tone: 'crit' | 'warn' | 'ok' } {
  if (p.critical) return { text: `${p.critical} 項需要立即確認`, tone: 'crit' }
  if (p.coverage !== 'covered' && p.coverage !== 'verified_empty') return { text: '植保收錄完整性待確認', tone: 'warn' }
  if (p.warning) return { text: `${p.warning} 項三源對不起來`, tone: 'warn' }
  if (!p.kdais_has_document) return { text: '高改場摘要表未列', tone: 'ok' }
  return { text: '目前已收錄資料未偵測到衝突', tone: 'ok' }
}

async function load(cropId: number) {
  loading.value = true
  try {
    data.value = await api.pestsForCrop(cropId)
    if (!data.value.pests.length) toast.warn('本站尚未收錄這個作物的可查詢組合，不能判定是否有核准用藥。')
  } catch (e) {
    toast.error((e as Error).message)
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(() => route.params.cropId, (id) => id && load(Number(id)))
onMounted(() => load(Number(route.params.cropId)))
</script>

<template>
  <div class="wrap">
    <div class="qbar">
      <SearchBox size="md" placeholder="改查其他作物或病蟲害⋯" />
      <span class="small muted">已選作物，接著選病蟲害</span>
    </div>

    <template v-if="data">
      <div class="head">
        <div>
          <h1>
            {{ data.crop.name }}
            <span v-if="data.crop.scientific_name" class="sci">{{ data.crop.scientific_name }}</span>
          </h1>
          <p>
            <span v-if="data.crop.aliases.length">也叫「{{ data.crop.aliases.join('、') }}」 · </span>
            <span v-if="data.crop.fda_category">食藥署類別 {{ data.crop.fda_category }} · </span>
            {{ data.pests.length }} 種病蟲害有收錄資料，請選擇
          </p>
        </div>

        <div class="chips">
          <button class="chip" :class="{ on: kind === 'all' }" @click="kind = 'all'">
            全部 <span class="cnt">{{ counts.all }}</span>
          </button>
          <button class="chip" :class="{ on: kind === 'disease' }" @click="kind = 'disease'">
            病害 <span class="cnt">{{ counts.disease }}</span>
          </button>
          <button class="chip" :class="{ on: kind === 'insect' }" @click="kind = 'insect'">
            蟲害 <span class="cnt">{{ counts.insect }}</span>
          </button>
          <button class="chip" :class="{ on: kind === 'other' }" @click="kind = 'other'">
            其他 <span class="cnt">{{ counts.other }}</span>
          </button>
        </div>
      </div>

      <div v-if="pests.length" class="crops">
        <router-link
          v-for="p in pests"
          :key="p.id"
          class="cropc"
          :to="{ name: 'result', query: { crop: data.crop.id, pest: p.id } }"
        >
          <div class="n">
            {{ p.name }}
            <span class="alias">{{ KIND_LABEL[p.kind] ?? p.kind }}</span>
          </div>
          <div v-if="p.aliases.length" class="xs muted alias-line">別名：{{ p.aliases.join('、') }}</div>
          <div class="cnt">
            <span class="c ppm">植保<b>{{ p.ppm_count }}</b></span>
            <span class="c" :class="p.fda_count ? 'fda' : 'absent'">
              食藥<b>{{ p.fda_count || '—' }}</b>
            </span>
            <span class="c" :class="p.kdais_count ? 'kdais' : 'absent'">
              高改<b>{{ p.kdais_count || '—' }}</b>
            </span>
          </div>
          <div class="flag" :class="flag(p).tone">
            <Icon :name="flag(p).tone === 'ok' ? 'check' : 'warn'" :size="13" />
            {{ flag(p).text }}
          </div>
        </router-link>
      </div>

      <p v-else class="muted empty">這個篩選條件下沒有病蟲害。</p>
    </template>

    <p v-else-if="loading" class="muted empty">載入中⋯</p>
  </div>
</template>

<style scoped>
.qbar { margin-bottom: 4px; }
.qbar :deep(.searchbox) { flex: 1; max-width: 640px; }
.cropc .flag.warn { color: var(--warn); }
.alias-line { margin-top: -4px; }
.empty { padding: 40px 0; text-align: center; }
.chip { font-family: inherit; cursor: pointer; }
</style>
