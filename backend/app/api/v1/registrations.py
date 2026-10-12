"""藥名搜尋只負責找候選，適用性仍由精確的登記列與作物決定。"""
from dataclasses import asdict
from fastapi import APIRouter,Depends,HTTPException,Query
from sqlalchemy import select,func,or_,case as sql_case
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import Registration,Product,ProductIngredient,ActiveIngredient,Crop,SourceSnapshot,SourceName
from app.services.matcher import compare
from app.services.consult import json_snapshot

router=APIRouter(prefix='/registrations',tags=['registrations'])


@router.get('/search')
def search(q:str=Query(min_length=1,max_length=100),crop_id:int | None=Query(None,gt=0),
           limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),db:Session=Depends(get_db)):
    term=q.strip().lower()
    if not term: raise HTTPException(422,'請輸入藥劑名或成分名')
    if crop_id is not None and not db.get(Crop,crop_id): raise HTTPException(404,'找不到作物')
    active=select(SourceSnapshot.id).where(SourceSnapshot.source==SourceName.PPM,SourceSnapshot.is_active.is_(True))
    ingredient_match=select(ProductIngredient.id).join(ActiveIngredient).where(ProductIngredient.product_id==Product.id,
        or_(func.lower(ActiveIngredient.name).contains(term,autoescape=True),
            func.lower(ActiveIngredient.english_name).contains(term,autoescape=True))).exists()
    query=select(Registration).join(Product).where(Registration.snapshot_id.in_(active),or_(
        func.lower(Product.name).contains(term,autoescape=True),
        func.lower(Product.english_composition).contains(term,autoescape=True),ingredient_match))
    total=db.scalar(select(func.count()).select_from(query.subquery()))
    if crop_id is not None:
        query=query.order_by(sql_case((Registration.crop_id==crop_id,0),else_=1))
    regs=db.scalars(query.order_by(Product.name,Registration.id).offset(offset).limit(limit)).all()
    pairs={}
    crops={}
    for reg in regs:
        if reg.product_id not in crops:
            rows=db.execute(select(Crop.id,Crop.name).join(Registration,Registration.crop_id==Crop.id).where(
                Registration.product_id==reg.product_id,Registration.snapshot_id.in_(active)).distinct().order_by(Crop.name)).all()
            crops[reg.product_id]=[{'id':r.id,'name':r.name} for r in rows]
        key=(reg.crop_id,reg.pest_id)
        if key not in pairs: pairs[key]=compare(db,*key)
    items=[]
    for reg in regs:
        result=pairs[(reg.crop_id,reg.pest_id)]
        row=next((r for r in result.rows if r.registration_id==reg.id),None)
        if row is None: continue
        applicable=None if crop_id is None else reg.crop_id==crop_id
        items.append({**asdict(row),'crop':result.crop,'pest':result.pest,'coverage':result.coverage,
            'applicable':applicable,'approved_crops':crops[reg.product_id],
            'can_add':applicable is True and not row.banned,
            'reason':'請先選擇案件作物' if applicable is None else ('此登記適用於另一作物，不可加入目前案件' if not applicable else ('涉及禁用成分，不可開立' if row.banned else '加入處方時另依案件採收日與施藥紀錄檢查'))})
    return json_snapshot({'total':total,'offset':offset,'limit':limit,'items':items,
        'notice':'核准作物清單為本站正式快照收錄；未命中不等於官方無登記。can_add 僅為作物及禁用候選檢查，儲存時仍須通過案件檢查。'})
