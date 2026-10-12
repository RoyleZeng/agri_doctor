"""★ 三源比對主端點 —— 植物醫生一天會按幾十次的那一個。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Crop, CropAlias, Pest, PestAlias
from app.schemas.common import ComparisonOut, RotationGroupOut, RotationOut
from app.services.matcher import compare

router = APIRouter(tags=["recommend"])


def _resolve_crop(db: Session, crop_id: int | None, crop: str | None) -> int:
    if crop_id is not None:
        return crop_id
    if not crop:
        raise HTTPException(400, "請提供 crop_id 或 crop")
    row = db.scalars(select(Crop).where(Crop.name == crop)).first()
    if row is None:
        alias = db.scalars(select(CropAlias).where(CropAlias.alias == crop)).first()
        row = alias.crop if alias else None
    if row is None:
        raise HTTPException(404, f"找不到作物「{crop}」")
    return row.id


def _resolve_pest(db: Session, pest_id: int | None, pest: str | None) -> int:
    if pest_id is not None:
        return pest_id
    if not pest:
        raise HTTPException(400, "請提供 pest_id 或 pest")
    row = db.scalars(select(Pest).where(Pest.name == pest)).first()
    if row is None:
        alias = db.scalars(select(PestAlias).where(PestAlias.alias == pest)).first()
        row = alias.pest if alias else None
    if row is None:
        raise HTTPException(404, f"找不到病蟲害「{pest}」")
    return row.id


@router.get("/recommend", response_model=ComparisonOut)
def recommend(
    crop_id: int | None = Query(None),
    pest_id: int | None = Query(None),
    crop: str | None = Query(None, description="作物名稱，與 crop_id 二擇一"),
    pest: str | None = Query(None, description="病蟲害名稱，與 pest_id 二擇一"),
    db: Session = Depends(get_db),
) -> ComparisonOut:
    """回傳合併三源的用藥比對表。

    每一列都帶著來源與抓取時間，衝突一律列出不隱藏 ——
    這個系統的職責是把「誰說了什麼」攤開，判斷仍然是植物醫生的。
    """
    resolved_crop = _resolve_crop(db, crop_id, crop)
    resolved_pest = _resolve_pest(db, pest_id, pest)

    try:
        result = compare(db, resolved_crop, resolved_pest)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc

    rotation = RotationOut(
        groups=[
            RotationGroupOut(
                code=str(g.code), label=g.code.label, product_names=g.product_names
            )
            for g in result.rotation.groups
        ],
        unknown_products=result.rotation.unknown_products,
        note=result.rotation.note,
    )

    return ComparisonOut(
        crop=result.crop,
        pest=result.pest,
        profile=result.profile,
        rows=[asdict(r) for r in result.rows],
        rotation=rotation,
        issues=[asdict(i) for i in result.issues],
        sources=result.sources,
        coverage=result.coverage,
    )
