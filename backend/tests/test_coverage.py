"""Isolated database tests for absence, completeness and publication semantics."""
from datetime import datetime, timezone
import hashlib
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models import SourceName, SourceSnapshot, SourceRecord, SnapshotStatus, Crop, Pest, Confidence, KdaisRecommendation, Registration
from app.models.updates import PpmCoverage, SourceChange
from app.services.coverage import pair_coverage, snapshot_coverage
from app.services.matcher import compare
from app.services.source_updates import publish_snapshot
from app.crawlers.base import activate_snapshot
from app.crawlers.ppm_parser import parse_crop_directory, parse_pest_detail, PpmSchemaError

@compiles(JSONB, 'sqlite')
def jsonb_as_json(element, compiler, **kw): return 'JSON'

@pytest.fixture
def db():
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session: yield session
    engine.dispose()


def snapshot(db, complete=False, active=True):
    s=SourceSnapshot(source=SourceName.PPM,status=SnapshotStatus.SUCCESS,is_active=active,
        started_at=datetime.now(timezone.utc),finished_at=datetime.now(timezone.utc),row_count=1,
        meta={'complete':complete,'discovery_complete':complete,'discovered':1})
    db.add(s);db.flush();return s


def setup_pair(db, drugs=None, complete=False):
    c,p=Crop(name='測試作物'),Pest(name='測試病害');db.add_all([c,p]);db.flush()
    s=snapshot(db,complete)
    r=SourceRecord(snapshot_id=s.id,source=SourceName.PPM,record_kind='ppm_pest_detail',
        crop_id=c.id,pest_id=p.id,source_url='https://otserv2.acri.gov.tw/PPM/',row_hash='a'*64,
        payload={'crop_name':c.name,'pest_name':p.name,'drugs':drugs or []})
    db.add(r);db.flush();return c,p,s,r


def receipt(db, s, r):
    db.add(PpmCoverage(snapshot_id=s.id,target_key='a'*64,target_name='測試作物測試病害',
        crop_name='測試作物',pest_name='測試病害',source_url=r.source_url,status='success',record_id=r.id,
        drug_count=len(r.payload['drugs']),html_sha256='a'*64))
    db.flush()


def test_missing_and_legacy_rows_are_not_negative_approval(db):
    c,p,s,r=setup_pair(db)
    assert pair_coverage(db,c.id,p.id)['state']=='unverified'
    assert not pair_coverage(db,c.id,p.id)['can_assert_page_absence']
    other=Pest(name='未收錄病害');db.add(other);db.flush()
    assert pair_coverage(db,c.id,other.id)['state']=='not_collected'
    assert snapshot_coverage(db,s)['percent'] is None


def test_verified_empty_is_distinct_from_uncollected(db):
    c,p,s,r=setup_pair(db,complete=True);receipt(db,s,r)
    assert pair_coverage(db,c.id,p.id)['state']=='verified_empty'
    assert pair_coverage(db,c.id,p.id)['can_assert_page_absence']
    assert snapshot_coverage(db,s)['percent']==100


def test_coverage_flag_without_receipts_cannot_prove_absence(db):
    c,p,s,r=setup_pair(db,complete=True)
    assert pair_coverage(db,c.id,p.id)['state']=='unverified'
    assert not snapshot_coverage(db,s)['complete']


def test_kdais_missing_match_is_coverage_warning(db):
    c,p,s,r=setup_pair(db)
    kd=SourceSnapshot(source=SourceName.KDAIS,status=SnapshotStatus.SUCCESS,is_active=True,started_at=datetime.now(timezone.utc),row_count=1);db.add(kd);db.flush()
    db.add(KdaisRecommendation(snapshot_id=kd.id,crop_id=c.id,pest_id=p.id,product_text='10%測試藥水懸劑',confidence=Confidence.HIGH));db.flush()
    out=compare(db,c.id,p.id)
    assert [i.issue_type for i in out.issues]==['PPM_COVERAGE_GAP']
    assert out.issues[0].severity=='warning'


def test_partial_snapshot_never_replaces_active(db):
    old=snapshot(db);new=snapshot(db,False,False);new.row_count=200
    assert activate_snapshot(db,new) is False
    assert old.is_active and not new.is_active


def test_publish_failure_rolls_back_active_and_facts(db,monkeypatch):
    from app.services import normalize
    c,p,old,r=setup_pair(db);db.commit()
    new=snapshot(db,True,False)
    new_rec=SourceRecord(snapshot_id=new.id,source=SourceName.PPM,record_kind='ppm_pest_detail',crop_id=c.id,pest_id=p.id,payload=r.payload,source_url=r.source_url,row_hash='b'*64)
    db.add(new_rec);db.flush();receipt(db,new,new_rec);db.commit()
    def broken(*a): raise ValueError('normalization failed')
    monkeypatch.setattr(normalize,'normalize_ppm',broken)
    with pytest.raises(ValueError,match='normalization failed'): publish_snapshot(db,new)
    assert db.get(SourceSnapshot,old.id).is_active
    assert not db.get(SourceSnapshot,new.id).is_active
    assert db.scalar(select(func.count()).select_from(SourceChange))==0


