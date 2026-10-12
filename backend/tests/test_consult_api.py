"""以記憶體 SQLite 驗證路由與快照，無需啟動 PostgreSQL 或連接官方來源。"""
from datetime import date,datetime,timezone
from copy import deepcopy
import hashlib
import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine,event,select,func,delete
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from app.db.base import Base
from app.db.session import get_db
from app.models import (Crop,Pest,Product,ProductIngredient,ActiveIngredient,Registration,
    SourceSnapshot,SourceRecord,SourceName,SnapshotStatus,CropAlias)
from app.models.planning import FarmAccount,FarmSession,DoctorFarmer,FarmPlan
from app.models.consult import ConsultCase,PrescriptionItem,ApplicationRecord
from app.api.v1 import planning,consult,registrations
from app.services import consult as service

@compiles(JSONB,'sqlite')
def jsonb_sqlite(element,compiler,**kw): return 'JSON'

TODAY=date(2030,3,1)

@pytest.fixture
def system(monkeypatch):
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    @event.listens_for(engine,'connect')
    def foreign_keys(conn,rec): conn.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False,autoflush=False)
    app=FastAPI()
    for router in (planning.router,consult.router,registrations.router): app.include_router(router,prefix='/api/v1')
    def get_session():
        with factory() as db: yield db
    app.dependency_overrides[get_db]=get_session
    monkeypatch.setattr(service,'taipei_today',lambda:TODAY)
    monkeypatch.setattr(consult,'taipei_today',lambda:TODAY)
    planning._attempts.clear()
    with factory() as db:
        for id,role in [('doc','doctor'),('other','doctor'),('farmer','farmer'),('stranger','farmer')]:
            db.add(FarmAccount(id=id,username=id,name=id,role=role,password_hash='unused'))
        db.flush()
        for id in ['doc','other','farmer','stranger']:
            db.add(FarmSession(token_hash=planning.digest(id+'-token'),account_id=id,expires_at=int(time.time())+10000))
        db.add(DoctorFarmer(doctor_id='doc',farmer_id='farmer'))
        db.add_all([Crop(id=1,name='試驗作物甲'),Crop(id=2,name='試驗作物乙'),Pest(id=1,name='試驗病害甲'),Pest(id=2,name='試驗害蟲乙')])
        db.add_all([SourceSnapshot(id=1,source=SourceName.PPM,status=SnapshotStatus.SUCCESS,is_active=True,row_count=3,started_at=datetime.now(timezone.utc)),
                    SourceSnapshot(id=2,source=SourceName.FDA,status=SnapshotStatus.SUCCESS,is_active=True,row_count=1,started_at=datetime.now(timezone.utc))])
        db.flush()
        db.add(CropAlias(crop_id=1,alias='田間作物甲'))
        db.add(ActiveIngredient(id=1,name='試驗成分甲',english_name='synthetic-alpha'))
        db.add(Product(id=1,name='試驗單劑',concentration_text='13%',formulation='試驗劑型',english_composition='synthetic-alpha',moa_text='FRAC 7'))
        db.add(Product(id=2,name='試驗複方',concentration_text='17%',formulation='試驗劑型',english_composition='synthetic-alpha + synthetic-beta',moa_text='FRAC 7 | IRAC 8'))
        db.flush()
        db.add(ProductIngredient(product_id=1,ingredient_id=1))
        for id,crop,pest,prod in [(1,1,1,1),(2,1,2,1),(3,2,1,1),(4,2,1,2)]:
            db.add(Registration(id=id,crop_id=crop,pest_id=pest,product_id=prod,snapshot_id=1,
                usage_hash=hashlib.sha256(str(id).encode()).hexdigest(),dilution_text='2345',dose_per_hectare='0.42公升',phi_days=4,application_method='測試情境'))
        db.add(FarmPlan(id='plan',farmer_id='farmer',crop_name='田間作物甲',field_name='甲田',area='0.6',variety='試驗品種',planted_on=date(2030,1,1),end_on=date(2030,3,9)))
        db.commit()
    clients={}
    for id in ['doc','other','farmer','stranger']:
        client=TestClient(app,headers={'X-Planning-Request':'1'})
        client.cookies.set(planning.COOKIE,id+'-token',path='/api/v1')
        clients[id]=client
    yield app,factory,clients
    engine.dispose()


