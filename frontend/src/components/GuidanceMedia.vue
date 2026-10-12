<script setup lang="ts">
import { ref } from 'vue'
import type { GuidanceAttachment } from '@/api/planning'
defineProps<{ items: GuidanceAttachment[] }>()
const failed = ref<Record<string, boolean>>({})
</script>
<template>
  <section v-if="items.length" class="guidance-media" aria-label="教學圖片與影片">
    <figure v-for="(m, i) in items" :key="`${i}-${m.url}`">
      <a v-if="m.kind === 'image' && !failed[m.url]" :href="m.url" target="_blank" rel="noopener noreferrer"><img :src="m.url" :alt="m.caption" loading="lazy" referrerpolicy="no-referrer" @error="failed[m.url] = true" /></a>
      <video v-else-if="m.kind === 'video' && m.url.startsWith('/guidance-demo/')" :src="m.url" controls preload="metadata" :aria-label="m.caption" />
      <a v-else class="media-link" :href="m.url" target="_blank" rel="noopener noreferrer">{{ m.kind === 'video' ? '▶ 開啟教學影片' : '圖片暫時無法載入，開啟原圖' }} ↗</a>
      <figcaption>{{ m.caption }}<a :href="m.source_url" target="_blank" rel="noopener noreferrer">{{ m.url.startsWith('/guidance-demo/') ? '農藝參考來源' : '查看來源' }} ↗</a><a v-if="m.url.startsWith('/guidance-demo/')" href="/guidance-demo/credits.html" target="_blank" rel="noopener noreferrer">本系統自製示意素材說明 ↗</a></figcaption>
    </figure>
  </section>
</template>
<style scoped>
.guidance-media { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
figure { margin: 0; padding: 12px; background: var(--bg); border: 1px solid var(--line); border-radius: 12px; }
video { width: 100%; border-radius: 8px; }
img { display: block; max-height: 300px; width: 100%; object-fit: contain; border-radius: 8px; }
figcaption { font-size: 13px; line-height: 1.7; margin-top: 10px; }
figcaption a { display: block; }
.media-link { display: block; padding: 24px 10px; background: var(--brand-soft); border-radius: 8px; }
</style>
