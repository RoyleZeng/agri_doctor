"""Full PPM directory crawl with per-target receipts and safe publication.

PLC02 uses href links directly to PLC0101. A menu-only traversal sees only the
six promoted lists and must never be described as complete site coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.crawlers.base import ThrottledClient, RawArchive, add_record, job_lock, snapshot_scope
from app.crawlers.ppm_parser import PpmSchemaError, extract_menu_links, parse_crop_directory, parse_pest_detail
from app.db.session import SessionLocal
from app.models import SourceName, SourceSnapshot, SnapshotStatus
from app.models.updates import PpmCoverage

log = logging.getLogger(__name__)
ALL_FUNCTIONS_MENU = "Menu.aspx?ASParam=JTdkWFBYJTE0JTE4NjZpdA=="


def discover_targets(client, archive):
    base = settings.ppm_base_url.rstrip("/") + "/"
    home = client.get(base)
    archive.save("home.html", home.content)
    menus = extract_menu_links(home.text, "Menu.aspx", base)
    menu_url = next((u for u in menus if u.endswith(ALL_FUNCTIONS_MENU)), base + ALL_FUNCTIONS_MENU)
    menu = client.get(menu_url)
    archive.save("menu.html", menu.content)
    catalogs = extract_menu_links(menu.text, "PLC02.aspx", base)
    if not catalogs:
        raise PpmSchemaError("選單未找到完整作物目錄，不能宣告全站覆蓋")
    targets = {}
    for i, url in enumerate(catalogs):
        response = client.get(url)
        archive.save(f"catalog_{i}.html", response.content)
        for item in parse_crop_directory(response.text, base):
            if item.target in targets and targets[item.target].detail_url != item.detail_url:
                raise PpmSchemaError(f"目錄出現重複但連結不同的名稱：{item.target}")
            targets[item.target] = item
    return targets


def _fill(db, snap, archive, client, *, limit=None, resume=False):
    targets = discover_targets(client, archive)
    ledger = db.scalars(select(PpmCoverage).where(PpmCoverage.snapshot_id == snap.id)).all()
    if resume:
        if set(targets) != {x.target_name for x in ledger}:
            raise PpmSchemaError("續跑時目錄已改變，請保留本次失敗快照並重新啟動全量爬取")
    else:
        ledger = [PpmCoverage(snapshot_id=snap.id, target_key=hashlib.sha256(name.encode()).hexdigest(),
            target_name=name, crop_name="", pest_name="", source_url=item.detail_url, status="pending")
            for name, item in targets.items()]
        db.add_all(ledger)
    snap.meta = {"coverage_version": 1, "discovery_complete": True, "discovered": len(targets),
        "requested_limit": limit, "complete": False, "catalog_url": settings.ppm_base_url.rstrip('/') + '/PLC02.aspx'}
    db.commit()
    todo = [row for row in ledger if row.status != "success"]
    if limit is not None:
        todo = todo[:limit]
    log.info("PPM directory: %d targets; %d remaining this run", len(targets), len(todo))
    for i, row in enumerate(todo):
        item = targets[row.target_name]
        row.source_url = item.detail_url
        try:
            response = client.get(item.detail_url)
            path = archive.save(f"detail_{row.target_key}.html", response.content)
            detail = parse_pest_detail(response.text, item.detail_url)
            payload = asdict(detail)
            payload["list_row"] = asdict(item)
            payload["validated_html_sha256"] = hashlib.sha256(response.content).hexdigest()
            payload["archive_path"] = str(path)
            rec = add_record(db, snap, kind="ppm_pest_detail", payload=payload, source_url=item.detail_url)
            db.flush()
            row.crop_name, row.pest_name = detail.crop_name, detail.pest_name
            row.record_id, row.drug_count = rec.id, len(detail.drugs)
            row.status, row.error = "success", None
            row.html_sha256 = payload["validated_html_sha256"]
            row.fetched_at = datetime.now(timezone.utc)
        except (RuntimeError, ValueError) as exc:
            row.status, row.error = "failed", f"{type(exc).__name__}: {str(exc)[:1200]}"
            log.warning("PPM target failed: %s: %s", item.target, row.error)
        snap.row_count = sum(r.status == "success" for r in ledger)
        snap.meta = {**snap.meta, "succeeded": snap.row_count,
            "failed": sum(r.status == "failed" for r in ledger),
            "pending": sum(r.status == "pending" for r in ledger),
            "heartbeat_at": datetime.now(timezone.utc).isoformat()}
        db.commit()
        if (i + 1) % 50 == 0:
            log.info("PPM %d/%d validated", snap.row_count, len(targets))
    complete = bool(targets) and snap.row_count == len(targets) and limit is None
    snap.meta = {**snap.meta, "complete": complete}
    db.commit()
    if not complete and limit is None:
        raise PpmSchemaError(f"尚未完整收錄：{snap.row_count}/{len(targets)}，未發布，失敗目標可續跑")


def crawl(db: Session, *, limit: int | None = None, activate: bool = True, resume_id: int | None = None) -> int:
    if limit is not None and limit < 1:
        raise ValueError("limit 必須大於0")
    with job_lock(db, "ppm"):
        if resume_id is None:
            with snapshot_scope(db, SourceName.PPM) as (snap, archive):
                with ThrottledClient() as client:
                    _fill(db, snap, archive, client, limit=limit)
        else:
            snap = db.get(SourceSnapshot, resume_id)
            if not snap or snap.source != SourceName.PPM or snap.is_active or (snap.meta or {}).get("requested_limit"):
                raise ValueError("僅能續跑未發布的全量 PPM 快照")
            archive = RawArchive(SourceName.PPM, datetime.now(timezone.utc))
            snap.status, snap.error = SnapshotStatus.RUNNING, None
            db.commit()
            try:
                with ThrottledClient() as client:
                    _fill(db, snap, archive, client, resume=True)
                snap.status = SnapshotStatus.SUCCESS
            except Exception as exc:
                db.rollback()
                snap.status, snap.error = SnapshotStatus.FAILED, str(exc)
                raise
            finally:
                snap.finished_at = datetime.now(timezone.utc)
                db.commit()
        if activate and (snap.meta or {}).get("complete"):
            from app.services.source_updates import publish_snapshot
            publish_snapshot(db, snap)
        return snap.row_count


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Crawl full PPM crop directory")
    parser.add_argument("--limit", type=int, help="僅測試，不發布")
    parser.add_argument("--no-activate", action="store_true")
    parser.add_argument("--resume", type=int, help="續跑指定失敗快照；先核对目錄未變")
    args = parser.parse_args()
    with SessionLocal() as db:
        print(f"ppm: {crawl(db, limit=args.limit, activate=not args.no_activate, resume_id=args.resume)} validated targets")


if __name__ == "__main__":
    main()