def create(system,**changes):
    payload={'farmer_text':'現場委託人','crop_id':1,'field_text':'甲田','expected_harvest_on':'2030-03-09'} | changes
    r=system[2]['doc'].post('/api/v1/consult/cases',json=payload)
    assert r.status_code==201,r.text
    return r.json()


def add(system,case,reg=1):
    return system[2]['doc'].post(f"/api/v1/consult/cases/{case['id']}/items",json={'registration_id':reg,'version':case['version']})


def banned(system,name='synthetic-alpha'):
    with system[1]() as db:
        db.add(SourceRecord(snapshot_id=2,source=SourceName.FDA,record_kind='fda_banned',row_hash='b'*64,
            payload={'name':'禁止試驗成分','english_name':name,'banned_sale_date':'已生效'}));db.commit()


def test_routes_require_doctor_owner_and_csrf(system):
    app,_,clients=system
    assert TestClient(app).get('/api/v1/consult/cases').status_code==401
    assert clients['farmer'].get('/api/v1/consult/cases').status_code==403
    doc=clients['doc'];r=doc.post('/api/v1/consult/cases',json={'farmer_text':'x'},headers={'X-Planning-Request':'0'})
    assert r.status_code==403
    case=create(system);url=f"/api/v1/consult/cases/{case['id']}"
    assert clients['other'].get(url).status_code==404
    assert clients['other'].patch(url,json={'symptoms':'越權'}).status_code==404
    assert clients['other'].delete(url).status_code==404
    assert clients['other'].get('/api/v1/consult/cases').json()['total']==0


def test_plan_prefill_alias_and_mismatched_farmer_cannot_leak(system):
    case=create(system,plan_id='plan')
    assert (case['farmer_id'],case['crop_id'],case['crop_text'],case['field_text'])==('farmer',1,'田間作物甲','甲田')
    assert case['expected_harvest_on']=='2030-03-09'
    doc=system[2]['doc']
    assert doc.post('/api/v1/consult/cases',json={'plan_id':'plan','farmer_id':'stranger'}).status_code==422
    assert system[2]['other'].post('/api/v1/consult/cases',json={'plan_id':'plan'}).status_code==404
    assert doc.post('/api/v1/consult/cases',json={'farmer_id':'stranger','crop_id':1}).status_code==404


def test_drug_search_returns_registration_rows_and_other_crops(system):
    client=TestClient(system[0])
    body=client.get('/api/v1/registrations/search',params={'q':'synthetic-alpha','crop_id':1}).json()
    assert body['total']==4
    assert [r['registration_id'] for r in body['items'][:2]]==[1,2]
    other=next(r for r in body['items'] if r['registration_id']==3)
    assert other['applicable'] is False and other['can_add'] is False
    assert {c['id'] for c in other['approved_crops']}=={1,2}
    assert client.get('/api/v1/registrations/search',params={'q':'試驗成分甲'}).json()['total']==3
    assert client.get('/api/v1/registrations/search',params={'q':'%'}).json()['total']==0
    assert client.get('/api/v1/registrations/search',params={'q':'試驗單劑','offset':1,'limit':1}).json()['total']==3
    assert client.get('/api/v1/registrations/search',params={'q':'  '}).status_code==422


def test_api_blocks_ban_phi_and_wrong_crop_without_writes(system):
    case=create(system,expected_harvest_on='2030-03-03')
    r=add(system,case)
    assert r.status_code==409 and next(c for c in r.json()['detail']['checks'] if c['type']=='phi')['shortfall_days']==2
    safe=create(system)
    assert add(system,safe,3).status_code==409
    banned(system)
    r=add(system,safe)
    assert r.status_code==409 and next(c for c in r.json()['detail']['checks'] if c['type']=='banned')['level']=='block'
    with system[1]() as db: assert db.scalar(select(func.count()).select_from(PrescriptionItem))==0


