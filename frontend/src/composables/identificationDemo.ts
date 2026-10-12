/** 展示候選刻意使用症候類別，不假裝照片已經過模型判讀。 */
export type ReviewDecision = 'pending' | 'confirmed' | 'excluded' | 'needs_lab'
export interface CandidateReview {
  id: string; name: string; kind: string; observation: string; nextStep: string
  decision: ReviewDecision; reason: string; photoIds: string[]; origin: 'demo' | 'doctor'
}
export const decisionLabels: Record<ReviewDecision,string> = {
  pending: '尚未判定', confirmed: '醫師確認', excluded: '醫師排除', needs_lab: '待補證／送鑑定',
}
export function demoCandidates(photoIds: string[]): CandidateReview[] {
  return [
    { id: 'leaf-spots', name: '葉斑類病害候選', kind: '病害', observation: '示範觀察：葉片可見分散斑點；僅憑外觀不足以確認病原。', nextStep: '補充病斑正反面、健康對照與發生進程。' },
    { id: 'sap-feeding', name: '刺吸式害蟲候選', kind: '蟲害', observation: '示範觀察：葉背局部聚集痕跡；可能與病斑同時存在。', nextStep: '補拍葉背蟲體近照，記錄是否有活蟲或蜜露。' },
    { id: 'abiotic', name: '非生物性逆境候選', kind: '其他原因', observation: '示範觀察：部分葉缘變色；需排除水分、營養或藥害因素。', nextStep: '補充灌溉、施肥與近期施藥紀錄。' },
  ].map(item => ({ ...item, decision: 'pending', reason: '', photoIds: photoIds.slice(0,1), origin: 'demo' }))
}
export function reviewProblems(candidates: CandidateReview[]): string[] {
  const problems: string[] = []
  if (!candidates.length) problems.push('至少保留一個候選或補充判斷。')
  for (const c of candidates) {
    if (c.decision === 'pending') problems.push(`${c.name}：尚未完成判讀。`)
    if (c.decision !== 'pending' && !c.reason.trim()) problems.push(`${c.name}：請補判讀理由。`)
    if (c.decision === 'confirmed' && !c.photoIds.length) problems.push(`${c.name}：請選擇佐證照片。`)
  }
  return problems
}
