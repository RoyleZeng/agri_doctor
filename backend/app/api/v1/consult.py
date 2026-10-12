"""醫師案件與處方共用農場登入，讀取歷史時不重算來源或檢查。"""
from contextlib import contextmanager, ExitStack
from datetime import date,datetime,time,timedelta,timezone
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo
from fastapi import APIRouter,Depends,HTTPException,Query
from fastapi.responses import Response
from pydantic import Field,model_validator
from sqlalchemy import select,func
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.v1.planning import Input,current_user,protect_write,role_required,access_farmer,access_plan
from app.models import Crop,CropAlias,Pest
from app.models.consult import ConsultCase,PrescriptionItem
from app.crawlers.base import job_lock
from app.services.consult import capture_item,case_rotation,check_item,ensure_allowed,item_output,taipei_today,json_snapshot

router=APIRouter(prefix='/consult',tags=['consult'],dependencies=[Depends(protect_write)])


def doctor(user=Depends(current_user)):
    role_required(user,'doctor')
    return user


@contextmanager
def stable_sources(db):
    # 與發布使用同一把鎖，避免取完來源、尚未保存處方時正規化刪除登記列。
    with ExitStack() as stack:
        try:
            stack.enter_context(job_lock(db,'publish'))
        except RuntimeError:
            raise HTTPException(503,'資料來源正在發布，請稍後重試')
        yield


class CaseCreate(Input):
    farmer_id:str | None=Field(None,max_length=36)
    farmer_text:str=Field('',max_length=120)
    plan_id:str | None=Field(None,max_length=36)
    crop_id:int | None=Field(None,gt=0)
    crop_text:str=Field('',max_length=80)
    variety:str=Field('',max_length=80)
    field_text:str=Field('',max_length=160)
    area_text:str=Field('',max_length=60)
    expected_harvest_on:date | None=None
    examined_by:list[Literal['on_site','sample','photo','other']]=Field(default_factory=list,max_length=4)
    symptoms:str=Field('',max_length=10000)
    diagnosis_pest_id:int | None=Field(None,gt=0)
    diagnosis_text:str=Field('',max_length=160)
    diagnosis_basis:list[Literal['visual','microscopy','culture','lab','other']]=Field(default_factory=list,max_length=5)
    cultural_control:str=Field('',max_length=10000)


class CasePatch(Input):
    version:int | None=Field(None,ge=1)
    farmer_text:str | None=Field(None,min_length=1,max_length=120)
    variety:str | None=Field(None,max_length=80)
    area_text:str | None=Field(None,max_length=60)
    expected_harvest_on:date | None=None
    examined_by:list[Literal['on_site','sample','photo','other']] | None=Field(None,max_length=4)
    symptoms:str | None=Field(None,max_length=10000)
    diagnosis_pest_id:int | None=Field(None,gt=0)
    diagnosis_text:str | None=Field(None,max_length=160)
    diagnosis_basis:list[Literal['visual','microscopy','culture','lab','other']] | None=Field(None,max_length=5)
    cultural_control:str | None=Field(None,max_length=10000)
    status:Literal['draft','saved'] | None=None

    @model_validator(mode='after')
    def no_null_text(self):
        for key in self.model_fields_set-{'expected_harvest_on','diagnosis_pest_id','version'}:
            if getattr(self,key) is None: raise ValueError('文字、狀態及陣列欄位不可為 null')
        return self


class ItemCreate(Input):
    registration_id:int=Field(gt=0)
    version:int | None=Field(None,ge=1)


class IssueInput(Input):
    version:int | None=Field(None,ge=1)


class ItemPatch(Input):
    version:int | None=Field(None,ge=1)
    dilution:str | None=Field(None,max_length=40)
    dose:str | None=Field(None,max_length=40)
    frequency:str | None=Field(None,max_length=80)
    advice:str | None=Field(None,max_length=10000)
    sort_order:int | None=Field(None,ge=0,le=1000)

    @model_validator(mode='after')
    def no_null_required(self):
        for key in self.model_fields_set & {'frequency','advice','sort_order'}:
            if getattr(self,key) is None: raise ValueError('此欄位不可為 null')
        return self


def owned_case(db,user,id,*,write=False,version=None):
    query=select(ConsultCase).where(ConsultCase.id==id)
    if user.role != "admin": query=query.where(ConsultCase.doctor_id==user.id)
    if write: query=query.with_for_update()
    case=db.scalar(query)
    if not case: raise HTTPException(404,'找不到此診療案件')
    if write:
        if case.status=='issued': raise HTTPException(409,'已開立案件不可修改')
        if version is not None and version!=case.version: raise HTTPException(409,'案件已更新，請重新載入')
        # 撤銷連結後仍能讀取自己留下的快照，但不再讀新施藥紀錄或新增處方。
        if case.farmer_id: access_farmer(db,user,case.farmer_id)
    return case


