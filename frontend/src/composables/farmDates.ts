import type { Kind, Plan } from '@/api/planning'
const DAY = 86400000
export const kinds: {
  key: Kind
  name: string
  icon: 'leaf' | 'check' | 'flask' | 'bug'
}[] = [
  { key: 'stage', name: '生育時期', icon: 'leaf' },
  { key: 'care', name: '農事管理', icon: 'check' },
  { key: 'nutrition', name: '肥培管理', icon: 'flask' },
  { key: 'pest', name: '病蟲害巡查', icon: 'bug' },
]

// Treat ISO dates as calendar days, independent of DST and browser timezone parsing.
export const day = (iso: string) =>
  Math.floor(Date.parse(iso + 'T00:00:00Z') / DAY)
export const iso = (n: number) => new Date(n * DAY).toISOString().slice(0, 10)
export const today = () =>
  new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Taipei' }).format(
    new Date(),
  )
export const plus = (date: string, days: number) =>
  Number.isFinite(day(date)) ? iso(day(date) + days) : ''
export const shortDate = (value: string) =>
  `${Number(value.slice(5, 7))}/${Number(value.slice(8, 10))}`
export function stageOf(p: Plan, date = today()) {
  if (date < p.planted_on) return '尚未種植'
  if (date > p.end_on) return '規劃期已結束'
  return (
    p.events.find(
      (e) => e.kind === 'stage' && e.start_on <= date && e.end_on >= date,
    )?.title || '生育期待確認'
  )
}
