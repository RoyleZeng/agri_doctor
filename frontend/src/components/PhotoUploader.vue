<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { planning, type LogPhoto } from '@/api/planning'
import Icon from './Icon.vue'

/**
 * 照片佇列。紀錄要先存好才有 log_id 可以傳照片，所以新增時先把檔案留在佇列裡，存檔後才 uploadAll；
 * 已存在的紀錄（補照片）給 logId，選了就傳。失敗的照片留在佇列可重試——田邊網路常斷，照片不能因此消失。
 */
const props = defineProps<{ logId?: string; compact?: boolean }>()
const emit = defineEmits<{ uploaded: [photo: LogPhoto] }>()

interface Item {
  key: number
  file: File
  preview: string
  status: 'pending' | 'uploading' | 'failed'
  error: string
  broken: boolean
}

const items = ref<Item[]>([])
const running = ref(false)
let seq = 0
const failed = computed(() => items.value.filter((item) => item.status === 'failed').length)
const waiting = computed(() => items.value.filter((item) => item.status !== 'uploading').length)

function choose(event: Event) {
  const input = event.target as HTMLInputElement
  for (const file of Array.from(input.files ?? [])) {
    items.value.push({ key: ++seq, file, preview: URL.createObjectURL(file), status: 'pending', error: '', broken: false })
  }
  input.value = ''
  if (props.logId) void uploadAll(props.logId)
}

function remove(item: Item) {
  URL.revokeObjectURL(item.preview)
  items.value = items.value.filter((entry) => entry !== item)
}

async function uploadAll(logId: string): Promise<{ uploaded: LogPhoto[]; failed: number }> {
  const uploaded: LogPhoto[] = []
  if (running.value) return { uploaded, failed: failed.value }
  running.value = true
  try {
    // 依序上傳：每張都要在伺服器解碼、轉檔，同時丟十張只會一起逾時。
    for (const item of items.value.filter((entry) => entry.status !== 'uploading')) {
      item.status = 'uploading'
      item.error = ''
      try {
        const photo = await planning.uploadPhoto(logId, item.file)
        uploaded.push(photo)
        remove(item)
        emit('uploaded', photo)
      } catch (e) {
        item.status = 'failed'
        item.error = (e as Error).message
      }
    }
  } finally {
    running.value = false
  }
  return { uploaded, failed: failed.value }
}

defineExpose({ uploadAll, waiting, failed })
onBeforeUnmount(() => items.value.forEach((item) => URL.revokeObjectURL(item.preview)))
</script>

<template>
  <div class="uploader" :class="{ compact }">
    <div class="up-row">
      <label class="up-btn" :class="{ small: compact }">
        <Icon name="camera" :size="compact ? 15 : 20" /><span>拍照</span>
        <input type="file" accept="image/*" capture="environment" @change="choose" />
      </label>
      <label v-if="!compact" class="up-btn">
        <Icon name="image" :size="20" /><span>相簿</span>
        <input type="file" accept="image/jpeg,image/png,image/heic,image/heif,.heic,.heif" multiple @change="choose" />
      </label>
      <div v-for="item in items" :key="item.key" class="up-item" :class="item.status" :title="item.error || item.file.name">
        <img v-if="!item.broken" :src="item.preview" alt="" @error="item.broken = true" />
        <span v-else class="up-name">{{ item.file.name }}</span>
        <span v-if="item.status === 'uploading'" class="up-state">上傳中</span>
        <span v-else-if="item.status === 'failed'" class="up-state">失敗</span>
        <button
          v-if="item.status !== 'uploading'"
          type="button"
          class="up-remove"
          :aria-label="`移除 ${item.file.name}`"
          @click="remove(item)"
        >
          <Icon name="close" :size="12" />
        </button>
      </div>
    </div>
    <p v-if="failed && logId" class="up-error" role="alert">
      {{ failed }} 張上傳失敗：{{ items.find((item) => item.status === 'failed')?.error }}
      <button type="button" class="text-btn" :disabled="running" @click="uploadAll(logId)">重試</button>
    </p>
  </div>
</template>

<style scoped>
.uploader { display: flex; flex-direction: column; gap: 6px; }
.up-row { display: flex; flex-wrap: wrap; gap: 8px; }
.up-btn { position: relative; width: 68px; height: 68px; display: flex !important; flex-direction: column; align-items: center; justify-content: center; gap: 3px; border: 1px dashed var(--line-strong); border-radius: 10px; background: #fff; color: var(--ink-2); font-size: 12px !important; cursor: pointer; }
.up-btn:hover { border-color: var(--brand); color: var(--brand); }
.up-btn.small { width: auto; height: 30px; flex-direction: row; padding: 0 10px; gap: 5px; border-radius: 8px; }
.up-btn input { position: absolute; inset: 0; opacity: 0; cursor: pointer; width: 100%; }
.up-btn:focus-within { outline: 3px solid #85a28f; outline-offset: 2px; }
.up-item { position: relative; width: 68px; height: 68px; border-radius: 10px; background: #eef2ec; }
.compact .up-item { width: 44px; height: 44px; }
.up-item img { width: 100%; height: 100%; object-fit: cover; border-radius: 10px; }
.up-name { display: block; padding: 6px; font-size: 10px; overflow: hidden; word-break: break-all; height: 100%; color: var(--muted); }
.up-state { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; border-radius: 10px; background: rgba(27, 38, 32, .5); color: #fff; font-size: 11px; font-weight: 600; }
.up-item.failed .up-state { background: rgba(192, 57, 43, .7); }
.up-remove { position: absolute; top: -6px; right: -6px; width: 20px; height: 20px; padding: 0; display: flex; align-items: center; justify-content: center; border: 0; border-radius: 50%; background: var(--ink); color: #fff; }
.up-error { margin: 0; font-size: 12px; color: var(--crit); }
</style>
