<script setup lang="ts">
import { ref } from 'vue'
import { planning, type Farmer } from '@/api/planning'
import FarmModal from './FarmModal.vue'
import { updateMe } from '@/stores/session'

const props = defineProps<{ farmer: Farmer }>()
const emit = defineEmits<{ close: []; saved: [farmer: Farmer] }>()
const location = ref(props.farmer.location)
const busy = ref(false)
const error = ref('')
async function save() {
  busy.value = true
  error.value = ''
  try {
    const farmer = await planning.saveLocation(location.value.trim())
    updateMe(farmer)
    emit('saved', farmer)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <FarmModal title="農場所在地" @close="!busy && emit('close')">
    <form class="farm-form" @submit.prevent="save">
      <p class="form-hint">填寫縣市與鄉鎮市區，查看所在鄉鎮的病蟲害彙整；不需要門牌或座標。</p>
      <label>縣市與鄉鎮市區<input v-model="location" required maxlength="120" placeholder="例如：高雄市大社區" autocomplete="off" /></label>
      <p class="form-hint">目前區域地圖僅提供高雄市。其他縣市仍可儲存所在地，地圖會顯示「尚未提供」。</p>
      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <div class="form-actions">
        <button type="button" class="btn" :disabled="busy" @click="emit('close')">取消</button>
        <button class="btn primary" :disabled="busy || !location.trim()">{{ busy ? '儲存中…' : '儲存所在地' }}</button>
      </div>
    </form>
  </FarmModal>
</template>
