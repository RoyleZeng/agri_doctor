/** 側邊欄清單與角色導向的離線測試：`npm run test:navigation`。
 *
 * 為什麼值得測：這些規則的錯法是「某個角色少一個項目」或「某個角色多看到不該看的項目」，
 * 兩種都不會讓建置失敗，也不會在自己常走的那條路上出現——只有換個身分登入才看得到。
 */
import assert from 'node:assert/strict'

import {
  PASSWORD_PATH,
  matchNav,
  mustChangePassword,
  navItems,
  needsAuth,
  redirectFor,
  type NavItem,
  type Role,
} from '../src/composables/navigation.js'

const ROLES: (Role | null)[] = ['farmer', 'doctor', 'agency', 'admin', null]
const labels = (items: NavItem[]) => items.map((i) => i.label)
const childLabels = (items: NavItem[], key: string) =>
  items.find((i) => i.key === key)?.children?.map((c) => c.label) ?? []
/** 所有真的到得了的路徑（父項本身連到第一個子項，不額外算一個目的地）。 */
const destinations = (items: NavItem[]) =>
  items.flatMap((i) => (i.children ? i.children.map((c) => c.to) : [i.to]))

// ---- 每個角色的清單 ----

{
  const farmer = navItems('farmer')
  assert.deepEqual(labels(farmer), ['我的農場', '附近病蟲害', '農業資料庫', '用藥查詢', '輔助辨識'])
  // 第 4 步加了「農藥廠商」，農民的「用藥查詢」就從平面項目長回群組。
  assert.deepEqual(childLabels(farmer, 'search'), ['三源比對', '農藥廠商'])
  assert.deepEqual(childLabels(farmer, 'farm'), ['作物規劃', '農事日誌', '精準模式'])
  // 農民不該看到「農民管理」「診療開單」「後台」。
  for (const forbidden of ['農民管理', '診療開單', '後台', '作物內容管理']) {
    assert.ok(!labels(farmer).includes(forbidden), `農民不該看到 ${forbidden}`)
  }
  // 農民的區域地圖叫「附近病蟲害」——他看得到的就只有自己那一格。
  assert.equal(farmer.find((i) => i.key === 'region')?.label, '附近病蟲害')
}

{
  const doctor = navItems('doctor')
  assert.deepEqual(labels(doctor), ['農民管理', '診療開單', '用藥查詢', '區域地圖', '輔助辨識', '農業資料庫', '後台'])
  assert.deepEqual(childLabels(doctor, 'search'), ['三源比對', '農藥廠商', '成分反查', '三源衝突'])
  assert.deepEqual(childLabels(doctor, 'backstage'), ['PDF 校對', '資料來源'])
  // 植物醫生沒有自己的農場，清單裡不該出現任何 /farm 開頭的目的地。
  assert.ok(!destinations(doctor).some((to) => to === '/farm' || to.startsWith('/farm/')))
  assert.equal(doctor.find((i) => i.key === 'region')?.label, '區域地圖')
}

{
  const admin = navItems('admin')
  const doctor = navItems('doctor')
  // 管理者＝植物醫生的清單，差別只在「後台」的子項。
  assert.deepEqual(labels(admin), labels(doctor))
  assert.deepEqual(childLabels(admin, 'backstage'),
    ['PDF 校對', '資料來源', '作物內容管理', '示範農場'])
  // 「示範農場」是後台項目，不是頂層的「我的農場」。
  assert.ok(!labels(admin).includes('我的農場'))
  assert.ok(childLabels(admin, 'backstage').includes('示範農場'))
  // 只有管理者看得到作物內容管理。
  for (const role of ROLES) {
    const has = destinations(navItems(role)).includes('/admin/farm-content')
    assert.equal(has, role === 'admin', `${role} 的作物內容管理可見性不對`)
  }
}

{
  const agency = navItems('agency')
  assert.deepEqual(labels(agency), ['區域地圖', '用藥查詢', '農業資料庫'])
  assert.deepEqual(childLabels(agency, 'search'), ['三源比對', '農藥廠商'])
  // 機關沒有農場也沒有農民清單。
  assert.ok(!destinations(agency).some((to) => to.startsWith('/farm')))
}

{
  // 未登入只留公開查詢，不該出現任何需要登入的目的地。
  const anon = navItems(null)
  assert.deepEqual(labels(anon), ['用藥查詢'])
  assert.ok(!destinations(anon).some(needsAuth))
}

// ---- 分組規則 ----

