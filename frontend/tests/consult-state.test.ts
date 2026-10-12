import { isModifiedField, phiStatus, replacePrescriptionItem } from '../src/composables/consultState.js'

function equal<T>(actual: T, expected: T, message: string) {
  if (actual !== expected) throw new Error(`${message}: expected ${String(expected)}, got ${String(actual)}`)
}

function test(name: string, run: () => void) {
  try { run(); console.log(`✓ ${name}`) }
  catch (error) { console.error(`✗ ${name}`); throw error }
}

test('PHI exceeds the remaining harvest days and blocks the recommendation', () => {
  const status = phiStatus(15, '2030-03-09', '2030-03-02')
  equal(status.blocked, true, 'must block')
  equal(status.daysUntilHarvest, 7, 'remaining days')
  equal(status.message, '距採收僅 7 天', 'button text')
})

test('missing harvest date is a warning hint, not a client-side block', () => {
  const status = phiStatus(15, null, '2030-03-02')
  equal(status.blocked, false, 'must remain addable')
  equal(status.message, '未填採收日', 'hint text')
})

test('saving one prescription retains another card local draft and modified tags', () => {
  const first = { id: 'first', dilution: '1000', modified_fields: ['dilution'] }
  const unsavedSecond = { id: 'second', dilution: 'local draft', modified_fields: ['advice'] }
  const savedFirst = { id: 'first', dilution: '1500', modified_fields: ['dilution'] }
  const result = replacePrescriptionItem([first, unsavedSecond], savedFirst)
  equal(result[0], savedFirst, 'saved item replaced')
  equal(result[1], unsavedSecond, 'other local draft retained')
  equal(isModifiedField(result[0], 'dilution'), true, 'modified field tag')
  equal(isModifiedField(result[0], 'advice'), false, 'unmodified field has no tag')
})
