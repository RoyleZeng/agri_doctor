<script setup lang="ts">
import { computed, ref } from 'vue'
import type { LogPhoto, TrashedPhoto } from '@/api/planning'
import Icon from './Icon.vue'

const props = withDefaults(defineProps<{
  photos: LogPhoto[]
  trashed?: TrashedPhoto[]
  canEdit?: boolean
  busy?: boolean
  max?: number
}>(), { trashed: () => [], canEdit: false, busy: false, max: 3 })
const emit = defineEmits<{ remove: [photo: LogPhoto]; restore: [photo: TrashedPhoto] }>()

const expanded = ref(false)
const showTrash = ref(false)
const visible = computed(() => (expanded.value ? props.photos : props.photos.slice(0, props.max)))
const hidden = computed(() => props.photos.length - visible.value.length)

const stamp = (value: string) =>
  new Intl.DateTimeFormat('zh-TW', { timeZone: 'Asia/Taipei', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value))

// 沒有 EXIF 拍攝時間的照片要看得出來：補助查核只認拍攝時間，上傳時間不能冒充。
const describe = (photo: LogPhoto) =>
  photo.taken_at_source === 'exif'
    ? `拍攝於 ${stamp(photo.taken_at)}${photo.lat !== null ? ' · 含拍攝位置' : ''}`
    : `無拍攝資訊，時間為上傳時間 ${stamp(photo.taken_at)}`
</script>

<template>
  <div class="strip">
    <div class="thumbs">
      <div v-for="(photo, index) in visible" :key="photo.id" class="thumb">
        <a :href="photo.url" target="_blank" rel="noopener" :title="describe(photo)">
          <img :src="photo.thumb_url" :alt="describe(photo)" loading="lazy" />
          <span v-if="index === visible.length - 1 && hidden > 0" class="more" @click.prevent="expanded = true">+{{ hidden }}</span>
        </a>
        <span v-if="photo.taken_at_source === 'upload'" class="no-exif" :title="describe(photo)">無拍攝資訊</span>
        <button
          v-if="canEdit"
          type="button"
          class="remove"
          :disabled="busy"
          :aria-label="`刪除照片（${describe(photo)}）`"
          @click="emit('remove', photo)"
        >
          <Icon name="close" :size="11" />
        </button>
      </div>
      <span v-if="!photos.length" class="none">無照片</span>
    </div>
    <button v-if="expanded && photos.length > max" type="button" class="text-btn" @click="expanded = false">收合照片</button>
    <template v-if="trashed.length && canEdit">
      <button type="button" class="text-btn trash-toggle" @click="showTrash = !showTrash">
        <Icon name="trash" :size="13" />最近刪除 {{ trashed.length }} 張
      </button>
      <div v-if="showTrash" class="trash">
        <div v-for="photo in trashed" :key="photo.id" class="trash-item">
          <img :src="photo.thumb_url" alt="已刪除的照片縮圖" loading="lazy" />
          <span>保留到 {{ stamp(photo.restorable_until).split(' ')[0] }}</span>
          <button type="button" class="text-btn" :disabled="busy" @click="emit('restore', photo)">
            <Icon name="undo" :size="13" />還原
          </button>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.strip { display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.thumbs { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.thumb { position: relative; width: 52px; height: 52px; }
.thumb a { display: block; width: 100%; height: 100%; border-radius: 8px; overflow: hidden; background: #e7ede4; }
.thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.more { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(27, 38, 32, .55); color: #fff; font-size: 13px; font-weight: 700; }
.no-exif { position: absolute; left: 2px; right: 2px; bottom: 2px; border-radius: 4px; background: rgba(255, 255, 255, .9); color: var(--warn); font-size: 9px; line-height: 14px; text-align: center; white-space: nowrap; overflow: hidden; pointer-events: none; }
.remove { position: absolute; top: -5px; right: -5px; width: 18px; height: 18px; padding: 0; display: none; align-items: center; justify-content: center; border: 0; border-radius: 50%; background: var(--ink); color: #fff; }
.thumb:hover .remove, .thumb:focus-within .remove { display: flex; }
@media (hover: none) { .remove { display: flex; } }
.none { font-size: 13px; color: var(--muted); }
.text-btn { font-size: 12px; padding: 2px 0; }
.trash { display: flex; flex-direction: column; gap: 6px; width: 100%; }
.trash-item { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--muted); }
.trash-item img { width: 36px; height: 36px; border-radius: 6px; object-fit: cover; opacity: .6; }
@media (max-width: 660px) {
  .strip, .thumbs { align-items: flex-start; justify-content: flex-start; }
}
</style>
