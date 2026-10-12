"""作物 / 病蟲害 自動完成。

植物醫生打字時會用俗名、台語名、簡寫，所以先比對本名與別名的前綴，
再退到 pg_trgm 相似度。相似度門檻刻意放寬（0.2），寧可多給幾個候選。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import Crop, CropAlias, Pest, PestAlias, Registration
from app.schemas.common import CropOut, PestOut, SuggestOut

router = APIRouter(tags=["search"])

SIMILARITY_THRESHOLD = 0.2


@router.get("/crops/search", response_model=list[SuggestOut])
def search_crops(
    q: str = Query("", description="作物名或別名關鍵字"),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
) -> list[SuggestOut]:
    if not q.strip():
        crops = db.scalars(select(Crop).order_by(Crop.name).limit(limit)).all()
        return [SuggestOut(id=c.id, name=c.name, matched_on="name") for c in crops]

    term = q.strip()
    out: list[SuggestOut] = []
    seen: set[int] = set()

    for crop in db.scalars(
        select(Crop).where(Crop.name.contains(term)).order_by(func.length(Crop.name)).limit(limit)
    ):
        seen.add(crop.id)
        out.append(SuggestOut(id=crop.id, name=crop.name, matched_on="name"))

    for alias in db.scalars(
        select(CropAlias).where(CropAlias.alias.contains(term)).limit(limit)
    ):
        if alias.crop_id in seen:
            continue
        seen.add(alias.crop_id)
        out.append(
            SuggestOut(
                id=alias.crop_id, name=alias.crop.name,
                matched_on="alias", alias=alias.alias,
            )
        )

    if len(out) < limit:
        fuzzy = db.execute(
            select(Crop.id, Crop.name, func.similarity(Crop.name, term).label("sim"))
            .where(func.similarity(Crop.name, term) > SIMILARITY_THRESHOLD)
            .order_by(func.similarity(Crop.name, term).desc())
            .limit(limit - len(out))
        ).all()
        for crop_id, name, _ in fuzzy:
            if crop_id not in seen:
                seen.add(crop_id)
                out.append(SuggestOut(id=crop_id, name=name, matched_on="fuzzy"))

    return out[:limit]


@router.get("/crops/{crop_id}", response_model=CropOut)
def get_crop(crop_id: int, db: Session = Depends(get_db)) -> Crop:
    crop = db.get(Crop, crop_id)
    if crop is None:
        raise HTTPException(404, "找不到該作物")
    return crop


@router.get("/crops/{crop_id}/pests", response_model=list[PestOut])
def list_pests_for_crop(crop_id: int, db: Session = Depends(get_db)) -> list[Pest]:
    """只列出這個作物「有核准用藥」的病蟲害 —— 沒藥可開的選項沒有意義。"""
    return list(
        db.scalars(
            select(Pest)
            .join(Registration, Registration.pest_id == Pest.id)
            .where(Registration.crop_id == crop_id)
            .distinct()
            .order_by(Pest.kind, Pest.name)
        )
    )


@router.get("/pests/search", response_model=list[SuggestOut])
def search_pests(
    q: str = Query("", description="病蟲害名或別名關鍵字"),
    crop_id: int | None = Query(None, description="限定在某作物底下"),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
) -> list[SuggestOut]:
    stmt = select(Pest)
    if crop_id is not None:
        stmt = stmt.join(Registration, Registration.pest_id == Pest.id).where(
            Registration.crop_id == crop_id
        )
    term = q.strip()
    if term:
        stmt = stmt.where(
            or_(
                Pest.name.contains(term),
                Pest.id.in_(select(PestAlias.pest_id).where(PestAlias.alias.contains(term))),
            )
        )

    pests = db.scalars(stmt.distinct().order_by(Pest.name).limit(limit)).all()
    return [SuggestOut(id=p.id, name=p.name, matched_on="name") for p in pests]
