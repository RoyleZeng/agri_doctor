<script setup lang="ts">
import type { IconName } from './icons'
import Icon from './Icon.vue'

defineProps<{
  icon: IconName
  label: string
  value: string
  unit?: string
  state: 'ok' | 'warn'
  sub: string
  age: string
}>()
</script>

<template>
  <div class="tile" :class="state">
    <div class="k"><Icon :name="icon" :size="16" />{{ label }}</div>
    <div class="v">{{ value }}<small v-if="unit">{{ unit }}</small></div>
    <div class="s">
      <span>{{ sub }}</span><span>{{ age }}</span>
    </div>
  </div>
</template>

<style scoped>
.tile {
  padding: 12px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid var(--line);
  box-shadow: var(--shadow);
  min-width: 0;
}
.tile .k {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--muted);
}
.tile .v {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.2;
  margin-top: 2px;
  font-variant-numeric: tabular-nums;
}
.tile .v small {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  margin-left: 3px;
}
.tile .s {
  font-size: 11px;
  color: var(--muted);
  margin-top: 4px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
}
.tile.warn {
  border-color: #f2dfb3;
  background: #fffdf7;
}
.tile.warn .v {
  color: var(--warn);
}
</style>