def items_of(db,case):
    return db.scalars(select(PrescriptionItem).where(PrescriptionItem.case_id==case.id)
        .order_by(PrescriptionItem.sort_order,PrescriptionItem.id)).all()


def output(db,case):
    items=items_of(db,case)
    return json_snapshot({c.name:getattr(case,c.name) for c in ConsultCase.__table__.columns} |
        {'items':[item_output(i,case.version) for i in items],
         # 案件層級的輪替摘要：逐項提示看不出「整張處方連續第幾次同機制」。
         'rotation':case_rotation(db,case,items)})


def list_output(db,case):
    """列表只提供辨識案件的資訊與處方摘要，完整來源快照留給 detail。"""
    levels={'block':3,'warning':2,'ok':1}
    summary=[]
    for item in items_of(db,case):
        highest=max((c.get('level','ok') for c in item.checks),key=lambda level:levels.get(level,0),default='ok')
        summary.append({'product_text':item.product_text,'dilution':item.dilution,
            'phi_days':item.phi_days,'check_level':highest})
    return json_snapshot({c.name:getattr(case,c.name) for c in ConsultCase.__table__.columns} |
        {'prescriptions':summary})


def resolve_crop(db,name):
    found=db.scalar(select(Crop).where(Crop.name==name))
    if found: return found.id
    ids=set(db.scalars(select(CropAlias.crop_id).where(CropAlias.alias==name)))
    return next(iter(ids)) if len(ids)==1 else None


def diagnosis(db,data):
    if data.get('diagnosis_pest_id') is not None:
        pest=db.get(Pest,data['diagnosis_pest_id'])
        if not pest: raise HTTPException(422,'診斷病蟲害不存在')
        if not data.get('diagnosis_text'): data['diagnosis_text']=pest.name


@router.post('/cases',status_code=201)
def create_case(payload:CaseCreate,db:Session=Depends(get_db),user=Depends(doctor)):
    data=payload.model_dump()
    if payload.plan_id:
        plan=access_plan(db,user,payload.plan_id)
        if payload.farmer_id and payload.farmer_id!=plan.farmer_id:
            raise HTTPException(422,'農戶與作物規劃不符')
        canonical=resolve_crop(db,plan.crop_name)
        if payload.crop_id and payload.crop_id!=canonical:
            raise HTTPException(422,'指定作物與農民規劃不符')
        data.update(farmer_id=plan.farmer_id,crop_id=canonical,crop_text=plan.crop_name,
            field_text=plan.field_name,variety=plan.variety,area_text=plan.area,expected_harvest_on=plan.end_on)
    if data['farmer_id']:
        farmer=access_farmer(db,user,data['farmer_id'])
        data['farmer_text']=farmer.name
    elif not data['farmer_text']:
        raise HTTPException(422,'現場散客請填委託人姓名')
    if data['crop_id']:
        crop=db.get(Crop,data['crop_id'])
        if not crop: raise HTTPException(422,'作物不存在')
        if not payload.plan_id: data['crop_text']=crop.name
    if not data['crop_text']: raise HTTPException(422,'請填作物或選擇作物規劃')
    diagnosis(db,data)
    today=taipei_today()
    # 使用隨機尾碼與唯一約束，避免兩位醫師同時開單時共用案號。
    case=ConsultCase(**data,doctor_id=user.id,case_no=f'{today.year-1911}-{today:%m%d}-{uuid4().hex[:12]}')
    db.add(case);db.commit()
    return output(db,case)


@router.get('/cases')
def list_cases(date_from:date | None=None,date_to:date | None=None,crop_id:int | None=Query(None,gt=0),
               farmer_id:str | None=None,farmer_text:str | None=Query(None,max_length=120),issued:bool | None=None,
               status:Literal['draft','saved','issued'] | None=None,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),
               db:Session=Depends(get_db),user=Depends(doctor)):
    if date_from and date_to and date_from>date_to: raise HTTPException(422,'日期範圍錯誤')
    query=select(ConsultCase)
    if user.role != "admin": query=query.where(ConsultCase.doctor_id==user.id)
    if date_from: query=query.where(ConsultCase.created_at>=datetime.combine(date_from,time.min,ZoneInfo('Asia/Taipei')).astimezone(timezone.utc))
    if date_to: query=query.where(ConsultCase.created_at<datetime.combine(date_to+timedelta(days=1),time.min,ZoneInfo('Asia/Taipei')).astimezone(timezone.utc))
    for field,value in [('crop_id',crop_id),('farmer_id',farmer_id),('status',status)]:
        if value is not None: query=query.where(getattr(ConsultCase,field)==value)
    if farmer_text: query=query.where(ConsultCase.farmer_text.contains(farmer_text,autoescape=True))
    if issued is not None: query=query.where(ConsultCase.status=='issued' if issued else ConsultCase.status!='issued')
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    cases=db.scalars(query.order_by(ConsultCase.created_at.desc(),ConsultCase.id.desc()).offset(offset).limit(limit)).all()
    return {'total':total,'limit':limit,'offset':offset,'items':[list_output(db,c) for c in cases]}


