"""SQLite 記憶體 DB 與 MockTransport：斷網、分頁失敗仍讀舊快照，沒有 PostgreSQL 或官方網路依賴。"""
from datetime import datetime,timedelta,timezone,date
import json
from pathlib import Path
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,select,func,event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.models.moa import MoaDataset,MoaSnapshot,MoaRecord
from app.services import moa
from app.services.moa_catalog import CATALOG,parse_catalog,weather_number,observed_state
from app.services.moa_store import DatabaseStore,dataset_key
from app.api.v1 import agri_data
from app.db.session import get_db

NOW=datetime(2026,9,21,3,tzinfo=timezone.utc)
NAME='ImportantAgriculturalPestDiagnosticsType'

def row(name='試驗害蟲',i='T'):
    return dict(ID=i,PestName_Ch=name,Crop_Name='試驗作物',ReceiptDate='2011-01-01')

@pytest.fixture
def db(monkeypatch,tmp_path):
    from pydantic import SecretStr
    monkeypatch.setattr(moa.settings,'moa_api_key',SecretStr(''))
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def fk(conn,_):conn.execute('PRAGMA foreign_keys=ON')
    for model in (MoaSnapshot,MoaDataset,MoaRecord):model.__table__.create(engine)
    monkeypatch.setattr(moa,'_RETRY',{})
    monkeypatch.setattr(moa.settings,'raw_data_dir',tmp_path/'raw')
    with sessionmaker(bind=engine,expire_on_commit=False)() as db:yield db
    engine.dispose()

def publish(db,rows,endpoint=NAME,now=NOW):
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=rows))) as client:
        return moa.dataset(endpoint,{},db=db,refresh=True,ttl=timedelta(hours=24),now=now,client=client,validate=lambda r:parse_catalog(endpoint,r))

def client_for(db):
    app=FastAPI();app.include_router(agri_data.router)
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[agri_data.current_user]=lambda:object()
    return TestClient(app)

def test_database_query_never_calls_upstream_even_when_expired(db):
    saved=publish(db,[row()]);assert saved['complete']
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('本地讀取不應連上游'))) as client:
        current=moa.dataset(NAME,{},ttl=timedelta(hours=1),validate=lambda r:r,db=db,client=client,now=NOW+timedelta(days=3))
    assert current['data']==saved['data'] and current['status']=='stale'
    assert current['snapshot_id']==saved['snapshot_id']
    assert current['storage']=='database'
    snap=db.get(MoaSnapshot,saved['snapshot_id']);assert snap.raw_pages==[[row()]]
    assert db.scalar(select(func.count()).select_from(MoaRecord))==1

def test_cold_database_does_not_claim_empty_complete_or_request_api(db):
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('查詢不可偷偷同步'))) as client:
        result=moa.costs(db=db,client=client)
    assert result['status']=='not_synced' and not result['complete']
    response=client_for(db).get(f'/agri-data/{NAME}/records').json()
    assert response['source']['available'] is False and response['source']['status']=='not_synced'

@pytest.mark.parametrize('mode,error',[('member','key_required'),('quota','rate_limited'),('schema','schema_drift'),('shrink','schema_drift'),('half','schema_drift'),('partial','partial')])
def test_failed_refresh_retains_active_and_failed_raw_receipt(db,mode,error):
    original=publish(db,[row(i=str(i)) for i in range(10)])
    def handler(request):
        if mode=='quota':return httpx.Response(429)
        if mode=='schema':return httpx.Response(200,json=[{}])
        if mode=='shrink':return httpx.Response(200,json=[row()])
        if mode=='half':return httpx.Response(200,json=[row(i=str(i)) for i in range(5)])
        if mode=='member' and request.url.params.get('Page')=='2':return httpx.Response(200,json={'RS':'ERROR','MSG':'非會員只限回傳第一頁資料'})
        return httpx.Response(200,json={'RS':'OK','Data':[row()],'Next':True})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bad=moa.dataset(NAME,{},db=db,refresh=True,ttl=timedelta(hours=1),now=NOW+timedelta(days=1),client=client,
                        max_pages=1 if mode=='partial' else 2,validate=lambda rows:parse_catalog(NAME,rows))
    assert bad['stale'] and bad['error']==error and bad['snapshot_id']==original['snapshot_id']
    key=dataset_key(NAME,{})
    assert db.get(MoaDataset,key).active_snapshot_id==original['snapshot_id']
    failed=db.scalar(select(MoaSnapshot).where(MoaSnapshot.complete.is_(False)))
    assert failed.error==error and failed.id!=original['snapshot_id']
    read=DatabaseStore(db).read(key,NOW+timedelta(days=2))
    assert read['data']==original['data'] and read['error']==error

def test_complete_new_snapshot_changes_pointer_and_preserves_history(db):
    old=publish(db,[row('害蟲甲')])
    new=publish(db,[row('害蟲乙')],now=NOW+timedelta(days=2))
    assert new['snapshot_id']!=old['snapshot_id']
    assert db.get(MoaSnapshot,old['snapshot_id']).data==[row('害蟲甲')]
    assert DatabaseStore(db).load(dataset_key(NAME,{}))['data']==[row('害蟲乙')]

def test_database_restart_does_not_lose_saved_data_or_failure(db):
    old=publish(db,[row()]);db.expire_all()
    moa._RETRY.clear()
    assert DatabaseStore(db).read(dataset_key(NAME,{}),NOW)['snapshot_id']==old['snapshot_id']