def test_unknown_phi_allowed_and_snapshot_keeps_original_and_doctor_values(system):
    with system[1]() as db: db.get(Registration,1).phi_days=None;db.commit()
    case=create(system);r=add(system,case);assert r.status_code==201,r.text
    item=r.json();assert item['phi_days'] is None
    assert next(c for c in item['checks'] if c['type']=='phi')['level']=='warning'
    url=f"/api/v1/consult/cases/{case['id']}/items/{item['id']}"
    r=system[2]['doc'].patch(url,json={'version':item['case_version'],'dilution':'2654','advice':'醫師囑咐'})
    assert r.status_code==200,r.text
    edited=r.json()
    assert edited['source_values']==item['source_values'] and edited['dilution']=='2654'
    assert set(edited['modified_fields'])=={'dilution','advice'}
    assert system[2]['doc'].patch(url,json={'phi_days':0}).status_code==422
    assert system[2]['doc'].patch(url,json={'source_values':{}}).status_code==422
    assert system[2]['doc'].patch(url,json={'version':1,'dose':'x'}).status_code==409


def test_source_refresh_leaves_historical_snapshot_and_checks_unchanged(system):
    case=create(system);item=add(system,case).json();url=f"/api/v1/consult/cases/{case['id']}"
    system[2]['doc'].patch(url,json={'status':'saved'})
    before=system[2]['doc'].get(url).json()['items'][0]
    with system[1]() as db:
        db.execute(delete(Registration));db.get(Crop,1).name='後來改名';db.commit()
    banned(system)
    after=system[2]['doc'].get(url).json()['items'][0]
    assert after['registration_id'] is None
    assert after['source_values']==before['source_values'] and after['checks']==before['checks']
    assert system[2]['doc'].get(url).json()['crop_text']=='試驗作物甲'
    assert system[2]['doc'].patch(url+'/items/'+item['id'],json={'advice':'新修改'}).status_code==409


def test_rotation_scopes_farmer_and_field_and_does_not_create_actual_use(system):
    case=create(system,plan_id='plan')
    with system[1]() as db:
        for id,farmer,field,when,codes in [('a','farmer','甲田',date(2030,2,23),['FRAC 7']),('b','farmer','甲田',date(2030,2,26),['FRAC 7','IRAC 8']),('c','farmer','乙田',date(2030,2,27),['FRAC 9']),('d','stranger','甲田',date(2030,2,28),['FRAC 9'])]:
            db.add(ApplicationRecord(id=id,farmer_id=farmer,field_text=field,product_text='試驗施藥',applied_on=when,moa_codes=codes))
        db.commit()
    item=add(system,case).json()
    check=next(c for c in item['checks'] if c['type']=='rotation')
    assert check['level']=='warning' and check['record_ids']==['b','a']
    with system[1]() as db: assert db.scalar(select(func.count()).select_from(ApplicationRecord))==4


def test_case_detail_carries_case_level_rotation_scoped_like_the_item_check(system):
    """橫幅用的案件層級摘要跟逐項檢查看同一批紀錄：別人的田、別人的農民都不能算進來。"""
    case=create(system,plan_id='plan')
    with system[1]() as db:
        for id,farmer,field,when,codes in [
                ('a','farmer','甲田',date(2030,2,20),['FRAC 7']),
                ('b','farmer','甲田',date(2030,2,26),['FRAC 7']),
                ('x','farmer','乙田',date(2030,2,27),['FRAC 7']),
                ('y','stranger','甲田',date(2030,2,28),['FRAC 7'])]:
            db.add(ApplicationRecord(id=id,farmer_id=farmer,field_text=field,product_text=f'試驗施藥{id}',applied_on=when,moa_codes=codes))
        db.commit()
    add(system,case)
    detail=system[2]['doc'].get(f"/api/v1/consult/cases/{case['id']}").json()
    rotation=detail['rotation']
    # 兩筆自己田的歷史＋本次＝連續第 3 次。
    assert rotation['streak']==3 and rotation['level']=='crit'
    assert rotation['exclude_groups']==['FRAC 7']
    # 序列由舊到新、最後一筆是本次；別人的紀錄不在裡面。
    products=[s['product_text'] for s in rotation['sequence']]
    assert products==['試驗施藥a','試驗施藥b','本次處方']


