<script setup lang="ts">
/** 工作區骨架：跟實際版面同高，切農民時撐住版面不讓畫面塌掉再彈回來。 */
</script>

<template>
  <div class="wsk" aria-hidden="true">
    <div class="wsk-row">
      <span class="sk" style="width: 220px; height: 26px" />
      <span class="sk" style="width: 120px; height: 34px; margin-left: auto" />
    </div>
    <div class="wsk-cards">
      <span v-for="i in 3" :key="i" class="sk card-sk" />
    </div>
    <span class="sk chart-sk" />
    <div class="wsk-cards">
      <span v-for="i in 3" :key="i" class="sk card-sk short" />
    </div>
  </div>
</template>

<style scoped>
.wsk { display: flex; flex-direction: column; gap: 16px; }
.wsk-row { display: flex; align-items: center; gap: 12px; }
.wsk-cards { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.card-sk { height: 96px; border-radius: var(--r-card); }
.card-sk.short { height: 150px; }
.chart-sk { height: 320px; border-radius: var(--r-card); }
.sk {
  display: block;
  background: linear-gradient(90deg, #eef1ec 25%, #f6f8f4 50%, #eef1ec 75%);
  background-size: 200% 100%;
  animation: wsk 1.2s ease-in-out infinite;
  border-radius: 6px;
}
@keyframes wsk { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
@media (prefers-reduced-motion: reduce) { .sk { animation: none; } }
@media (max-width: 900px) { .wsk-cards { grid-template-columns: 1fr; } }
</style>
