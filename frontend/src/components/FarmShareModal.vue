<script setup lang="ts">
import { computed, ref } from 'vue'

import { planning, type Farmer } from '@/api/planning'
import FarmModal from '@/components/FarmModal.vue'
import { toast } from '@/composables/toast'
import { refreshLinks, session } from '@/stores/session'

/**
 * 連結彈窗：同一個對話框兩種身分——植物醫生貼上連結碼，農民產生連結碼。
 * 兩邊的頁面（農民管理、我的農場）都用它，所以獨立成元件，不在各頁各寫一份。
 */
const props = defineProps<{ links: Farmer[] }>()
const emit = defineEmits<{ close: []; linked: []; unlink: [farmer: Farmer] }>()

const isDoctor = computed(() => session.me?.role === 'doctor')
const links = computed(() => props.links)

const busy = ref(false)
const authError = ref('')
const linkCode = ref('')
const invite = ref<{ code: string; expires_at: number } | null>(null)

async function createInvite() {
  busy.value = true
  try {
    invite.value = await planning.invite()
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function copyCode() {
  try {
    await navigator.clipboard.writeText(invite.value?.code || '')
    toast.ok('連結碼已複製，請自行交給您的植物醫生')
  } catch {
    // 非 HTTPS 或使用者拒絕剪貼簿權限時會走到這裡；連結碼本身看得到，請他自己選取即可。
    toast.info('請選取連結碼後手動複製')
  }
}

async function linkFarmer() {
  busy.value = true
  authError.value = ''
  try {
    await planning.link(linkCode.value.trim())
    linkCode.value = ''
    await refreshLinks()
    emit('linked')
    emit('close')
    toast.ok('已連結農民的農場')
  } catch (e) {
    authError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <FarmModal
    :title="isDoctor ? '連結農民的農場' : '與植物醫生協作'"
    @close="!busy && emit('close')"
      ><form
        v-if="isDoctor"
        class="farm-form"
        @submit.prevent="linkFarmer"
      >
        <p class="form-hint">
          農民提供連結碼後，您可查看作物、調整事項及留下共同紀錄。
        </p>
        <label
          >農民提供的連結碼<input
            v-model="linkCode"
            required
            minlength="12"
            maxlength="100"
            autocomplete="off"
            spellcheck="false"
            placeholder="貼上連結碼"
        /></label>
        <p v-if="authError" class="form-error" role="alert">{{ authError }}</p>
        <button class="btn primary" :disabled="busy">
          {{ busy ? '連結中…' : '連結農場' }}
        </button>
      </form>
      <div v-else class="farm-form">
        <p>
          將連結碼交給您信任的植物醫生，對方即可查看您的作物規劃、調整管理事項與留下紀錄。
        </p>
        <button class="btn primary" :disabled="busy" @click="createInvite">
          {{ invite ? '重新產生連結碼' : '產生一次性連結碼' }}
        </button>
        <div v-if="invite" class="invite-code">
          <code>{{ invite.code }}</code
          ><button class="btn sm" @click="copyCode">複製</button>
          <p>24小時內有效，僅可使用一次。重新產生會使舊碼失效。</p>
        </div>
        <h3>已連結的植物醫生</h3>
        <div v-for="doctor in links" :key="doctor.id" class="linked-doctor">
          <strong>{{ doctor.name }}</strong
          ><button
            class="text-btn"
            @click="emit('unlink', doctor)"
          >
            解除連結
          </button>
        </div>
        <p v-if="!links.length" class="form-hint">目前沒有已連結的植物醫生。</p>
      </div></FarmModal
    >
</template>