def test_full_directory_href_is_discovered_and_deduplicated():
    html='<a href="PLC0101.aspx?a=1">水稻病害</a><a href="PLC0101.aspx?a=1">水稻病害</a><a href="PLC0101.aspx?a=2">甘藍病害</a>'
    assert len(parse_crop_directory(html,'https://otserv2.acri.gov.tw/PPM/'))==2
    with pytest.raises(PpmSchemaError): parse_crop_directory('<html>Error</html>')


def test_missing_structure_is_not_an_empty_registration_list():
    with pytest.raises(PpmSchemaError): parse_pest_detail('<html>Service unavailable</html>')


def test_all_drug_tables_and_application_context_are_retained():
    table='<table id="table{n}"><tr><th>藥劑名稱</th><th>每公頃施藥量</th><th>施藥方法</th></tr><tr><td>10% 測試藥 水懸劑</td><td>1公升</td><td>請核對原文</td></tr></table>'
    html='<body><div class="CropNo_title"><span>測試作物</span>測試病害</div><div id="drug_show"><table><tr><th>品種</th></tr></table>'
    for n,context in [(2,'本田'),(3,'育苗箱')]:
        html+=f'<div style="width:100%">{context}<div><div>'+table.format(n=n)+'</div></div></div>'
    html+='</div></body>'
    detail=parse_pest_detail(html)
    assert len(detail.drugs)==2
    assert '本田' in detail.drugs[0].application_method
    assert '育苗箱' in detail.drugs[1].application_method


def test_normalization_retains_equal_dose_with_different_treatment_context(db):
    from app.services.normalize import EntityCache, NormalizeResult, normalize_ppm
    drugs=[{'product_name':'測試藥','concentration':'10%','formulation':'水懸劑','dose_per_hectare':'1公升','dilution_text':'1000','application_method':context} for context in ['本田','育苗箱']]
    setup_pair(db,drugs)
    normalize_ppm(db,EntityCache(db),NormalizeResult());db.flush()
    assert db.scalar(select(func.count()).select_from(Registration))==2


def test_ban_use_date_not_manufacture_date_controls_future_notice(db):
    from app.services.matcher import banned_ingredient_names, future_ban_notices
    s=SourceSnapshot(source=SourceName.FDA,status=SnapshotStatus.SUCCESS,is_active=True,started_at=datetime.now(timezone.utc),row_count=1)
    db.add(s);db.flush()
    r=SourceRecord(snapshot_id=s.id,source=SourceName.FDA,record_kind='fda_banned',row_hash='c'*64,
        payload={'name':'測試','english_name':'future','banned_manufacture_date':'114年1月1日','banned_sale_date':'199年1月1日'})
    db.add(r);db.flush()
    assert 'future' not in banned_ingredient_names(db)
    assert future_ban_notices(db)['future']['effective_date']=='2110-01-01'
    r.payload={**r.payload,'banned_sale_date':'依公告分階段'};db.flush()
    assert 'future' in banned_ingredient_names(db)


def test_successful_publish_is_idempotent_and_preserves_source_versions(db):
    c,p,old,r=setup_pair(db);db.commit()
    new=snapshot(db,True,False)
    payload={**r.payload,'drugs':[{'product_name':'範例藥','concentration':'10%','formulation':'水懸劑','dilution_text':'1000','application_method':'本田：僅作測試'}]}
    rec=SourceRecord(snapshot_id=new.id,source=SourceName.PPM,record_kind='ppm_pest_detail',payload=payload,source_url=r.source_url,row_hash='c'*64)
    db.add(rec);db.flush();receipt(db,new,rec);db.commit()
    assert publish_snapshot(db,new)
    assert not old.is_active and new.is_active
    assert db.scalar(select(Registration.snapshot_id))==new.id
    changes=db.scalar(select(func.count()).select_from(SourceChange))
    assert changes==1 and not publish_snapshot(db,new)
    assert db.scalar(select(func.count()).select_from(SourceChange))==changes


