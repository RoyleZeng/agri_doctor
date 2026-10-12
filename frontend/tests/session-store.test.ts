/** 切農民／換頁時不能重抓 catalog 與 me——第三輪回饋「切農民卡卡、整頁閃」的根因就是每次都重抓。
 *
 * 注入假的 API 來數呼叫次數，不需要瀏覽器也不需要後端（照 tests/ 其他測試的離線作法）。
 */
import assert from 'node:assert/strict'

import { createSession, type SessionApi } from '../src/stores/session.js'

const calls = { catalog: 0, me: 0, links: 0, logout: 0 }
const farmer = { id: 'f1', name: '陳示範', role: 'farmer', farm_name: '示範農場', location: '高雄市大社區' }

const fake: SessionApi = {
  catalog: async () => {
    calls.catalog++
    return { version: 'v1', notice: '', sources: [], crops: [] } as never
  },
  me: async () => {
    calls.me++
    return farmer as never
  },
  links: async () => {
    calls.links++
    return [] as never
  },
  logout: async () => {
    calls.logout++
    return undefined
  },
}

const s = createSession(fake)

// 第一次：三支各抓一次
await s.ensureSession()
assert.deepEqual(calls, { catalog: 1, me: 1, links: 1, logout: 0 })
assert.equal(s.state.me?.id, 'f1')

// 之後不管誰再呼叫（換頁、切農民、路由守衛）都不該再打
await s.ensureSession()
await Promise.all([s.ensureSession(), s.ensureSession(), s.ensureSession()])
assert.equal(calls.catalog, 1, '重複呼叫 ensureSession 不該重抓 catalog')
assert.equal(calls.me, 1, '重複呼叫 ensureSession 不該重抓 me')

// 併發的「第一次」要共用同一個請求，不是各打一次
const fresh = createSession(fake)
const before = calls.catalog
await Promise.all([fresh.ensureSession(), fresh.ensureSession(), fresh.ensureSession()])
assert.equal(calls.catalog - before, 1, '併發首次呼叫應共用同一個請求')

// 上面的併發測試另外建了一個 store，也會打一次；之後的斷言以此為基準。
const baseline = { catalog: calls.catalog, me: calls.me }

// 連結農民後只重抓 links
const links = calls.links
await s.refreshLinks()
assert.equal(calls.links, links + 1, 'refreshLinks 應該重抓 links')
assert.equal(calls.catalog, baseline.catalog, 'refreshLinks 不該碰 catalog')
assert.equal(calls.me, baseline.me, 'refreshLinks 不該碰 me')

// 登入換人：links 更新，catalog 與登入者無關所以不重抓
await s.signedIn({ ...farmer, id: 'doc', role: 'doctor' } as never)
assert.equal(s.state.me?.role, 'doctor')
assert.equal(calls.catalog, baseline.catalog, '登入不該重抓 catalog')

// 登出：清掉身分，但 catalog 留著（公開資料，換人也一樣）
await s.signOut()
assert.equal(s.state.me, null)
assert.equal(s.state.links.length, 0)
assert.equal(calls.catalog, baseline.catalog, '登出不該重抓 catalog')

console.log('✓ 切農民／換頁不重抓 catalog 與 me；併發首次呼叫共用同一個請求')
