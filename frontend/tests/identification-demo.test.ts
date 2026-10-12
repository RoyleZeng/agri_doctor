import { demoCandidates, reviewProblems } from '../src/composables/identificationDemo.js'

function assert(value: unknown, message: string) { if (!value) throw new Error(message) }
const candidates = demoCandidates(['photo-a','photo-b'])
assert(candidates.length === 3 && candidates.every(c => c.decision === 'pending'), '展示候選必須保持未判定')
assert(reviewProblems(candidates).length === 3, '未審閱的展示輸出不可當成完整回饋')
candidates[0]!.decision = 'confirmed'; candidates[0]!.reason = '照片甲的局部斑點'
candidates[1]!.decision = 'confirmed'; candidates[1]!.reason = '照片乙的活蟲'; candidates[1]!.photoIds = ['photo-b']
candidates[2]!.decision = 'needs_lab'; candidates[2]!.reason = '尚缺灌溉與施肥紀錄'
assert(!reviewProblems(candidates).length, '允許同時確認多種候選並保留待鑑定項')
candidates[0]!.photoIds = []
assert(reviewProblems(candidates).length === 1, '確認結果必須連結到佐證照片')
candidates[0]!.decision = 'excluded'; candidates[0]!.reason = ''
assert(reviewProblems(candidates).length === 1, '排除結果也必須有理由')
assert(demoCandidates(['new-photo'])[0]!.decision === 'pending', '新案例不能繼承舊案例確認標籤')
console.log('✓ 多標籤判讀、證據連結、排除理由與案例隔離')
