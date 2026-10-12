"""食藥署農藥殘留容許量爬蟲驅動。

原本想用站方的「匯出Excel」一次拿全表，但那顆按鈕是 __doPostBack，
實測從程式送 POST（帶齊 cookie / __VIEWSTATE / __EVENTVALIDATION /
Referer / Origin）一律被回「找不到網路資源」，看起來是前端擋非瀏覽器請求。

好在分頁是單純的 GET `&p=N`，所以改走逐頁抓：
    容許量 828 頁 + 禁用 7 頁 + 得免訂定 5 頁 ≈ 840 次請求。
以 1.5 秒節流計約 21 分鐘，每週跑一次可以接受，而且比對抗 WAF 穩定得多。
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import asdict

from sqlalchemy.orm import Session

from app.core.config import settings
from app.crawlers.base import ThrottledClient, add_record, snapshot_scope, job_lock
from app.crawlers.fda_parser import (
    TAB_BANNED, TAB_EXEMPT, TAB_EXOGENOUS, TAB_MRL, FdaSchemaError, page_url, parse_list_page,
)
from app.db.session import SessionLocal
from app.models import SourceName

log = logging.getLogger(__name__)

TABS = [
    (TAB_MRL, "fda_mrl", "農藥殘留容許量"),
    (TAB_EXOGENOUS, "fda_mrl_exogenous", "外源性農藥殘留容許量"),
    (TAB_EXEMPT, "fda_exempt", "得免訂定容許量之農藥"),
    (TAB_BANNED, "fda_banned", "公告禁用農藥"),
]


def _rows_of(page, tab: int) -> list:
    if tab in (TAB_MRL, TAB_EXOGENOUS):
        return page.mrl_rows
    if tab == TAB_BANNED:
        return page.banned_rows
    return page.exempt_rows


def crawl_tab(
    db: Session, snap, archive, client: ThrottledClient,
    tab: int, kind: str, label: str, *, max_pages: int | None = None,
) -> int:
    base = settings.fda_list_url
    first_url = page_url(base, tab=tab, page=1)
    resp = client.get(first_url)
    archive.save(f"tk{tab}_p001.html", resp.text)

    first = parse_list_page(resp.text, tab)
    if first.total_pages is None and first.total_rows is not None and first.total_rows == len(_rows_of(first, tab)):
        first.total_pages = 1  # Single-page tabs omit the paginator; declared total still verifies all rows.
    if first.total_pages is None or first.total_rows is None:
        raise FdaSchemaError("來源未提供總頁數與總筆數，無法驗證完整性")
    total_pages = first.total_pages
    if max_pages:
        total_pages = min(total_pages, max_pages)
    log.info("%s: %s 筆 / %s 頁", label, first.total_rows, first.total_pages)

    count = 0
    seen_seq = set()
    for row in _rows_of(first, tab):
        if not row.seq or row.seq in seen_seq:
            raise FdaSchemaError("項次缺漏或重複，不能驗證完整性")
        seen_seq.add(row.seq)
        add_record(db, snap, kind=kind, payload=asdict(row), source_url=first_url,
                   hash_parts=(kind, row.seq, first_url))
        count += 1

    for p in range(2, total_pages + 1):
        url = page_url(base, tab=tab, page=p)
        try:
            resp = client.get(url)
        except RuntimeError:
            log.exception("%s p=%s failed", label, p)
            raise

        archive.save(f"tk{tab}_p{p:03d}.html", resp.text)

        try:
            page = parse_list_page(resp.text, tab)
        except FdaSchemaError:
            log.exception("%s p=%s schema error", label, p)
            raise

        rows = _rows_of(page, tab)
        if page.total_rows != first.total_rows or page.total_pages != first.total_pages:
            raise FdaSchemaError("爬取期間來源總數改變，請保留快照後重跑")
        if not rows:
            raise FdaSchemaError(f"{label} 第{p}頁沒有資料列")
        for row in rows:
            if not row.seq or row.seq in seen_seq:
                raise FdaSchemaError("項次缺漏或重複，可能重複取得同一頁")
            seen_seq.add(row.seq)
            add_record(db, snap, kind=kind, payload=asdict(row), source_url=url,
                       hash_parts=(kind, row.seq, url))
            count += 1

        if p % 25 == 0:
            db.commit()
            log.info("  %s %d/%d pages, %d rows", label, p, total_pages, count)

    if max_pages is None and count != first.total_rows:
        raise FdaSchemaError(f"{label} 實收 {count} 與來源宣告 {first.total_rows} 不同")
    db.commit()
    return count


def _crawl(
    db: Session, *, max_pages: int | None = None, activate: bool = True,
    tabs: list[int] | None = None,
) -> int:
    wanted = tabs or [t[0] for t in TABS]

    with snapshot_scope(db, SourceName.FDA) as (snap, archive):
        with ThrottledClient() as client:
            totals: dict[str, int] = {}
            for tab, kind, label in TABS:
                if tab not in wanted:
                    continue
                totals[kind] = crawl_tab(
                    db, snap, archive, client, tab, kind, label, max_pages=max_pages
                )
                snap.row_count = sum(totals.values())
                snap.meta = {**totals, "complete": False, "requested_max_pages": max_pages}
                db.commit()

            snap.row_count = sum(totals.values())
            snap.meta = {**totals, "complete": max_pages is None and set(wanted) == {t[0] for t in TABS}, "validated_page_totals": True}
            db.commit()

    if activate and snap.meta["complete"]:
        from app.services.source_updates import publish_snapshot
        publish_snapshot(db, snap)
    return snap.row_count


def crawl(db: Session, **kwargs):
    with job_lock(db, "fda"):
        return _crawl(db, **kwargs)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Crawl 食藥署 農藥殘留容許量")
    ap.add_argument("--max-pages", type=int, help="每個分頁籤最多抓 N 頁（煙霧測試用）")
    ap.add_argument("--no-activate", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        n = crawl(db, max_pages=args.max_pages, activate=not args.no_activate)
        print(f"fda: {n} records")
    finally:
        db.close()


if __name__ == "__main__":
    main()
