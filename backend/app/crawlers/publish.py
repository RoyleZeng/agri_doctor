"""Publish a captured snapshot, optionally waiting for its existing crawl.

Does not crawl again, retry failed targets, or roll back to an older snapshot.
Completeness, normalization, source changes and issue scans share publication's
transaction. Useful after a reviewed --no-activate full crawl.
"""
from __future__ import annotations
import argparse
import logging
import time
from datetime import datetime,timezone
from app.db.session import SessionLocal
from app.models import SourceSnapshot,SnapshotStatus
from app.services.source_updates import publish_snapshot

log=logging.getLogger(__name__)


def attempt(db, snapshot_id):
    snap=db.get(SourceSnapshot,snapshot_id)
    if snap is None:
        raise ValueError('快照不存在')
    if snap.is_active:
        return 'already_published'
    if snap.status == SnapshotStatus.RUNNING:
        return 'waiting'
    if snap.status != SnapshotStatus.SUCCESS or not (snap.meta or {}).get('complete'):
        raise ValueError('爬取失敗或不完整，保留原本正式資料；請查看來源狀態與錯誤紀錄')
    try:
        publish_snapshot(db,snap)
    except Exception as exc:
        db.rollback()
        snap=db.get(SourceSnapshot,snapshot_id)
        snap.meta={**(snap.meta or {}),'publication_error':type(exc).__name__,
            'publication_failed_at':datetime.now(timezone.utc).isoformat()}
        db.commit()
        raise
    return 'published'


def main():
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot_id',type=int)
    parser.add_argument('--wait-seconds',type=int,default=0)
    args=parser.parse_args()
    if not 0 <= args.wait_seconds <= 43200:
        parser.error('--wait-seconds must be between 0 and 43200')
    deadline=time.monotonic()+args.wait_seconds
    log.info('Monitoring existing snapshot %s; no additional crawl will be started',args.snapshot_id)
    while True:
        with SessionLocal() as db:
            state=attempt(db,args.snapshot_id)
        if state!='waiting':
            log.info('Snapshot %s: %s',args.snapshot_id,state)
            return
        remaining=deadline-time.monotonic()
        if remaining<=0:
            raise TimeoutError('等待期限已到；未發布快照，原正式資料保留')
        time.sleep(min(30,remaining))


if __name__=='__main__':
    main()
