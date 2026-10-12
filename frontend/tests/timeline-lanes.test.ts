/** 複合圖 v3 色帶邏輯的離線測試：`npm run test:timeline`，不需要瀏覽器或 API。 */
import assert from 'node:assert/strict'

import {
  LANE_OF_KIND,
  LANES,
  axisStart,
  logInRange,
  mergeSavedLog,
  plannedRows,
  plannedStatus,
  rangeBounds,
  rowInRange,
  unplannedLogs,
  isOverdue,
  logColor,
  midpoint,
  offsetFrom,
  offsetLabel,
  matchRegionAlert,
  placeLogs,
  stackRows,
  type LaneEvent,
  type LaneLog,
} from '../src/composables/timelineLanes.js'

const event = (over: Partial<LaneEvent> = {}): LaneEvent => ({
  id: 'e1',
  kind: 'care',
  title: '套袋',
  start_on: '2026-05-10',
  end_on: '2026-05-16',
  completed: false,
  log_id: null,
  ...over,
})

const log = (over: Partial<LaneLog> = {}): LaneLog => ({
  id: 'l1',
  kind: 'spray',
  title: '施藥',
  done_on: '2026-05-12',
  event_id: null,
  photo_count: 0,
  ...over,
})

// 四條色帶的順序是規格定的：生育在最上、巡查在最下，實際作業緊貼規劃事項才好上下對照。
assert.deepEqual(
  LANES.map((l) => l.key),
  ['stage', 'planned', 'actual', 'pest'],
)
assert.equal(LANE_OF_KIND.care, 'planned')
assert.equal(LANE_OF_KIND.nutrition, 'planned')
assert.equal(LANE_OF_KIND.stage, 'stage')
assert.equal(LANE_OF_KIND.pest, 'pest')

// 奇數與偶數長度的窗口都要落在窗口內，不能因為取中點跑到窗口外。
assert.equal(midpoint({ start_on: '2026-05-10', end_on: '2026-05-16' }), '2026-05-13')
assert.equal(midpoint({ start_on: '2026-05-10', end_on: '2026-05-11' }), '2026-05-10')
assert.equal(midpoint({ start_on: '2026-05-10', end_on: '2026-05-10' }), '2026-05-10')

// 早／晚是相對「窗口」不是相對某一天：窗口內一律如期，不該報「早 2 天」讓農民以為做錯。
assert.equal(offsetFrom(event(), '2026-05-10'), 0)
assert.equal(offsetFrom(event(), '2026-05-16'), 0)
assert.equal(offsetFrom(event(), '2026-05-08'), -2)
assert.equal(offsetFrom(event(), '2026-05-19'), 3)
assert.equal(offsetLabel(0), '如期')
assert.equal(offsetLabel(-2), '早 2 天')
assert.equal(offsetLabel(3), '晚 3 天')

// 跨月與跨年要用真實天數，不能用「月份差×30」這種算法。
assert.equal(offsetFrom({ start_on: '2026-02-26', end_on: '2026-02-28' }, '2026-03-03'), 3)
assert.equal(offsetFrom({ start_on: '2025-12-30', end_on: '2025-12-31' }, '2026-01-02'), 2)

// 對到規劃事項的點畫在事項中點（正下方），沒對到的照完成日擺。
{
  const events = [event()]
  const placed = placeLogs(events, [
    log({ id: 'a', event_id: 'e1', done_on: '2026-05-08' }),
    log({ id: 'b', event_id: null, done_on: '2026-05-20' }),
    log({ id: 'c', event_id: 'missing', done_on: '2026-05-21' }),
  ])
  assert.equal(placed[0].anchor, '2026-05-13')
  assert.equal(placed[0].event?.id, 'e1')
  assert.equal(placed[0].offsetText, '早 2 天')
  assert.equal(placed[1].anchor, '2026-05-20')
  assert.equal(placed[1].offsetDays, null)
  assert.equal(placed[1].offsetText, '')
  // 指到不存在的事項（事項被刪掉）要當成沒對到，不能整張圖壞掉。
  assert.equal(placed[2].event, null)
  assert.equal(placed[2].anchor, '2026-05-21')
}

// 巡田走巡查色帶，其他都走實際作業色帶。
{
  const placed = placeLogs([], [log({ kind: 'scout' }), log({ kind: 'spray' }), log({ kind: 'harvest' })])
  assert.deepEqual(
    placed.map((p) => p.lane),
    ['pest', 'actual', 'actual'],
  )
}

// 有照片才標紫點。
{
  const placed = placeLogs([], [log({ photo_count: 0 }), log({ photo_count: 2 })])
  assert.equal(placed[0].photo, false)
  assert.equal(placed[1].photo, true)
}

