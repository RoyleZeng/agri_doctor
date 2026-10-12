/** 就地記錄流程的離線測試：`npm run test:record-flow`。
 *
 * 這支存在的唯一理由是釘住一件「沒有發生的事」——**存檔之後不可以重抓**。
 * 畫面看起來對、build 也過，卻偷偷多打兩支 API 把捲動位置與已選作物洗掉，
 * 是第三輪真的發生過的退步；只有數呼叫次數抓得到。
 */
import assert from 'node:assert/strict'

import {
  MAX_LOG_PAGES,
  createRecordFlow,
  mergeLog,
  type FlowLog,
  type FlowPlan,
} from '../src/composables/recordFlow.js'

interface TestLog extends FlowLog {
  id: string
  done_on: string
  plan?: { id: string } | null
}

const plan: FlowPlan = {
  id: 'p1',
  planted_on: '2026-05-01',
  end_on: '2026-09-01',
  events: [
    { id: 'e1', title: '套袋', log_id: null },
    { id: 'e2', title: '追肥', log_id: 'l9' },
  ],
}
const log = (over: Partial<TestLog> = {}): TestLog => ({ id: 'l1', done_on: '2026-06-01', ...over })

/** 假 API：記下每次呼叫，回傳指定的頁面。 */
function fakeApi(pages: { total: number; items: TestLog[] }[]) {
  const calls: { farmerId: string; page: number }[] = []
  return {
    calls,
    logs: async (farmerId: string, params: { from: string; to: string; page: number }) => {
      calls.push({ farmerId, page: params.page })
      return pages[params.page - 1] ?? { total: 0, items: [] }
    },
  }
}

const RANGE = { from: '2026-04-01', to: '2026-09-01' }

// ---- 載入：該打的時候要打 ----

{
  const api = fakeApi([{ total: 1, items: [log()] }])
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs('f1', RANGE)
  assert.equal(api.calls.length, 1)
  assert.deepEqual(flow.logs.value.map((l) => l.id), ['l1'])
  assert.equal(flow.truncated.value, false)
}

{
  // 分頁：抓到總數就停，不多打一頁。
  const api = fakeApi([
    { total: 3, items: [log({ id: 'a' }), log({ id: 'b' })] },
    { total: 3, items: [log({ id: 'c' })] },
    { total: 3, items: [log({ id: 'd' })] },
  ])
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs('f1', RANGE)
  assert.equal(api.calls.length, 2)
  assert.deepEqual(flow.logs.value.map((l) => l.id), ['a', 'b', 'c'])
}

{
  // 超過上限就停手並標記，不無限翻頁也不假裝畫完了。
  const page = { total: 999, items: [log()] }
  const api = fakeApi(Array.from({ length: 20 }, () => page))
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs('f1', RANGE)
  assert.equal(api.calls.length, MAX_LOG_PAGES)
  assert.equal(flow.truncated.value, true)
}

{
  // 沒有農民或沒有期間：一次都不該打。
  const api = fakeApi([{ total: 1, items: [log()] }])
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs(null, RANGE)
  await flow.loadLogs('f1', null)
  assert.equal(api.calls.length, 0)
  assert.deepEqual(flow.logs.value, [])
}

{
  // 抓不到日誌不該讓整張圖壞掉：清空、不拋出。
  const flow = createRecordFlow<FlowPlan, TestLog>({
    logs: async () => { throw new Error('boom') },
  })
  await flow.loadLogs('f1', RANGE)
  assert.deepEqual(flow.logs.value, [])
  assert.equal(flow.truncated.value, false)
}

// ---- 開抽屜：一次都不該打 API ----

{
  const api = fakeApi([{ total: 1, items: [log({ id: 'l9' })] }])
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs('f1', RANGE)
  const after = api.calls.length

  flow.openRecord(plan, 'e1')
  const opened = flow.drawer.value
  assert.equal(opened?.kind, 'record')
  assert.equal(opened?.title, '記錄「套袋」')

  // 已經記過的事項改成「看紀錄」，而且是從手上的 logs 找，不是再去要一次。
  flow.openRecord(plan, 'e2')
  const reopened = flow.drawer.value
  assert.equal(reopened?.kind, 'view')
  assert.equal(reopened?.kind === 'view' ? reopened.log.id : null, 'l9')

  flow.openNew(plan)
  assert.equal(flow.drawer.value?.kind, 'new')
  flow.openView(log({ id: 'l1' }))
  const viewed = flow.drawer.value
  assert.equal(viewed?.kind, 'view')
  flow.close()
  assert.equal(flow.drawer.value, null)

  assert.equal(api.calls.length, after, '開／關抽屜不可以打 API')
}

// ---- 存檔：這才是重點，一次都不可以打 ----

{
  const api = fakeApi([{ total: 1, items: [log({ id: 'l1' })] }])
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs('f1', RANGE)
  const before = api.calls.length

  flow.openRecord(plan, 'e1')
  const savedPlan = flow.saved(log({ id: 'l2', plan: { id: 'p1' } }))

  // 新的那筆就地併進來，不是重抓一次整批。
  assert.deepEqual(flow.logs.value.map((l) => l.id), ['l1', 'l2'])
  // 後端一併回傳的規劃交給呼叫端塞回 workspace——也不是重抓。
  assert.deepEqual(savedPlan, { id: 'p1' })
  assert.equal(flow.drawer.value, null)
  assert.equal(api.calls.length, before, '存檔後不可以重抓日誌')
}

{
  // 編輯既有的那筆：取代而不是又加一筆，而且同樣不重抓。
  const api = fakeApi([{ total: 2, items: [log({ id: 'a', done_on: '2026-06-01' }), log({ id: 'b' })] }])
  const flow = createRecordFlow<FlowPlan, TestLog>(api)
  await flow.loadLogs('f1', RANGE)
  const before = api.calls.length
  flow.saved(log({ id: 'a', done_on: '2026-06-09' }))
  assert.deepEqual(flow.logs.value.map((l) => l.id), ['a', 'b'])
  assert.equal(flow.logs.value[0]!.done_on, '2026-06-09')
  assert.equal(api.calls.length, before)
}

{
  // 沒有附帶規劃時回 null，呼叫端不該被迫處理 undefined。
  const flow = createRecordFlow<FlowPlan, TestLog>(fakeApi([]))
  assert.equal(flow.saved(log()), null)
}

// ---- 併入陣列本身 ----

assert.deepEqual(mergeLog([{ id: 'a' }], { id: 'b' }), [{ id: 'a' }, { id: 'b' }])
assert.deepEqual(mergeLog([{ id: 'a', v: 1 }], { id: 'a', v: 2 }), [{ id: 'a', v: 2 }])
{
  // 不可以就地改原陣列——畫面別處可能還拿著同一個參照。
  const original = [{ id: 'a' }]
  mergeLog(original, { id: 'b' })
  assert.deepEqual(original, [{ id: 'a' }])
}

console.log('record-flow: ok')
