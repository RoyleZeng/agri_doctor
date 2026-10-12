<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { api } from '@/api/client'
import type { ComparisonRow, Issue, SourceKey } from '@/api/types'
import FarmModal from '@/components/FarmModal.vue'
import Icon from '@/components/Icon.vue'

const props = defineProps<{ row: ComparisonRow; cropName: string }>()

/* ---- 許可證（配方層級） ----
 * 只講「這個配方有幾張許可證、幾家廠商」，**不挑其中一張**：
 * 同配方多家廠商是常態，把某一家的字號印在處方卡上就是替植物醫生指定品牌。
 * 三元組（英文成分名＋含量＋劑型）任一項對不上就整行不顯示，不猜。
 */
const permitInfo = ref<{ permits: number; vendors: number; revoked: number; all_revoked: boolean } | null>(null)

onMounted(async () => {
  try {
    const result = await api.formulationPermits(props.row)
    if (result.matched) permitInfo.value = result.summary
  } catch {
    // 許可證查不到不該影響三源比對本身：那是附加資訊，不是用藥依據。
  }
})

/** 帶三元組到農藥廠商查詢：那裡才看得到是哪幾家、哪幾張。 */
const vendorLink = computed(() => ({
  path: '/vendors',
  query: {
    q: props.row.english_composition ?? '',
    contents: props.row.concentration ?? '',
    formulation: props.row.formulation ?? '',
  },
}))

const open = ref(false)
const methodOpen = ref(false)

const ORDER: SourceKey[] = ['ppm', 'fda', 'kdais']
const SOURCE_LABEL: Record<SourceKey, string> = {
  ppm: '植保系統 · 法定核准',
  fda: '食藥署 · 殘留容許量',
  kdais: '高改場 · 實務推薦',
}

const fullName = computed(() =>
  [props.row.concentration, props.row.product_name, props.row.formulation]
    .filter(Boolean)
    .join(' '),
)

/** 藥名底下那行：英文成分 + 作用機制代碼。 */
const subtitle = computed(() => {
  const parts: string[] = []
  if (props.row.english_composition) parts.push(props.row.english_composition)
  const moa = props.row.moa_codes.map((c) => c.split(';')[0]).filter(Boolean)
  if (moa.length) parts.push(moa.join(' + '))
  return parts.join(' · ')
})

/** 食藥署容許量：多成分時把每個都列出來，查無的照實說。 */
const mrlSummary = computed(() => {
  const found = props.row.mrls.filter((m) => m.found)
  if (!props.row.mrls.length) return { value: '—', note: '未對應到成分' }
  if (!found.length) return { value: '查無', note: `${props.row.mrls.length} 個成分皆查無` }
  return {
    value: found.map((m) => m.limit_text ?? '—').join(' / '),
    note: found.map((m) => m.ingredient_name).join('、'),
  }
})

const kdaisEntry = computed(() => props.row.kdais_entries[0] ?? null)

/**
 * 施藥方法常是一整段官方文字。卡片只保留可辨識的摘要，避免一筆長方法
 * 把同列的採收期與容許量擠到看不見；原文仍由視窗完整顯示，不做截字後改寫。
 */
const methodPreview = computed(() => {
  const value = props.row.application_method?.replace(/\s+/g, ' ').trim() ?? ''
  return value.length > 44 ? `${value.slice(0, 44)}…` : value
})