// 顏色：未知的 kind 要退回灰色，不能是 undefined 讓點變透明。
assert.equal(logColor('spray'), '#b23b2d')
assert.equal(logColor('整地以外的新類型'), logColor('other'))

// 逾期未記錄：窗口過了、沒記錄、沒標完成。
const TODAY = '2026-05-20'
assert.equal(isOverdue(event(), TODAY), true)
// 已標完成的不算逾期（清單卡另外提醒「未記錄」，圖上不重複喊）。
assert.equal(isOverdue(event({ completed: true }), TODAY), false)
// 已經有日誌的不算。
assert.equal(isOverdue(event({ log_id: 'l9' }), TODAY), false)
// 窗口還沒結束的不算，當天結束也不算——還有一天可以做。
assert.equal(isOverdue(event({ end_on: '2026-05-25' }), TODAY), false)
assert.equal(isOverdue(event({ end_on: TODAY }), TODAY), false)
// 生育時期與巡查窗口不標逾期：那不是待辦。
assert.equal(isOverdue(event({ kind: 'stage' }), TODAY), false)
assert.equal(isOverdue(event({ kind: 'pest' }), TODAY), false)

// 疊點：距離夠就同一列，不夠就往下一列，而且第三點要回到第一列（不是無限往下疊）。
assert.deepEqual(stackRows([0, 100, 200], 20), [0, 0, 0])
assert.deepEqual(stackRows([0, 5, 10], 20), [0, 1, 2])
assert.deepEqual(stackRows([0, 5, 30], 20), [0, 1, 0])
// 輸入順序不影響結果，回傳的列號要對回原本的索引。
assert.deepEqual(stackRows([30, 0, 5], 20), [0, 0, 1])
assert.deepEqual(stackRows([], 20), [])

// 區域警戒對上巡查事項：雙向包含，但不同種類不可互相命中。
{
  const alert = (pest_text: string) => ({ pest_text, metric: 'reports_30d', value: 9, warn_value: 5, basis: '示範' })
  const alerts = [alert('稻熱病'), alert('東方果實蠅')]
  // 事項標題較長、警戒名較短
  assert.equal(matchRegionAlert(alerts, '稻熱病・紋枯病')?.pest_text, '稻熱病')
  // 警戒名較長、事項標題較短
  assert.equal(matchRegionAlert([alert('小黃薊馬')], '薊馬')?.pest_text, '小黃薊馬')
  // 沒對上就是沒對上，不能給紅標
  assert.equal(matchRegionAlert(alerts, '二化螟（枯心）'), null)
  assert.equal(matchRegionAlert([alert('二化螟')], '三化螟'), null)
  // 空字串不能命中全部——警戒名沒填時不該讓每一項都變紅標
  assert.equal(matchRegionAlert([alert('')], '稻熱病'), null)
  assert.equal(matchRegionAlert([], '稻熱病'), null)
}

// 橫軸起點：沒有更早的日誌就固定前推 14 天。
assert.equal(axisStart([100], []), 86)
// 日誌沒有比「種植日−14」更早時，起點還是種植日−14（有日誌不代表要變窄）。
assert.equal(axisStart([100], [92, 120]), 86)
// 有更早的日誌就跟著往前。
assert.equal(axisStart([100], [80]), 80)
// ……但最多 45 天；上一期的日誌不該把這一期壓扁。
assert.equal(axisStart([100], [10]), 55)
assert.equal(axisStart([100], [55]), 55)
// 多期作物看最早種植的那一期。
assert.equal(axisStart([200, 100, 150], []), 86)
assert.equal(axisStart([200, 100], [70]), 70)

// ---- 「規劃 vs 實際」面板 ----

{
  const T = '2026-05-20'
  const win = { start_on: '2026-05-18', end_on: '2026-05-22' }
  // 有日誌＝已做，那是事實。
  assert.equal(plannedStatus(event(win), [log()], T), 'done')
  // 「標完成」不是「已記錄」：產銷履歷要的是日誌，不是宣稱。
  assert.equal(plannedStatus(event({ ...win, completed: true }), [], T), 'pending')
  assert.equal(plannedStatus(event(win), [], T), 'pending')
  assert.equal(plannedStatus(event({ start_on: '2026-05-01', end_on: '2026-05-10' }), [], T), 'overdue')
  assert.equal(plannedStatus(event({ start_on: '2026-05-25', end_on: '2026-05-30' }), [], T), 'upcoming')
  // 邊界：窗口今天開始算 pending、今天結束也算 pending（還有一天可以做）。
  assert.equal(plannedStatus(event({ start_on: T, end_on: '2026-05-30' }), [], T), 'pending')
  assert.equal(plannedStatus(event({ start_on: '2026-05-01', end_on: T }), [], T), 'pending')
}

