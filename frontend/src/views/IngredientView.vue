<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'

import { api } from '@/api/client'
import type { IngredientOverview } from '@/api/types'
import Icon from '@/components/Icon.vue'
import { toast } from '@/composables/toast'

type Hit = { id: number; name: string; english_name: string | null }

const q = ref('')
const options = ref<Hit[]>([])
const open = ref(false)
const data = ref<IngredientOverview | null>(null)
const loading = ref(false)

let timer: ReturnType<typeof setTimeout> | undefined

watch(q, (value) => {
  clearTimeout(timer)
  const term = value.trim()
  if (!term) {
    options.value = []
    open.value = false
    return
  }
  open.value = true
  timer = setTimeout(async () => {
    try {
      options.value = await api.searchIngredients(term)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }, 180)
})

/** 延遲關閉，否則點選項目時 blur 會先觸發、mousedown 就吃不到。 */
function closeSoon() {
  setTimeout(() => (open.value = false), 140)
}

async function pick(hit: Hit) {
  open.value = false
  q.value = ''
  loading.value = true
  try {
    data.value = await api.ingredientOverview(hit.id)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    loading.value = false
  }
}

const KIND_LABEL: Record<string, string> = {
  disease: '病害', insect: '蟲害', weed: '雜草', other: '其他',
}

onBeforeUnmount(() => clearTimeout(timer))
</script>

<template>
  <div class="wrap">
    <div class="qbar">
      <div class="input picker">
        <span v-if="data" class="sel">{{ data.ingredient.name }}</span>
        <input
          v-model="q"
          type="text"
          placeholder="輸入中文或英文成分名⋯"
          @focus="q && (open = true)"
          @blur="closeSoon"
        />
        <div v-if="open && options.length" class="dd">
          <div class="g">
            <div
              v-for="o in options"
              :key="o.id"
              class="opt"
              @mousedown.prevent="pick(o)"
            >
              <span class="icon pest"><Icon name="flask" :size="18" /></span>
              <span class="t">
                <div class="n">{{ o.name }}</div>
                <div v-if="o.english_name" class="s">{{ o.english_name }}</div>
              </span>
            </div>
          </div>
        </div>
      </div>
      <span class="small muted">手上這支成分，還能用在哪些作物、容許量各是多少</span>
    </div>

    <template v-if="data">
      <section class="card head">
        <div>
          <h1>
            {{ data.ingredient.name }}
            <span v-if="data.ingredient.english_name" class="sci">
              {{ data.ingredient.english_name }}
            </span>
          </h1>
          <div class="meta">
            <span>
              禁用狀態
              <b v-if="data.ingredient.banned" style="color: var(--crit)">列於公告禁用名單</b>
              <b v-else style="color: var(--kdais-ink)">未列於公告禁用名單</b>
            </span>
            <!-- 以群組訂容許量的成分要講清楚，否則會以為那是自己的值 -->
            <span v-if="data.ingredient.mrl_group">
              食藥署 <b>以「{{ data.ingredient.mrl_group.name }}」群組訂容許量</b>
            </span>
            <span v-else>食藥署 <b>依成分本名訂容許量</b></span>
          </div>
        </div>

        <div class="stats">
          <div class="stat fda">
            <div class="k">食藥署 容許量</div>
            <div class="v num">{{ data.stats.mrl_categories }}<small>作物類別</small></div>
          </div>
          <div class="stat ppm">
            <div class="k">植保系統 核准範圍</div>
            <div class="v num">{{ data.stats.registrations }}<small>作物×病蟲害</small></div>
          </div>
          <div class="stat kdais">
            <div class="k">高改場 摘要表列入</div>
            <div class="v num">{{ data.stats.kdais_rows }}<small>筆</small></div>
          </div>
        </div>
      </section>

      <div class="grid">
        <section class="card">
          <div class="card-h">
            <span class="title"><span class="dot fda" /> 殘留容許量（食藥署）</span>
            <span class="small muted">{{ data.limits.length }} 筆</span>
          </div>
          <div class="table-scroll" style="max-height: 560px; overflow-y: auto">
            <table class="table">
              <thead>
                <tr><th>作物類別</th><th class="r">容許量 (ppm)</th><th>備註</th></tr>
              </thead>
              <tbody>
                <tr v-for="m in data.limits" :key="m.crop_category">
                  <td>{{ m.crop_category }}</td>
                  <td class="r num">{{ m.limit_text ?? '—' }}</td>
                  <td class="small muted">{{ m.note }}</td>
                </tr>
                <tr v-if="!data.limits.length">
                  <td colspan="3" class="muted" style="text-align: center">
                    食藥署查無這個成分的容許量
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="card">
          <div class="card-h">
            <span class="title"><span class="dot ppm" /> 核准使用範圍（植保系統）</span>
            <span class="small muted">
              {{ data.stats.registrations }} 筆 · 依作物分組 · 點病蟲害開啟三源比對
            </span>
          </div>
          <div v-if="data.usages.length">
            <div v-for="g in data.usages" :key="g.crop_id" class="crop-g">
              <div class="ch"><b>{{ g.crop }}</b><span class="small muted">{{ g.items.length }} 筆</span></div>
              <router-link
                v-for="(u, i) in g.items"
                :key="i"
                class="use"
                :to="{ name: 'result', query: { crop: g.crop_id, pest: u.pest_id } }"
              >
                <span>
                  {{ u.pest }}
                  <span class="xs muted">{{ KIND_LABEL[u.pest_kind] ?? '' }}</span>
                </span>
                <span class="small muted">{{ u.product }}</span>
                <span class="num">{{ u.dilution ?? '—' }}</span>
                <span class="num">
                  <template v-if="u.phi_days !== null">{{ u.phi_days }} 天</template>
                  <span v-else class="muted">未載明</span>
                </span>
                <span class="xs muted">{{ u.moa ?? '' }}</span>
              </router-link>
            </div>
          </div>
          <div v-else class="card-b muted">植保系統查無含這個成分的核准登記。</div>
        </section>
      </div>
    </template>

    <p v-else-if="loading" class="muted empty">載入中⋯</p>
    <p v-else class="muted empty">在上面輸入成分名開始查詢。</p>
  </div>
</template>

<style scoped>
.qbar { display: flex; align-items: center; gap: 12px; margin-bottom: 20px; flex-wrap: wrap; }
.qbar .input.picker { position: relative; height: 46px; width: min(520px, 100%); color: var(--ink); }
.qbar .input input {
  flex: 1; min-width: 0; border: none; outline: none;
  background: transparent; font: inherit; color: inherit;
}
.qbar .sel {
  display: inline-flex; align-items: center; padding: 2px 10px; border-radius: 8px;
  background: #efe9f6; color: #5b3d8a; font-weight: 600; font-size: 14px;
}
.dd {
  position: absolute; left: 0; right: 0; top: 52px; z-index: 3;
  background: #fff; border: 1px solid var(--line); border-radius: 14px;
  box-shadow: 0 12px 32px rgba(20, 30, 25, 0.12); overflow: hidden;
  max-height: 320px; overflow-y: auto;
}
.opt { cursor: pointer; }

.head { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 22px 24px; margin-bottom: 20px; flex-wrap: wrap; }
.head h1 { font-size: 26px; font-weight: 700; }
.head h1 .sci { font-size: 16px; margin-left: 8px; }
.head .meta { display: flex; flex-wrap: wrap; gap: 6px 16px; margin-top: 8px; font-size: 13px; color: var(--muted); }
.head .meta b { color: var(--ink-2); font-weight: 500; }

.stats { display: flex; gap: 10px; flex-wrap: wrap; }
.stat { min-width: 150px; padding: 10px 14px; border-radius: 10px; }
.stat .k { font-size: 12px; font-weight: 600; }
.stat .v { font-size: 20px; font-weight: 700; line-height: 1.2; }
.stat .v small { font-size: 12px; font-weight: 500; color: var(--muted); margin-left: 3px; }
.stat.fda { background: var(--fda-soft); } .stat.fda .k { color: var(--fda-ink); }
.stat.ppm { background: var(--ppm-soft); } .stat.ppm .k { color: var(--ppm-ink); }
.stat.kdais { background: var(--kdais-soft); } .stat.kdais .k { color: var(--kdais-ink); }

.grid { display: grid; grid-template-columns: 440px minmax(0, 1fr); gap: 20px; align-items: start; }
@media (max-width: 1100px) { .grid { grid-template-columns: minmax(0, 1fr); } }

.crop-g { border-bottom: 1px solid var(--line); }
.crop-g:last-child { border-bottom: none; }
.crop-g .ch { display: flex; align-items: baseline; gap: 10px; padding: 12px 20px 6px; }
.crop-g .ch b { font-size: 15px; }
.use {
  display: grid; grid-template-columns: minmax(0, 1fr) 1.2fr 90px 90px 110px;
  gap: 10px; align-items: center; padding: 8px 20px; font-size: 14px; color: var(--ink);
}
.use:hover { background: #fafbf9; text-decoration: none; }
@media (max-width: 760px) { .use { grid-template-columns: minmax(0, 1fr) 80px; } .use > :nth-child(n + 3) { display: none; } }
.empty { padding: 60px 0; text-align: center; }
</style>
