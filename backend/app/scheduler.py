"""Staggered government-source updates with singleton execution and heartbeat."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from apscheduler.schedulers.blocking import BlockingScheduler
from app.crawlers import fda, kdais, ppm
from app.crawlers.base import job_lock
from app.db.session import SessionLocal
from app.models.updates import UpdateJob
from app.services.matcher import scan_all_issues
from app.services.normalize import normalize_all

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger(__name__)


def _run(name, fn):
    with SessionLocal() as db:
        try:
            log.info('%s started', name)
            fn(db)  # validated publication also normalizes and emits changes
            log.info('%s finished', name)
        except Exception:
            db.rollback()
            log.exception('%s failed; previous published data retained', name)


def rebuild(db=None):
    own = db is None
    db = db or SessionLocal()
    try:
        with job_lock(db, 'publish'):
            normalize_all(db)
            scan_all_issues(db)
    finally:
        if own: db.close()


def build_scheduler():
    sched = BlockingScheduler(timezone='Asia/Taipei', job_defaults={'max_instances':1,'coalesce':True,'misfire_grace_time':3600})
    schedules = {
        'kdais':('高改場：每日 03:10',kdais.crawl,{'hour':3,'minute':10}),
        'ppm':('植保完整目錄：每週一 03:40',ppm.crawl,{'day_of_week':'mon','hour':3,'minute':40}),
        'fda':('食藥署完整四類資料：每週一 05:30',fda.crawl,{'day_of_week':'mon','hour':5,'minute':30}),
    }
    for name, (_,fn,trigger) in schedules.items():
        sched.add_job(_run,'cron',id=name,args=[name,fn],**trigger)
    def heartbeat():
        with SessionLocal() as db:
            now = datetime.now(timezone.utc)
            for name,(label,_,_) in schedules.items():
                job = sched.get_job(name)
                db.merge(UpdateJob(name=name,status='online',heartbeat_at=now,
                    next_run_at=getattr(job,'next_run_time',None),schedule_label=label))
            db.commit()
    sched.add_job(heartbeat,'interval',seconds=60,id='heartbeat',next_run_time=datetime.now(timezone.utc))
    from app.services.notifications import deliver_ban_updates
    sched.add_job(_run, 'interval', minutes=5, id='email', args=['email', deliver_ban_updates])
    # 農事日誌照片：軟刪超過保留期與孤兒檔在離峰時清掉；照片列本身不刪。
    from app.services.farm_media import purge_media_job
    sched.add_job(_run, 'cron', id='media_purge', args=['media_purge', purge_media_job], hour=4, minute=30)
    from app.jobs import moa_sync
    # 每半小時巡檢，個別資料集的 TTL 決定是否需要真的查上游；跨程序以 advisory lock 防重跑。
    sched.add_job(_run, 'cron', id='moa_sync', args=['moa_sync', moa_sync], minute='7,37')
    return sched


def main():
    with SessionLocal() as db, job_lock(db, 'scheduler'):
        build_scheduler().start()


if __name__ == '__main__':
    main()