def test_case_detail_rotation_is_quiet_without_history(system):
    case=create(system,plan_id='plan')
    add(system,case)
    rotation=system[2]['doc'].get(f"/api/v1/consult/cases/{case['id']}").json()['rotation']
    assert rotation['level']!='crit' and rotation['exclude_groups']==[]


def test_case_harvest_change_rechecks_atomically_and_saved_record_cannot_be_deleted(system):
    case=create(system);item=add(system,case).json();url=f"/api/v1/consult/cases/{case['id']}";doc=system[2]['doc']
    assert doc.patch(url,json={'expected_harvest_on':'2030-03-02','symptoms':'不應留下'}).status_code==409
    body=doc.get(url).json();assert body['symptoms']=='' and body['expected_harvest_on']=='2030-03-09'
    assert doc.patch(url,json={'status':'saved','symptoms':'新病徵','examined_by':['photo','sample']}).status_code==200
    assert doc.delete(url).status_code==409
    assert doc.patch(url,json={'status':'draft'}).status_code==409
    assert doc.patch(url,json={'status':'issued'}).status_code==422
    with system[1]() as db: db.get(ConsultCase,case['id']).status='issued';db.commit()
    assert doc.patch(url,json={'symptoms':'不能修改'}).status_code==409
    assert doc.delete(url+'/items/'+item['id']).status_code==409


def test_delete_items_nested_ownership_filters_and_draft_delete(system):
    first=create(system);second=create(system,crop_id=2,farmer_text='另一委託人');item=add(system,first).json();doc=system[2]['doc']
    wrong=f"/api/v1/consult/cases/{second['id']}/items/{item['id']}"
    assert doc.patch(wrong,json={'dose':'x'}).status_code==404 and doc.delete(wrong).status_code==404
    body=doc.get('/api/v1/consult/cases',params={'crop_id':2,'farmer_text':'另一','issued':False}).json()
    assert body['total']==1 and body['items'][0]['id']==second['id']
    assert doc.get('/api/v1/consult/cases',params={'date_from':'2030-03-09','date_to':'2030-03-01'}).status_code==422
    assert doc.delete(f"/api/v1/consult/cases/{first['id']}/items/{item['id']}").status_code==200
    assert doc.delete(f"/api/v1/consult/cases/{first['id']}").status_code==200
    assert doc.get(f"/api/v1/consult/cases/{first['id']}").status_code==404


def test_cookie_login_old_path_migration_and_logout_cover_consult(system):
    client=TestClient(system[0],headers={'X-Planning-Request':'1'})
    r=client.post('/api/v1/planning/register',json={'username':'new-doctor','password':'offline-test-password','name':'離線醫師','role':'doctor'})
    assert r.status_code==200
    assert client.get('/api/v1/consult/cases').status_code==200
    assert any(c.path=='/api/v1' for c in client.cookies.jar)
    assert client.post('/api/v1/planning/logout').status_code==200
    assert client.get('/api/v1/consult/cases').status_code==401
    client.cookies.set(planning.COOKIE,'doc-token',path='/api/v1/planning')
    assert client.get('/api/v1/consult/cases').status_code==401
    assert client.get('/api/v1/planning/me').status_code==200
    assert client.get('/api/v1/consult/cases').status_code==200


def test_link_revocation_preserves_own_history_but_blocks_new_farmer_access(system):
    case=create(system,plan_id='plan');item=add(system,case).json();doc=system[2]['doc'];url=f"/api/v1/consult/cases/{case['id']}"
    with system[1]() as db: db.execute(delete(DoctorFarmer));db.commit()
    assert doc.get(url).status_code==200
    assert doc.patch(url+'/items/'+item['id'],json={'advice':'不可再讀新歷史'}).status_code==404
    assert doc.post(url+'/items',json={'registration_id':2}).status_code==404


