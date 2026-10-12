"""後台：觸發爬蟲、跑正規化與比對、PDF 人工校對。

爬蟲是長時間工作，走 BackgroundTasks 立刻回 202，
進度到 source_snapshots 看。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal, get_db
from app.models import Confidence, KdaisDocument, KdaisRecommendation, SourceName, SourceSnapshot
from app.services.matcher import scan_all_issues
from app.services.normalize import normalize_all

router = APIRouter(prefix="/admin", tags=["admin"])
log = logging.getLogger(__name__)


def _run_crawl(source: str) -> None:
    """背景執行；自己開 session，不共用 request 的。"""
    from app.crawlers import fda, kdais, ppm

    runners = {"ppm": ppm.crawl, "fda": fda.crawl, "kdais": kdais.crawl}
    db = SessionLocal()
    try:
        runners[source](db)
    except Exception:  # noqa: BLE001 - 失敗已寫進 snapshot，這裡只記 log
        log.exception("crawl %s failed", source)
    finally:
        db.close()


def _run_rebuild() -> None:
    db = SessionLocal()
    try:
        from app.scheduler import rebuild
        rebuild(db)
    except Exception:  # noqa: BLE001
        log.exception("rebuild failed")
    finally:
        db.close()


@router.post("/crawl/{source}", status_code=202)
def trigger_crawl(source: SourceName, tasks: BackgroundTasks) -> dict:
    tasks.add_task(_run_crawl, source.value)
    return {"status": "accepted", "source": source.value,
            "hint": "進度請看 GET /api/v1/admin/snapshots"}


@router.post("/rebuild", status_code=202)
def trigger_rebuild(tasks: BackgroundTasks) -> dict:
    """把 active snapshot 的原始列重新正規化並重跑比對。不重新爬。"""
    tasks.add_task(_run_rebuild)
    return {"status": "accepted"}


@router.get("/snapshots")
def list_snapshots(limit: int = Query(30, le=200), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(
        select(SourceSnapshot).order_by(SourceSnapshot.id.desc()).limit(limit)
    ).all()
    return [
        {
            "id": s.id, "source": s.source.value, "status": s.status.value,
            "is_active": s.is_active, "row_count": s.row_count,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "error": s.error, "meta": s.meta,
        }
        for s in rows
    ]


# --------------------------------------------------------------------------
# 高改場 PDF 人工校對
# --------------------------------------------------------------------------

@router.get("/kdais/documents")
def kdais_documents(db: Session = Depends(get_db)) -> list[dict]:
    docs = db.scalars(select(KdaisDocument).order_by(KdaisDocument.crop_text)).all()
    return [
        {
            "id": d.id, "title": d.title, "crop_text": d.crop_text,
            "revision_date": d.revision_date, "pdf_url": d.pdf_url,
            "file_size": d.file_size,
            "parsed_at": d.parsed_at.isoformat() if d.parsed_at else None,
            "parse_note": d.parse_note,
        }
        for d in docs
    ]


@router.get("/kdais/pending")
def kdais_pending(
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """PDF 解析信心不足、尚未人工確認的列。這些不會進推薦表。"""
    stmt = select(KdaisRecommendation).where(
        KdaisRecommendation.confidence == Confidence.LOW
    )
    total = len(db.scalars(stmt).all())
    rows = db.scalars(
        stmt.order_by(KdaisRecommendation.id).offset(offset).limit(limit)
    ).all()
    return {"total": total, "items": [_kdais_row(r) for r in rows]}


def _kdais_row(r: KdaisRecommendation) -> dict:
    return {
        "id": r.id,
        "crop_text": r.crop_text, "pest_text": r.pest_text,
        "product_text": r.product_text, "moa_text": r.moa_text,
        "dilution_text": r.dilution_text, "dose_text": r.dose_text,
        "phi_text": r.phi_text, "tolerance_text": r.tolerance_text,
        "method_text": r.method_text, "remark": r.remark,
        "document_title": r.document_title,
        "raw_row": r.raw_row, "pdf_url": r.pdf_url, "pdf_page": r.pdf_page,
        "revision_date": r.revision_date, "confidence": r.confidence.value,
        "verified_by": r.verified_by,
        "verified_at": r.verified_at.isoformat() if r.verified_at else None,
    }


class KdaisVerifyIn(BaseModel):
    crop_text: str | None = None
    pest_text: str | None = None
    product_text: str | None = None
    dilution_text: str | None = None
    phi_text: str | None = None
    tolerance_text: str | None = None
    remark: str | None = None
    verified_by: str
    discard: bool = False       # 這列根本不是資料（例如 PDF 的區段標題）


@router.patch("/kdais/{row_id}")
def verify_kdais_row(
    row_id: int, payload: KdaisVerifyIn, db: Session = Depends(get_db)
) -> dict:
    row = db.get(KdaisRecommendation, row_id)
    if row is None:
        raise HTTPException(404, "找不到該列")

    if payload.discard:
        db.delete(row)
        db.commit()
        return {"status": "discarded", "id": row_id}

    for attr in (
        "crop_text", "pest_text", "product_text",
        "dilution_text", "phi_text", "tolerance_text", "remark",
    ):
        value = getattr(payload, attr)
        if value is not None:
            setattr(row, attr, value)

    # 校對過的列重跑正規化時不會被覆蓋（見 normalize_kdais）。
    row.confidence = Confidence.VERIFIED
    row.verified_by = payload.verified_by
    row.verified_at = datetime.now(timezone.utc)

    # 校對後把文字重新接回 canonical 作物／病蟲害。
    from app.services.normalize import EntityCache

    cache = EntityCache(db)
    if row.crop_text:
        crop = cache.crop(row.crop_text)
        row.crop_id = crop.id if crop else None
    if row.pest_text:
        pest = cache.pest(row.pest_text)
        row.pest_id = pest.id if pest else None

    db.commit()
    return {"status": "verified", "item": _kdais_row(row)}
