<script setup lang="ts">
import { computed, ref } from 'vue'

import { planning, type CreatedFarmer, type Farmer } from '@/api/planning'
import FarmModal from '@/components/FarmModal.vue'
import Icon from '@/components/Icon.vue'
import { toast } from '@/composables/toast'

/**
 * 新增農民：替農民建立帳號／連結既有帳號兩種模式。
 *
 * 為什麼要有「替農民建立」：第四輪回饋是農民不太會用，連「自己註冊再產生連結碼」這一步
 * 都卡住。植物醫生代建之後農民只要拿一張紙卡登入就好。
 *
 * **代建不等於永久授權**：帳號首次登入強制改密碼（改完連建立者都進不去），
 * 農民本人隨時可以解除連結。這兩句話直接寫在畫面上，不是只寫在文件裡。
 */
const props = defineProps<{ links: Farmer[] }>()
const emit = defineEmits<{ close: []; created: [farmer: CreatedFarmer]; linked: [farmer: Farmer] }>()

const mode = ref<'create' | 'code'>('create')
const busy = ref(false)
const error = ref('')

const form = ref({ name: '', farm_name: '', location: '', username: '' })
const code = ref('')
/** 建立成功後停在這一頁顯示帳號與一次性密碼——關掉就再也看不到明文。 */
const created = ref<CreatedFarmer | null>(null)

const canSubmit = computed(() => form.value.name.trim() && form.value.location.trim().length >= 2)

