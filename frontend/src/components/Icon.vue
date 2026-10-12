<script setup lang="ts">
import { computed } from 'vue'
import { ICON_PATHS, type IconName } from './icons'

const props = withDefaults(
  defineProps<{ name: IconName; size?: number | string }>(),
  { size: 18 },
)

const inner = computed(() => ICON_PATHS[props.name] ?? '')
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.8"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    class="ico"
    v-html="inner"
  />
</template>

<style scoped>
.ico { flex: none; display: inline-block; vertical-align: -0.15em; }
</style>
