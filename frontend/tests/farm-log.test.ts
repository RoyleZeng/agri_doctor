import {
  canBackfillRegistration, canModifyLog, dayHeading, exportFileName, exportRangeError, daySubtitle, groupByDay, harvestAfter, matchesFilters,
  missingFields, missingText, moaSource, monthRange, recentMonths, saveLabel, upsertLog,
} from '../src/composables/farmLog.js'

function equal<T>(actual: T, expected: T, message: string) {
  const a = JSON.stringify(actual)
  const e = JSON.stringify(expected)
  if (a !== e) throw new Error(`${message}: expected ${e}, got ${a}`)
}

function test(name: string, run: () => void) {
  try { run(); console.log(`✓ ${name}`) }
  catch (error) { console.error(`✗ ${name}`); throw error }
}

const log = (id: string, done_on: string, done_time: string | null, created_at: string) =>
  ({ id, done_on, done_time, created_at, photos: [] as unknown[] })

test('saved log is inserted in server order without touching other cards', () => {
  const older = log('a', '2030-03-08', null, '2030-03-08T01:00:00Z')
  const untimed = log('b', '2030-03-09', null, '2030-03-09T09:00:00Z')
  const list = [untimed, older]
  const timed = log('c', '2030-03-09', '06:30', '2030-03-09T02:00:00Z')
  const result = upsertLog(list, timed)
  equal(result.map((item) => item.id), ['c', 'b', 'a'], 'timed entry sorts before untimed on the same day')
  equal(result[1] === untimed, true, 'other cards keep their object identity')
  const moved = upsertLog(result, { ...untimed, done_on: '2030-03-01' })
  equal(moved.map((item) => item.id), ['c', 'a', 'b'], 'editing the date moves the card')
})

test('day groups count photos and label today', () => {
  const groups = groupByDay([
    { ...log('a', '2030-03-10', null, ''), photos: [1, 2] },
    { ...log('b', '2030-03-10', null, ''), photos: [3] },
    log('c', '2030-03-09', null, ''),
  ])
  equal(groups.map((group) => [group.date, group.items.length, group.photoCount]), [['2030-03-10', 2, 3], ['2030-03-09', 1, 0]], 'groups')
  equal(daySubtitle(groups[0], '2030-03-10'), '今天 · 2 筆 · 3 張照片', 'today subtitle')
  equal(daySubtitle(groups[1], '2030-03-10'), '1 筆 · 無照片', 'past subtitle')
  equal(dayHeading('2030-03-10'), '3/10 週日', 'weekday is a calendar day, not browser timezone')
})

test('months cross the year and month ends are real calendar ends', () => {
  equal(recentMonths('2030-02-15', 3), ['2030-02', '2030-01', '2029-12'], 'recent months')
  equal(monthRange('2028-02'), { from: '2028-02-01', to: '2028-02-29' }, 'leap February')
  equal(matchesFilters({ done_on: '2030-03-31', kind: 'spray', field_text: '乙' }, { month: '2030-03', kind: 'spray', field: '甲' }), false, 'other field excluded')
  equal(matchesFilters({ done_on: '2030-03-31', kind: 'spray', field_text: '甲' }, { month: '2030-03', kind: '', field: '' }), true, 'last day included')
  equal(matchesFilters({ done_on: '2030-04-01', kind: 'spray', field_text: '甲' }, { month: '2030-03', kind: '', field: '' }), false, 'next month excluded')
})

test('compliance hint lists missing fields once per concept', () => {
  const missing = missingFields('spray', { material_name: '試驗藥劑', material_supplier: ' ', amount_value: '', amount_unit: '', area_text: '0.1 公頃' })
  equal(missing, ['material_supplier', 'material_lot', 'amount_value', 'amount_unit'], 'missing keys follow backend order')
  equal(missingText(missing), '供應者、批號、用量', 'unit folded into amount')
  equal(missingFields('irrigate', {}), [], 'kinds without rules have nothing missing')
})

test('moa source never comes from a typed drug name', () => {
  equal(moaSource('spray', false, false), 'missing', 'typed name only')
  equal(moaSource('spray', true, false), 'prescription', 'prescription')
  equal(moaSource('spray', false, true), 'registration', 'registration')
  equal(moaSource('fertilize', false, false), 'na', 'not a spray')
  equal(canBackfillRegistration({ kind: 'spray', registration_snapshot: null, prescription_item_id: null }), true, 'backfill allowed')
  equal(canBackfillRegistration({ kind: 'spray', registration_snapshot: null, prescription_item_id: 'rx' }), false, 'prescription already has a source')
  equal(canBackfillRegistration({ kind: 'spray', registration_snapshot: { moa_codes: [] }, prescription_item_id: null }), false, 'only once')
})

test('export file names are safe and ranges are validated before calling the server', () => {
  equal(exportFileName('試驗 農場/甲', '2030-03-01', '2030-03-31', 'pdf'), '試驗_農場_甲_農事紀錄表_20300301-20300331.pdf', 'reserved characters replaced')
  equal(exportFileName('  ', '2030-03-01', '2030-03-31', 'xlsx'), '農場_農事紀錄表_20300301-20300331.xlsx', 'blank farm name falls back')
  equal(exportRangeError('2030-03-02', '2030-03-01'), '起始日期不能晚於結束日期', 'reversed range')
  equal(exportRangeError('', '2030-03-01'), '請選擇起訖日期', 'missing date')
  equal(exportRangeError('2030-03-01', '2030-03-01'), '', 'single day allowed')
})

test('permissions and save button wording', () => {
  const entry = { farmer_id: 'farmer', created_by: 'doc' }
  equal([canModifyLog(entry, 'farmer'), canModifyLog(entry, 'doc'), canModifyLog(entry, 'doc2'), canModifyLog(entry, null)], [true, true, false, false], 'who can modify')
  equal(saveLabel({ kind: 'spray', fromEvent: false, editing: false }), '儲存 · 並寫入施藥紀錄', 'spray')
  equal(saveLabel({ kind: 'fertilize', fromEvent: true, editing: false }), '儲存到農事日誌並標完成', 'from event')
  equal(saveLabel({ kind: 'spray', fromEvent: false, editing: true }), '儲存修改', 'editing')
  equal(harvestAfter('2030-02-26', 4), '2030-03-02', 'phi crosses month end')
  equal(harvestAfter('2030-02-26', null), null, 'unknown phi is not zero')
})
