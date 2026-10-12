"""Publish validated sources and semantic diffs in one transaction."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from sqlalchemy import select

from app.models import SourceRecord, SourceSnapshot, SourceName, SnapshotStatus
from app.models.updates import SourceChange
from app.crawlers.base import activate_snapshot, job_lock


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def date_from_notice(value):
    """Only unambiguous full dates; do not guess staged or qualified notices."""
    if not value:
        return None
    normalized = re.sub(r'\s+', '', value).replace('民國', '')
    m = re.fullmatch(r'(\d{2,4})[年./-](\d{1,2})[月./-](\d{1,2})日?', normalized)
    if not m:
        return None
    from datetime import date
    y, month, day = map(int, m.groups())
    try:
        return date(y + 1911 if y < 1911 else y, month, day)
    except ValueError:
        return None


def _key(p, fields):
    return tuple(re.sub(r'\s+', '', str(p.get(f) or '')).casefold() for f in fields)


def semantic_changes(source, previous, current):
    """Pure diff: page numbers, sequence numbers and moving URLs are not versions."""
    changes = []
    if source == 'fda':
        for record_kind, key_fields, value_fields, prefix in [
            ('fda_banned', ('english_name', 'name'), ('name','english_name','banned_manufacture_date','banned_sale_date'), '禁用名單'),
            ('fda_mrl', ('international_name','crop_category'), ('international_name','common_name','crop_category','limit_text','note'), 'MRL'),
            ('fda_mrl_exogenous', ('international_name','crop_category'), ('international_name','common_name','crop_category','limit_text','note'), '外源性MRL'),
        ]:
            def index(records):
                result = {}
                for record in records:
                    if record['kind'] != record_kind:
                        continue
                    p = record['payload']
                    value = {k:p.get(k) for k in value_fields}
                    key = _key(p, key_fields)
                    if key in result and result[key]['value'] != value:
                        raise ValueError(f'來源出現同鍵不同值：{record_kind} {key}')
                    result[key] = {'value':value, 'url':record['url']}
                return result
            old, new = index(previous), index(current)
            for key in sorted(set(old) | set(new)):
                a, b = old.get(key), new.get(key)
                if a and b and a['value'] == b['value']:
                    continue
                value = (b or a)['value']
                label = value.get('name') or value.get('common_name') or value.get('international_name')
                crop = value.get('crop_category')
                mode = 'added' if not a else ('removed' if not b else 'changed')
                changes.append(dict(kind=f'{record_kind}_{mode}', title=f'{prefix}｜{label}' + (f'・{crop}' if crop else '') +
                    {'added':'：本次新收錄', 'removed':'：本次來源未再列出，需人工核對', 'changed':'：內容有異動'}[mode],
                    before=a['value'] if a else None, after=b['value'] if b else None,
                    source_url=(b or a)['url'], effective_date=value.get('banned_sale_date')))
    elif source == 'kdais':
        def documents(records):
            out = {}
            for r in records:
                if r['kind'] != 'kdais_document':
                    continue
                p = r['payload']
                # Use the title with only the explicitly recorded revision removed.
                name = (p.get('title') or '').replace(p.get('revision_date') or '\0', '')
                name = re.sub(r'[\s()（）]', '', name)
                if name in out and out[name]['payload'] != p:
                    raise ValueError('摘要表標題鍵重複，需人工核對')
                out[name] = r
            return out
        old, new = documents(previous), documents(current)
        for key in sorted(set(old) | set(new)):
            a, b = old.get(key), new.get(key)
            if a and b and all(a['payload'].get(k) == b['payload'].get(k) for k in ('revision_date','sha256')):
                continue
            p = (b or a)['payload']
            mode = 'added' if not a else ('removed' if not b else 'changed')
            changes.append(dict(kind=f'kdais_document_{mode}', title=f"摘要表｜{p['title']}：" +
                {'added':'本次新收錄','removed':'目錄未再列出，請核對','changed':'修訂日期或檔案內容變更'}[mode],
                before=a['payload'] if a else None, after=b['payload'] if b else None,
                source_url=(b or a)['url'], effective_date=None))
    return changes


def _records(db, snap):
    return [{'kind':r.record_kind, 'payload':r.payload, 'url':r.source_url} for r in db.scalars(
        select(SourceRecord).where(SourceRecord.snapshot_id == snap.id))] if snap else []


def publish_snapshot(db, snap):
    """No API can observe a new active snapshot with the previous fact tables."""
    from app.services.normalize import (EntityCache, NormalizeResult, active_snapshot, normalize_fda,
        normalize_ppm, normalize_kdais, seed_crop_aliases, seed_ingredient_groups)
    from app.services.matcher import scan_all_issues
    with job_lock(db, 'publish'):
        previous = active_snapshot(db, snap.source)
        if previous and previous.id == snap.id:
            return False
        if previous and previous.id > snap.id:
            raise ValueError('已有較新的正式快照，不允許背景作業切回較舊資料')
        if not activate_snapshot(db, snap, commit=False):
            raise ValueError('快照未通過完整性或筆數檢查，未發布')
        try:
            existing = db.scalar(select(SourceChange.id).where(SourceChange.snapshot_id == snap.id))
            if not existing:
                current, old = _records(db, snap), _records(db, previous)
                changes = semantic_changes(snap.source.value, old, current) if previous else []
                # A legacy KDAIS snapshot did not archive document fingerprints.
                baseline_missing = not previous or (snap.source == SourceName.KDAIS and not any(r['kind']=='kdais_document' for r in old))
                if baseline_missing:
                    changes = []
                if not previous or not changes:
                    kind = 'baseline' if baseline_missing else ('source_refreshed' if snap.source == SourceName.PPM else 'source_checked')
                    title = '建立來源比較基準；不代表官方本週沒有變更' if baseline_missing else (f'植保目錄重新核驗：{previous.row_count} → {snap.row_count} 個防治對象' if snap.source == SourceName.PPM else '來源已重新核驗；未偵測到可比較欄位的異動')
                    changes.append(dict(kind=kind, title=title,
                        before={'row_count':previous.row_count} if previous else None,
                        after={'row_count':snap.row_count, 'complete':True},
                        source_url=None, effective_date=None))
                now = datetime.now(timezone.utc)
                for change in changes:
                    db.add(SourceChange(source=snap.source.value, snapshot_id=snap.id,
                        previous_snapshot_id=previous.id if previous else None,
                        change_key=hashlib.sha256(canonical(change).encode()).hexdigest(),
                        detected_at=now, **change))
            cache, result = EntityCache(db), NormalizeResult()
            if snap.source == SourceName.FDA:
                normalize_fda(db, cache, result)
                seed_ingredient_groups(db)
            elif snap.source == SourceName.PPM:
                normalize_ppm(db, cache, result)
                seed_crop_aliases(db)
            else:
                normalize_kdais(db, cache, result)
                from app.models import KdaisDocument
                for rec in db.scalars(select(SourceRecord).where(SourceRecord.snapshot_id == snap.id, SourceRecord.record_kind == 'kdais_document')):
                    p = rec.payload
                    doc = db.scalar(select(KdaisDocument).where(KdaisDocument.title == p['title']))
                    if doc is None:
                        doc = KdaisDocument(title=p['title'], pdf_url=p['pdf_url'])
                        db.add(doc)
                    for key in ('crop_text','revision_date','pdf_url','file_size','local_path'):
                        setattr(doc, key, p.get(key))
                    doc.parsed_at = snap.finished_at
                    doc.parse_note = '; '.join(p.get('warnings') or []) or None
            db.flush()
            scan_all_issues(db, commit=False)
            meta = dict(snap.meta or {})
            meta.pop('publication_error', None)
            meta.pop('publication_failed_at', None)
            snap.meta = {**meta, 'published_at':datetime.now(timezone.utc).isoformat(), 'normalized':True}
            db.commit()
            return True
        except Exception:
            db.rollback()
            raise
