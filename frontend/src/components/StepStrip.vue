<script setup lang="ts">
/** Observe → Analyze → Decide → Act，對照專家簡報的四步驟；顏色固定，內容由外層算好傳進來。 */
defineProps<{ observe: string; analyze: string; decide: string; act: string }>()
const steps = [
  { n: 1, color: '#2f8f5b', key: 'observe', title: 'Observe', sub: '感測' },
  { n: 2, color: '#2f6fb5', key: 'analyze', title: 'Analyze', sub: '分析' },
  { n: 3, color: '#b8770b', key: 'decide', title: 'Decide', sub: '決策' },
  { n: 4, color: '#5b3d8a', key: 'act', title: 'Act', sub: '執行' },
] as const
</script>

<template>
  <div class="steps">
    <div v-for="step in steps" :key="step.key" class="step">
      <span class="n" :style="{ background: step.color }">{{ step.n }}</span>
      <div>
        <div class="k">{{ step.title }}<small>{{ step.sub }}</small></div>
        <div class="d">{{ { observe, analyze, decide, act }[step.key] }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.steps {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
  margin-bottom: 16px;
}
.step {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 12px 14px;
  border-radius: 12px;
  background: #fff;
  border: 1px solid var(--line);
}
.step .n {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
  color: #fff;
  flex: none;
}
.step .k {
  font-weight: 700;
  font-size: 14px;
}
.step .k small {
  font-weight: 500;
  color: var(--muted);
  margin-left: 6px;
  font-size: 12px;
}
.step .d {
  font-size: 12px;
  color: var(--ink-2);
  margin-top: 2px;
  line-height: 1.5;
}
@media (max-width: 900px) {
  .steps {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 480px) {
  .steps {
    grid-template-columns: 1fr;
  }
}
</style>
