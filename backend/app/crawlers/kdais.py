"""高改場「作物安全用藥資訊」爬蟲驅動。

清單頁只有兩頁、35 份 PDF，但每份 0.3~0.7MB，所以用標題裡的修訂日期
做增量判斷：修訂日期沒變就不重抓、不重解。
"""

from __future__ import annotations

import argparse
import logging
import hashlib
from sqlalchemy import select
from dataclasses import asdict
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.crawlers.base import ThrottledClient, add_record, snapshot_scope, job_lock
from app.crawlers.kdais_parser import KdaisDoc, find_next_page, parse_document_list, parse_pdf
from app.db.session import SessionLocal
from app.models import KdaisDocument, SourceName, SourceRecord
from app.services.normalize import active_snapshot

log = logging.getLogger(__name__)

SITE_ROOT = "https://www.kdais.gov.tw/"


def list_documents(client: ThrottledClient, archive) -> list[KdaisDoc]:
    docs: list[KdaisDoc] = []
    seen_urls: set[str] = set()
    url: str | None = settings.kdais_list_url
    page = 0

    while url and page < 10:      # 目前只有兩頁，上限純粹是防呆
        resp = client.get(url)
        page += 1
        archive.save(f"list_p{page}.html", resp.text)
        for doc in parse_document_list(resp.text, SITE_ROOT):
            if doc.pdf_url not in seen_urls:
                seen_urls.add(doc.pdf_url)
                docs.append(doc)
        nxt = find_next_page(resp.text, SITE_ROOT)
        url = nxt if nxt and nxt != url else None

    if url:
        raise RuntimeError("高改場目錄超過分頁上限，不能宣告完整")
    log.info("found %d KDAIS documents", len(docs))
    return docs


def crawl(db: Session, *, force: bool = False, activate: bool = True) -> int:
    with job_lock(db, "kdais"):
        previous = active_snapshot(db, SourceName.KDAIS)
        previous_records = db.scalars(select(SourceRecord).where(SourceRecord.snapshot_id == previous.id)).all() if previous else []
        old_docs = {r.payload['title']:r.payload for r in previous_records if r.record_kind == 'kdais_document'}
        with snapshot_scope(db, SourceName.KDAIS) as (snap, archive):
            with ThrottledClient() as client:
                docs = list_documents(client, archive)
                if not docs:
                    raise RuntimeError("目錄未找到任何摘要表")
                count, failures = 0, []
                for index, doc in enumerate(docs):
                    try:
                        response = client.get(doc.pdf_url)
                        if not response.content.startswith(b'%PDF'):
                            raise ValueError('來源未回傳 PDF')
                        fingerprint = hashlib.sha256(response.content).hexdigest()
                        path = archive.save(f'{doc.title}.pdf', response.content)
                        old = old_docs.get(doc.title)
                        previous_rows = [r.payload for r in previous_records if r.record_kind == 'kdais_row' and r.payload.get('document_title') == doc.title]
                        if not force and old and old.get('sha256') == fingerprint and previous_rows:
                            payloads, warnings = previous_rows, old.get('warnings', [])
                        else:
                            rows, warnings = parse_pdf(str(path))
                            if not rows:
                                raise ValueError('PDF 未解析到資料列，需人工確認')
                            payloads = [asdict(row) | {'crop_text':doc.crop_text,'document_title':doc.title,'revision_date':doc.revision_date} for row in rows]
                        for payload in payloads:
                            add_record(db, snap, kind='kdais_row', payload=payload, source_url=doc.pdf_url)
                        add_record(db, snap, kind='kdais_document', payload={'title':doc.title,'crop_text':doc.crop_text,
                            'revision_date':doc.revision_date,'pdf_url':doc.pdf_url,'file_size':doc.file_size,
                            'local_path':str(path),'sha256':fingerprint,'rows':len(payloads),'warnings':warnings[:20]}, source_url=doc.pdf_url)
                        count += len(payloads)
                    except Exception as exc:
                        failures.append({'title':doc.title,'error':str(exc)[:1000]})
                        log.exception('高改場下載或解析失敗：%s', doc.title)
                    snap.row_count = count
                    snap.meta = {'documents':len(docs),'processed':index + 1, 'failures':failures,'complete':False}
                    db.commit()
                snap.meta = {**snap.meta,'complete':not failures,'processed':len(docs)-len(failures)}
                db.commit()
                if failures:
                    raise RuntimeError(f'{len(failures)} 份 PDF 失敗，本次不發布')
        if activate:
            from app.services.source_updates import publish_snapshot
            publish_snapshot(db, snap)
        return snap.row_count


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Crawl 高改場 作物安全用藥資訊")
    ap.add_argument("--force", action="store_true", help="忽略修訂日期，全部重抓")
    ap.add_argument("--no-activate", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        n = crawl(db, force=args.force, activate=not args.no_activate)
        print(f"kdais: {n} records")
    finally:
        db.close()


if __name__ == "__main__":
    main()