{
  // 分組的規則：有 children 就一定要兩個以上。只有一個子項時父項旁掛一個箭頭、
  // 展開後只有一行，點兩次才到得了同一個地方——那種情況要攤平成平面項目。
  for (const role of ROLES) {
    for (const item of navItems(role)) {
      if (item.children) {
        assert.ok(item.children.length >= 2, `${role} 的「${item.label}」只有一個子項，不該分組`)
        // 父項本身連到第一個子項。
        assert.equal(item.to, item.children[0]!.to, `${role} 的「${item.label}」父項連錯地方`)
      }
    }
  }
  assert.equal(navItems('farmer').find((i) => i.key === 'farm')!.to, '/farm')
  assert.equal(navItems('doctor').find((i) => i.key === 'search')!.to, '/search')
}

{
  // key 在同一份清單裡要唯一，否則 localStorage 的展開狀態會互相蓋掉。
  for (const role of ROLES) {
    const keys = navItems(role).map((i) => i.key)
    assert.equal(new Set(keys).size, keys.length, `${role} 的 key 有重複`)
  }
}

// ---- 當前項目比對 ----

{
  const farmer = navItems('farmer')
  // 取最長相符：站在 /farm/log 不可以連「作物規劃」(/farm) 一起亮。
  assert.deepEqual(matchNav(farmer, '/farm/log'), { key: 'farm', child: '/farm/log' })
  assert.deepEqual(matchNav(farmer, '/farm'), { key: 'farm', child: '/farm' })
  assert.deepEqual(matchNav(farmer, '/farm/precision'), { key: 'farm', child: '/farm/precision' })
  // 平面項目沒有子項。
  assert.deepEqual(matchNav(farmer, '/region'), { key: 'region', child: null })
  // 對不上就是對不上，不要硬挑一個亮起來。
  assert.deepEqual(matchNav(farmer, '/login'), { key: null, child: null })
}

{
  const doctor = navItems('doctor')
  // 子頁面要讓父項亮起：植物醫生在某位農友的日誌頁，亮的是「農民管理」。
  assert.deepEqual(matchNav(doctor, '/farmers/abc/log'), { key: 'farmers', child: null })
  assert.deepEqual(matchNav(doctor, '/admin/sources'), { key: 'backstage', child: '/admin/sources' })
  assert.deepEqual(matchNav(doctor, '/issues'), { key: 'search', child: '/issues' })
}

// ---- 路徑界線：/farm 不可以吃掉 /farmers ----

assert.equal(needsAuth('/farm'), true)
assert.equal(needsAuth('/farmers/abc'), true)
assert.equal(needsAuth('/search'), false)
assert.equal(needsAuth('/farming-tips'), false)

assert.equal(redirectFor('doctor', '/farmers'), null)
assert.equal(redirectFor('doctor', '/farmers/abc/log'), null)
assert.equal(redirectFor('doctor', '/farm'), '/farmers')
assert.equal(redirectFor('doctor', '/farm/log'), '/farmers')
assert.equal(redirectFor('farmer', '/farmers/abc'), '/farm')
assert.equal(redirectFor('farmer', '/farm/log'), null)
assert.equal(redirectFor('agency', '/farmers'), '/region')
assert.equal(redirectFor('agency', '/region'), null)
assert.equal(redirectFor('admin', '/farm'), null)
assert.equal(redirectFor('admin', '/admin/farm-content'), null)
assert.equal(redirectFor('doctor', '/admin/farm-content'), '/farmers')
assert.equal(redirectFor('farmer', '/admin/farm-content'), '/farm')
assert.equal(redirectFor('agency', '/admin/farm-content'), '/region')
// 未登入不導向（守衛另外處理成去登入頁），不要在這裡多做一次判斷。
assert.equal(redirectFor(null, '/farm'), null)

// 清單裡的每個目的地，這個角色都必須進得去——列出來卻會被守衛彈開是第三輪那個 bug。
for (const role of ROLES) {
  for (const to of destinations(navItems(role))) {
    assert.equal(redirectFor(role, to), null, `${role} 點「${to}」會被導開`)
  }
}

// ---- 代建帳號首次登入強制改密碼 ----

// 沒有這個旗標時一律不擋。
assert.equal(mustChangePassword(false, '/farm'), null)
assert.equal(mustChangePassword(false, PASSWORD_PATH), null)
// 有旗標就一律導去改密碼頁，不管本來要去哪。
assert.equal(mustChangePassword(true, '/farm'), PASSWORD_PATH)
assert.equal(mustChangePassword(true, '/farm/log'), PASSWORD_PATH)
assert.equal(mustChangePassword(true, '/search'), PASSWORD_PATH)
// 改密碼頁本身不可以再導一次，否則無限循環。
assert.equal(mustChangePassword(true, PASSWORD_PATH), null)
// 登入頁要留著，不然登出之後會被關在裡面出不去。
assert.equal(mustChangePassword(true, '/login'), null)

console.log('navigation: ok')
