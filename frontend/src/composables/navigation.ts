/** 側邊欄項目與角色導向規則：純函式，不碰 Vue 也不碰路由器，離線可測（`npm run test:navigation`）。
 *
 * 第三輪回饋「植物醫生進入後介面怪怪的」有一半來自導覽：舊版頂列對所有角色都列「農場規劃」，
 * 植物醫生點了會被 `FarmPlanningView` 彈回 `/farmers`。修法是**一開始就不要列出用不到的項目**，
 * 而不是讓人點了再導開。
 *
 * 第四輪把清單改成兩層：一層列到十項以上時，每一項看起來一樣重要，人只好整排讀過去。
 * 分組之後「我現在在哪一區」有了答案，收合成 64px 時也還有東西可以 hover。
 */
import type { IconName } from '../components/icons.js'

export type Role = 'farmer' | 'doctor' | 'agency' | 'admin'

export interface NavChild {
  to: string
  label: string
}

export interface NavItem {
  /** 展開狀態記在 localStorage 時用的鍵；用路徑當鍵會在改路由時把使用者的展開狀態弄丟。 */
  key: string
  to: string
  label: string
  icon: IconName
  /** 有子項才是可展開的父項。父項本身可點，連到第一個子項。 */
  children?: NavChild[]
  /** 群組分隔線之後的標題；只有「後台」用得到。 */
  group?: string
}

/**
 * 只有一個子項就不值得多一層：父項旁邊掛一個箭頭、展開後只有一行，
 * 點兩次才到得了同一個地方。兩個以上才收成群組。
 *
 * （第 4 步把「農藥廠商」加進「用藥查詢」之後，農民那組就會自己長出第二個子項。）
 */
function group(item: Omit<NavItem, 'children'>, children: NavChild[]): NavItem {
  if (children.length < 2) {
    const only = children[0]
    return only ? { ...item, to: only.to } : item
  }
  return { ...item, to: children[0]!.to, children }
}

const SEARCH_CHILDREN: NavChild[] = [
  { to: '/search', label: '三源比對' },
  { to: '/vendors', label: '農藥廠商' },
]
const INGREDIENTS: NavChild = { to: '/ingredients', label: '成分反查' }
const ISSUES: NavChild = { to: '/issues', label: '三源衝突' }

const REGION = (label: string): NavItem => ({ key: 'region', to: '/region', label, icon: 'pin' })
const IDENTIFY: NavItem = { key: 'identify', to: '/identify', label: '輔助辨識', icon: 'eye' }
const AGRI: NavItem = { key: 'agri', to: '/agri-data', label: '農業資料庫', icon: 'layers' }

const BACKSTAGE: NavChild[] = [
  { to: '/admin/pdf-review', label: 'PDF 校對' },
  { to: '/admin/sources', label: '資料來源' },
]

/** 依角色列出側邊欄項目。植物醫生看不到「我的農場」，農民看不到「農民管理」。 */
export function navItems(role: Role | null): NavItem[] {
  if (role === 'farmer') {
    return [
      group({ key: 'farm', to: '/farm', label: '我的農場', icon: 'leaf' }, [
        { to: '/farm', label: '作物規劃' },
        { to: '/farm/log', label: '農事日誌' },
        { to: '/farm/precision', label: '精準模式' },
      ]),
      // 農民的區域地圖只看得到自己的鄉鎮，名字叫「附近病蟲害」比「區域地圖」貼近他實際看到的東西。
      REGION('附近病蟲害'),
      // 行情與生產成本文件農民用得到，不是只有植物醫生要看。
      AGRI,
      group({ key: 'search', to: '/search', label: '用藥查詢', icon: 'search' }, SEARCH_CHILDREN),
      IDENTIFY,
    ]
  }
  if (role === 'doctor' || role === 'admin') {
    // 管理者＝植物醫生的清單，差別只在「後台」多兩個子項；兩份清單分開維護遲早會走鐘。
    const backstage = role === 'admin'
      ? [...BACKSTAGE,
         { to: '/admin/farm-content', label: '作物內容管理' },
         // 管理者的 /farm 是拿來看範例的，歸在後台，不跟農民的「我的農場」混在一起。
         { to: '/farm', label: '示範農場' }]
      : BACKSTAGE
    return [
      { key: 'farmers', to: '/farmers', label: '農民管理', icon: 'people' },
      { key: 'consult', to: '/consult', label: '診療開單', icon: 'file' },
      group({ key: 'search', to: '/search', label: '用藥查詢', icon: 'search' },
            [...SEARCH_CHILDREN, INGREDIENTS, ISSUES]),
      REGION('區域地圖'),
      IDENTIFY,
      AGRI,
      group({ key: 'backstage', to: '/admin/pdf-review', label: '後台', icon: 'file', group: '後台' }, backstage),
    ]
  }
  if (role === 'agency') {
    // 機關只看區域彙總；用藥查詢對他們是查證用，不需要成分反查與衝突清單。
    return [REGION('區域地圖'), group({ key: 'search', to: '/search', label: '用藥查詢', icon: 'search' }, SEARCH_CHILDREN), AGRI]
  }
  // 未登入：只留公開查詢；登入入口由側邊欄底部另外給。
  return [
    group({ key: 'search', to: '/search', label: '用藥查詢', icon: 'search' },
          [...SEARCH_CHILDREN, INGREDIENTS, ISSUES]),
  ]
}