/** 每一列的三源格子：有沒有／說了什麼／多新。缺的來源用灰虛線（缺 ≠ 錯）。 */
const boxes = computed(() =>
  ORDER.map((key) => {
    const info = props.row.sources?.[key]
    const present = !!info?.present
    let value = '—'
    let sub = info?.version_label ?? ''

    if (key === 'ppm') {
      value = '核准登記'
      sub = `${props.cropName} · 抓取 ${fmtShort(info?.fetched_at)}`
    } else if (key === 'fda') {
      if (props.row.banned) {
        value = '公告禁用'
      } else if (present) {
        const m = props.row.mrls.find((x) => x.found)
        value = `${m?.limit_text ?? '—'} ppm（${m?.matched_category ?? ''}）`
      } else {
        value = '查無容許量'
      }
      sub = `${info?.detail ?? ''} · 抓取 ${fmtShort(info?.fetched_at)}`
    } else {
      if (present && kdaisEntry.value) {
        const bits = [
          kdaisEntry.value.dilution ? `稀釋 ${kdaisEntry.value.dilution}` : null,
          kdaisEntry.value.phi ? `採收期 ${kdaisEntry.value.phi}` : null,
        ].filter(Boolean)
        value = bits.length ? `推薦 · ${bits.join(' · ')}` : '推薦'
        sub = `${info?.document ?? '摘要表'}${
          kdaisEntry.value.pdf_page ? ` · PDF 第 ${kdaisEntry.value.pdf_page} 頁` : ''
        }`
      } else {
        value = '未列入摘要表'
        sub = info?.document ?? info?.version_label ?? '高改場無此作物摘要表'
      }
    }

    return { key, label: SOURCE_LABEL[key], present, value, sub, danger: key === 'fda' && props.row.banned }
  }),
)

const ISSUE_LABEL: Record<string, string> = {
  BANNED_INGREDIENT: '禁用農藥',
  MRL_MISSING: '查無容許量',
  MRL_MISMATCH: '容許量不一致',
  KDAIS_NOT_RECOMMENDED: '高改場未列',
  KDAIS_ONLY: '無核准登記',
  DILUTION_MISMATCH: '稀釋倍數不一致',
  PHI_MISMATCH: '採收期不一致',
  INGREDIENT_UNMATCHED: '成分未對應',
}

const badges = computed(() => {
  if (props.row.banned) {
    return props.row.issues.map(toBadge)
  }
  if (!props.row.issues.length) {
    return [{ text: '三源一致', tone: 'ok' as const }]
  }
  return props.row.issues.map(toBadge)
})

function toBadge(i: Issue) {
  return {
    text: ISSUE_LABEL[i.issue_type] ?? i.issue_type,
    tone: i.severity === 'critical' ? ('crit' as const) : i.severity === 'warning' ? ('warn' as const) : ('info' as const),
  }
}

