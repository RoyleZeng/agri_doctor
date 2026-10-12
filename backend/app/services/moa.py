"""農業部 API 共用快取：完整分頁才發布；失敗保留舊快照，金鑰只留伺服器。"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
from threading import Lock
import time

import httpx

from app.core.config import settings
from app.crawlers.base import tls_context
from app.services.moa_parser import MoaDataError, unwrap, parse_costs, parse_prices, crop_query

BASE = 'https://data.moa.gov.tw/api/v1/'
SOURCE = 'https://data.moa.gov.tw/api.aspx'
_LOCK = Lock()
_RETRY = {}
_LAST_REQUEST = 0.0


class RedactMoaKey(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        record.msg = re.sub(r'([?&]api_key=)[^&\s"\']+', r'\1[redacted]', message, flags=re.I)
        record.args = ()
        return True


# httpx 會在 INFO 印 request URL；上游要求 query key，必須先遮蔽再交給 handler。
logging.getLogger('httpx').addFilter(RedactMoaKey())


def cache_root():
    return settings.raw_data_dir.parent / 'moa'


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    temporary.replace(path)


def dataset(endpoint, params, *, ttl, validate, max_pages=8, now=None, client=None, db=None, refresh=False, request_params=None):
    """回傳完整性、取得時間與快照 ID；日誌不記原始錯誤（可能包含 api_key）。"""
    now = now or datetime.now(timezone.utc)
    identity = json.dumps([endpoint, params], sort_keys=True, ensure_ascii=False)
    key = hashlib.sha256(identity.encode()).hexdigest()
    from app.services.moa_store import DatabaseStore
    store = DatabaseStore(db) if db is not None else None
    if store and not refresh:
        # 互動查詢完全不連外；上游故障、限額或同步較慢，不會阻塞田間操作。
        result = store.read(key, now)
        if not result.get('complete') and endpoint == 'AgriProductsTransType':
            from app.models.moa import MoaDataset
            if store.state(key) is None:
                db.add(MoaDataset(key=key, endpoint=endpoint, params=params)); db.commit()
            result = store.previous_market(params, now) or result
        return result
    root = cache_root()
    pointer = root / 'current' / (key + '.json')
    with _LOCK:
        cached = None
        if store:
            cached = store.load(key)
        else:
            try:
                cached = json.loads(pointer.read_text())
                if not cached.get('complete'): cached = None
            except (OSError, ValueError): pass
        if cached and now.timestamp() < cached['refresh_after']:
            return {**cached, 'cached': True, 'stale': False, 'status': 'ok', 'error': None}
        if key in _RETRY and now.timestamp() < _RETRY[key][0]:
            return fallback(cached, _RETRY[key][1])
        try:
            pages, rows, complete = [], [], False
            seen = set()
            owned = client is None
            http = client or httpx.Client(timeout=15, verify=tls_context(), follow_redirects=False)
            try:
                for page in range(1, max_pages + 1):
                    query = dict(request_params if request_params is not None else params)
                    if page > 1: query['Page'] = str(page)
                    secret = settings.moa_api_key.get_secret_value().strip()
                    if secret: query['api_key'] = secret
                    if owned:
                        global _LAST_REQUEST
                        wait = 1.5 - (time.monotonic() - _LAST_REQUEST)
                        if wait > 0: time.sleep(wait)
                        _LAST_REQUEST = time.monotonic()
                    response = http.get(BASE + endpoint + '/', params=query)
                    if response.status_code == 429: raise MoaDataError('rate_limited')
                    if response.status_code in (401, 403): raise MoaDataError('key_required')
                    response.raise_for_status()
                    body = response.json()
                    batch, more = unwrap(body)
                    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                    if digest in seen or (more and not batch): raise MoaDataError('pagination_error')
                    seen.add(digest); pages.append(body); rows.extend(batch)
                    if not more:
                        complete = True; break
            finally:
                if owned: http.close()
            if not complete: raise MoaDataError('partial')
            parsed = validate(rows)
            if cached and cached['row_count'] >= 10 and len(rows) <= cached['row_count'] / 2:
                raise MoaDataError('schema_drift')
            # 不同查詢可能回相同內容（尤其空陣列），識別碼也要綁定查詢，避免快照來源串錯。
            payload = dict(endpoint=endpoint, params=params, pages=pages)
            if request_params is not None: payload['upstream_params'] = request_params
            sha = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            stamp = now.isoformat()
            result = dict(data=parsed, fetched_at=stamp, refresh_after=(now + ttl).timestamp(), complete=True,
                          row_count=len(rows), snapshot_id=sha, source_url=SOURCE, endpoint=endpoint, params=params)
            if request_params is not None: result['upstream_params'] = request_params
            # 原始頁面不可變，便於解析器改版時離線重播；pointer 只在完整驗證後取代。
            if store:
                result = store.save(key, result, pages)
            else:
                raw_file = root / 'snapshots' / f'{sha}.json'
                if not raw_file.exists(): atomic_json(raw_file, {**payload, 'fetched_at': stamp})
                atomic_json(pointer, result)
            _RETRY.pop(key, None)
            return {**result, 'status': 'ok', 'cached': False, 'stale': False, 'error': None}
        except (MoaDataError, httpx.HTTPError, ValueError, TypeError, KeyError, OSError) as exc:
            error = exc.status if isinstance(exc, MoaDataError) else 'unavailable'
            if store: store.failed(key, endpoint, params, now, error, pages)
            _RETRY[key] = (now.timestamp() + 120, error)
            return fallback(cached, error)


def fallback(cached, error):
    return {**(cached or {}), 'data': cached['data'] if cached else None, 'status': 'stale' if cached else error,
            'cached': bool(cached), 'stale': bool(cached), 'error': error,
            'complete': bool(cached), 'fetched_at': cached.get('fetched_at') if cached else None,
            'snapshot_id': cached.get('snapshot_id') if cached else None, 'source_url': SOURCE}


def public_meta(result):
    return {k: result.get(k) for k in ('status', 'cached', 'stale', 'error', 'complete', 'fetched_at', 'snapshot_id', 'source_url', 'storage')}


def market(crop, days=7, *, today=None, client=None, db=None, refresh=False):
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    start = today - timedelta(days=days-1)
    roc = lambda d: f'{d.year-1911:03}.{d.month:02}.{d.day:02}'
    query = crop_query(crop)
    result = dataset('AgriProductsTransType', {'Start_time': roc(start), 'End_time': roc(today), 'CropName': query},
                     ttl=timedelta(minutes=30), validate=lambda rows: parse_prices(rows, start.isoformat(), today.isoformat()), client=client, db=db, refresh=refresh)
    data = deepcopy(result.get('data') or dict(rows=[], closed=[], ignored_rows=0))
    # 上游 CropName 搜尋並非品種等同：前端仍須選明確品項、市場，禁止把不同品種合成均價。
    from app.services.moa_parser import trade_date
    actual = result.get('params') or {}
    return {**public_meta(result), **data, 'query': query, 'requested_crop': crop,
            'from': trade_date(actual.get('Start_time')) or start.isoformat(), 'to': trade_date(actual.get('End_time')) or today.isoformat(),
            'requested_from': start.isoformat(), 'requested_to': today.isoformat(),
            'price_unit': '元／公斤', 'volume_unit': '公斤', 'key_configured': bool(settings.moa_api_key.get_secret_value()),
            'notice': '批發市場成交行情，不是農場收購價。休市不視為零元，不同品項或市場不混算；不據此自動改變採收或施藥日期。'}


def costs(crop='', *, client=None, db=None, refresh=False):
    result = dataset('ProductCost', {}, ttl=timedelta(hours=24), validate=parse_costs, client=client, db=db, refresh=refresh)
    query = crop_query(crop)
    documents = result.get('data') or []
    # 此處僅為文件名稱搜尋，包含「設施／露天」等不同調查條件，必須由使用者核對。
    matches = [r for r in documents if not query or query in r['title']]
    return {**public_meta(result), 'documents': matches, 'query': query, 'total_documents': len(documents),
            'key_configured': bool(settings.moa_api_key.get_secret_value()),
            'notice': '此 API 提供年度文件連結，可能是調查表、填表範例或說明；不是每公頃成本數據，也不是您農場的實際成本。請先核對文件年度、作物及栽培方式。'}