def test_email_delivery_disabled_then_idempotent_without_network(db, monkeypatch, tmp_path):
    from app.services.notifications import deliver_ban_updates
    from app.core.config import settings
    snap=snapshot(db)
    change=SourceChange(source='fda',snapshot_id=snap.id,change_key='n'*64,kind='fda_banned_added',title='測試公告',after={'name':'測試'},detected_at=datetime.now(timezone.utc))
    db.add(change);db.commit()
    monkeypatch.setattr(settings,'raw_data_dir',tmp_path)
    monkeypatch.setattr(settings,'update_email_enabled',False)
    assert deliver_ban_updates(db)['status']=='disabled'
    monkeypatch.setattr(settings,'update_email_enabled',True)
    monkeypatch.setattr(settings,'update_email_to','test@example.invalid')
    monkeypatch.setattr(settings,'update_email_from','sender@example.invalid')
    monkeypatch.setattr(settings,'smtp_host','smtp.example.invalid')
    captured=[]
    assert deliver_ban_updates(db,sender=captured.append)['sent']==1
    assert deliver_ban_updates(db,sender=captured.append)['sent']==0
    assert len(captured)==1 and '使用日期' in captured[0].get_content()


def test_full_ppm_rebuilds_generated_aliases_without_merging_official_names(db):
    from app.models import PestAlias
    from app.services.normalize import EntityCache, NormalizeResult, normalize_ppm
    c,wrong,s,r=setup_pair(db,complete=True)
    db.add_all([PestAlias(pest_id=wrong.id,alias='真正病害',source=SourceName.PPM),
                PestAlias(pest_id=wrong.id,alias='0.3公升',source=SourceName.PPM),
                PestAlias(pest_id=wrong.id,alias='人工審核別名',source=None)])
    r.payload={**r.payload,'pest_name':'真正病害','pest_aliases':['新確認俗名']};db.flush()
    normalize_ppm(db,EntityCache(db),NormalizeResult());db.flush()
    correct=db.scalar(select(Pest).where(Pest.name=='真正病害'))
    assert correct.id!=wrong.id and r.pest_id==correct.id
    aliases={a.alias:a.pest_id for a in db.scalars(select(PestAlias))}
    assert aliases=={'人工審核別名':wrong.id,'新確認俗名':correct.id}
    assert {a['alias'] for a in s.meta['previous_ppm_aliases']}=={'真正病害','0.3公升'}


def test_fda_repeated_page_is_rejected_instead_of_double_counted(db,monkeypatch,tmp_path):
    from app.crawlers import fda
    from app.crawlers.base import RawArchive
    from app.crawlers.fda_parser import FdaSchemaError, ListPage, MrlRow
    from types import SimpleNamespace
    import dataclasses
    page=ListPage(total_pages=2,total_rows=2,mrl_rows=[MrlRow(seq='1',common_name='範例',international_name='test',crop_category='米',limit_text='0.1')])
    monkeypatch.setattr(fda,'parse_list_page',lambda *args:page)
    archive=SimpleNamespace(save=lambda *args:None)
    client=SimpleNamespace(get=lambda *args:SimpleNamespace(text='synthetic page'))
    snap=snapshot(db,False,False)
    with pytest.raises(FdaSchemaError,match='重複'):
        fda.crawl_tab(db,snap,archive,client,1,'fda_mrl','測試')
    assert not snap.is_active


def test_no_rotation_claim_for_empty_or_unidentified_products():
    from app.services.resistance import build_rotation_advice
    assert not build_rotation_advice([]).groups
    assert '尚無可用' in build_rotation_advice([]).note
    advice=build_rotation_advice([(1,'藥劑A',['11']),(2,'藥劑B',[])])
    assert '全部' not in advice.note


def test_new_ppm_does_not_keep_removed_profiles_or_moa(db):
    from app.models import Product, PestProfile
    from app.services.normalize import EntityCache, NormalizeResult, normalize_ppm
    drugs=[{'product_name':'藥劑A','concentration':'10%','formulation':'水懸劑','moa_codes':['11']}]
    c,p,s,r=setup_pair(db,drugs,complete=True)
    normalize_ppm(db,EntityCache(db),NormalizeResult());db.flush()
    assert db.scalar(select(Product.moa_text))=='11'
    r.payload={**r.payload,'drugs':[{**drugs[0],'moa_codes':[]}], 'symptoms':None};db.flush()
    db.scalar(select(PestProfile)).symptoms='已撤下舊病徵';db.flush()
    normalize_ppm(db,EntityCache(db),NormalizeResult());db.flush()
    assert db.scalar(select(Product.moa_text)) is None
    assert db.scalar(select(PestProfile.symptoms)) is None


def test_deferred_publish_waits_without_restarting_and_rejects_failed_crawl(db):
    from app.crawlers.publish import attempt
    snap=snapshot(db,False,False);snap.status=SnapshotStatus.RUNNING;db.commit()
    assert attempt(db,snap.id)=='waiting'
    snap.status=SnapshotStatus.FAILED;db.commit()
    with pytest.raises(ValueError,match='不完整'): attempt(db,snap.id)
    assert not snap.is_active


def test_older_snapshot_cannot_replace_newer_published_snapshot(db):
    old=snapshot(db,True,False);new=snapshot(db,True,True);db.commit()
    with pytest.raises(ValueError,match='較新的'): publish_snapshot(db,old)
    assert new.is_active and not old.is_active
