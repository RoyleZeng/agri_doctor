import {
  ageLabel, analyzeSummary, canAccept, canDecide, canMarkDone, colorForValue, decidedSummary, fiveRChips, pointRange,
  precisionUnlocked, showActions, showDone, statusLabel, tileState, toPolyline,
} from '../src/composables/precisionState.js'

function equal<T>(actual: T, expected: T, message: string) {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a !== e) throw new Error(`${message}: expected ${e}, got ${a}`)
}

function test(name: string, run: () => void) {
  try { run(); console.log(`✓ ${name}`) }
  catch (error) { console.error(`✗ ${name}`); throw error }
}

test('only the farmer can decide, and only while a recommendation is still proposed', () => {
  equal(canDecide('farmer', 'farmer'), true, 'farmer owns it')
  equal(canDecide('doc', 'farmer'), false, '植物醫生不算')
  equal(canDecide(null, 'farmer'), false, '未登入')
  equal(showActions('proposed', 'farmer', 'farmer'), true, 'accept/dismiss show while proposed')
  equal(showActions('accepted', 'farmer', 'farmer'), false, '已接受不再顯示三個按鈕')
  equal(showActions('proposed', 'doc', 'farmer'), false, '植物醫生看不到按鈕')
  equal(showDone('accepted', 'farmer', 'farmer'), true, '已接受仍可標記已執行')
  equal(showDone('done', 'farmer', 'farmer'), false, '已執行不能再執行一次')
  equal(showDone('proposed', 'doc', 'farmer'), false, '植物醫生不能標已執行')
})

test('the card component only has status, not identity — its status-only helpers agree with showActions/showDone', () => {
  for (const status of ['proposed', 'accepted', 'done', 'dismissed', 'expired'] as const) {
    equal(canAccept(status), showActions(status, 'farmer', 'farmer'), `canAccept(${status})`)
    equal(canMarkDone(status), showDone(status, 'farmer', 'farmer'), `canMarkDone(${status})`)
  }
})

test('decided cards show a summary line instead of the three buttons', () => {
  const base = { id: 'r1', kind: 'irrigate' as const, status: 'proposed' as const, title: '補灌 C 區', field_id: 'f',
    event_id: null, work_log_id: null, status_note: '', expired_reason: '' }
  equal(decidedSummary({ ...base, status: 'accepted' }, '9/14'), '已排入 9/14 待辦', 'accepted')
  equal(decidedSummary({ ...base, status: 'accepted' }), '已排入待辦', 'accepted without date')
  equal(decidedSummary({ ...base, status: 'done' }), '已記錄到農事日誌', 'done')
  equal(decidedSummary({ ...base, status: 'dismissed', status_note: '先觀察一週' }), '已略過：先觀察一週', 'dismissed with reason')
  equal(decidedSummary({ ...base, status: 'dismissed' }), '已略過', 'dismissed without reason')
  equal(decidedSummary({ ...base, status: 'expired', expired_reason: 'C 區水分回升至 40%' }), 'C 區水分回升至 40%', 'expired reason shown verbatim')
  equal(decidedSummary({ ...base, status: 'expired' }), '已過期，請重新分析', 'expired without a stored reason still says something')
  equal(decidedSummary(base), '', 'proposed has no summary line — the buttons are the UI')
  equal(statusLabel('proposed'), '建議中', 'status label')
})

test('five-r chips keep a fixed order and drop blanks', () => {
  const chips = fiveRChips({ material: '灌溉水', rate: '', place: 'C 區', time: '今晚', method: '滴灌' })
  equal(chips, [
    { label: '資材', value: '灌溉水' },
    { label: '地點', value: 'C 區' },
    { label: '時間', value: '今晚' },
    { label: '方式', value: '滴灌' },
  ], 'blank rate dropped, order preserved')
})

test('re-analyze always reports something, even when nothing changed', () => {
  equal(analyzeSummary(1, 1, 0), '已更新：1 項建議、1 項已撤回。', 'both')
  equal(analyzeSummary(2, 0, 0), '已更新：2 項建議。', 'new only')
  equal(analyzeSummary(0, 0, 3), '目前沒有新建議，請看下方原因。', 'notes explain the silence')
  equal(analyzeSummary(0, 0, 0), '目前沒有可以分析的資料。', 'nothing at all')
})

test('zone color scale interpolates within the documented domain and never breaks on missing values', () => {
  equal(colorForValue('ndvi', 0.3), '#e8dcc3', 'low end of the ndvi domain')
  equal(colorForValue('ndvi', 0.75), '#2f8f5b', 'high end of the ndvi domain')
  equal(colorForValue('ndvi', 0.2), colorForValue('ndvi', 0.3), 'clamped below domain')
  equal(colorForValue('ndvi', 0.9), colorForValue('ndvi', 0.75), 'clamped above domain')
  equal(colorForValue('soil_moisture', null), '#e2e7e1', 'missing value is neutral grey, not white (white would read as 0)')
  equal(colorForValue('ph', undefined), '#e2e7e1', 'missing value (undefined)')
})

test('sensor tiles flag values on the wrong side of the threshold from rules.json, not a hardcoded number', () => {
  equal(tileState(19, 30, 'below'), 'warn', '19% under a 30% threshold')
  equal(tileState(35, 30, 'below'), 'ok', '35% clears a 30% threshold')
  equal(tileState(null, 30), 'ok', 'missing reading is not flagged as a problem')
  equal(tileState(33, 30, 'above'), 'warn', 'heat-style threshold: above is the warning')
  equal(ageLabel(0), '剛剛更新', 'just now')
  equal(ageLabel(45), '45 分鐘前', 'minutes')
  equal(ageLabel(180), '3 小時前', 'hours')
  equal(ageLabel(60 * 40), '2 天前', 'days')
  equal(ageLabel(null), '尚無資料', 'no data at all, not "0 分鐘前"')
})

test('sparkline scaling handles flat series and produces a well-formed polyline', () => {
  equal(pointRange([5, 5]), [4, 6], 'flat series still gets a visible range')
  equal(pointRange([]), [0, 1], 'empty series has a safe default')
  equal(toPolyline([1], 100, 40), '', 'a single point cannot form a line')
  const points = toPolyline([10, 20, 10], 100, 40)
  const coords = points.split(' ').map((pair) => pair.split(',').map(Number))
  equal(coords.length, 3, 'one coordinate per value')
  equal(coords[0][0], 0, 'first point sits at x=0')
  equal(coords[2][0], 100, 'last point sits at the right edge')
  equal(coords[1][1] < coords[0][1], true, 'the middle (higher) value plots higher, i.e. a smaller y')
})

test('the precision tab stays reachable without the toggle when demo data already exists', () => {
  equal(precisionUnlocked(false, true), true, 'demo field unlocks the dashboard even before opting in')
  equal(precisionUnlocked(true, false), true, 'opted in but no field yet — still unlocked, dashboard explains next steps')
  equal(precisionUnlocked(false, false), false, 'neither — show the explainer and an enable button')
})