function fmtShort(iso?: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()}`
}
</script>

<template>
  <article class="drug" :class="{ banned: row.banned }">
    <div class="head">
      <div style="flex: 1; min-width: 0">
        <div class="name">
          <span v-if="row.banned" class="tag crit solid">禁用</span>
          {{ fullName }}
        </div>
        <div v-if="subtitle" class="en">{{ subtitle }}</div>
        <!-- 配方層級的許可證：不挑廠商、不挑到期日，只說有幾張、幾家，要看細節就去農藥廠商查詢。 -->
        <router-link v-if="permitInfo" class="permit-line" :to="vendorLink">
          <Icon name="file" :size="13" />
          <span>
            此配方 {{ permitInfo.permits }} 張許可證 · {{ permitInfo.vendors }} 家廠商<template
              v-if="permitInfo.revoked"
            >
              · <b class="permit-revoked">{{ permitInfo.revoked }} 張已撤銷</b></template
            >
          </span>
          <b v-if="permitInfo.all_revoked" class="permit-revoked">此配方目前無有效登記</b>
          <span class="permit-go">農藥廠商查詢 →</span>
        </router-link>
      </div>
      <div class="badges">
        <span v-for="(b, i) in badges" :key="i" class="tag" :class="b.tone">
          <Icon v-if="b.tone === 'ok'" name="check" :size="12" />{{ b.text }}
        </span>
      </div>
    </div>

    <div class="facts">
      <div class="fact">
        <div class="l">稀釋倍數</div>
        <div class="n num">
          <template v-if="row.dilution_text">{{ row.dilution_text }}<small>倍</small></template>
          <template v-else>—</template>
        </div>
      </div>
      <div class="fact">
        <div class="l">每公頃用量</div>
        <div class="n num">{{ row.dose_per_hectare ?? '—' }}</div>
      </div>
      <div class="fact">
        <div class="l">安全採收期</div>
        <div class="n num">
          <template v-if="row.phi_days !== null">{{ row.phi_days }}<small>天</small></template>
          <!-- 抽不到就明說未載明，絕不填 0 -->
          <template v-else><span class="unset">未載明</span></template>
        </div>
      </div>
      <div class="fact">
        <div class="l">殘留容許量（{{ cropName }}）</div>
        <div class="n num">
          {{ mrlSummary.value }}<small v-if="mrlSummary.value !== '查無' && mrlSummary.value !== '—'">ppm</small>
          <div class="fnote">{{ mrlSummary.note }}</div>
        </div>
      </div>
      <div v-if="row.application_method" class="fact">
        <div class="l">施藥方法</div>
        <button
          type="button"
          class="n method method-preview"
          :aria-label="`查看「${fullName}」完整施藥方法`"
          @click="methodOpen = true"
        >
          <span>{{ methodPreview }}</span><small>查看完整方法</small>
        </button>
      </div>
    </div>

    <div class="srcs">
      <div class="src-row">
        <div
          v-for="b in boxes"
          :key="b.key"
          class="src"
          :class="[b.key, { absent: !b.present }]"
        >
          <span class="bar" />
          <div>
            <div class="k">{{ b.label }}</div>
            <div class="v" :style="b.danger ? 'color: var(--crit);' : undefined">{{ b.value }}</div>
            <div class="sub">{{ b.sub }}</div>
          </div>
        </div>
      </div>
    </div>

    <button class="toggle" @click="open = !open">
      <Icon :name="open ? 'up' : 'down'" :size="16" />
      {{ open ? '收合' : '看三源原文' }}
    </button>

    <div v-if="open" class="detail">
      <div class="col">
        <h4><span class="dot ppm" />植保系統</h4>
        <div class="ver">{{ row.sources?.ppm?.version_label }} · 抓取 {{ fmtShort(row.sources?.ppm?.fetched_at) }}</div>
        <p v-if="row.application_method">{{ row.application_method }}</p>
        <ol v-if="row.precautions.length">
          <li v-for="(p, i) in row.precautions" :key="i">{{ p }}</li>
        </ol>
        <p v-else class="muted">注意事項未載明</p>
        <p v-if="row.source_url" style="margin-top: 10px">
          <a :href="row.source_url" target="_blank" rel="noopener">
            開啟原始頁面 <Icon name="external" :size="13" />
          </a>
        </p>
      </div>

      <div class="col">
        <h4><span class="dot fda" />食藥署</h4>
        <div class="ver">{{ row.sources?.fda?.version_label }} · 抓取 {{ fmtShort(row.sources?.fda?.fetched_at) }}</div>
        <table class="mini">
          <thead>
            <tr><th>成分</th><th>容許量</th><th>適用類別</th></tr>
          </thead>
          <tbody>
            <tr v-for="m in row.mrls" :key="m.ingredient_name">
              <td>
                {{ m.ingredient_name }}
                <div v-if="m.ingredient_english" class="xs muted">{{ m.ingredient_english }}</div>
              </td>
              <td class="num">
                <template v-if="m.found">{{ m.limit_text }}</template>
                <span v-else class="tag warn">查無</span>
              </td>
              <td>
                <template v-if="m.found">
                  {{ m.matched_category }}
                  <!-- 用大類查到的要講清楚，不能讓人以為是這個作物本身的值 -->
                  <div v-if="m.match_basis?.includes('fda_category')" class="xs muted">以大類匹配</div>
                  <div v-if="m.group_name" class="xs muted">以「{{ m.group_name }}」群組計</div>
                </template>
                <span v-else class="muted">—</span>
              </td>
            </tr>
            <tr v-if="!row.mrls.length"><td colspan="3" class="muted">此藥劑未對應到任何成分</td></tr>
          </tbody>
        </table>
        <p v-if="row.ppm_mrl_text" class="xs muted" style="margin-top: 8px">
          植保系統頁面上記載：{{ row.ppm_mrl_text }}
        </p>
      </div>

      <div class="col">
        <h4><span class="dot kdais" />高改場</h4>
        <div class="ver">
          <template v-if="row.sources?.kdais?.version">
            版本 {{ row.sources.kdais.version }} · 抓取 {{ fmtShort(row.sources?.kdais?.fetched_at) }}
          </template>
          <template v-else>{{ row.sources?.kdais?.version_label }}</template>
        </div>
        <template v-if="row.kdais_entries.length">
          <div v-for="(k, i) in row.kdais_entries" :key="i" class="kd">
            <div style="font-weight: 500">{{ k.product_text }}</div>
            <dl class="kv">
              <dt>稀釋倍數</dt><dd>{{ k.dilution ?? '—' }}</dd>
              <dt>安全採收期</dt><dd>{{ k.phi ?? '—' }}</dd>
              <dt>安全容許量</dt><dd>{{ k.tolerance ?? '—' }}</dd>
            </dl>
            <p v-if="k.remark" class="small" style="margin-top: 6px">{{ k.remark }}</p>
            <p class="xs muted" style="margin-top: 6px">
              {{ k.document_title }}
              <a v-if="k.pdf_url" :href="k.pdf_url" target="_blank" rel="noopener">
                PDF 第 {{ k.pdf_page }} 頁 <Icon name="external" :size="12" />
              </a>
            </p>
          </div>
        </template>
        <p v-else class="muted">摘要表未列入這支藥。</p>
      </div>
    </div>

    <FarmModal
      v-if="methodOpen && row.application_method"
      title="完整施藥方法"
      @close="methodOpen = false"
    >
      <article class="method-dialog">
        <p class="method-product">{{ fullName }}</p>
        <p class="method-copy">{{ row.application_method }}</p>

        <section v-if="row.precautions.length" class="method-precautions">
          <h3>注意事項</h3>
          <ol>
            <li v-for="(precaution, index) in row.precautions" :key="index">{{ precaution }}</li>
          </ol>
        </section>

        <p class="method-source">
          植保系統 · {{ row.sources?.ppm?.version_label || '法定核准登記' }} ·
          抓取 {{ fmtShort(row.sources?.ppm?.fetched_at) }}
        </p>
        <a
          v-if="row.source_url"
          :href="row.source_url"
          target="_blank"
          rel="noopener"
        >
          開啟植保系統原始頁面 <Icon name="external" :size="13" />
        </a>
      </article>
    </FarmModal>
  </article>
</template>

<style scoped>
.drug .name { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.fact .n.method { font-size: 14px; font-weight: 500; line-height: 1.5; }
.fact .method-preview {
  display: flex;
  width: 100%;
  min-height: 50px;
  padding: 0;
  border: 0;
  background: none;
  color: var(--ink);
  cursor: pointer;
  flex-direction: column;
  align-items: flex-start;
  text-align: left;
  font-family: inherit;
}
.method-preview > span {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}
.method-preview small { margin-top: 5px; color: var(--brand); font-size: 12px; font-weight: 600; }
.method-preview:hover small, .method-preview:focus-visible small { text-decoration: underline; }
.method-preview:focus-visible { outline: 2px solid var(--brand); outline-offset: 4px; border-radius: 4px; }
.fact .n .unset { font-size: 14px; font-weight: 500; color: var(--muted); }
.fact .fnote { font-size: 11px; font-weight: 400; color: var(--muted); margin-top: 2px; line-height: 1.4; }
.toggle { width: 100%; border: none; font-family: inherit; cursor: pointer; }
.toggle:hover { background: #f2f5f1; }
.detail .col h4 .dot { width: 9px; height: 9px; }
.kd + .kd { margin-top: 12px; padding-top: 12px; border-top: 1px dashed var(--line); }
.mini .num { font-variant-numeric: tabular-nums; }
.method-dialog { display: grid; gap: 16px; }
.method-dialog p, .method-dialog h3, .method-dialog ol { margin: 0; }
.method-product { color: var(--brand); font-size: 13px; font-weight: 600; }
.method-copy { white-space: pre-wrap; line-height: 1.9; font-size: 15px; }
.method-precautions { padding: 14px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg); }
.method-precautions h3 { font-size: 14px; }
.method-precautions ol { padding-left: 20px; margin-top: 8px; line-height: 1.75; }
.method-source { color: var(--muted); font-size: 12px; }
.permit-line {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 4px;
  font-size: 12px;
  color: var(--muted);
}
.permit-line:hover { text-decoration: none; color: var(--ink-2); }
.permit-revoked { color: #a8342a; }
.permit-go { color: var(--brand); margin-left: auto; white-space: nowrap; }
</style>
