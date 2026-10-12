<script setup lang="ts">
import { computed } from 'vue'
import type { FieldLayer, LayerKind, PrecisionStation, PrecisionZone } from '@/api/planning'
import { PALETTES, colorForValue } from '@/composables/precisionState'

const props = defineProps<{
  gridCols: number
  gridRows: number
  zones: PrecisionZone[]
  stations: PrecisionStation[]
  layer: FieldLayer | null
  kind: LayerKind
  /** 邊界匯入後，中心點落在邊界外的格子；沒有邊界（示範格子）時是空陣列，格子沒有內外之分。 */
  outsideCells?: [number, number][]
}>()
const emit = defineEmits<{ 'update:kind': [LayerKind] }>()

const LAYERS: { key: LayerKind; label: string }[] = [
  { key: 'ndvi', label: 'NDVI' },
  { key: 'soil_moisture', label: '土壤水分' },
  { key: 'ph', label: '土壤 pH' },
  { key: 'yield_prev', label: '上季產量' },
]

const CELL = 40
const width = computed(() => props.gridCols * CELL)
const height = computed(() => props.gridRows * CELL)

const zoneOf = computed(() => {
  const map = new Map<string, PrecisionZone>()
  for (const zone of props.zones) for (const [col, row] of zone.cells) map.set(`${col},${row}`, zone)
  return map
})
const outsideSet = computed(() => new Set((props.outsideCells ?? []).map(([col, row]) => `${col},${row}`)))
const cells = computed(() =>
  Array.from({ length: props.gridRows }, (_, row) =>
    Array.from({ length: props.gridCols }, (_, col) => {
      const key = `${col},${row}`
      const zone = zoneOf.value.get(key)
      const outside = outsideSet.value.has(key)
      const value = outside ? null : (props.layer?.cell_values[key] ?? null)
      return { col, row, zone, value, outside, x: col * CELL, y: row * CELL }
    }),
  ).flat(),
)
// 分區字母放在該分區格子的中心。
const zoneLabels = computed(() =>
  props.zones.map((zone) => {
    const cols = zone.cells.map((c) => c[0])
    const rows = zone.cells.map((c) => c[1])
    return { code: zone.code, x: ((Math.min(...cols) + Math.max(...cols) + 1) / 2) * CELL, y: (Math.min(...rows) + 0.5) * CELL }
  }),
)
const points = computed(() =>
  props.stations
    .filter((s) => s.grid_col !== null && s.grid_row !== null)
    .map((s) => ({ id: s.id, x: (s.grid_col! + 0.5) * CELL, y: (s.grid_row! + 0.5) * CELL, code: s.code })),
)
const captured = computed(() =>
  props.layer ? new Date(props.layer.captured_on).toLocaleDateString('zh-TW', { month: 'numeric', day: 'numeric' }) : null,
)
</script>

<template>
  <section class="card">
    <div class="card-h">
      <div>
        <span class="title">田區分區圖</span>
        <div class="xs muted">同一田區不是均一的：依變異分區，每區各自決定投入量</div>
      </div>
      <div class="lyr" role="radiogroup" aria-label="圖層切換">
        <button
          v-for="option in LAYERS"
          :key="option.key"
          type="button"
          class="chip"
          :class="{ on: kind === option.key }"
          :aria-pressed="kind === option.key"
          @click="emit('update:kind', option.key)"
        >
          {{ option.label }}{{ kind === option.key && captured ? ` ${captured}` : '' }}
        </button>
      </div>
    </div>
    <div class="mapwrap">
      <div>
        <svg :viewBox="`0 0 ${width} ${height}`" :width="width" :height="height" role="img" aria-label="田區分區色塊圖">
          <g font-family="inherit" font-size="10">
            <rect
              v-for="cell in cells"
              :key="`${cell.col},${cell.row}`"
              :x="cell.x + 1"
              :y="cell.y + 1"
              :width="CELL - 2"
              :height="CELL - 2"
              rx="3"
              :fill="cell.outside ? '#f4f4f0' : colorForValue(kind, cell.value)"
              :opacity="cell.outside ? 0.5 : 1"
            />
          </g>
          <g font-weight="700" font-size="16" fill="#1b2620">
            <text v-for="label in zoneLabels" :key="label.code" :x="label.x" :y="label.y" text-anchor="middle" dominant-baseline="middle">
              {{ label.code }}
            </text>
          </g>
          <g fill="#fff" stroke="#1b2620" stroke-width="1.5">
            <circle v-for="point in points" :key="point.id" :cx="point.x" :cy="point.y" r="4">
              <title>{{ point.code }}</title>
            </circle>
          </g>
        </svg>
        <div class="legend">
          {{ LAYERS.find((l) => l.key === kind)?.label }}
          <i v-for="(stop, index) in PALETTES[kind].stops" :key="index" :style="{ background: stop }" />
          <span v-for="value in PALETTES[kind].legendValues" :key="value" class="xs muted">{{ value }}{{ PALETTES[kind].unit }}</span>
          <span class="legend-note">{{ layer ? layer.source_text : '尚無資料' }}</span>
        </div>
        <p v-if="!layer" class="quiet-state map-empty">這個圖層目前沒有資料。</p>
      </div>
      <div><slot name="table" /></div>
    </div>
  </section>
</template>

<style scoped>
.lyr {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.lyr .chip {
  height: 30px;
  font-size: 13px;
  padding: 0 12px;
}
.mapwrap {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 16px;
  padding: 16px 20px;
}
svg {
  max-width: 100%;
  height: auto;
}
.legend {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
  margin-top: 8px;
  flex-wrap: wrap;
}
.legend i {
  display: inline-block;
  width: 22px;
  height: 10px;
  border-radius: 2px;
}
.legend-note {
  margin-left: auto;
}
.map-empty {
  margin-top: 8px;
}
@media (max-width: 760px) {
  .mapwrap {
    grid-template-columns: 1fr;
  }
}
</style>