async function submit() {
  if (!canSubmit.value || busy.value) return
  busy.value = true
  error.value = ''
  try {
    created.value = await planning.createFarmer({
      name: form.value.name.trim(),
      farm_name: form.value.farm_name.trim() || undefined,
      location: form.value.location.trim(),
      username: form.value.username.trim() || undefined,
    })
    emit('created', created.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function linkByCode() {
  if (!code.value.trim() || busy.value) return
  busy.value = true
  error.value = ''
  try {
    const farmer = await planning.link(code.value.trim())
    toast.ok(`已連結 ${farmer.name}`)
    emit('linked', farmer)
    emit('close')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}

async function printCard() {
  if (!created.value) return
  busy.value = true
  try {
    const blob = await planning.welcomeCard(created.value.id, created.value.one_time_password)
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `${created.value.name}-農場帳號.pdf`
    anchor.click()
    setTimeout(() => URL.revokeObjectURL(url), 10000)
  } catch (e) {
    toast.error((e as Error).message)
  } finally {
    busy.value = false
  }
}

async function copyCard() {
  if (!created.value) return
  const text = [
    `${created.value.name} 的農場帳號`,
    `帳號：${created.value.username}`,
    `一次性密碼：${created.value.one_time_password}`,
    '第一次登入後請自己換一組密碼。',
  ].join('\n')
  try {
    await navigator.clipboard.writeText(text)
    toast.ok('已複製，可貼到 LINE 傳給農民')
  } catch {
    toast.error('這個瀏覽器不允許複製，請用「列印小卡」或手動抄寫。')
  }
}
</script>

<template>
  <FarmModal :title="created ? '帳號已建立' : '新增農民'" wide @close="!busy && emit('close')">
    <div class="cf">
      <!-- 建立成功：明文只有這一次看得到，所以這一屏什麼都不做，只負責交付那兩行字。 -->
      <template v-if="created">
        <p class="cf-done"><Icon name="check" :size="16" />{{ created.name }} 的帳號已建立並連結到您。</p>
        <dl class="cf-creds">
          <dt>帳號</dt><dd>{{ created.username }}</dd>
          <dt>一次性密碼</dt><dd class="cf-otp">{{ created.one_time_password }}</dd>
        </dl>
        <p class="cf-warn">
          <Icon name="warn" :size="15" />
          <span>
            這組密碼 <b>72 小時內有效</b>，而且只會顯示這一次——關掉這個視窗就查不到了。
            農民第一次登入後必須自己換一組；換過之後，<b>連您也進不去他的帳號</b>。
          </span>
        </p>
        <div class="form-actions">
          <button class="btn" type="button" :disabled="busy" @click="copyCard">複製帳號密碼</button>
          <button class="btn" type="button" :disabled="busy" @click="printCard">列印小卡（A6 PDF）</button>
          <button class="btn primary" type="button" @click="emit('close')">我已經記下來了</button>
        </div>
      </template>

      <template v-else>
        <div class="cf-tabs">
          <button type="button" :class="{ on: mode === 'create' }" @click="mode = 'create'">替農民建立帳號</button>
          <button type="button" :class="{ on: mode === 'code' }" @click="mode = 'code'">連結既有帳號</button>
        </div>

        <form v-if="mode === 'create'" class="farm-form" @submit.prevent="submit">
          <label>農民姓名<input v-model="form.name" required maxlength="80" placeholder="例如：陳明" /></label>
          <label>農場名稱<input v-model="form.farm_name" maxlength="120" placeholder="留白就是「我的農場」" /></label>
          <label>
            所在鄉鎮<span class="req">必填</span>
            <input v-model="form.location" required maxlength="120" placeholder="例如：高雄市大社區" />
            <small class="form-hint">附近病蟲害與天氣都靠這一欄，事後才補等於這兩個功能先壞一段時間。</small>
          </label>
          <label>
            帳號<span class="opt">選填</span>
            <input v-model="form.username" maxlength="120" placeholder="留白就自動產生，之後可改" />
          </label>
          <p class="cf-note">
            建立後會自動連結到您，農民<b>隨時可以自己解除</b>；解除後您就看不到他的田區、日誌與規劃。
          </p>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <div class="form-actions">
            <button class="btn" type="button" :disabled="busy" @click="emit('close')">取消</button>
            <button class="btn primary" :disabled="busy || !canSubmit">
              {{ busy ? '建立中⋯' : '建立帳號並產生密碼' }}
            </button>
          </div>
        </form>

        <form v-else class="farm-form" @submit.prevent="linkByCode">
          <label>
            農民提供的連結碼
            <input v-model="code" required minlength="12" maxlength="100" placeholder="請農民從「與植物醫生協作」產生" />
          </label>
          <p class="cf-note">已經有帳號的農民用這個方式連結；連結碼 24 小時內有效。</p>
          <p v-if="error" class="form-error" role="alert">{{ error }}</p>
          <div class="form-actions">
            <button class="btn" type="button" :disabled="busy" @click="emit('close')">取消</button>
            <button class="btn primary" :disabled="busy || !code.trim()">{{ busy ? '連結中⋯' : '連結' }}</button>
          </div>
        </form>

        <p class="cf-links">目前已連結 {{ props.links.length }} 位農友。</p>
      </template>
    </div>
  </FarmModal>
</template>

<style scoped>
.cf { display: flex; flex-direction: column; gap: 14px; }
.cf-tabs { display: inline-flex; border: 1px solid var(--line-strong); border-radius: var(--r-ctl); overflow: hidden; align-self: flex-start; }
.cf-tabs button { height: 34px; padding: 0 14px; border: 0; background: #fff; color: var(--ink-2); font-size: 13px; cursor: pointer; }
.cf-tabs button + button { border-left: 1px solid var(--line); }
.cf-tabs button.on { background: var(--brand); color: #fff; font-weight: 600; }
.req { font-size: 11px; color: #a8342a; margin-left: 6px; }
.opt { font-size: 11px; color: var(--muted); margin-left: 6px; }
.cf-note { font-size: 12px; color: var(--ink-2); line-height: 1.7; }
.cf-links { font-size: 12px; color: var(--muted); }

.cf-done { display: flex; align-items: center; gap: 8px; font-size: 14px; color: var(--brand); }
.cf-creds {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr);
  gap: 8px 12px;
  background: var(--bg);
  border-radius: 12px;
  padding: 14px 16px;
  align-items: center;
}
.cf-creds dt { font-size: 12px; color: var(--muted); }
.cf-creds dd { margin: 0; font-size: 18px; font-weight: 700; letter-spacing: 0.04em; }
.cf-otp { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.cf-warn {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.7;
  color: #7a2119;
  background: #fdecea;
  border: 1px solid #f3cfc9;
  border-radius: 10px;
  padding: 12px 14px;
}
</style>
