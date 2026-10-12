/** 複合圖的檢視設定：放在元件外面，切農民時不重設。
 *
 * 第三輪回饋「切農民要重挑一次作物、捲動位置也跑掉」的原因是這些狀態都是元件內的 ref，
 * 元件一重建就歸零。搬到這裡之後，只有在「新農民沒有這些規劃」時才重新選取。
 */
import { reactive } from 'vue'

import type { Kind } from '@/api/planning'
import { LANES, type LaneKey } from '@/composables/timelineLanes'

export const ALL_KINDS: Kind[] = ['stage', 'care', 'nutrition', 'pest']
export const ALL_LANES: LaneKey[] = LANES.map((l) => l.key)

/** 色帶 → 規劃事項的 `kind`。「實際作業」不對應任何 kind，它的來源是日誌。 */
export const LANE_KINDS: Record<LaneKey, Kind[]> = {
  stage: ['stage'],
  planned: ['care', 'nutrition'],
  actual: [],
  pest: ['pest'],
}

export const workspaceView = reactive({
  selectedIds: [] as string[],
  showArchived: false,
  view: 'timeline' as 'timeline' | 'list',
  zoom: 'month' as 'month' | 'week',
  visibleLanes: [...ALL_LANES] as LaneKey[],
  /** 「新增作物規劃」按鈕在頁頂列（PageBar），表單在儀表板裡，用這個旗標接起來。 */
  planFormOpen: false,
})

/** 匯出 SVG 與事項清單仍以 `kind` 為單位（後端端點沒變），從色帶換算回來。 */
export const visibleKinds = (): Kind[] =>
  workspaceView.visibleLanes.flatMap((lane) => LANE_KINDS[lane])

/**
 * 換了工作區之後同步已選作物：能沿用的就沿用（同一位農民切分頁、重新整理都不該重挑），
 * 一個都對不上才視為換了農民，改成全選。
 */
export function syncSelection(planIds: string[]): void {
  const kept = workspaceView.selectedIds.filter((id) => planIds.includes(id))
  workspaceView.selectedIds = kept.length ? kept : [...planIds]
}
