<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { planning } from '@/api/planning'
import Icon from '@/components/Icon.vue'
import { signedIn } from '@/stores/session'

/**
 * 登入／註冊。原本嵌在 `FarmPlanningView` 裡，所以每一頁都得先載入那 1,200 行才看得到登入表單；
 * 獨立成頁之後，路由守衛可以在未登入時直接帶來這裡，並用 `?next=` 記住原本要去的地方。
 */
const route = useRoute()
const router = useRouter()

const accountMode = ref<'login' | 'register'>('login')
const loginForm = ref({ username: '', password: '', name: '', role: 'farmer', farm_name: '', location: '' })
const authError = ref('')
const busy = ref(false)

/** 登入後要回哪裡：優先用守衛帶過來的 next，其次照角色落地。 */
const next = computed(() => (typeof route.query.next === 'string' ? route.query.next : ''))

function landingFor(role: string): string {
  if (role === 'admin') return '/admin/farm-content'
  if (role === 'doctor') return '/farmers'
  if (role === 'agency') return '/region'
  return '/farm'
}

function switchAuth(mode: 'login' | 'register') {
  accountMode.value = mode
  authError.value = ''
}

async function authenticate() {
  busy.value = true
  authError.value = ''
  try {
    const form = loginForm.value
    const result =
      accountMode.value === 'login'
        ? await planning.login({ username: form.username, password: form.password })
        : await planning.register({ ...form, farm_name: form.farm_name.trim() || '我的農場' })
    loginForm.value.password = ''
    await signedIn(result) // 側邊欄依 store 的角色決定項目，登入後要立刻更新
    // next 只接受站內路徑，避免被塞外部網址當成轉址跳板。
    const target = next.value.startsWith('/') && !next.value.startsWith('//') ? next.value : landingFor(result.role)
    await router.replace(target)
  } catch (e) {
    authError.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="wrap farm-page">
  <section class="farm-welcome">
    <div class="welcome-story">
      <span class="farm-eyebrow">從種下的第一天，照顧每一期收成</span>
      <h1>把整座農場的事，<br />安排得清清楚楚。</h1>
      <p>
        種了什麼、現在該做什麼、要留意哪些病蟲害。<br />讓您與植物醫生，一起看見作物的每個階段。
      </p>
      <div class="welcome-steps">
        <div>
          <span>01</span><strong>選作物、填日期</strong
          ><small>依田區建立每一期種植規劃</small>
        </div>
        <div>
          <span>02</span><strong>看時程、做紀錄</strong
          ><small>一張圖掌握多種作物與待辦</small>
        </div>
        <div>
          <span>03</span><strong>與植物醫生一起管理</strong
          ><small>由您決定誰可以查看農場</small>
        </div>
      </div>
      <router-link to="/farm?demo=1" class="btn preview-btn"
        ><Icon name="calendar" />先看農場規劃範例 <span>↗</span></router-link
      >
      <div class="welcome-crops">
        <span>🌾 水稻</span><span>🍅 番茄</span><span>🥬 甘藍</span
        ><span>＋ 自訂作物</span>
      </div>
    </div>
    <div class="card auth-card">
      <div class="auth-tabs">
        <button
          :class="{ active: accountMode === 'login' }"
          @click="switchAuth('login')"
        >
          登入工作區</button
        ><button
          :class="{ active: accountMode === 'register' }"
          @click="switchAuth('register')"
        >
          建立帳號
        </button>
      </div>
      <form class="farm-form" @submit.prevent="authenticate">
        <div>
          <h2>
            {{
              accountMode === 'login' ? '歡迎回到農場' : '建立您的農場工作區'
            }}
          </h2>
          <p class="form-hint">
            {{
              accountMode === 'login'
                ? '登入後，接著照顧您的作物。'
                : '選擇角色，開始管理作物或照顧農友。'
            }}
          </p>
        </div>
        <template v-if="accountMode === 'register'"
          ><label
            >您的角色<select v-model="loginForm.role">
              <option value="farmer">農民：管理自己的農場</option>
              <option value="doctor">植物醫生：查看已連結的農民</option>
              <option value="agency">主管機關／改良場：查看區域地圖</option>
            </select></label
          ><label
            >姓名<input
              v-model="loginForm.name"
              required
              maxlength="80"
              autocomplete="name"
          /></label>
          <div v-if="loginForm.role === 'farmer'" class="form-grid">
            <label
              >農場名稱<input
                v-model="loginForm.farm_name"
                maxlength="120"
                placeholder="例如：青禾農場" /></label
            ><label
              >所在地<input
                v-model="loginForm.location"
                maxlength="120"
                placeholder="例如：彰化縣溪州鄉"
            /></label></div></template
        ><label
          >帳號<input
            v-model="loginForm.username"
            required
            minlength="3"
            maxlength="120"
            autocomplete="username"
            placeholder="英數帳號或電子信箱"
            autocapitalize="none"
            spellcheck="false" /></label
        ><label
          >密碼<input
            v-model="loginForm.password"
            type="password"
            required
            minlength="10"
            maxlength="128"
            :autocomplete="
              accountMode === 'login' ? 'current-password' : 'new-password'
            "
            placeholder="至少10個字元"
        /></label>
        <p v-if="authError" class="form-error" role="alert">
          {{ authError }}
        </p>
        <button class="btn primary auth-submit" :disabled="busy">
          {{
            busy
              ? '處理中…'
              : accountMode === 'login'
                ? '登入工作區 →'
                : '建立帳號並開始 →'
          }}
        </button>
        <p class="form-hint">農場與田間紀錄僅供您及您連結的植物醫生查看。</p>
      </form>
    </div>
  </section>

  </div>
</template>
