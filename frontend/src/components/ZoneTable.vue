<script setup lang="ts">
import { computed } from 'vue'
import type { PrecisionZone } from '@/api/planning'
import { colorForValue } from '@/composables/precisionState'

const props = defineProps<{
  zones: PrecisionZone[]
  ndvi: Record<string, number> | null
  moisture: Record<string, number> | null
  thresholdPct: number | null
}>()

const rows = computed(() =>
  props.zones.map((zone) => ({
    zone,
    ndvi: props.ndvi?.[zone.code] ?? null,
    moisture: props.moisture?.[zone.code] ?? null,
  })),
)
const lowest = computed(() => {
  const known = rows.value.filter((row) => row.ndvi !== null)
  return known.length ? known.reduce((min, row) => (row.ndvi! < min.ndvi! ? row : min)).zone.code : null
})
</script>

<template>
  <table class="zt">
    <thead>
      <tr>
        <th>分區</th>
        <th>NDVI</th>
        <th>水分</th>
        <th>面積</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="row in rows" :key="row.zone.code">
        <td><span class="zc" :style="{ background: row.ndvi === null ? '#e2e7e1' : colorForValue('ndvi', row.ndvi) }" />{{ row.zone.code }} {{ row.zone.label }}</td>
        <td class="num">{{ row.ndvi === null ? '—' : row.ndvi.toFixed(2) }}</td>
        <td class="num" :class="{ warn: thresholdPct !== null && row.moisture !== null && row.moisture < thresholdPct }">
          {{ row.moisture === null ? '—' : `${row.moisture.toFixed(0)}%` }}
        </td>
        <td class="num">{{ row.zone.area_ha === null ? '未填' : `${row.zone.area_ha.toFixed(2)} ha` }}</td>
      </tr>
    </tbody>
  </table>
  <p v-if="lowest" class="zt-note">{{ lowest }} 區生長勢相對偏低，可留意是否需要優先處理。</p>
</template>

<style scoped>
.zt {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.zt th {
  text-align: left;
  font-size: 11px;
  color: var(--muted);
  font-weight: 600;
  padding: 4px 6px;
  border-bottom: 1px solid var(--line);
}
.zt td {
  padding: 6px 6px;
  border-bottom: 1px solid var(--line);
  vertical-align: middle;
}
.zt tr:last-child td {
  border-bottom: none;
}
.zt .zc {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: 3px;
  margin-right: 6px;
  vertical-align: middle;
}
.num {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.num.warn {
  color: var(--warn);
  font-weight: 600;
}
.zt-note {
  margin: 10px 0 0;
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
</style>
