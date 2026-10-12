import type { CropTemplate, Source, Kind, GuidanceAttachment } from './planning'
export interface ContentEvent { kind: Kind; title: string; start: number; end: number; detail: string; source_ids: string[]; media: GuidanceAttachment[] }
export interface Content extends CropTemplate { events: ContentEvent[]; sources: Source[] }
export interface ContentRow { key: string; revision: number; draft: Content; published_version: string | null }
export async function contentRequest<T>(path = '', method = 'GET', body?: unknown): Promise<T> {
  const r = await fetch(`/api/v1/admin/farm-content${path}`, {
    method, credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Planning-Request': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const data = await r.json()
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : (data.detail || []).map((e: {msg: string}) => e.msg).join('；') || '無法儲存')
  return data
}
