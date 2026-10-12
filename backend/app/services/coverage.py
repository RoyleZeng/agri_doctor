"""A missing local row is never evidence of absent approval."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from app.models import SourceName, SourceSnapshot, SourceRecord
from app.models.updates import PpmCoverage

PPM_URL = "https://otserv2.acri.gov.tw/PPM/"


def active_ppm(db):
    return db.scalar(select(SourceSnapshot).where(SourceSnapshot.source == SourceName.PPM,
        SourceSnapshot.is_active.is_(True)).order_by(SourceSnapshot.id.desc()))


def snapshot_coverage(db, snap):
    if not snap:
        return {"state": "never", "stored": 0, "verified": 0, "discovered": None,
                "failed": 0, "pending": 0, "percent": None, "complete": False}
    counts = dict(db.execute(select(PpmCoverage.status, func.count()).where(
        PpmCoverage.snapshot_id == snap.id).group_by(PpmCoverage.status)).all())
    meta = snap.meta or {}
    discovered = meta.get("discovered") if meta.get("discovery_complete") else None
    verified = counts.get("success", 0)
    complete = bool(meta.get("complete") and discovered and verified == discovered)
    return {"state": "complete" if complete else ("partial" if discovered else "unknown"),
            "stored": snap.row_count, "verified": verified, "discovered": discovered,
            "failed": counts.get("failed", 0), "pending": counts.get("pending", 0),
            "percent": round(verified / discovered * 100, 1) if discovered else None,
            "complete": complete, "snapshot_id": snap.id,
            "disclaimer": "比例僅對本次成功解析的官方目錄；不保證涵蓋目錄以外或最新公告。"}


def coverage_overview(db):
    active = active_ppm(db)
    latest = db.scalar(select(SourceSnapshot).where(SourceSnapshot.source == SourceName.PPM)
        .order_by(SourceSnapshot.id.desc()))
    def info(s):
        status = s.status.value if s else 'never'
        if s and (s.meta or {}).get('publication_error'):
            status = 'publication_failed'
        elif s and status == 'running':
            heartbeat = (s.meta or {}).get('heartbeat_at')
            last = datetime.fromisoformat(heartbeat) if heartbeat else s.started_at
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - last > timedelta(minutes=10):
                status = 'stalled'
        return {**snapshot_coverage(db, s), "status": status,
                "started_at": s.started_at.isoformat() if s else None,
                "finished_at": s.finished_at.isoformat() if s and s.finished_at else None,
                "heartbeat_at": (s.meta or {}).get("heartbeat_at") if s else None,
                "limited_run": bool((s.meta or {}).get("requested_limit")) if s else False,
                "is_active": bool(s and s.is_active)}
    return {"active": info(active), "latest": info(latest), "source_url": PPM_URL,
            "message": "未收錄或完整性未驗證，不等於官方無核准用藥。"}


def pair_coverage(db, crop_id, pest_id):
    snap = active_ppm(db)
    records = db.scalars(select(SourceRecord).where(SourceRecord.snapshot_id == snap.id,
        SourceRecord.record_kind == "ppm_pest_detail", SourceRecord.crop_id == crop_id,
        SourceRecord.pest_id == pest_id)).all() if snap else []
    receipts = db.scalars(select(PpmCoverage).where(PpmCoverage.snapshot_id == snap.id,
        PpmCoverage.record_id.in_([r.id for r in records]), PpmCoverage.status == "success")).all() if records else []
    verified = bool(records and len(receipts) == len(records) and (snap.meta or {}).get("complete"))
    drug_count = sum(len(r.payload.get("drugs") or []) for r in records)
    state = "not_collected" if not records else ("unverified" if not verified else ("covered" if drug_count else "verified_empty"))
    messages = {
        "not_collected": "這個組合尚未收錄，不能判定是否有核准用藥。請查閱植保系統原站。",
        "unverified": "已有部分資料，但這個組合的爬取完整性尚未驗證，清單可能不完整。",
        "covered": "此組合已完成頁面解析；以下為該次快照列出的用藥，使用前仍請核對最新公告。",
        "verified_empty": "此組合已完成頁面解析，該次收錄頁面未列藥劑；不代表最新公告或其他作物群組均無核准用藥。",
    }
    return {"state": state, "label": {"not_collected":"尚未收錄", "unverified":"完整性待驗證",
        "covered":"已收錄用藥", "verified_empty":"已檢索・頁面未列藥劑"}[state],
        "message": messages[state], "can_assert_page_absence": verified,
        "snapshot_id": snap.id if snap else None,
        "fetched_at": snap.finished_at.isoformat() if snap and snap.finished_at else None,
        "source_url": records[0].source_url if records else PPM_URL,
        "registered_rows": drug_count}