def test_ban_recheck_on_patch_uses_snapshot_components_and_rolls_back_edits(system):
    case=create(system);item=add(system,case).json();banned(system)
    url=f"/api/v1/consult/cases/{case['id']}";doc=system[2]['doc']
    r=doc.patch(url+'/items/'+item['id'],json={'dilution':'1111'})
    assert r.status_code==409
    assert doc.get(url).json()['items'][0]['dilution']=='2345'
    assert doc.get(url).json()['items'][0]['checks']==item['checks']


def test_api_no_reg_null_bypass_and_precise_registration_context(system):
    case=create(system);doc=system[2]['doc'];url=f"/api/v1/consult/cases/{case['id']}/items"
    assert doc.post(url,json={'registration_id':None,'product_text':'自行輸入藥名'}).status_code==422
    assert doc.post(url,json={'registration_id':999999}).status_code==409
    first=add(system,case);assert first.status_code==201
    assert doc.post(url,json={'registration_id':1}).status_code==409
    assert doc.post(url,json={'registration_id':2}).status_code==201
    with system[1]() as db:
        db.get(SourceSnapshot,1).is_active=False;db.commit()
    assert doc.post(url,json={'registration_id':3}).status_code==409


def test_search_ban_flag_uses_existing_compare(system):
    banned(system)
    r=TestClient(system[0]).get('/api/v1/registrations/search',params={'q':'試驗單劑','crop_id':1})
    assert r.status_code==200
    assert all(item['banned'] and not item['can_add'] for item in r.json()['items'])


def test_annotation_only_does_not_rewrite_original_assessment(system):
    case=create(system);item=add(system,case).json();banned(system)
    url=f"/api/v1/consult/cases/{case['id']}";doc=system[2]['doc']
    result=doc.patch(url,json={'symptoms':'補記當時觀察'})
    assert result.status_code==200
    assert result.json()['items'][0]['checks']==item['checks']
    assert doc.patch(url,json={'status':'saved'}).status_code==409


def test_case_list_has_prescription_summary_but_never_source_snapshot(system):
    case=create(system)
    item=add(system,case).json()
    response=system[2]['doc'].get('/api/v1/consult/cases')
    assert response.status_code==200
    listed=next(row for row in response.json()['items'] if row['id']==case['id'])
    assert 'items' not in listed
    assert listed['prescriptions']==[{
        'product_text':item['product_text'], 'dilution':item['dilution'],
        'phi_days':item['phi_days'],
        'check_level':max((check['level'] for check in item['checks']),key={'block':3,'warning':2,'ok':1}.get),
    }]
    assert 'source_values' not in str(listed)
    assert 'comparison' not in str(listed)


def test_new_capture_has_one_comparison_snapshot_and_detail_reads_legacy_snapshot(system):
    case=create(system)
    item=add(system,case).json()
    assert 'mrls' not in item['source_values']
    assert 'sources' not in item['source_values']
    assert 'mrls' in item['source_values']['comparison']
    assert 'sources' in item['source_values']['comparison']

    # 已儲存的舊資料不可因這次去重而被改寫或失去可重印內容。
    legacy=deepcopy(item['source_values'])
    legacy['mrls']=legacy['comparison']['mrls']
    legacy['sources']=legacy['comparison']['sources']
    with system[1]() as db:
        db.get(PrescriptionItem,item['id']).source_values=legacy
        db.commit()
    detail=system[2]['doc'].get(f"/api/v1/consult/cases/{case['id']}").json()
    stored=detail['items'][0]['source_values']
    assert stored['mrls']==stored['comparison']['mrls']
    assert stored['sources']==stored['comparison']['sources']


