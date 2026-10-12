"""農業部行情與成本：存檔與 MockTransport 離線重播，不連網、不依賴 DB。"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import httpx
from pydantic import SecretStr
import pytest
from app.services import moa
from app.services.moa_parser import MoaDataError, unwrap, number, trade_date, parse_prices, parse_costs, document_url

FIXTURES = Path(__file__).parent / 'fixtures/moa'
NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)

def read(name): return json.loads((FIXTURES / f'{name}.json').read_text())

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(moa.settings, 'raw_data_dir', tmp_path / 'raw')
    monkeypatch.setattr(moa.settings, 'moa_api_key', SecretStr(''))
    monkeypatch.setattr(moa, '_RETRY', {})

def fetch(handler, now=NOW, **kwargs):
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return moa.dataset('fixture', {}, ttl=timedelta(minutes=30), validate=lambda rows: rows, now=now, client=client, **kwargs)

@pytest.mark.parametrize('raw', [None, '', '未載明', 'NaN', 'Infinity', '-1', True])
def test_missing_is_not_zero(raw): assert number(raw) is None

def test_real_saved_responses():
    rows, more = unwrap(read('market'))
    assert more is False
    parsed = parse_prices(rows, '2026-09-15', '2026-09-21')
    assert parsed['rows'] and parsed['closed']
    assert all(r['crop'] != '休市' for r in parsed['rows'])
    assert all(r['date'].startswith('2026-09') for r in parsed['rows'])
    costs = parse_costs(unwrap(read('cost'))[0])
    assert len(costs) == 792
    assert {r['kind'] for r in costs} == {'填表範例', '填表說明', '調查表', '成本相關文件'}
    assert any(r['url'] is None for r in costs)
    assert any('番茄' in r['title'] for r in costs)

def test_dates_quantity_and_conflicting_rows():
    assert trade_date('115.09.21') == trade_date('2026/09/21') == '2026-09-21'
    assert trade_date('115.02.30') is None
    assert number('1,200.5') == 1200.5
    row = dict(TransDate='115.09.21', CropCode='T', CropName='測試作物', MarketCode='X', MarketName='測試市場', Avg_Price=15, Trans_Quantity=0)
    item = parse_prices([row, row], '2026-09-21', '2026-09-21')['rows'][0]
    assert item['average'] is None and item['volume_kg'] == 0
    with pytest.raises(MoaDataError, match='schema_drift'):
        parse_prices([row, {**row, 'Trans_Quantity': 10}], '2026-09-21', '2026-09-21')
    with pytest.raises(MoaDataError, match='schema_drift'):
        parse_prices([{}], '2026-09-21', '2026-09-21')

@pytest.mark.parametrize('value', ['javascript:alert(1)', 'https://evil.test/x.pdf', 'https://agrcost.afa.gov.tw.evil.test/a.pdf', 'http://agrcost.afa.gov.tw/', '有附件', 'https://u:p@agrcost.afa.gov.tw/a.pdf'])
def test_cost_link_is_official_document(value): assert document_url(value) is None

def test_cost_example_not_misclassified_as_cost_amount():
    docs = parse_costs([{'作物名稱': '測試生產成本調查表_範例', '年度': '', '群組': '雜糧', '成本檔URL': 'http://agrcost.afa.gov.tw/data/test.pdf'}])
    assert docs[0]['kind'] == '填表範例'
    assert docs[0]['year'] is None and docs[0]['url'].endswith('.pdf')

@pytest.mark.parametrize('body,status', [({'RS':'每日超過上限'},'rate_limited'), ({'RS':'API_KEY required'},'key_required'), ({'RS':'OK','Data':[]},'schema_drift')])
def test_upstream_error_not_empty_result(body, status):
    with pytest.raises(MoaDataError) as error: unwrap(body)
    assert error.value.status == status

def test_pagination_ttl_and_secret_not_saved(monkeypatch, caplog):
    monkeypatch.setattr(moa.settings, 'moa_api_key', SecretStr('test-secret-never-persist'))
    requests = []
    def handler(request):
        requests.append(request)
        assert request.url.params['api_key'] == 'test-secret-never-persist'
        page = int(request.url.params.get('Page', '1'))
        return httpx.Response(200, json={'RS':'OK','Data':[{'page':page}],'Next':page == 1})
    with caplog.at_level(logging.INFO, logger='httpx'): result = fetch(handler)
    assert result['data'] == [{'page':1},{'page':2}]
    assert result['complete'] and not result['stale'] and len(requests) == 2
    again = fetch(lambda _: pytest.fail('TTL 內不應呼叫上游'))
    assert again['cached'] and again['snapshot_id'] == result['snapshot_id']
    assert 'test-secret-never-persist' not in caplog.text
    for file in moa.cache_root().rglob('*.json'): assert 'test-secret-never-persist' not in file.read_text()

@pytest.mark.parametrize('mode,error', [('quota','rate_limited'),('shrink','schema_drift'),('repeat','pagination_error'),('pages','partial')])
def test_failed_refresh_keeps_exact_snapshot(mode, error):
    first = fetch(lambda _: httpx.Response(200,json=[{'id':i} for i in range(10)]))
    original = {f: f.read_bytes() for f in moa.cache_root().rglob('*.json')}
    def handler(request):
        if mode == 'quota': return httpx.Response(429)
        if mode == 'shrink': return httpx.Response(200,json=[{'id':1}])
        return httpx.Response(200,json={'RS':'OK','Data':[{'id':1 if mode == 'repeat' else request.url.params.get('Page','1')}],'Next':True})
    failed = fetch(handler, now=NOW+timedelta(hours=1), max_pages=2)
    assert failed['stale'] and failed['error'] == error
    assert failed['data'] == first['data'] and failed['snapshot_id'] == first['snapshot_id']
    assert failed['fetched_at'] == first['fetched_at']
    assert {f:f.read_bytes() for f in moa.cache_root().rglob('*.json')} == original
    fetch(lambda _: pytest.fail('失敗重試間隔未到不應重抓'), now=NOW+timedelta(hours=1,seconds=30))

def test_initial_failure_has_no_published_data():
    result = fetch(lambda _: httpx.Response(200,json={'RS':'OK','Data':[{'x':1}],'Next':True}),max_pages=1)
    assert result['status'] == 'partial' and not result['complete'] and result['data'] is None
    assert not list(moa.cache_root().rglob('*.json'))

def test_equal_payloads_keep_distinct_query_provenance():
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,json=[]))) as client:
        a, b = [moa.dataset('fixture', {'crop':crop}, ttl=timedelta(minutes=1), validate=lambda rows: rows, client=client)
                for crop in ('作物甲','作物乙')]
    assert a['snapshot_id'] != b['snapshot_id']
    for result in (a,b):
        raw = json.loads((moa.cache_root()/'snapshots'/f"{result['snapshot_id']}.json").read_text())
        assert raw['params'] == result['params']

def test_market_dates_alias_units_and_cost_api():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200,json=read('market' if 'AgriProducts' in request.url.path else 'cost'))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        market = moa.market('蕃茄',today=date(2026,9,21),client=client)
        costs = moa.costs('蕃茄',client=client)
    assert requests[0].url.params['Start_time'] == '115.09.15'
    assert requests[0].url.params['End_time'] == '115.09.21'
    assert requests[0].url.params['CropName'] == '番茄'
    assert set(requests[0].url.params) == {'Start_time','End_time','CropName'}
    assert market['price_unit'] == '元／公斤' and market['rows']
    assert costs['documents'] and all('番茄' in r['title'] for r in costs['documents'])

def test_api_validates_query_and_uses_session(monkeypatch):
    from app.api.v1.economics import router, current_user
    app = FastAPI(); app.include_router(router)
    app.dependency_overrides[current_user] = lambda: {'role':'farmer'}
    monkeypatch.setattr(moa,'market',lambda crop, days, **kwargs: {'query':crop,'days':days})
    monkeypatch.setattr(moa,'costs',lambda crop, **kwargs: {'query':crop})
    client = TestClient(app)
    assert client.get('/economics/market',params={'crop':' 番茄 '}).json() == {'query':'番茄','days':7}
    for params in ({'crop':' '},{'crop':'x','days':15},{'crop':'x'*61}):
        assert client.get('/economics/market',params=params).status_code == 422
    assert client.get('/economics/costs').json() == {'query':''}
    def denied(): raise HTTPException(401)
    app.dependency_overrides[current_user] = denied
    assert client.get('/economics/market?crop=x').status_code == 401
    assert client.get('/economics/costs').status_code == 401

def test_offline_archive_covers_all_paths_and_checksums(tmp_path):
    from scripts.archive_moa_docs import build, FILES
    archive = Path(__file__).resolve().parents[2] / 'docs/external/moa/snapshots/20260921T032546Z'
    raw = {name:(archive/'raw'/name).read_bytes() for name in FILES}
    manifest = build(raw,tmp_path/'archive')
    saved_manifest = json.loads((archive/'manifest.json').read_text())
    assert manifest['files'] == saved_manifest['files']
    spec = json.loads(raw['openapi.json'])
    catalog = json.loads((tmp_path/'archive/catalog.json').read_text())
    assert {r['path'] for r in catalog} == set(spec['paths'])
    assert manifest['paths'] == 60 and manifest['document_entries'] == 61
    assert len(catalog) == 60 and manifest['duplicate_document_paths']
    for item in manifest['files']:
        assert hashlib.sha256((archive/item['file']).read_bytes()).hexdigest() == item['sha256']
    for item in catalog: assert (tmp_path/'archive'/item['file']).is_file()
