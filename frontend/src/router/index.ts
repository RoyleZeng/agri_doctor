import { createRouter, createWebHistory } from 'vue-router'

import { mustChangePassword, needsAuth, redirectFor } from '@/composables/navigation'
import { ensureSession, role, session } from '@/stores/session'

/** 查詢流程：一個搜尋框 → 混搜候選 → 挑另一半 → 三源比對結果。 */
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/admin/farm-content', component: () => import('@/views/admin/FarmContentView.vue'), meta: { title: '作物內容管理' } },
    { path: '/agri-data', name: 'agri-data', component: () => import('@/views/AgriDataView.vue'), meta: { title: '農業資料庫' } },
    { path: '/identify', name: 'identify', component: () => import('@/views/IdentificationView.vue'), meta: { title: '病蟲害輔助辨識展示' } },
    { path: '/login', name: 'login', component: () => import('@/views/LoginView.vue'), meta: { title: '登入農場工作區' } },
    { path: '/farm', name: 'farm', component: () => import('@/views/FarmerHomeView.vue'), meta: { title: '我的農場' } },
    // 日誌與精準模式從 ?tab= 改成獨立路徑，側邊欄才連得到；舊網址（診療開單的「記到日誌」等）保留。
    { path: '/farm/log', name: 'farm-log', component: () => import('@/views/FarmerHomeView.vue'), meta: { title: '農事日誌' } },
    { path: '/farm/precision', name: 'farm-precision', component: () => import('@/views/FarmerHomeView.vue'), meta: { title: '精準模式' } },
    { path: '/farmers', name: 'farmers', component: () => import('@/views/FarmerDirectoryView.vue'), meta: { title: '農民管理' } },
    { path: '/farmers/:farmerId', name: 'farmer-workspace', component: () => import('@/views/FarmerWorkspaceView.vue'), meta: { title: '農民作物規劃' } },
    // 植物醫生也要看得到農友的日誌與精準模式：抗藥性輪替檢查看的就是施藥日誌，
    // 看不到等於開單少一隻眼睛。入口在農友工作區的頁頂列，不在側邊欄（那排是農民自己的）。
    { path: '/farmers/:farmerId/log', name: 'farmer-log', component: () => import('@/views/FarmerWorkspaceView.vue'), meta: { title: '農友農事日誌' } },
    { path: '/farmers/:farmerId/precision', name: 'farmer-precision', component: () => import('@/views/FarmerWorkspaceView.vue'), meta: { title: '農友精準模式' } },
    { path: '/password', name: 'password', component: () => import('@/views/ChangePasswordView.vue'), meta: { title: '設定您的密碼' } },
    { path: '/vendors', name: 'vendors', component: () => import('@/views/VendorSearchView.vue'), meta: { title: '農藥廠商查詢' } },
    { path: '/consult', name: 'consult', component: () => import('@/views/ConsultView.vue'), meta: { title: '診療開單' } },
    { path: '/region', name: 'region', component: () => import('@/views/RegionMapView.vue'), meta: { title: '區域作物與病蟲害地圖' } },
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/HomeView.vue'),
      meta: { title: '用藥查詢' },
    },
    // 搜尋框選了病蟲害 → 挑作物
    {
      path: '/pest/:pestId/crops',
      name: 'pick-crop',
      component: () => import('@/views/PickCropView.vue'),
      meta: { title: '選擇作物' },
    },
    // 搜尋框選了作物 → 挑病蟲害
    {
      path: '/crop/:cropId/pests',
      name: 'crop-pests',
      component: () => import('@/views/PickPestView.vue'),
      meta: { title: '選擇病蟲害' },
    },
    {
      path: '/search',
      name: 'result',
      component: () => import('@/views/ResultView.vue'),
      meta: { title: '三源比對' },
    },
    {
      path: '/ingredients',
      name: 'ingredients',
      component: () => import('@/views/IngredientView.vue'),
      meta: { title: '成分反查' },
    },
    {
      path: '/issues',
      name: 'issues',
      component: () => import('@/views/IssuesView.vue'),
      meta: { title: '三源衝突' },
    },
    {
      path: '/admin/pdf-review',
      name: 'pdf-review',
      component: () => import('@/views/admin/PdfReviewView.vue'),
      meta: { title: 'PDF 校對' },
    },
    {
      path: '/admin/sources',
      name: 'sources',
      component: () => import('@/views/admin/SourcesView.vue'),
      meta: { title: '資料來源' },
    },
  ],
})

/**
 * 角色導向在**進頁之前**做完，這是第三輪「植物醫生進去先閃一次農民版畫面」的正解：
 * 舊版是在 `FarmPlanningView.load()` 拿到資料之後才 `router.replace`，畫面已經畫過一輪了。
 */
router.beforeEach(async (to) => {
  // 舊網址 /farm?tab=log｜precision → 新路徑，其餘 query（month、log、rx）原樣帶過去。
  if (to.path === '/farm' && (to.query.tab === 'log' || to.query.tab === 'precision')) {
    const { tab, ...rest } = to.query
    return { path: `/farm/${tab}`, query: rest, replace: true }
  }

  await ensureSession()

  // 未登入進需要登入的頁：帶著原本要去的地方轉去登入頁。?demo=1 是公開的操作範例，放行。
  if (!role.value && needsAuth(to.path) && to.query.demo !== '1') {
    return { path: '/login', query: { next: to.fullPath }, replace: true }
  }
  // 已登入還停在登入頁就送他去自己的落地頁。
  if (role.value && to.path === '/login') {
    return { path: redirectFor(role.value, '/farm') ?? '/farm', replace: true }
  }

  // 代建帳號沒換掉一次性密碼之前擋在改密碼頁；那組密碼植物醫生也知道。
  const password = mustChangePassword(!!session.me?.must_change_password, to.path)
  if (password) return { path: password, replace: true }

  const target = redirectFor(role.value, to.path)
  return target && target !== to.path ? { path: target, replace: true } : true
})
