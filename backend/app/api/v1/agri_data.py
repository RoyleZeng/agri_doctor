"""農業資料庫只讀端點：查本地已發布快照，沒有隱藏的上游 API 呼叫。"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.api.v1.planning import current_user
from app.models.moa import MoaRecord, MoaSnapshot
from app.services.moa_catalog import CATALOG, FIELDS, NOTICES, normalize, observed_state
from app.services.moa_store import DatabaseStore, dataset_key, aware

router=APIRouter(prefix='/agri-data',tags=['本地農業資料庫'],dependencies=[Depends(current_user)])

def meta(db,name,now):
    store=DatabaseStore(db);state=store.state(dataset_key(name,{}))
    snap=db.get(MoaSnapshot,state.active_snapshot_id) if state and state.active_snapshot_id else None
    stale=bool(snap and (now>=aware(snap.refresh_after) or state.last_error))
    return dict(endpoint=name,name=CATALOG[name].name,group=CATALOG[name].group,available=bool(snap),storage='database',
        status=('stale' if stale else 'ok') if snap else (state.last_error if state and state.last_error else 'not_synced'),
        row_count=snap.row_count if snap else 0,snapshot_id=snap.id if snap else None,
        scope=snap.params if snap and name.startswith('Auto') else {},
        fetched_at=aware(snap.fetched_at).isoformat() if snap else None,
        last_attempt_at=aware(state.last_attempt_at).isoformat() if state and state.last_attempt_at else None,
        last_error=state.last_error if state else None,source_url='https://data.moa.gov.tw/api.aspx',
        hours=CATALOG[name].hours,notice=NOTICES[CATALOG[name].group],exact_search=bool(CATALOG[name].exact))

@router.get('/datasets')
def datasets(db:Session=Depends(get_db)):
    now=datetime.now(timezone.utc)
    return [meta(db,name,now) for name in CATALOG]

@router.get('/{name}/records')
def records(name:str,q:str=Query(default='',max_length=120),page:int=Query(default=1,ge=1),
            page_size:int=Query(default=20,ge=1,le=50),db:Session=Depends(get_db)):
    if name not in CATALOG:raise HTTPException(404,'未知的農業資料集')
    now=datetime.now(timezone.utc);source=meta(db,name,now);needle=normalize(q)
    fields=FIELDS[name];rows=[];total=0
    if source['available']:
        stmt=select(MoaRecord).where(MoaRecord.snapshot_id==source['snapshot_id'])
        if needle:stmt=stmt.where(MoaRecord.search_text.contains(needle,autoescape=True))
        if needle and CATALOG[name].exact:
            # 藥名只能完整相等；候選先 SQL 縮小範圍，再用與寫入相同的 NFKC 規則比較。
            # 不用字串包含關係，避免把單劑當成名稱含該單劑的複方。
            candidates=db.scalars(stmt.order_by(MoaRecord.ordinal)).all()
            def matches(record):
                payload=record.payload
                values={**payload,'_permit':str(payload.get('Permit',''))+str(payload.get('PermitNumber',''))}
                return any(normalize(values.get(k))==needle for k in CATALOG[name].exact)
            found=[r for r in candidates if matches(r)];total=len(found)
            rows=found[(page-1)*page_size:page*page_size]
        else:
            total=db.scalar(select(func.count()).select_from(stmt.subquery()))
            rows=db.scalars(stmt.order_by(MoaRecord.ordinal).offset((page-1)*page_size).limit(page_size)).all()
    items=[]
    for record in rows:
        observation=observed_state(record.payload,now) if name.startswith('Auto') else None
        items.append(dict(id=record.ordinal,title=record.title,values=record.payload,observation_state=observation))
    return dict(source=source,fields=fields,items=items,total=total,page=page,page_size=page_size,query=q)
