/** 與後端同樣以日曆日比較採收期，避免時分秒或瀏覽器時區造成一天誤差。 */
function dayNumber(isoDate: string): number {
  const [year, month, day] = isoDate.split('-').map(Number)
  return Date.UTC(year, month - 1, day) / 86_400_000
}

export function taipeiToday(now = new Date()): string {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei', year: 'numeric', month: '2-digit', day: '2-digit',
  }).formatToParts(now)
  const take = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? ''
  return `${take('year')}-${take('month')}-${take('day')}`
}

export function phiStatus(phiDays: number | null, expectedHarvestOn: string | null, today = taipeiToday()) {
  if (!expectedHarvestOn) return { blocked: false, daysUntilHarvest: null, message: '未填採收日' }
  const daysUntilHarvest = dayNumber(expectedHarvestOn) - dayNumber(today)
  if (phiDays === null || phiDays <= daysUntilHarvest) {
    return { blocked: false, daysUntilHarvest, message: '' }
  }
  return {
    blocked: true,
    daysUntilHarvest,
    message: `距採收僅 ${Math.max(0, daysUntilHarvest)} 天`,
  }
}

/** 只換掉 API 已確認儲存的那一筆；其他卡片中的未存草稿維持原物件與輸入值。 */
export function replacePrescriptionItem<T extends { id: string }>(items: T[], saved: T): T[] {
  return items.map((item) => item.id === saved.id ? saved : item)
}

export function isModifiedField(item: { modified_fields: string[] }, field: string): boolean {
  return item.modified_fields.includes(field)
}
