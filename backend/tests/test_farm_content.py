"""內容發布與農場流程回歸，SQLite、來源與素材皆離線。"""
from copy import deepcopy
import time
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from tests.test_farm_logs import system, URL
from app.api.v1 import farm_content, planning, consult, precision, data_sources
from app.models.farm_content import FarmContentRelease
from app.models.planning import FarmAccount, FarmSession, FarmEvent
from app.models.farm_log import FarmWorkLog
from app.services.farm_content import template_base
from app.services.farm_templates import TEMPLATES


def setup(system):
    app, factory, clients, _ = system
    for module in (farm_content, consult, precision, data_sources):
        app.include_router(module.router, prefix='/api/v1')
    with factory() as db:
        db.add(FarmAccount(id='admin', username='admin', name='測試管理者', role='admin', password_hash='unused'))
        db.flush()
        db.add(FarmSession(token_hash=planning.digest('admin-token'), account_id='admin', expires_at=int(time.time())+10000))
        db.commit()
    admin = TestClient(app, headers={'X-Planning-Request': '1'})
    admin.cookies.set(planning.COOKIE, 'admin-token', path='/api/v1')
    return admin, clients, factory


BASE = '/api/v1/admin/farm-content'


def test_admin_is_not_self_registerable_and_only_admin_edits(system):
    admin, clients, _ = setup(system)
    payload = dict(username='new-admin', password='test-password-123', name='測試', role='admin')
    assert clients['farmer'].post('/api/v1/planning/register', json=payload).status_code == 422
    for client in clients.values():
        assert client.get(BASE).status_code == 403
    assert admin.get(BASE).status_code == 200
    assert admin.put(BASE+'/rice', json={}, headers={'X-Planning-Request':'0'}).status_code == 403


def test_publish_snapshot_media_and_stale_draft(system):
    admin, clients, factory = setup(system)
    content = template_base(TEMPLATES[0])
    content['events'][0]['media'] = [dict(kind='image', url='https://example.org/test.png', caption='試驗圖', source_url='https://example.org/source')]
    saved = admin.put(BASE+'/rice', json=dict(revision=0, content=content))
    assert saved.status_code == 200, saved.text
    assert admin.put(BASE+'/rice', json=dict(revision=0, content=content)).status_code == 409
    assert admin.post(BASE+'/rice/publish', json=dict(revision=1)).status_code == 200
    assert admin.post(BASE+'/rice/publish', json=dict(revision=1)).status_code == 200
    payload = dict(crop_key='rice', crop_name='水稻', field_name='試驗', planted_on='2030-01-01', end_on='2030-05-01')
    old = clients['farmer'].post('/api/v1/planning/plans', json=payload).json()
    assert old['template_version'] == 'rice.r1'
    old_stage = next(e for e in old['events'] if e['title'] == content['events'][0]['title'])
    assert old_stage['media'][0]['caption'] == '試驗圖'
    content['events'][0]['detail'] = '新版詳細內容'
    content['events'][0]['media'][0]['caption'] = '新版圖說'
    assert admin.put(BASE+'/rice', json=dict(revision=1, content=content)).status_code == 200
    # 草稿不應進入新建規劃。
    assert clients['farmer'].post('/api/v1/planning/plans', json=payload).json()['template_version'] == 'rice.r1'
    assert admin.post(BASE+'/rice/publish', json=dict(revision=2)).status_code == 200
    new = clients['farmer'].post('/api/v1/planning/plans', json=payload).json()
    assert new['template_version'] == 'rice.r2'
    assert any(e['detail'] == '新版詳細內容' for e in new['events'])
    current = clients['farmer'].get('/api/v1/planning/farmers/farmer').json()
    assert next(p for p in current['plans'] if p['id'] == old['id']) == old
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FarmContentRelease)) == 2
    assert len(admin.get(BASE+'/rice/releases').json()) == 2


