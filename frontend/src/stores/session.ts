/** 登入狀態與跨頁共用的資料：`me`／`links`／`catalog` 全站只抓一次。
 *
 * 為什麼要有這支：第三輪回饋「切農民、重開卡卡、整頁閃一下」的原因是
 * `FarmPlanningView.load()` 每次 `route.path` 一動就重抓 catalog＋me＋links＋workspace 四支 API，
 * 而前三支的內容跟看哪個農民無關。把它們收進這裡，切頁只會重抓真正會變的 workspace。
 *
 * 用 `reactive` 單例而不是 Pinia：這裡只有四個欄位與幾個方法，用不到 store 的任何特性；
 * 專案的立場是不為了單一用途裝整套套件（Element Plus 也是這樣被拿掉的）。
 */
import { computed, reactive } from 'vue'

import { PlanningError, planning, type Catalog, type Farmer } from '../api/planning.js'

export type Role = 'farmer' | 'doctor' | 'agency' | 'admin'

/** store 只依賴這四支 API；測試注入假的就能數呼叫次數，正式程式碼不用留測試用的 hook。 */
export interface SessionApi {
  catalog: () => Promise<Catalog>
  me: () => Promise<Farmer>
  links: () => Promise<Farmer[]>
  logout: () => Promise<unknown>
}

export function createSession(api: SessionApi) {
const state = reactive({
  /** ensureSession() 是否已經完成過一次；未完成前路由守衛要等它，不然會用空的角色做導向。 */
  ready: false,
  me: null as Farmer | null,
  links: [] as Farmer[],
  catalog: null as Catalog | null,
})

/** 同時有多個地方呼叫 ensureSession（App 掛載＋路由守衛）時共用同一個請求，不重打。 */
let inflight: Promise<void> | null = null

async function fetchSession(): Promise<void> {
  const [catalog, me] = await Promise.all([
    api.catalog(),
    // 沒登入是正常狀態，不是錯誤；其他錯誤照樣往上拋。
    api.me().catch((e) => {
      if (e instanceof PlanningError && e.status === 401) return null
      throw e
    }),
  ])
  state.catalog = catalog
  state.me = me
  state.links = me ? await api.links().catch(() => []) : []
  state.ready = true
}

function ensureSession(): Promise<void> {
  if (state.ready) return Promise.resolve()
  if (!inflight) {
    inflight = fetchSession().finally(() => {
      inflight = null
    })
  }
  return inflight
}

/** 登入／註冊後呼叫：換人就要重抓連結清單，catalog 不會變所以留著。 */
async function signedIn(me: Farmer): Promise<void> {
  state.me = me
  state.ready = true
  state.links = await api.links().catch(() => [])
}

async function signOut(): Promise<void> {
  await api.logout()
  state.me = null
  state.links = []
}

async function refreshLinks(): Promise<void> {
  if (state.me) state.links = await api.links().catch(() => [])
}

/** 農民本人改了設定（天氣縣市、精準模式、同意匿名彙整…）後，把最新的帳號寫回來。 */
function updateMe(me: Farmer): void {
  state.me = me
}

  return { state, ensureSession, signedIn, signOut, refreshLinks, updateMe }
}

const instance = createSession(planning)

export const session = instance.state
export const ensureSession = instance.ensureSession
export const signedIn = instance.signedIn
export const signOut = instance.signOut
export const refreshLinks = instance.refreshLinks
export const updateMe = instance.updateMe
export const role = computed<Role | null>(() => (instance.state.me?.role as Role | undefined) ?? null)
export const signedInUser = computed(() => instance.state.me)
