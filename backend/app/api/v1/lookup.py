"""混合搜尋與「病蟲害 → 作物」。

介面上只有一個搜尋框：植物醫生想到什麼打什麼（作物、病名、蟲名、俗名），
不必先決定要從哪一邊查起。所以這裡把三類候選一起找出來、分組回傳，
每一筆再附上「有幾種作物有核准用藥」之類的數字，讓人在點進去之前
就知道值不值得點。

也支援一次打兩個詞（「芒果 炭疽」），直接解析成 作物 × 病蟲害。
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Integer, func, literal, or_, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import (
    Crop, CropAlias, CropFdaCategory, KdaisRecommendation, MatchIssue, MrlLimit,
    Pest, PestAlias, PestKind, PestProfile, Product, ProductIngredient, Registration, Severity, SourceName, SourceSnapshot,
)

from app.services.coverage import pair_coverage

router = APIRouter(tags=["lookup"])

SIMILARITY_THRESHOLD = 0.25


def current_kdais():
    return KdaisRecommendation.snapshot_id.in_(select(SourceSnapshot.id).where(
        SourceSnapshot.source == SourceName.KDAIS, SourceSnapshot.is_active.is_(True)))


def _tokens(q: str) -> list[str]:
    return [t for t in re.split(r"[\s　,，、/]+", q.strip()) if t]


# --------------------------------------------------------------------------
# 混合搜尋
# --------------------------------------------------------------------------

def _crop_candidates(db: Session, term: str, limit: int) -> list[dict]:
    """作物候選。附上「有幾種病蟲害有核准用藥」。"""
    alias_hit = (
        select(CropAlias.crop_id, func.min(CropAlias.alias).label("alias"))
        .where(CropAlias.alias.contains(term))
        .group_by(CropAlias.crop_id)
        .subquery()
    )
    pest_count = (
        select(func.count(func.distinct(Registration.pest_id)))
        .where(Registration.crop_id == Crop.id)
        .scalar_subquery()
    )

    stmt = (
        select(Crop, alias_hit.c.alias, pest_count.label("pest_count"))
        .outerjoin(alias_hit, alias_hit.c.crop_id == Crop.id)
        .where(or_(Crop.name.contains(term), alias_hit.c.alias.is_not(None)))
        # 有核准用藥的排前面，其次名稱短的（通常是本名而不是分群名）
        .order_by(pest_count.desc(), func.length(Crop.name))
        .limit(limit)
    )

    return [
        {
            "kind": "crop",
            "id": crop.id,
            "name": crop.name,
            "alias": alias,
            "scientific_name": crop.scientific_name,
            "count": count or 0,
            "count_label": f"已收錄 {count or 0} 種病蟲害的用藥資料",
        }
        for crop, alias, count in db.execute(stmt).all()
    ]


def _pest_candidates(db: Session, term: str, limit: int) -> list[dict]:
    """病蟲害候選。附上「有幾種作物有核准用藥」—— 這是點進去前最有用的數字。"""
    alias_agg = (
        select(
            PestAlias.pest_id,
            func.string_agg(func.distinct(PestAlias.alias), literal("、")).label("aliases"),
        )
        .group_by(PestAlias.pest_id)
        .subquery()
    )
    matched_alias = (
        select(PestAlias.pest_id)
        .where(PestAlias.alias.contains(term))
        .scalar_subquery()
    )
    crop_count = (
        select(func.count(func.distinct(Registration.crop_id)))
        .where(Registration.pest_id == Pest.id)
        .scalar_subquery()
    )

    stmt = (
        select(Pest, alias_agg.c.aliases, crop_count.label("crop_count"))
        .outerjoin(alias_agg, alias_agg.c.pest_id == Pest.id)
        .where(or_(Pest.name.contains(term), Pest.id.in_(matched_alias)))
        .order_by(crop_count.desc(), func.length(Pest.name))
        .limit(limit)
    )

    return [
        {
            "kind": pest.kind.value,
            "id": pest.id,
            "name": pest.name,
            "alias": aliases,
            "scientific_name": pest.scientific_name,
            "english_name": pest.english_name,
            "count": count or 0,
            "count_label": f"已收錄 {count or 0} 種作物的用藥資料",
        }
        for pest, aliases, count in db.execute(stmt).all()
    ]


def _resolve_pair(db: Session, tokens: list[str]) -> dict | None:
    """「芒果 炭疽」這種兩個詞的輸入，直接解析成 作物 × 病蟲害。

    兩個詞哪個是作物哪個是病蟲害不一定，所以兩種順序都試。
    只有真的存在核准登記的組合才算命中，否則還是回候選清單讓人自己選。
    """
    if len(tokens) < 2:
        return None

    def first_crop(term: str) -> Crop | None:
        stmt = (
            select(Crop)
            .outerjoin(CropAlias, CropAlias.crop_id == Crop.id)
            .where(or_(Crop.name.contains(term), CropAlias.alias.contains(term)))
            .order_by(func.length(Crop.name))
            .limit(1)
        )
        return db.scalars(stmt).first()

    def first_pest(term: str) -> Pest | None:
        stmt = (
            select(Pest)
            .outerjoin(PestAlias, PestAlias.pest_id == Pest.id)
            .where(or_(Pest.name.contains(term), PestAlias.alias.contains(term)))
            .order_by(func.length(Pest.name))
            .limit(1)
        )
        return db.scalars(stmt).first()

    for a, b in ((tokens[0], tokens[1]), (tokens[1], tokens[0])):
        crop, pest = first_crop(a), first_pest(b)
        if crop is None or pest is None:
            continue
        exists = db.scalar(
            select(func.count())
            .select_from(Registration)
            .where(Registration.crop_id == crop.id, Registration.pest_id == pest.id)
        )
        if exists or (crop.name == a and pest.name == b):
            return {
                "crop": {"id": crop.id, "name": crop.name},
                "pest": {"id": pest.id, "name": pest.name},
                "registration_count": exists,
            }
    return None


@router.get("/search")
def unified_search(
    q: str = Query("", description="作物、病名或害蟲名，可一次打兩個詞"),
    limit: int = Query(8, le=30, description="每一組最多幾筆"),
    db: Session = Depends(get_db),
) -> dict:
    """一個搜尋框查三類。回傳依 作物／病害／蟲害／其他 分組。"""
    tokens = _tokens(q)
    if not tokens:
        return {"query": q, "direct": None, "groups": [], "total": 0}

    # 兩個詞先試著直接解析成一組 作物 × 病蟲害
    direct = _resolve_pair(db, tokens)

    # 候選比對用最長的那個詞（通常是資訊量最大的）
    term = max(tokens, key=len)
    crops = _crop_candidates(db, term, limit)
    pests = _pest_candidates(db, term, limit)

    by_kind: dict[str, list[dict]] = {}
    for item in pests:
        by_kind.setdefault(item["kind"], []).append(item)

    groups: list[dict] = []
    if crops:
        groups.append({"key": "crop", "label": "作物", "items": crops})
    for kind, label in (
        (PestKind.DISEASE.value, "病害"),
        (PestKind.INSECT.value, "蟲害"),
        (PestKind.WEED.value, "雜草"),
        (PestKind.OTHER.value, "其他"),
    ):
        if by_kind.get(kind):
            groups.append({"key": kind, "label": label, "items": by_kind[kind]})

    return {
        "query": q,
        "term": term,
        "direct": direct,
        "groups": groups,
        "total": sum(len(g["items"]) for g in groups),
    }


@router.get("/sources")
def source_status(db: Session = Depends(get_db)) -> dict:
    """三源目前的資料狀態與版本。首頁與頁首都用這一支。"""
    from app.services.matcher import source_freshness

    return source_freshness(db)


# --------------------------------------------------------------------------
# 病蟲害 → 作物
# --------------------------------------------------------------------------

@router.get("/pests/{pest_id}/crops")
def crops_for_pest(pest_id: int, db: Session = Depends(get_db)) -> dict:
    """先選病蟲害時，列出有核准用藥的作物。

    每張作物卡先給三源筆數與衝突摘要，進結果頁之前就知道哪一站有、哪一站沒有 ——
    不用一個一個點進去才發現高改場根本沒有這個作物的摘要表。
    """
    pest = db.get(Pest, pest_id)
    if pest is None:
        raise HTTPException(404, "找不到該病蟲害")

    # 植保系統：核准藥劑數
    ppm_counts = dict(
        db.execute(
            select(Registration.crop_id, func.count(Registration.id))
            .where(Registration.pest_id == pest_id)
            .group_by(Registration.crop_id)
        ).all()
    )
    extra = db.scalars(select(KdaisRecommendation.crop_id).where(current_kdais(), KdaisRecommendation.pest_id == pest_id, KdaisRecommendation.crop_id.is_not(None)).union(
        select(PestProfile.crop_id).where(PestProfile.pest_id == pest_id))).all()
    crop_ids = sorted(set(ppm_counts) | set(extra))

    # 食藥署：至少一個成分查得到容許量的藥劑數
    category = (
        select(CropFdaCategory.category)
        .where(CropFdaCategory.crop_id == Registration.crop_id)
        .scalar_subquery()
    )
    has_mrl = (
        select(MrlLimit.id)
        .join(ProductIngredient, ProductIngredient.ingredient_id == MrlLimit.ingredient_id)
        .where(
            ProductIngredient.product_id == Registration.product_id,
            MrlLimit.crop_category.in_(category),
        )
        .exists()
    )
    fda_counts = dict(
        db.execute(
            select(Registration.crop_id, func.count(Registration.id))
            .where(Registration.pest_id == pest_id, has_mrl)
            .group_by(Registration.crop_id)
        ).all()
    )

    # 高改場：摘要表列了幾列
    kdais_counts = dict(
        db.execute(
            select(KdaisRecommendation.crop_id, func.count(KdaisRecommendation.id))
            .where(
                KdaisRecommendation.pest_id == pest_id,
                current_kdais(),
                KdaisRecommendation.crop_id.in_(crop_ids),
            )
            .group_by(KdaisRecommendation.crop_id)
        ).all()
    )

    # 衝突摘要直接用預先算好的 match_issues，不用現場重跑比對
    issue_rows = db.execute(
        select(MatchIssue.crop_id, MatchIssue.severity, func.count(MatchIssue.id))
        .where(
            MatchIssue.pest_id == pest_id,
            MatchIssue.crop_id.in_(crop_ids),
            MatchIssue.resolved.is_(False),
        )
        .group_by(MatchIssue.crop_id, MatchIssue.severity)
    ).all()
    issues: dict[int, dict[str, int]] = {}
    for crop_id, severity, count in issue_rows:
        issues.setdefault(crop_id, {})[severity.value] = count

    crops = db.scalars(select(Crop).where(Crop.id.in_(crop_ids))).all()
    alias_map = _crop_alias_map(db, crop_ids)

    out = []
    for crop in crops:
        sev = issues.get(crop.id, {})
        kdais = kdais_counts.get(crop.id, 0)
        out.append(
            {
                "id": crop.id,
                "name": crop.name,
                "aliases": alias_map.get(crop.id, []),
                "fda_category": crop.fda_category,
                "ppm_count": ppm_counts.get(crop.id, 0),
                "fda_count": fda_counts.get(crop.id, 0),
                "kdais_count": kdais,
                # 高改場只有 35 種作物的摘要表，沒有不等於有問題，要分開講。
                "kdais_has_document": kdais > 0,
                "critical": sev.get(Severity.CRITICAL.value, 0),
                "warning": sev.get(Severity.WARNING.value, 0),
                "info": sev.get(Severity.INFO.value, 0),
                "coverage": pair_coverage(db, crop.id, pest.id)["state"],
            }
        )

    out.sort(key=lambda c: (-c["ppm_count"], c["name"]))
    return {"pest": _pest_brief(pest), "crops": out}


@router.get("/crops/{crop_id}/pest-cards")
def pests_for_crop(crop_id: int, db: Session = Depends(get_db)) -> dict:
    """先選作物時，列出這個作物有核准用藥的病蟲害（與 pests/{id}/crops 對稱）。"""
    crop = db.get(Crop, crop_id)
    if crop is None:
        raise HTTPException(404, "找不到該作物")

    ppm_counts = dict(
        db.execute(
            select(Registration.pest_id, func.count(Registration.id))
            .where(Registration.crop_id == crop_id)
            .group_by(Registration.pest_id)
        ).all()
    )
    extra = db.scalars(select(KdaisRecommendation.pest_id).where(current_kdais(), KdaisRecommendation.crop_id == crop_id, KdaisRecommendation.pest_id.is_not(None)).union(
        select(PestProfile.pest_id).where(PestProfile.crop_id == crop_id))).all()
    pest_ids = sorted(set(ppm_counts) | set(extra))

    category = (
        select(CropFdaCategory.category)
        .where(CropFdaCategory.crop_id == crop_id)
        .scalar_subquery()
    )
    has_mrl = (
        select(MrlLimit.id)
        .join(ProductIngredient, ProductIngredient.ingredient_id == MrlLimit.ingredient_id)
        .where(
            ProductIngredient.product_id == Registration.product_id,
            MrlLimit.crop_category.in_(category),
        )
        .exists()
    )
    fda_counts = dict(
        db.execute(
            select(Registration.pest_id, func.count(Registration.id))
            .where(Registration.crop_id == crop_id, has_mrl)
            .group_by(Registration.pest_id)
        ).all()
    )

    kdais_counts = dict(
        db.execute(
            select(KdaisRecommendation.pest_id, func.count(KdaisRecommendation.id))
            .where(
                KdaisRecommendation.crop_id == crop_id,
                current_kdais(),
                KdaisRecommendation.pest_id.in_(pest_ids),
            )
            .group_by(KdaisRecommendation.pest_id)
        ).all()
    )

    issue_rows = db.execute(
        select(MatchIssue.pest_id, MatchIssue.severity, func.count(MatchIssue.id))
        .where(
            MatchIssue.crop_id == crop_id,
            MatchIssue.pest_id.in_(pest_ids),
            MatchIssue.resolved.is_(False),
        )
        .group_by(MatchIssue.pest_id, MatchIssue.severity)
    ).all()
    issues: dict[int, dict[str, int]] = {}
    for pest_id, severity, count in issue_rows:
        issues.setdefault(pest_id, {})[severity.value] = count

    alias_rows = db.scalars(
        select(PestAlias).where(PestAlias.pest_id.in_(pest_ids))
    ).all()
    alias_map: dict[int, list[str]] = {}
    for row in alias_rows:
        alias_map.setdefault(row.pest_id, []).append(row.alias)

    pests = db.scalars(select(Pest).where(Pest.id.in_(pest_ids))).all()
    out = []
    for pest in pests:
        sev = issues.get(pest.id, {})
        kdais = kdais_counts.get(pest.id, 0)
        out.append(
            {
                "id": pest.id,
                "name": pest.name,
                "kind": pest.kind.value,
                "aliases": sorted(set(alias_map.get(pest.id, []))),
                "scientific_name": pest.scientific_name,
                "ppm_count": ppm_counts.get(pest.id, 0),
                "fda_count": fda_counts.get(pest.id, 0),
                "kdais_count": kdais,
                "kdais_has_document": kdais > 0,
                "critical": sev.get(Severity.CRITICAL.value, 0),
                "warning": sev.get(Severity.WARNING.value, 0),
                "info": sev.get(Severity.INFO.value, 0),
                "coverage": pair_coverage(db, crop.id, pest.id)["state"],
            }
        )

    out.sort(key=lambda p: (-p["ppm_count"], p["name"]))
    return {"crop": _crop_brief(db, crop), "pests": out}


def _crop_brief(db: Session, crop: Crop) -> dict:
    aliases = db.scalars(
        select(CropAlias.alias).where(CropAlias.crop_id == crop.id)
    ).all()
    return {
        "id": crop.id,
        "name": crop.name,
        "scientific_name": crop.scientific_name,
        "fda_category": crop.fda_category,
        "aliases": sorted(set(aliases)),
    }


def _pest_brief(pest: Pest) -> dict:
    return {
        "id": pest.id,
        "name": pest.name,
        "kind": pest.kind.value,
        "scientific_name": pest.scientific_name,
        "english_name": pest.english_name,
    }


def _crop_alias_map(db: Session, crop_ids: list[int]) -> dict[int, list[str]]:
    rows = db.scalars(
        select(CropAlias).where(CropAlias.crop_id.in_(crop_ids))
    ).all()
    out: dict[int, list[str]] = {}
    for row in rows:
        out.setdefault(row.crop_id, []).append(row.alias)
    return out
