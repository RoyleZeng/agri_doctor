"""處方由既有三源比對取值；歷史讀取只使用當時保存的 JSON。"""
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select, or_
from app.models import Registration, SourceSnapshot, SourceName
from app.models.consult import ApplicationRecord, PrescriptionItem
from app.services import matcher
from app.services.consult_checks import prescription_checks, rotation_summary

# 橫幅要畫得出施藥序列，多抓幾筆；逐項檢查仍然只看最近兩筆。
ROTATION_HISTORY_LIMIT=5


def taipei_today():
    return datetime.now(ZoneInfo('Asia/Taipei')).date()


def json_snapshot(value):
    return jsonable_encoder(value, custom_encoder={Decimal:str})


def source_row(db, registration_id):
    reg=db.get(Registration,registration_id)
    if not reg:
        raise HTTPException(409,'用藥來源已更新或不存在，請重新搜尋')
    active=db.scalar(select(SourceSnapshot.id).where(SourceSnapshot.source==SourceName.PPM,SourceSnapshot.is_active.is_(True)))
    if not active or reg.snapshot_id!=active:
        raise HTTPException(409,'此登記不是目前正式來源，請重新搜尋')
    result=matcher.compare(db,reg.crop_id,reg.pest_id)
    row=next((r for r in result.rows if r.registration_id==reg.id),None)
    if row is None:
        raise HTTPException(409,'此登記已不在查詢結果中，請重新搜尋')
    return reg,result,row


def capture_item(db, case, registration_id):
    reg,result,row=source_row(db,registration_id)
    if case.crop_id is None or reg.crop_id!=case.crop_id:
        raise HTTPException(409,'此登記與案件作物不符，不可加入處方；請先確認案件作物')
    names=[]
    for link in reg.product.ingredients:
        if link.ingredient:
            names.extend(n for n in (link.ingredient.name,link.ingredient.english_name) if n)
    from app.services.normalize import split_composition
    names=list(dict.fromkeys(names+split_composition(row.english_composition)))
    product_text=' '.join(x for x in (row.concentration,row.product_name,row.formulation) if x)
    ingredient_text='、'.join(dict.fromkeys(link.ingredient.name or link.ingredient.english_name
        for link in reg.product.ingredients if link.ingredient and (link.ingredient.name or link.ingredient.english_name))) or (row.english_composition or '')
    values=json_snapshot({'registration_id':reg.id,'registration_snapshot_id':reg.snapshot_id,
        'crop_id':reg.crop_id,'pest_id':reg.pest_id,'crop':result.crop,'pest':result.pest,
        'product_id':row.product_id,'dilution':row.dilution_text,'dose':row.dose_per_hectare,
        'frequency':'','phi_days':row.phi_days,'advice':'','ingredient_names':names,'moa_codes':row.moa_codes,
        'comparison':asdict(row),'coverage':result.coverage,'captured_at':datetime.now(ZoneInfo('Asia/Taipei')).isoformat()})
    fields={'product_text':product_text,'ingredient_text':ingredient_text,'moa_text':' | '.join(row.moa_codes),
            'dilution':row.dilution_text,'dose':row.dose_per_hectare}
    # 超長原文不可靜默截斷成另一個處方；原始值完整留在來源查詢中。
    for name,limit in [('product_text',200),('ingredient_text',200),('moa_text',60),('dilution',40),('dose',40)]:
        if len(fields[name] or '')>limit:
            raise HTTPException(422,f'來源的 {name} 超出處方欄位長度，請人工核對，未截斷存入')
    return PrescriptionItem(case_id=case.id,registration_id=reg.id,**fields,
        frequency='',advice='',phi_days=row.phi_days,source_values=values,sort_order=0)


def application_history(db, case, today, limit):
    """這個案件對應田區／規劃的實際施藥紀錄，由新到舊。

    逐項檢查只需要最近兩筆，案件層級的輪替摘要要多幾筆才畫得出序列，所以 limit 由呼叫端給。
    """
    if not (case.farmer_id and (case.field_text or case.plan_id)):
        return []
    scope=[]
    if case.field_text: scope.append(ApplicationRecord.field_text==case.field_text)
    if case.plan_id: scope.append(ApplicationRecord.plan_id==case.plan_id)
    records=db.scalars(select(ApplicationRecord).where(ApplicationRecord.farmer_id==case.farmer_id,
        or_(*scope),ApplicationRecord.applied_on<=today).order_by(ApplicationRecord.applied_on.desc(),
            ApplicationRecord.created_at.desc(),ApplicationRecord.id.desc()).limit(limit)).all()
    return [{k:getattr(r,k) for k in ('id','applied_on','moa_codes','created_at','product_text')}
            for r in records]


def case_rotation(db, case, items):
    """案件層級的輪替摘要，給開單頁最上面那條橫幅用。

    逐項提示各自成立、卻沒有人把它們加起來看——上一輪留下的就是這個缺口。
    """
    today=taipei_today()
    return rotation_summary(today=today,
        item_codes=[i.source_values.get('moa_codes') or [] for i in items],
        history=application_history(db,case,today,ROTATION_HISTORY_LIMIT))


def check_item(db, case, item):
    today=taipei_today()
    history=application_history(db,case,today,2)
    checks=prescription_checks(today=today,expected_harvest_on=case.expected_harvest_on,
        phi_days=item.phi_days,ingredient_names=item.source_values['ingredient_names'],
        banned_names=matcher.banned_ingredient_names(db),moa_codes=item.source_values['moa_codes'],history=history)
    fda_id=db.scalar(select(SourceSnapshot.id).where(SourceSnapshot.source==SourceName.FDA,SourceSnapshot.is_active.is_(True)))
    banned_check=next(c for c in checks if c['type']=='banned')
    banned_check['snapshot_id']=fda_id
    if fda_id is None and banned_check['level']!='block':
        banned_check.update(level='warning',message='尚無正式食藥署禁用快照，請核對官方名單。')
    return checks


def ensure_allowed(checks):
    if any(c['level']=='block' for c in checks):
        raise HTTPException(409,{'message':'處方未通過開單檢查，資料未儲存','checks':checks})


def item_output(item, version):
    data={c.name:getattr(item,c.name) for c in PrescriptionItem.__table__.columns}
    data['modified_fields']=[k for k in ('dilution','dose','frequency','advice') if getattr(item,k)!=item.source_values.get(k)]
    return {**data,'case_version':version}