@router.get('/cases/{id}')
def get_case(id:str,db:Session=Depends(get_db),user=Depends(doctor)):
    return output(db,owned_case(db,user,id))


@router.post('/cases/{id}/issue')
def issue_case(id:str,payload:IssueInput=IssueInput(),db:Session=Depends(get_db),user=Depends(doctor)):
    # 與所有案件寫入共用 row lock，檢查與狀態轉換不能被另一個處方寫入穿插。
    case=owned_case(db,user,id,write=True,version=payload.version)
    for item in items_of(db,case):
        ensure_allowed(item.checks)
    case.status='issued'
    case.issued_at=datetime.now(timezone.utc)
    case.version+=1
    db.commit()
    return output(db,case)


@router.get('/cases/{id}/report.pdf')
def report_pdf(id:str,db:Session=Depends(get_db),user=Depends(doctor)):
    from app.services.consult_report import render_report_pdf
    case=owned_case(db,user,id)
    # 只取案件及處方的歷史欄位，沒有任何 matcher、來源表或帳號即時資料查詢。
    pdf=render_report_pdf(output(db,case))
    return Response(pdf,media_type='application/pdf',headers={
        'Content-Disposition':f'inline; filename="consult-{case.case_no}.pdf"',
        'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'})


@router.patch('/cases/{id}')
def patch_case(id:str,payload:CasePatch,db:Session=Depends(get_db),user=Depends(doctor)):
    with stable_sources(db):
        case=owned_case(db,user,id,write=True,version=payload.version)
        data=payload.model_dump(exclude_unset=True,exclude={'version'})
        if case.status=='saved' and data.get('status')=='draft':
            raise HTTPException(409,'已儲存案件不可改回可刪除的草稿')
        diagnosis(db,data)
        for key,value in data.items(): setattr(case,key,value)
        # 補記病徵不應悄悄改寫當時判斷；更改採收日或明確儲存才重新評估。
        if 'expected_harvest_on' in data or data.get('status')=='saved':
            for item in items_of(db,case):
                checks=check_item(db,case,item);ensure_allowed(checks);item.checks=checks
        case.version+=1;db.commit()
        return output(db,case)


@router.delete('/cases/{id}')
def delete_case(id:str,version:int | None=Query(None,ge=1),db:Session=Depends(get_db),user=Depends(doctor)):
    case=owned_case(db,user,id,write=True,version=version)
    if case.status!='draft': raise HTTPException(409,'已儲存的診療紀錄不可刪除')
    db.delete(case);db.commit()
    return {'ok':True}


@router.post('/cases/{id}/items',status_code=201)
def add_item(id:str,payload:ItemCreate,db:Session=Depends(get_db),user=Depends(doctor)):
    with stable_sources(db):
        case=owned_case(db,user,id,write=True,version=payload.version)
        existing=items_of(db,case)
        if any(i.source_values.get('registration_id')==payload.registration_id and i.registration_id is not None for i in existing):
            raise HTTPException(409,'此施用方式已在處方中，請編輯原項目')
        item=capture_item(db,case,payload.registration_id)
        item.checks=check_item(db,case,item);ensure_allowed(item.checks)
        item.sort_order=max((i.sort_order for i in existing),default=-1)+1
        db.add(item);case.version+=1;db.commit()
        return json_snapshot(item_output(item,case.version))


@router.patch('/cases/{id}/items/{iid}')
def patch_item(id:str,iid:str,payload:ItemPatch,db:Session=Depends(get_db),user=Depends(doctor)):
    with stable_sources(db):
        case=owned_case(db,user,id,write=True,version=payload.version)
        item=db.scalar(select(PrescriptionItem).where(PrescriptionItem.id==iid,PrescriptionItem.case_id==case.id))
        if not item: raise HTTPException(404,'找不到此處方項')
        for key,value in payload.model_dump(exclude_unset=True,exclude={'version'}).items(): setattr(item,key,value)
        item.checks=check_item(db,case,item);ensure_allowed(item.checks)
        case.version+=1;db.commit()
        return json_snapshot(item_output(item,case.version))


@router.delete('/cases/{id}/items/{iid}')
def delete_item(id:str,iid:str,version:int | None=Query(None,ge=1),db:Session=Depends(get_db),user=Depends(doctor)):
    case=owned_case(db,user,id,write=True,version=version)
    item=db.scalar(select(PrescriptionItem).where(PrescriptionItem.id==iid,PrescriptionItem.case_id==case.id))
    if not item: raise HTTPException(404,'找不到此處方項')
    db.delete(item);case.version+=1;db.commit()
    return {'ok':True,'case_version':case.version}