{
  const T = '2026-05-20'
  const events = [
    event({ id: 'e2', title: '施肥', kind: 'nutrition', start_on: '2026-05-18', end_on: '2026-05-22' }),
    event({ id: 'e1', title: '套袋', start_on: '2026-05-01', end_on: '2026-05-10' }),
    event({ id: 's1', title: '分蘗期', kind: 'stage', start_on: '2026-05-01', end_on: '2026-06-01' }),
    event({ id: 'p1', title: '二化螟', kind: 'pest', start_on: '2026-05-01', end_on: '2026-06-01' }),
  ]
  const placed = placeLogs(events, [
    log({ id: 'l1', event_id: 'e2', done_on: '2026-05-19' }),
    log({ id: 'l2', event_id: null, done_on: '2026-05-15' }),
    log({ id: 'l3', event_id: null, done_on: '2026-05-17' }),
  ])
  const rows = plannedRows(events, placed, T)
  // 只有規劃事項那條色帶的 kind；生育與巡查不是待辦，不該出現在面板上半部。
  assert.deepEqual(rows.map((r) => r.event.id), ['e1', 'e2'])
  assert.equal(rows[0].status, 'overdue')
  assert.equal(rows[1].status, 'done')
  assert.equal(rows[1].doneOn, '2026-05-19')
  assert.equal(rows[1].offsetText, '如期')
  // 規劃外的實際作業：新的在前。
  assert.deepEqual(unplannedLogs(placed).map((l) => l.id), ['l3', 'l2'])
}

{
  // 同一個事項分兩天做完：兩筆都留著，實際日期取最早那筆。
  const e = event({ id: 'e1', start_on: '2026-05-18', end_on: '2026-05-22' })
  const placed = placeLogs([e], [
    log({ id: 'b', event_id: 'e1', done_on: '2026-05-21' }),
    log({ id: 'a', event_id: 'e1', done_on: '2026-05-19' }),
  ])
  const row = plannedRows([e], placed, '2026-05-25')[0]
  assert.deepEqual(row.logs.map((l) => l.id), ['a', 'b'])
  assert.equal(row.doneOn, '2026-05-19')
}

{
  // 範圍：整期不篩；週／月以今天前後推。
  assert.equal(rangeBounds('season', '2026-05-20'), null)
  assert.deepEqual(rangeBounds('week', '2026-05-20'), { from: '2026-05-13', to: '2026-05-27' })
  assert.deepEqual(rangeBounds('month', '2026-05-20'), { from: '2026-04-20', to: '2026-06-19' })

  const bounds = rangeBounds('week', '2026-05-20')!
  const row = (start: string, end: string) =>
    plannedRows([event({ start_on: start, end_on: end })], [], '2026-05-20')[0]
  // 窗口只要跟範圍**重疊**就算：跨三週的事項不該因為起日在範圍外就消失。
  assert.equal(rowInRange(row('2026-05-01', '2026-05-25'), bounds), true)
  assert.equal(rowInRange(row('2026-05-01', '2026-05-10'), bounds), false)
  assert.equal(rowInRange(row('2026-06-01', '2026-06-10'), bounds), false)
  // 剛好碰到邊界算在內。
  assert.equal(rowInRange(row('2026-05-27', '2026-06-10'), bounds), true)
  assert.equal(rowInRange(row('2026-05-01', '2026-05-13'), bounds), true)
  // 整期一律放行。
  assert.equal(rowInRange(row('2020-01-01', '2020-01-02'), null), true)

  assert.equal(logInRange(log({ done_on: '2026-05-20' }), bounds), true)
  assert.equal(logInRange(log({ done_on: '2026-05-01' }), bounds), false)
  assert.equal(logInRange(log({ done_on: '2026-05-01' }), null), true)
}

// 存檔後就地更新：新的補在後面，同 id 取代而不是再加一筆。
{
  const a = { id: 'a', v: 1 }
  const b = { id: 'b', v: 1 }
  assert.deepEqual(mergeSavedLog([a], b), [a, b])
  assert.deepEqual(mergeSavedLog([a, b], { id: 'a', v: 2 }), [{ id: 'a', v: 2 }, b])
  assert.deepEqual(mergeSavedLog([], a), [a])
  // 不可以就地改原陣列——畫面別處可能還拿著同一個參照。
  const original = [a]
  mergeSavedLog(original, b)
  assert.deepEqual(original, [a])
}

console.log('timeline-lanes: ok')