def test_invalid_media_and_unreferenced_publish_rejected(system):
    admin, _, _ = setup(system)
    content = template_base(TEMPLATES[0])
    content['events'][0]['media'] = [dict(kind='image', url='javascript:alert(1)', caption='試驗', source_url='https://example.org')]
    assert admin.put(BASE+'/rice', json=dict(revision=0, content=content)).status_code == 422
    content['events'][0]['media'] = []
    content['events'][0]['source_ids'] = []
    assert admin.put(BASE+'/rice', json=dict(revision=0, content=content)).status_code == 200
    assert admin.post(BASE+'/rice/publish', json=dict(revision=1)).status_code == 422
    content['events'][0]['end'] = 999
    assert admin.put(BASE+'/rice', json=dict(revision=1, content=content)).status_code == 422


def test_guidance_readonly_and_completion_requires_log(system):
    _, clients, factory = setup(system)
    client = clients['farmer']
    payload = dict(version=1, kind='stage', title='試驗生育期', start_on='2030-03-05', end_on='2030-03-15', detail='', note='', completed=False)
    assert client.put('/api/v1/planning/plans/plan/events/stage', json=payload).status_code == 403
    payload.update(kind='care', title='試驗作業甲', completed=True)
    assert client.put('/api/v1/planning/plans/plan/events/care', json=payload).status_code == 409
    # 同一次交易保存完成與實際日期；重送不會多一筆。
    data = dict(kind='other', event_id='care', done_on='2030-03-09')
    result = client.post(URL, json=data)
    assert result.status_code == 201, result.text
    assert next(e for e in result.json()['plan']['events'] if e['id'] == 'care')['completed']
    assert client.post(URL, json=data).status_code == 409
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FarmWorkLog)) == 1
        assert db.get(FarmEvent, 'care').completed


def test_admin_accesses_all_farm_workflows_without_links(system):
    admin, clients, _ = setup(system)
    assert len(admin.get('/api/v1/planning/links').json()) == 2
    assert admin.get('/api/v1/planning/farmers/stranger').status_code == 200
    assert admin.get('/api/v1/consult/cases/other-case').status_code == 200
    assert admin.patch('/api/v1/consult/cases/doc2-case', json={'symptoms':'不能覆寫已開立'}).status_code == 409
    result = admin.post('/api/v1/planning/plans', json=dict(farmer_id='stranger', crop_name='試驗自訂作物', field_name='測試田', planted_on='2030-01-01', end_on='2030-06-01'))
    assert result.status_code == 200, result.text
    assert result.json()['farmer_id'] == 'stranger'
    assert admin.patch('/api/v1/precision/me', json={'enabled':True, 'farmer_id':'stranger'}).status_code == 200
    assert clients['doc'].post('/api/v1/planning/plans', json=dict(farmer_id='stranger', crop_name='試驗',field_name='試驗',planted_on='2030-01-01',end_on='2030-06-01')).status_code == 403


def test_demo_seed_is_repeatable_without_overwriting_edits(system):
    from scripts.seed_demo_content import seed, ADMIN
    from app.models.farm_content import FarmContent
    _, factory, _, _ = system
    with factory() as db:
        seed(db)
        before = db.scalar(select(func.count()).select_from(FarmWorkLog))
        row = db.get(FarmContent, 'rice')
        changed = deepcopy(row.draft); changed['scope'] = '人工確認的範圍'; row.draft = changed
        db.commit()
        seed(db)
        assert db.get(FarmContent, 'rice').draft['scope'] == '人工確認的範圍'
        assert db.scalar(select(func.count()).select_from(FarmWorkLog)) == before
        assert db.get(FarmAccount, ADMIN).role == 'admin'


def test_content_migration_preserves_legacy_events():
    from importlib.util import spec_from_file_location, module_from_spec
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text, inspect
    spec = spec_from_file_location('content_migration', Path(__file__).parents[1] / 'alembic/versions/0003_farm_content.py')
    module = module_from_spec(spec); spec.loader.exec_module(module)
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE farm_events (id TEXT PRIMARY KEY, detail TEXT)'))
        conn.execute(text("INSERT INTO farm_events VALUES ('old', '原始建議')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            assert conn.execute(text('SELECT detail, media FROM farm_events')).one() == ('原始建議', '[]')
            assert inspect(conn).has_table('farm_content_releases')
            module.downgrade()
            assert conn.execute(text('SELECT detail FROM farm_events')).scalar() == '原始建議'