def test_pesticide_search_is_exact_and_never_matches_compound(db):
    def drug(name,permit):return dict(Permit='測試字',PermitNumber=permit,ChineseName=name,ExpireDate='100-01-01',RevocationType='撤銷',contents='試驗含量',formCode='測試劑型')
    publish(db,[drug('試驗甲','01'),drug('乙試驗甲','02')],endpoint='PesticideDataQueryType')
    c=client_for(db);data=c.get('/agri-data/PesticideDataQueryType/records',params={'q':'試驗甲'}).json()
    assert data['total']==1 and data['items'][0]['values']['ChineseName']=='試驗甲'
    assert data['items'][0]['values']['RevocationType']=='撤銷'
    assert c.get('/agri-data/PesticideDataQueryType/records',params={'q':'試驗'}).json()['total']==0
    assert c.get('/agri-data/PesticideDataQueryType/records',params={'q':'測試字01'}).json()['total']==1

def test_catalog_search_pagination_and_incomplete_distinction(db):
    publish(db,[row('試驗害蟲',str(i)) for i in range(23)])
    c=client_for(db)
    result=c.get(f'/agri-data/{NAME}/records',params={'q':'試驗作物','page':2}).json()
    assert result['total']==23 and len(result['items'])==3
    assert c.get(f'/agri-data/{NAME}/records',params={'q':'%'}).json()['total']==0
    all_data=c.get('/agri-data/datasets').json()
    assert len(all_data)==14 and sum(d['available'] for d in all_data)==1
    assert c.get('/agri-data/unknown/records').status_code==404
    assert c.get(f'/agri-data/{NAME}/records?page=0').status_code==422

def test_weather_sentinels_negative_temperature_and_age():
    assert weather_number('-99','TEMP') is None
    assert weather_number('（儀器校驗中）','TEMP') is None
    assert weather_number('-3','TEMP')==-3
    assert weather_number('-3','RAIN') is None
    assert weather_number('0','RAIN')==0
    assert observed_state({'TIME':'2026/09/21 10:00'},NOW)=='recent'
    assert observed_state({'TIME':'2011/01/01 10:00'},NOW)=='outdated'
    assert observed_state({'TIME':''},NOW)=='unknown'

def test_previous_market_survives_day_rollover_without_claiming_current_date(db):
    body=json.loads((Path(__file__).parent/'fixtures/moa/market.json').read_text())
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body))) as client:
        old=moa.market('番茄',db=db,refresh=True,client=client,today=date(2026,9,21))
    with httpx.Client(transport=httpx.MockTransport(lambda _:pytest.fail('跨日仍不可連外'))) as client:
        result=moa.market('番茄',db=db,client=client,today=date(2026,9,22))
    assert result['rows']==old['rows'] and result['snapshot_id']==old['snapshot_id']
    assert result['error']=='period_outdated' and result['to']=='2026-09-21' and result['requested_to']=='2026-09-22'

def test_import_validated_file_cache_before_network_refresh(db):
    from app.services.moa_sync import import_files
    body=json.loads((Path(__file__).parent/'fixtures/moa/cost.json').read_text())
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=body))) as client:old=moa.costs(client=client)
    assert import_files(db)==1 and import_files(db)==0
    result=moa.costs(db=db)
    assert result['documents']==old['documents'] and result['storage']=='database'

@pytest.mark.parametrize('name',list(CATALOG))
def test_all_fourteen_real_fixtures_preserve_unknown_fields(name):
    raw=json.loads((Path(__file__).parent/'fixtures/moa/reference'/f'{name}.json').read_text())
    parsed=parse_catalog(name,raw)
    assert len(parsed)==len(raw)>0
    assert set(parsed[0])==set(raw[0])
    if name=='PestDiseaseDiagnosisServiceType':
        assert parsed[0]['Question'] and parsed[0]['Answer'] and parsed[0]['Provision']


def test_job_reports_sync_failure_to_scheduler(db,monkeypatch):
    from app.jobs import moa_sync
    monkeypatch.setattr('app.services.moa_sync.sync',lambda db:{'fixture':'key_required'})
    with pytest.raises(RuntimeError,match='未完整成功'):moa_sync(db)


def test_api_reads_even_when_network_is_disabled(db,monkeypatch):
    publish(db,[row()])
    monkeypatch.setattr(httpx.Client,'get',lambda *a,**k:pytest.fail('不應打上游 HTTP'))
    # TestClient 走 request，而業務端的同步 transport 才會使用 httpx.Client.get。
    response=client_for(db).request('GET',f'/agri-data/{NAME}/records')
    assert response.status_code==200 and response.json()['total']==1


def test_weather_window_is_bound_to_snapshot_but_local_reads_share_stable_key(db):
    name='AutoWeatherStationType';window={'Start_time':'2026/09/20 11:00','End_time':'2026/09/21 11:00'}
    rows=[dict(Station_ID='T',TIME='2026/09/21 10:00',TEMP='29',CITY='試驗市')]
    def handler(request):
        assert dict(request.url.params)==window
        return httpx.Response(200,json=rows)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result=moa.dataset(name,{},db=db,refresh=True,client=client,now=NOW,request_params=window,
            ttl=timedelta(hours=1),validate=lambda r:parse_catalog(name,r))
    snap=db.get(MoaSnapshot,result['snapshot_id'])
    assert snap.params==window and snap.dataset_key==dataset_key(name,{})
    assert client_for(db).get(f'/agri-data/{name}/records').json()['source']['scope']==window
