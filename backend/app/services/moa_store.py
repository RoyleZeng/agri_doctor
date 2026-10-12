"""本地資料庫是查詢來源；只有同步工作會連上游，失敗快照不替換 active。"""
from datetime import datetime, timezone
import hashlib
import json
import uuid
from sqlalchemy import select
from app.models.moa import MoaDataset, MoaSnapshot, MoaRecord
from app.services.moa_catalog import CATALOG, normalize, title_for

SOURCE='https://data.moa.gov.tw/api.aspx'

def dataset_key(endpoint,params):
    return hashlib.sha256(json.dumps([endpoint,params],sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

class DatabaseStore:
    def __init__(self,db):self.db=db
    def state(self,key):return self.db.get(MoaDataset,key)
    def load(self,key):
        state=self.state(key)
        snap=self.db.get(MoaSnapshot,state.active_snapshot_id) if state and state.active_snapshot_id else None
        if not snap or not snap.complete:return None
        return dict(data=snap.data,fetched_at=aware(snap.fetched_at).isoformat(),refresh_after=aware(snap.refresh_after).timestamp(),complete=True,
                    row_count=snap.row_count,snapshot_id=snap.id,content_hash=snap.content_hash,source_url=SOURCE,
                    endpoint=snap.endpoint,params=snap.params,storage='database',last_error=state.last_error)
    def save(self,key,result,pages):
        now=datetime.fromisoformat(result['fetched_at'])
        state=self.state(key)
        if state is None:
            state=MoaDataset(key=key,endpoint=result['endpoint'],params=result['params']);self.db.add(state)
        snap=MoaSnapshot(id=str(uuid.uuid4()),dataset_key=key,endpoint=result['endpoint'],params=result.get('upstream_params',result['params']),fetched_at=now,
            refresh_after=datetime.fromtimestamp(result['refresh_after'],timezone.utc),complete=True,row_count=result['row_count'],
            content_hash=result['snapshot_id'],raw_pages=pages,data=result['data'])
        self.db.add(snap);self.db.flush()
        if snap.endpoint in CATALOG:
            self.db.add_all([MoaRecord(snapshot_id=snap.id,ordinal=i,title=title_for(snap.endpoint,row),
                search_text=normalize(' '.join(str(v) for v in row.values() if v is not None)),payload=row) for i,row in enumerate(result['data'])])
        state.active_snapshot_id=snap.id;state.last_attempt_at=now;state.last_error=None
        self.db.commit()
        return self.load(key)
    def failed(self,key,endpoint,params,now,error,pages):
        state=self.state(key)
        if state is None:
            state=MoaDataset(key=key,endpoint=endpoint,params=params);self.db.add(state)
        self.db.add(MoaSnapshot(id=str(uuid.uuid4()),dataset_key=key,endpoint=endpoint,params=params,fetched_at=now,refresh_after=now,
            complete=False,row_count=sum(len(p if isinstance(p,list) else p.get('Data',[])) for p in pages),content_hash=None,error=error,raw_pages=pages,data=None))
        state.last_attempt_at=now;state.last_error=error
        self.db.commit()
    def read(self,key,now):
        cached=self.load(key);state=self.state(key)
        if not cached:
            error=state.last_error if state else 'not_synced'
            return dict(data=None,status=error,error=error,complete=False,cached=False,stale=False,fetched_at=None,snapshot_id=None,source_url=SOURCE,storage='database')
        error=cached.pop('last_error',None)
        stale=bool(error or now.timestamp()>=cached['refresh_after'])
        return {**cached,'status':'stale' if stale else 'ok','error':error or ('refresh_due' if stale else None),'cached':True,'stale':stale}
    def previous_market(self,params,now):
        states=self.db.scalars(select(MoaDataset).where(MoaDataset.endpoint=='AgriProductsTransType',MoaDataset.active_snapshot_id.is_not(None))).all()
        from app.services.moa_parser import trade_date
        def span(p):
            try:return (datetime.fromisoformat(trade_date(p['End_time']))-datetime.fromisoformat(trade_date(p['Start_time']))).days
            except (ValueError,TypeError,KeyError):return None
        eligible=[s for s in states if s.params.get('CropName')==params.get('CropName') and span(s.params)==span(params)]
        results=[self.read(s.key,now) for s in eligible]
        if not results:return None
        result=max(results,key=lambda r:r['fetched_at'])
        return {**result,'status':'stale','stale':True,'error':'period_outdated'}