/** 這個路徑算不算落在這個連結底下。`/farm` 不可以吃掉 `/farmers`，所以界線要看下一個字元。 */
function under(to: string, path: string): boolean {
  return path === to || path.startsWith(to + '/')
}

export interface NavMatch {
  /** 當前路由所屬的父項 key；平面項目也有 key。 */
  key: string | null
  /** 當前亮起的子項路徑；平面項目為 null。 */
  child: string | null
}

/**
 * 當前路由落在哪一項。
 *
 * 取**最長**的相符路徑，不是第一個：「作物規劃」是 `/farm`、「農事日誌」是 `/farm/log`，
 * 照順序比對的話站在 `/farm/log` 會先命中 `/farm`，兩個子項同時亮起。
 */
export function matchNav(items: NavItem[], path: string): NavMatch {
  let best: NavMatch = { key: null, child: null }
  let bestLength = -1
  for (const item of items) {
    const leaves: { to: string; child: string | null }[] = item.children
      ? item.children.map((c) => ({ to: c.to, child: c.to }))
      : [{ to: item.to, child: null }]
    for (const leaf of leaves) {
      if (under(leaf.to, path) && leaf.to.length > bestLength) {
        bestLength = leaf.to.length
        best = { key: item.key, child: leaf.child }
      }
    }
  }
  return best
}

/** 需要登入才有意義的路徑前綴。未登入時進這些頁要先登入。 */
const PRIVATE_PREFIXES = ['/farm', '/farmers', '/consult', '/region', '/agri-data', '/admin/farm-content']

export function needsAuth(path: string): boolean {
  return PRIVATE_PREFIXES.some((prefix) => under(prefix, path))
}

/**
 * 角色導向：回傳要改去的路徑，`null` 代表這個角色可以留在原地。
 *
 * 這是「植物醫生先閃一次農民版畫面」的正解——在 `router.beforeEach`（載入前）判斷，
 * 而不是像舊版在 `load()` 拿到資料之後才 `router.replace`。
 */
export const PASSWORD_PATH = '/password'

/**
 * 代建帳號首次登入必須改密碼，在改完之前擋在這一頁。
 *
 * 為什麼是「擋住」而不是「提醒」：那組一次性密碼是植物醫生也知道的共用秘密，
 * 沒換掉之前這個帳號等於兩個人的。登出的路要留著，不然人會被關在裡面出不去。
 */
export function mustChangePassword(needsChange: boolean, path: string): string | null {
  if (!needsChange || path === PASSWORD_PATH || path === '/login') return null
  return PASSWORD_PATH
}

export function redirectFor(role: Role | null, path: string): string | null {
  if (under('/admin/farm-content', path) && role && role !== 'admin') {
    return role === 'doctor' ? '/farmers' : role === 'agency' ? '/region' : '/farm'
  }
  if (role === 'admin') return null
  if (role === 'doctor') {
    // 植物醫生沒有自己的農場；/farm 系列一律導到農民管理。
    if (under('/farm', path)) return '/farmers'
    return null
  }
  if (role === 'agency') {
    // 機關只看區域彙總，沒有農場也沒有農民清單。
    if (under('/farm', path) || under('/farmers', path)) return '/region'
    return null
  }
  if (role === 'farmer') {
    // 農民沒有「農民管理」；連到別人的工作區也一律回自己的農場。
    if (under('/farmers', path)) return '/farm'
    return null
  }
  return null
}
