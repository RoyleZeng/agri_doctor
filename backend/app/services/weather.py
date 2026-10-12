"""CWA F-C0032-001 forecast; server-side secret and conservative care cues."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Lock
from zoneinfo import ZoneInfo
import httpx

from app.core.config import settings
from app.crawlers.base import tls_context

COUNTIES = ['基隆市','臺北市','新北市','桃園市','新竹市','新竹縣','苗栗縣','臺中市','彰化縣','南投縣','雲林縣','嘉義市','嘉義縣','臺南市','高雄市','屏東縣','宜蘭縣','花蓮縣','臺東縣','澎湖縣','金門縣','連江縣']
DATASET = 'F-C0032-001'
API_URL = 'https://opendata.cwa.gov.tw/api/v1/rest/datastore/' + DATASET
SOURCE_URL = 'https://opendata.cwa.gov.tw/dataset/all/' + DATASET
TAIPEI = ZoneInfo('Asia/Taipei')
_CACHE = {}
_FETCH_LOCK = Lock()


def county_from_location(value):
    value = (value or '').replace('台', '臺')
    matches = [c for c in COUNTIES if c in value]
    return matches[0] if len(matches) == 1 else None


def timestamp(value):
    value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return value.replace(tzinfo=TAIPEI) if value.tzinfo is None else value.astimezone(TAIPEI)


def parse_forecast(body, county, now=None):
    now = now or datetime.now(TAIPEI)
    if str(body.get('success')).lower() != 'true':
        raise ValueError('CWA response unsuccessful')
    locations = body.get('records', {}).get('location', [])
    matches = [r for r in locations if r.get('locationName') == county]
    if len(matches) != 1:
        raise ValueError('CWA location mismatch')
    indexed = {}
    for element in matches[0].get('weatherElement', []):
        name = element.get('elementName')
        if name not in ('Wx','PoP','MinT','MaxT'):
            continue
        for item in element.get('time', []):
            start, end = timestamp(item['startTime']), timestamp(item['endTime'])
            if end <= start or end - start > timedelta(hours=24):
                raise ValueError('Invalid forecast interval')
            key = (start.isoformat(), end.isoformat())
            row = indexed.setdefault(key, {'start_at':key[0], 'end_at':key[1], 'description':None,
                'rain_probability':None, 'min_temp':None, 'max_temp':None})
            raw = item.get('parameter', {}).get('parameterName')
            if name == 'Wx':
                row['description'] = str(raw)[:200] if raw is not None else None
            else:
                try:
                    value = float(raw)
                except (ValueError, TypeError):
                    continue  # Missing is not zero.
                low, high = (0,100) if name == 'PoP' else (-30,60)
                if not low <= value <= high:
                    continue
                row[{'PoP':'rain_probability','MinT':'min_temp','MaxT':'max_temp'}[name]] = value
    periods = sorted(indexed.values(), key=lambda r:r['start_at'])
    if not periods:
        raise ValueError('No usable forecast intervals')
    if any(r['min_temp'] is not None and r['max_temp'] is not None and r['min_temp'] > r['max_temp'] for r in periods):
        raise ValueError('Invalid temperature range')
    periods = [r for r in periods if timestamp(r['end_at']) > now and timestamp(r['start_at']) < now + timedelta(hours=48)]
    if not periods:
        return {'status':'expired', 'periods':[], 'advice':[], 'message':'氣象署回傳的預報已過期，目前不產生天氣建議。'}
    advice = []
    rain = [r for r in periods if r['rain_probability'] is not None and r['rain_probability'] >= 60]
    hot = [r for r in periods if r['max_temp'] is not None and r['max_temp'] >= 33]
    cold = [r for r in periods if r['min_temp'] is not None and r['min_temp'] <= 12]
    if rain:
        advice.append({'key':'rain', 'title':'先確認田區排水', 'text':'巡視排水溝與低窪處，確認設施固定；露天作業安排前再核對現地天候。',
            'basis':'預報降雨機率達60%以上；不是降雨量，也不是病害發生機率。', 'periods':[r['start_at'] for r in rain]})
    if hot:
        advice.append({'key':'heat', 'title':'調整巡田時段、留意苗株', 'text':'可將巡田安排在較涼時段，觀察萎凋及土壤水分後，再決定是否需要調整供水或遮蔭。',
            'basis':'預報最高溫達33°C以上；此為平台照護門檻，並非氣象署高溫特報。', 'periods':[r['start_at'] for r in hot]})
    if cold:
        advice.append({'key':'cold', 'title':'確認幼苗與設施保溫', 'text':'依作物品種與實際生育狀況，檢查幼苗保護及設施開口。',
            'basis':'預報最低溫達12°C以下；此為平台照護門檻，不等於作物已受寒害。', 'periods':[r['start_at'] for r in cold]})
    return {'status':'ok', 'periods':periods, 'advice':advice,
        'message':'縣市預報供規劃參考；未涵蓋田區微氣候、風速、雨量及葉面濕度，不能據此判定適合施藥或預測發病。'}


def get_forecast(county, *, now=None, client=None):
    now = now or datetime.now(TAIPEI)
    if county not in COUNTIES:
        raise ValueError('請選擇臺灣縣市')
    base = {'county':county, 'source':'中央氣象署', 'dataset':DATASET, 'source_url':SOURCE_URL,
        'resolution':'縣市・今明36小時', 'fetched_at':None, 'issued_at':None, 'cached':False,
        'rules_version':'care-cues-2026-09-08', 'periods':[], 'advice':[]}
    key = settings.cwa_api_key.get_secret_value().strip()
    if not key:
        return {**base,'status':'not_configured','message':'尚未連接氣象署預報。管理者設定 API 授權碼後即可顯示，現在不提供即時天氣建議。'}
    # Fetch all 22 counties together, then share one cache across farm users.
    # At most 96 normal refreshes/day/process, rather than 22 x 96 requests.
    with _FETCH_LOCK:
        cached = _CACHE.get('raw')
        hit = bool(cached and now < cached['retry_at'])
        if not hit:
            try:
                if client is None:
                    with httpx.Client(timeout=12, verify=tls_context(), follow_redirects=False) as http:
                        response = http.get(API_URL, headers={'Authorization':key}, params={'format':'JSON'})
                else:
                    response = client.get(API_URL, headers={'Authorization':key}, params={'format':'JSON'})
                response.raise_for_status()
                body = response.json()
                if str(body.get('success')).lower() != 'true':
                    raise ValueError('CWA response unsuccessful')
                cached = {'body':body,'fetched_at':now.isoformat(),'retry_at':now+timedelta(minutes=15)}
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                cached = {'body':None,'fetched_at':None,'retry_at':now+timedelta(minutes=2)}
            _CACHE['raw'] = cached
        try:
            if cached['body'] is None:
                raise ValueError('No current source data')
            parsed = parse_forecast(cached['body'], county, now)
            result = {**base, **parsed, 'fetched_at':cached['fetched_at'], 'cached':hit}
        except (ValueError, KeyError, TypeError):
            result = {**base, 'status':'unavailable', 'message':'暫時無法取得有效氣象署預報，請查看官方天氣頁；目前不產生天氣建議。'}
        return deepcopy(result)
