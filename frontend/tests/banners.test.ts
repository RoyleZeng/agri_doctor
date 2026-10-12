/** 橫幅與作用機制比對的離線測試：`npm run test:banners`。 */
import assert from 'node:assert/strict'

import {
  MAX_VISIBLE,
  bannerId,
  isDismissed,
  normalizeMoa,
  normalizeMoaList,
  pruneDismissals,
  splitByMoa,
  usesExcludedMoa,
  visibleBanners,
  type Banner,
} from '../src/composables/banners.js'

const DAY = 86_400_000
const NOW = Date.UTC(2026, 8, 23)
const banner = (over: Partial<Banner> = {}): Banner => ({
  id: 'x', tone: 'warn', title: '標題', dismissDays: 7, ...over,
})

// ---- id 由內容決定 ----

// 同樣的內容永遠是同一條；內容變了就是新的一條，不會被舊的關閉狀態蓋掉。
assert.equal(bannerId('warn', '大社區', '小黃薊馬'), bannerId('warn', '大社區', '小黃薊馬'))
assert.notEqual(bannerId('warn', '大社區', '小黃薊馬'), bannerId('warn', '大社區', '東方果實蠅'))
// tone 不同就是不同條：同一段文字用 crit 講跟用 info 講不是同一件事。
assert.notEqual(bannerId('crit', 'a'), bannerId('warn', 'a'))
// null／空字串不影響結果，呼叫端不用自己先清一遍。
assert.equal(bannerId('warn', 'a', null, '', undefined, 'b'), bannerId('warn', 'a', 'b'))
assert.ok(bannerId('warn', 'a').startsWith('warn:'))

// ---- 關閉與復現 ----

{
  const b = banner({ id: 'w1', dismissDays: 7 })
  assert.equal(isDismissed(b, {}, NOW), false)
  assert.equal(isDismissed(b, { w1: NOW - 3 * DAY }, NOW), true)
  // 滿 7 天就該再出現——關掉不等於永久靜音。
  assert.equal(isDismissed(b, { w1: NOW - 7 * DAY }, NOW), false)
  assert.equal(isDismissed(b, { w1: NOW - 8 * DAY }, NOW), false)
  // 不可關閉的橫幅（輪替、示範資料）不受關閉紀錄影響。
  assert.equal(isDismissed(banner({ id: 'w1', dismissDays: 0 }), { w1: NOW }, NOW), false)
  assert.equal(isDismissed(banner({ id: 'w1', dismissDays: undefined }), { w1: NOW }, NOW), false)
}

// ---- 排序與數量 ----

{
  const list = [
    banner({ id: 'i', tone: 'info', title: 'info' }),
    banner({ id: 'd', tone: 'demo', title: 'demo' }),
    banner({ id: 'c', tone: 'crit', title: 'crit', dismissDays: 0 }),
    banner({ id: 'w', tone: 'warn', title: 'warn' }),
  ]
  const shown = visibleBanners(list, {}, NOW)
  assert.equal(shown.length, MAX_VISIBLE)
  assert.deepEqual(shown.map((b) => b.tone), ['crit', 'warn'])
  // 關掉 warn 之後換 info 遞補，crit 不動。
  assert.deepEqual(visibleBanners(list, { w: NOW }, NOW).map((b) => b.tone), ['crit', 'info'])
}

{
  // 同嚴重度維持呼叫端給的順序，不要自己重排。
  const same = [banner({ id: 'a', tone: 'warn', title: 'A' }), banner({ id: 'b', tone: 'warn', title: 'B' })]
  assert.deepEqual(visibleBanners(same, {}, NOW).map((b) => b.title), ['A', 'B'])
  assert.deepEqual(visibleBanners([...same].reverse(), {}, NOW).map((b) => b.title), ['B', 'A'])
}

assert.deepEqual(visibleBanners([], {}, NOW), [])

// 過期紀錄要清掉，不然 localStorage 只進不出。
assert.deepEqual(pruneDismissals({ a: NOW - 40 * DAY, b: NOW - DAY }, NOW), { b: NOW - DAY })

// ---- 作用機制正規化：必須跟後端 resistance.parse_moa_codes 同一套 ----

assert.equal(normalizeMoa('FRAC 3'), 'FRAC 3')
assert.equal(normalizeMoa('IRAC 14'), 'IRAC 14')
// 分號後是分類字母，取前段當代表。
assert.equal(normalizeMoa('FRAC BM02;BM02'), 'FRAC BM02')
assert.equal(normalizeMoa('FRAC 24;D3'), 'FRAC 24')
// 括號註解要去掉，不然「FRAC 3（DMI）」會跟「FRAC 3」變成兩組。
assert.equal(normalizeMoa('FRAC 3（DMI）'), 'FRAC 3')
assert.equal(normalizeMoa('FRAC 3 (DMI)'), 'FRAC 3')
assert.equal(normalizeMoa('frac 3'), 'FRAC 3')
assert.equal(normalizeMoa('FRAC: 3'), 'FRAC 3')
// 認不得就是認不得，不要猜成某一組。
assert.equal(normalizeMoa('未載明'), null)
assert.equal(normalizeMoa('FRAC'), null)
assert.equal(normalizeMoa(''), null)
// 去重並保留順序。
assert.deepEqual(normalizeMoaList(['FRAC 3（DMI）', 'FRAC 3', 'IRAC 1', '未載明']), ['FRAC 3', 'IRAC 1'])
assert.deepEqual(normalizeMoaList(null), [])

// ---- 排除比對 ----

assert.equal(usesExcludedMoa(['FRAC 3;G1'], ['FRAC 3']), true)
assert.equal(usesExcludedMoa(['FRAC 11'], ['FRAC 3']), false)
// 複方只要其中一個機制命中就算命中。
assert.equal(usesExcludedMoa(['FRAC 11', 'FRAC 3'], ['FRAC 3']), true)
// 數字相同但分類制度不同不可視為同組。
assert.equal(usesExcludedMoa(['IRAC 3'], ['FRAC 3']), false)
// 沒有要排除的東西時一律放行。
assert.equal(usesExcludedMoa(['FRAC 3'], []), false)
// 機制未載明不等於命中，也不等於安全——由 splitByMoa 分開處理。
assert.equal(usesExcludedMoa([], ['FRAC 3']), false)
assert.equal(usesExcludedMoa(null, ['FRAC 3']), false)

// ---- 三分堆 ----

{
  const items = [
    { name: '可換甲', codes: ['FRAC 11'] },
    { name: '同機制', codes: ['FRAC 3;G1'] },
    { name: '未載明', codes: [] as string[] },
    { name: '可換乙', codes: ['IRAC 3'] },
  ]
  const split = splitByMoa(items, (i) => i.codes, ['FRAC 3'])
  assert.deepEqual(split.allowed.map((i) => i.name), ['可換甲', '可換乙'])
  // 同機制的不刪掉，收起來讓植物醫生自己決定——有時就是只剩這一支能用。
  assert.deepEqual(split.excluded.map((i) => i.name), ['同機制'])
  // 未載明不可以混進「可換」，那等於宣稱它安全。
  assert.deepEqual(split.unknown.map((i) => i.name), ['未載明'])
}

{
  // 沒有要排除的機制時，只分「有代碼」與「未載明」，不會憑空把誰排除掉。
  const split = splitByMoa([{ c: ['FRAC 3'] }, { c: [] as string[] }], (i) => i.c, [])
  assert.equal(split.allowed.length, 1)
  assert.equal(split.excluded.length, 0)
  assert.equal(split.unknown.length, 1)
}

console.log('banners: ok')
