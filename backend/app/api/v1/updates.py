from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models import SourceSnapshot, SourceName
from app.models.updates import SourceChange, UpdateJob
from app.services.coverage import coverage_overview

router = APIRouter(tags=['source updates'])


@router.get('/coverage')
def coverage(response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return coverage_overview(db)


@router.get('/updates')
def updates(response: Response, days: int = Query(7, ge=1, le=365),
            limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    since = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(SourceChange).where(SourceChange.detected_at >= since)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(SourceChange.detected_at.desc(), SourceChange.id.desc()).limit(limit).offset(offset)).all()
    latest = []
    for source in SourceName:
        snap = db.scalar(select(SourceSnapshot).where(SourceSnapshot.source == source).order_by(SourceSnapshot.id.desc()))
        latest.append({'source':source.value,'status':('publication_failed' if (snap.meta or {}).get('publication_error') else snap.status.value) if snap else 'never',
            'finished_at':snap.finished_at.isoformat() if snap and snap.finished_at else None,
            'published':bool(snap and snap.is_active),
            'complete':bool(snap and (snap.meta or {}).get('complete'))})
    return {'days':days,'total':total,'items':[{'id':r.id,'source':r.source,'kind':r.kind,
        'title':r.title,'before':r.before,'after':r.after,'source_url':r.source_url,
        'effective_date':r.effective_date,'detected_at':r.detected_at.isoformat(),
        'snapshot_id':r.snapshot_id,'previous_snapshot_id':r.previous_snapshot_id} for r in rows],
        'sources':latest,'notice':'這裡是本站抓取快照的差異，發現日不是公告日或生效日。沒有變更紀錄不代表官方沒有更新。'}


@router.get('/update-schedule')
def update_schedule(response: Response, db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    now = datetime.now(timezone.utc)
    rows = db.scalars(select(UpdateJob).order_by(UpdateJob.name)).all()
    return [{'name':r.name,'status':r.status if now-r.heartbeat_at < timedelta(minutes=3) else 'offline',
        'heartbeat_at':r.heartbeat_at.isoformat(), 'next_run_at':r.next_run_at.isoformat() if r.next_run_at else None,
        'schedule_label':r.schedule_label} for r in rows]
