<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'

import { planning } from '@/api/planning'
import Icon from '@/components/Icon.vue'
import PageBar from '@/components/PageBar.vue'
import { redirectFor } from '@/composables/navigation'
import { toast } from '@/composables/toast'
import { role, session, updateMe } from '@/stores/session'

/**
 * 代建帳號的首次改密碼。
 *
 * 這一頁是關卡不是提醒：那組一次性密碼植物醫生也知道，沒換掉之前帳號等於兩個人的。
 * 換完之後連建立帳號的人都進不去——這句話要讓農民看到，他才知道這一步在保護誰。
 */
const router = useRouter()
const password = ref('')
const confirm = ref('')
const busy = ref(false)
const error = ref('')

const tooShort = computed(() => password.value.length > 0 && password.value.length < 10)
const mismatch = computed(() => confirm.value.length > 0 && password.value !== confirm.value)
const canSubmit = computed(() => password.value.length >= 10 && password.value === confirm.value)

async function submit() {
  if (!canSubmit.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    updateMe(await planning.changePassword(password.value))
    toast.ok('密碼已更新，這個帳號現在只有您進得去。')
    await router.replace(redirectFor(role.value, '/farm') ?? '/farm')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <PageBar title="設定您的密碼" :note="session.me?.name" />
  <div class="wrap pw">
    <section class="card pw-card">
      <p class="pw-why">
        <Icon name="info" :size="16" />
        <span>
          您現在用的是植物醫生給的<b>一次性密碼</b>，他也知道那組字。
          換成只有您知道的密碼之後，<b>連建立帳號的人都進不去這個帳號</b>。
        </span>
      </p>
      <form class="farm-form" @submit.prevent="submit">
        <label>
          新密碼
          <input v-model="password" type="password" required minlength="10" autocomplete="new-password" placeholder="至少 10 個字元" />
          <small v-if="tooShort" class="form-error">還差 {{ 10 - password.length }} 個字元。</small>
        </label>
        <label>
          再輸入一次
          <input v-model="confirm" type="password" required autocomplete="new-password" />
          <small v-if="mismatch" class="form-error">兩次輸入不一樣。</small>
        </label>
        <p v-if="error" class="form-error" role="alert">{{ error }}</p>
        <button class="btn primary" :disabled="busy || !canSubmit">{{ busy ? '儲存中⋯' : '設定密碼並開始使用' }}</button>
      </form>
    </section>
  </div>
</template>

<style scoped>
.pw { max-width: 520px; }
.pw-card { padding: 20px 22px; margin-top: 20px; }
.pw-why {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ink-2);
  background: var(--bg);
  border-radius: 10px;
  padding: 12px 14px;
  margin-bottom: 16px;
}
</style>
