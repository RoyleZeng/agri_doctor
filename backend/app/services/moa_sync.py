"""完整分頁同步；可獨立排程，讀取 API 不觸發官方請求。"""
from datetime import datetime, timedelta, timezone
import json
import hashlib
import logging
from sqlalchemy import select
from app.crawlers.base import job_lock
from app.models.moa import MoaDataset
from app.models.planning import FarmPlan
from app.services import moa
from app.services.moa_catalog import CATALOG, parse_catalog
from app.services.moa_store import DatabaseStore, dataset_key, aware
from app.services.moa_parser import parse_prices, parse_costs, trade_date, unwrap

log=logging.getLogger(__name__)

def import_files(db):
    """舊版檔案快取先重播驗證再遷入 DB；缺原始檔或不完整的舊資料不匯入。"""
    store=DatabaseStore(db);count=0
    for path in (moa.cache_root()/'current').glob('*.json'):
        try:
            saved=json.loads(path.read_text()); endpoint=saved['endpoint'];params=saved['params']
            key=dataset_key(endpoint,params)
            if store.load(key) or not saved.get('complete'):continue
            raw=json.loads((moa.cache_root()/'snapshots'/f"{saved['snapshot_id']}.json").read_text())
            if raw['endpoint']!=endpoint or raw['params']!=params:continue
            pages=raw['pages'];rows=[]
            payload=dict(endpoint=endpoint,params=params,pages=pages)
            # 舊版曾只雜湊 pages；兩版都需驗過原始內容，不能只相信檔名。
            digests={hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest() for value in (pages,payload)}
            if saved['snapshot_id'] not in digests:continue
            for i,page in enumerate(pages):
                batch,more=unwrap(page)
                if more != (i<len(pages)-1):raise ValueError('incomplete')
                rows.extend(batch)
            if not pages:continue
            if endpoint=='ProductCost':parsed=parse_costs(rows)
            elif endpoint=='AgriProductsTransType':parsed=parse_prices(rows,trade_date(params['Start_time']),trade_date(params['End_time']))
            else:continue
            if len(rows)!=saved['row_count'] or parsed!=saved['data']:continue
            store.save(key,saved,pages);count+=1
        except (OSError,ValueError,TypeError,KeyError):continue
    return count

def sync(db, *, names=None, client=None, now=None, force=False):
    now=now or datetime.now(timezone.utc)
    selected=list(CATALOG) if names is None else list(names)
    results={}
    with job_lock(db,'moa-sync'):
        import_files(db)
        for name in selected:
            spec=CATALOG[name];key=dataset_key(name,{})
            store=DatabaseStore(db);state=store.state(key)
            if not force and state and state.last_error and (now-aware(state.last_attempt_at)).total_seconds()<120:
                results[name]=state.last_error;continue
            window=None
            if name.startswith('Auto'):
                # 氣象預設含長期歷史；只同步滾動 24 小時，保留快照形成本站歷史，不每小時重抓多年資料。
                end=now.astimezone(timezone(timedelta(hours=8)))
                window={'Start_time':(end-timedelta(hours=24)).strftime('%Y/%m/%d %H:%M'), 'End_time':end.strftime('%Y/%m/%d %H:%M')}
            result=moa.dataset(name,{},ttl=timedelta(hours=spec.hours),validate=lambda rows,n=name:parse_catalog(n,rows),max_pages=200,
                now=now,client=client,db=db,refresh=True,request_params=window)
            results[name]=result['error'] or result['status']
            log.info('MOA %s status=%s rows=%s',name,results[name],result.get('row_count',0))
        if names is None:
            results['ProductCost']=moa.costs(db=db,refresh=True,client=client)['status']
            # 僅同步現有作物與使用者查過的期間，避免每次全臺全作物行情耗盡額度。
            queries={(c,7) for c in db.scalars(select(FarmPlan.crop_name).where(FarmPlan.archived.is_(False))).all() if c}
            states=db.scalars(select(MoaDataset).where(MoaDataset.endpoint=='AgriProductsTransType')).all()
            for state in states:
                try:
                    days=(datetime.fromisoformat(trade_date(state.params['End_time']))-datetime.fromisoformat(trade_date(state.params['Start_time']))).days+1
                    if 1<=days<=14:queries.add((state.params['CropName'],days))
                except (ValueError,TypeError,KeyError):continue
            for crop,days in sorted(queries):
                result=moa.market(crop,days,db=db,refresh=True,client=client)
                results[f'market:{crop}:{days}']=result['error'] or result['status']
    return results