def test_issue_locks_every_case_and_item_write(system,monkeypatch):
    case=create(system);item=add(system,case).json();doc=system[2]['doc'];url=f"/api/v1/consult/cases/{case['id']}"
    def forbidden(*args,**kwargs): raise AssertionError('開立及重印不得重算歷史檢查')
    monkeypatch.setattr(consult,'check_item',forbidden)
    assert doc.post(url+'/issue',json={'version':1}).status_code==409
    issued=doc.post(url+'/issue',json={'version':item['case_version']})
    assert issued.status_code==200,issued.text
    body=issued.json()
    assert body['status']=='issued' and body['issued_at'] and body['version']==item['case_version']+1
    assert body['items'][0]['checks']==item['checks']
    before=doc.get(url).json()  # SQLite 重讀 DateTime 不附時區；比較同種持久化讀取結果。
    attempts=[('patch',url,{'symptoms':'不可覆寫'}),('delete',url,None),
        ('post',url+'/items',{'registration_id':2}),('patch',url+'/items/'+item['id'],{'dose':'x'}),
        ('delete',url+'/items/'+item['id'],None),('post',url+'/issue',{})]
    for method,path,payload in attempts:
        assert doc.request(method,path,json=payload).status_code==409,(method,path)
    assert doc.get(url).json()==before


@pytest.mark.parametrize('check_type',['banned','phi','rotation'])
def test_issue_refuses_any_stored_block_without_changing_state(system,check_type):
    case=create(system);item=add(system,case).json()
    with system[1]() as db:
        db.get(PrescriptionItem,item['id']).checks=[{'type':check_type,'level':'block','message':'測試阻擋'}]
        db.commit()
    url=f"/api/v1/consult/cases/{case['id']}"
    assert system[2]['other'].post(url+'/issue',json={}).status_code==404
    assert system[2]['doc'].post(url+'/issue',json={}).status_code==409
    current=system[2]['doc'].get(url).json()
    assert current['status']=='draft' and current['issued_at'] is None and current['version']==item['case_version']


def test_pdf_reprint_uses_snapshot_after_upstream_changes(system,monkeypatch):
    import io
    import pdfplumber
    import unicodedata
    case=create(system,symptoms='葉片出現測試斑點',diagnosis_text='試驗複合症候群',examined_by=['photo'])
    item=add(system,case).json();doc=system[2]['doc'];url=f"/api/v1/consult/cases/{case['id']}"
    assert doc.post(url+'/issue',json={}).status_code==200
    def forbidden(*args,**kwargs): raise AssertionError('重印不得查詢現行比對或檢查')
    monkeypatch.setattr(service.matcher,'compare',forbidden)
    monkeypatch.setattr(consult,'check_item',forbidden)
    footer_pt=17/25.4*72  # @page 下邊界 17mm；頁碼印在這裡，不算進內文字型檢查
    def text_pdf(response):
        assert response.status_code==200,response.text if response.status_code!=200 else ''
        assert response.headers['content-type']=='application/pdf'
        assert response.headers['cache-control']=='private, no-store'
        with pdfplumber.open(io.BytesIO(response.content)) as pdf:
            assert len(pdf.pages)>=2
            body=[c for page in pdf.pages for c in page.chars if c['top']<page.height-footer_pt]
            assert body and all('SourceHanSerif' in c['fontname'] for c in body)
            pages=len(pdf.pages)
            footers=[''.join(c['text'] for c in page.chars if c['top']>=page.height-footer_pt).replace(' ','')
                     for page in pdf.pages]
            # 本機／CI 沒有安裝 AgriPageNumber 子集字型（只在 Docker 映像檔裡），退回純數字格式；
            # 不論哪種格式都不能省略頁碼，見 test_farm_log_export.py::test_page_footer_never_omits_page_numbers。
            assert footers==[f'{n}/{pages}' for n in range(1,pages+1)]
            return unicodedata.normalize('NFKC','\n'.join(page.extract_text() for page in pdf.pages))
    first=text_pdf(doc.get(url+'/report.pdf'))
    with system[1]() as db:
        db.execute(delete(Registration))
        db.get(Crop,1).name='上游作物已改名'
        db.get(Product,1).name='上游藥劑已改名'
        db.get(Pest,1).name='上游病名已改名'
        db.commit()
    banned(system)
    second=text_pdf(doc.get(url+'/report.pdf'))
    assert first==second
    for original in ('葉片出現測試斑點','試驗複合症候群','試驗單劑','2345'):
        assert original in second
    assert '上游' not in second
    assert system[2]['other'].get(url+'/report.pdf').status_code==404
