"""爬蟲共用骨架：節流、重試、原始檔存檔、snapshot 生命週期。

原則
----
1. 對政府站台友善：預設 1.5 秒一次請求，只在離峰排程。
2. 原始 HTML/PDF 一律落地到 data/raw/<source>/<date>/，之後 parser 改版
   可以重跑而不用再打人家的站。
3. 失敗不覆蓋舊資料：新 snapshot 通過檢查才 activate。
"""

from __future__ import annotations

import hashlib
import logging
import ssl
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx
import certifi
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import SnapshotStatus, SourceName, SourceRecord, SourceSnapshot

log = logging.getLogger(__name__)


def tls_context():
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(Path(__file__).parent / "certs/twca_intermediates.pem")
    return context


@contextmanager
def job_lock(db: Session, name: str):
    """Dedicated connection keeps the PostgreSQL lock across batch commits."""
    key = int.from_bytes(hashlib.sha256(f"agri:{name}".encode()).digest()[:7], "big")
    if db.get_bind().dialect.name != "postgresql":
        yield
        return
    with db.get_bind().connect() as conn:
        if not conn.scalar(text("select pg_try_advisory_lock(:key)"), {"key": key}):
            raise RuntimeError(f"{name} 已有工作執行中，未啟動重複工作")
        try:
            yield
        finally:
            conn.execute(text("select pg_advisory_unlock(:key)"), {"key": key})


def row_hash(*parts: object) -> str:
    joined = "".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


class ThrottledClient:
    """httpx client + 固定間隔節流 + 指數退避重試。"""

    def __init__(self, base_url: str = "", rate_limit: float | None = None) -> None:
        self.rate_limit = settings.crawl_rate_limit if rate_limit is None else rate_limit
        self._last_request = 0.0
        self._client = httpx.Client(
            base_url=base_url,
            headers={"User-Agent": settings.user_agent},
            timeout=settings.crawl_timeout,
            follow_redirects=True,
            verify=tls_context(),
        )

    def __enter__(self) -> "ThrottledClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self._last_request = time.monotonic()

    def request(self, method: str, url: str, *, retries: int = 3, **kw) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(retries):
            self._wait()
            try:
                resp = self._client.request(method, url, **kw)
                if resp.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        f"server {resp.status_code}", request=resp.request, response=resp
                    )
                resp.raise_for_status()
                return resp
            except (httpx.HTTPError, httpx.TimeoutException) as exc:
                last_exc = exc
                backoff = 2**attempt
                log.warning("request failed (%s), retry in %ss: %s", url, backoff, exc)
                time.sleep(backoff)
        raise RuntimeError(f"giving up on {url}") from last_exc

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    @property
    def cookies(self) -> httpx.Cookies:
        return self._client.cookies


class RawArchive:
    """把抓回來的原始位元組存到 data/raw/<source>/<timestamp>/。"""

    def __init__(self, source: SourceName, started: datetime) -> None:
        stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
        self.dir = Path(settings.raw_data_dir) / source.value / stamp
        self.dir.mkdir(parents=True, exist_ok=True)

    def save(self, name: str, data: bytes | str) -> Path:
        safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:180]
        path = self.dir / safe
        if isinstance(data, str):
            path.write_text(data, encoding="utf-8")
        else:
            path.write_bytes(data)
        return path


@contextmanager
def snapshot_scope(db: Session, source: SourceName):
    """開一個 snapshot，成功就 SUCCESS，例外就 FAILED 並保留錯誤訊息。

    activate 由呼叫端在資料檢查通過後自行決定（見 activate_snapshot）。
    """
    started = datetime.now(timezone.utc)
    snap = SourceSnapshot(
        source=source, status=SnapshotStatus.RUNNING, started_at=started
    )
    db.add(snap)
    db.commit()
    db.refresh(snap)

    archive = RawArchive(source, started)
    snap.raw_dir = str(archive.dir)
    db.commit()

    try:
        yield snap, archive
    except Exception as exc:  # noqa: BLE001 - 要把錯誤寫進 snapshot 再拋
        db.rollback()
        snap.status = SnapshotStatus.FAILED
        snap.error = f"{type(exc).__name__}: {exc}"
        snap.finished_at = datetime.now(timezone.utc)
        db.commit()
        raise
    else:
        if snap.status == SnapshotStatus.RUNNING:
            snap.status = SnapshotStatus.SUCCESS
        snap.finished_at = datetime.now(timezone.utc)
        db.commit()


def activate_snapshot(db: Session, snap: SourceSnapshot, *, min_rows: int = 1, commit: bool = True) -> bool:
    """資料量合理才切換 active，避免官網改版後用空資料蓋掉好資料。"""
    if (snap.status != SnapshotStatus.SUCCESS or snap.row_count < min_rows
            or (snap.meta or {}).get("complete") is not True):
        log.error(
            "refusing to activate %s snapshot %s (status=%s rows=%s)",
            snap.source, snap.id, snap.status, snap.row_count,
        )
        return False

    if snap.source == SourceName.PPM:
        from app.services.coverage import snapshot_coverage
        from sqlalchemy import func, select
        actual = db.scalar(select(func.count()).select_from(SourceRecord).where(
            SourceRecord.snapshot_id == snap.id, SourceRecord.record_kind == "ppm_pest_detail"))
        if not snapshot_coverage(db, snap)["complete"] or actual != snap.row_count:
            log.error("PPM receipts or record count do not support completeness")
            return False

    previous = (
        db.query(SourceSnapshot)
        .filter(SourceSnapshot.source == snap.source, SourceSnapshot.is_active.is_(True))
        .all()
    )
    # 資料量掉超過一半，多半是官網改版或爬到一半斷線，擋下來人工看。
    for old in previous:
        if old.row_count and snap.row_count < old.row_count * 0.5:
            snap.status = SnapshotStatus.SCHEMA_DRIFT
            snap.error = (
                f"row count dropped from {old.row_count} to {snap.row_count}; "
                "refusing to activate - check whether the site changed"
            )
            db.commit()
            log.error(snap.error)
            return False

    for old in previous:
        old.is_active = False

    snap.is_active = True
    db.flush()
    if commit:
        db.commit()
    return True


def add_record(
    db: Session,
    snap: SourceSnapshot,
    *,
    kind: str,
    payload: dict,
    source_url: str | None = None,
    hash_parts: tuple = (),
) -> SourceRecord:
    rec = SourceRecord(
        snapshot_id=snap.id,
        source=snap.source,
        record_kind=kind,
        source_url=source_url,
        payload=payload,
        row_hash=row_hash(*(hash_parts or (kind, source_url, str(payload)))),
    )
    db.add(rec)
    return rec
