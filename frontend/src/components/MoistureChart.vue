<script setup lang="ts">
import { computed } from 'vue'
import type { SensorPoint } from '@/api/planning'

/** 近 30 天單一分區的土壤水分＋降雨＋生育期色帶。決策看的是趨勢與生育期，不是單一時點——所以固定畫 30 天，不是即時值。 */
const props = defineProps<{
  zoneCode: string
  series: SensorPoint[]
  rain: SensorPoint[]
  stages: { title: string; start_on: string; end_on: string }[]
  thresholdPct: number | null
  loading?: boolean
}>()

const WIDTH = 900
const HEIGHT = 170
const RAIN_HEIGHT = 34
const PLOT_TOP = 10
const PLOT_BOTTOM = HEIGHT - RAIN_HEIGHT - 24

const domain = computed(() => {
  const times = props.series.map((p) => new Date(p.ts).getTime())
  if (!times.length) return null
  return { start: Math.min(...times), end: Math.max(...times) }
})
const x = (ts: string) => {
  if (!domain.value) return 0
  const { start, end } = domain.value
  return end === start ? 0 : ((new Date(ts).getTime() - start) / (end - start)) * WIDTH
}
const valueDomain = computed(() => {
  const values = props.series.map((p) => p.value)
  const top = Math.max(props.thresholdPct ?? 0, ...values, 10) * 1.15
  return { min: 0, max: Math.ceil(top / 10) * 10 }
})
const y = (value: number) => {
  const { min, max } = valueDomain.value
  return PLOT_BOTTOM - ((value - min) / (max - min || 1)) * (PLOT_BOTTOM - PLOT_TOP)
}
const linePoints = computed(() => props.series.map((p) => `${x(p.ts).toFixed(1)},${y(p.value).toFixed(1)}`).join(' '))
const thresholdY = computed(() => (props.thresholdPct !== null ? y(props.thresholdPct) : null))
const latest = computed(() => props.series.at(-1) ?? null)
const rainMax = computed(() => Math.max(1, ...props.rain.map((p) => p.value)))
const ticks = computed(() => {
  if (!domain.value) return []
  const { start, end } = domain.value
  const days = Math.max(1, Math.round((end - start) / 86_400_000))
  const step = Math.max(1, Math.round(days / 5))
  const out: { x: number; label: string }[] = []
  for (let offset = 0; offset <= days; offset += step) {
    const ts = start + offset * 86_400_000
    out.push({ x: (offset / days) * WIDTH, label: new Date(ts).toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric' }) })
  }
  return out
})
const stageBands = computed(() =>
  props.stages
    .map((stage) => {
      const start = Math.max(x(`${stage.start_on}T00:00:00Z`), 0)
      const end = Math.min(x(`${stage.end_on}T23:59:59Z`), WIDTH)
      return { title: stage.title, x: start, width: Math.max(0, end - start) }
    })
    .filter((band) => band.width > 0),
)
</script>

<template>
  <section class="card chart">
    <div class="h">
      <b>時間變異 · 近 30 天土壤水分與降雨（{{ zoneCode }} 區）</b>
      <span>同一位置在不同時間需要不同管理 · 灰帶＝生育期切換</span>
    </div>
    <p v-if="loading" class="quiet-state">正在載入圖表⋯</p>
    <p v-else-if="!series.length" class="quiet-state">這段期間沒有讀數。</p>
    <svg v-else :viewBox="`0 0 ${WIDTH} ${HEIGHT}`" role="img" :aria-label="`${zoneCode} 區近 30 天土壤水分與降雨`">
      <rect v-for="band in stageBands" :key="band.title" :x="band.x" :y="PLOT_TOP" :width="band.width" :height="PLOT_BOTTOM - PLOT_TOP" fill="#f2f2ee" />
      <text v-for="band in stageBands" :key="`${band.title}-t`" :x="band.x + 6" :y="PLOT_TOP + 12" font-size="11" fill="#7b877f">{{ band.title }}</text>

      <line v-if="thresholdY !== null" :x1="0" :y1="thresholdY" :x2="WIDTH" :y2="thresholdY" stroke="#b8770b" stroke-width="1" stroke-dasharray="4 3" />
      <text v-if="thresholdY !== null" :x="WIDTH - 6" :y="thresholdY - 4" font-size="11" fill="#b8770b" text-anchor="end">閾值 {{ thresholdPct }}%</text>

      <polyline :points="linePoints" fill="none" stroke="#2f6fb5" stroke-width="2" />
      <circle v-if="latest" :cx="x(latest.ts)" :cy="y(latest.value)" r="3" fill="#2f6fb5" />
      <text v-if="latest" :x="x(latest.ts) - 6" :y="y(latest.value) - 8" font-size="11" fill="#2f6fb5" text-anchor="end">
        今天 {{ latest.value.toFixed(0) }}%
      </text>

      <g v-for="point in rain" :key="point.ts">
        <rect
          v-if="point.value > 0"
          :x="x(point.ts) - 1.5"
          :y="HEIGHT - (point.value / rainMax) * RAIN_HEIGHT - 20"
          width="3"
          :height="(point.value / rainMax) * RAIN_HEIGHT"
          fill="#b9cfe0"
        />
      </g>
      <g font-size="11" fill="#7b877f">
        <text v-for="tick in ticks" :key="tick.x" :x="tick.x" :y="HEIGHT - 4">{{ tick.label }}</text>
      </g>
    </svg>
    <p v-if="series.length" class="xs muted chart-foot">藍線＝土壤水分、藍柱＝降雨 mm。決策看的是趨勢與生育期，不是單一時點。</p>
  </section>
</template>

<style scoped>
.chart {
  padding: 14px 20px 8px;
}
.chart .h {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.chart .h b {
  font-size: 15px;
}
.chart .h span {
  font-size: 12px;
  color: var(--muted);
}
svg {
  width: 100%;
  height: auto;
  display: block;
}
.chart-foot {
  margin: 6px 0 4px;
}
</style>
