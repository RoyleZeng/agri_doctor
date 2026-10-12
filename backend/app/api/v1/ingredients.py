"""成分反查：這支成分還能用在哪些作物、容許量各是多少。

植物醫生常見的第二種問法是「我手上有這支藥，還能噴什麼」，
所以做成獨立端點。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import (
    ActiveIngredient, Crop, MrlLimit, Pest, Product, ProductIngredient, Registration,
)

router = APIRouter(tags=["ingredients"])


@router.get("/ingredients/search")
def search_ingredients(
    q: str = Query("", description="中文或英文成分名"),
    limit: int = Query(20, le=100),
    db: Session = Depends(get_db),
) -> list[dict]:
    stmt = select(ActiveIngredient)
    if q.strip():
        term = q.strip()
        stmt = stmt.where(
            ActiveIngredient.name.contains(term)
            | ActiveIngredient.english_name.ilike(f"%{term}%")
        )
    rows = db.scalars(stmt.order_by(ActiveIngredient.name).limit(limit)).all()
    return [
        {"id": r.id, "name": r.name, "english_name": r.english_name}
        for r in rows
    ]


@router.get("/ingredients/{ingredient_id}/mrl")
def ingredient_mrl(ingredient_id: int, db: Session = Depends(get_db)) -> dict:
    ingredient = db.get(ActiveIngredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(404, "找不到該成分")

    limits = db.scalars(
        select(MrlLimit)
        .where(MrlLimit.ingredient_id == ingredient_id)
        .order_by(MrlLimit.crop_category)
    ).all()

    return {
        "ingredient": {
            "id": ingredient.id,
            "name": ingredient.name,
            "english_name": ingredient.english_name,
        },
        "limits": [
            {
                "crop_category": m.crop_category,
                "limit_ppm": None if m.limit_ppm is None else str(m.limit_ppm),
                "limit_text": m.limit_text,
                "note": m.note,
            }
            for m in limits
        ],
    }


@router.get("/ingredients/{ingredient_id}/overview")
def ingredient_overview(ingredient_id: int, db: Session = Depends(get_db)) -> dict:
    """成分頁需要的全部東西：基本資料、禁用狀態、三源筆數、容許量、使用範圍。

    一次給完，前端不用發四支請求。
    """
    from app.models import KdaisRecommendation
    from app.services.matcher import banned_ingredient_names

    ingredient = db.get(ActiveIngredient, ingredient_id)
    if ingredient is None:
        raise HTTPException(404, "找不到該成分")

    banned = banned_ingredient_names(db)
    is_banned = (
        (ingredient.name or "").lower() in banned
        or (ingredient.english_name or "").lower() in banned
    )

    limits = db.scalars(
        select(MrlLimit)
        .where(MrlLimit.ingredient_id == ingredient_id)
        .order_by(MrlLimit.crop_category)
    ).all()

    usage_rows = db.execute(
        select(
            Crop.id, Crop.name, Pest.id, Pest.name, Pest.kind, Product.name,
            Product.concentration_text, Product.formulation, Product.moa_text,
            Registration.dilution_text, Registration.phi_days,
        )
        .join(Registration, Registration.crop_id == Crop.id)
        .join(Pest, Pest.id == Registration.pest_id)
        .join(Product, Product.id == Registration.product_id)
        .join(ProductIngredient, ProductIngredient.product_id == Product.id)
        .where(ProductIngredient.ingredient_id == ingredient_id)
        .order_by(Crop.name, Pest.name)
    ).all()

    # 依作物分組，方便「這支藥還能噴什麼」一眼看完
    grouped: dict[int, dict] = {}
    for c_id, c_name, p_id, p_name, p_kind, prod, conc, form, moa, dil, phi in usage_rows:
        g = grouped.setdefault(c_id, {"crop_id": c_id, "crop": c_name, "items": []})
        g["items"].append(
            {
                "pest_id": p_id,
                "pest": p_name,
                "pest_kind": p_kind.value,
                "product": " ".join(x for x in (conc, prod, form) if x),
                "moa": (moa or "").split(" | ")[0] or None,
                "dilution": dil,
                "phi_days": phi,
            }
        )

    kdais_count = db.scalar(
        select(func.count(func.distinct(KdaisRecommendation.id)))
        .select_from(KdaisRecommendation)
        .join(ProductIngredient, ProductIngredient.product_id == KdaisRecommendation.product_id)
        .where(ProductIngredient.ingredient_id == ingredient_id)
    ) or 0

    group_parent = ingredient.mrl_group
    return {
        "ingredient": {
            "id": ingredient.id,
            "name": ingredient.name,
            "english_name": ingredient.english_name,
            "frac_code": ingredient.frac_code,
            "irac_code": ingredient.irac_code,
            "banned": is_banned,
            # 以群組訂容許量的成分要講清楚，否則會以為是自己的值
            "mrl_group": None if group_parent is None else {
                "id": group_parent.id,
                "name": group_parent.name,
                "english_name": group_parent.english_name,
            },
        },
        "stats": {
            "mrl_categories": len(limits),
            "registrations": len(usage_rows),
            "kdais_rows": kdais_count,
        },
        "limits": [
            {
                "crop_category": m.crop_category,
                "limit_ppm": None if m.limit_ppm is None else str(m.limit_ppm),
                "limit_text": m.limit_text,
                "note": m.note,
            }
            for m in limits
        ],
        "usages": sorted(grouped.values(), key=lambda g: (-len(g["items"]), g["crop"])),
    }


@router.get("/ingredients/{ingredient_id}/usages")
def ingredient_usages(
    ingredient_id: int,
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    """含這個成分的藥劑，在哪些 作物x病蟲害 上有核准登記。"""
    rows = db.execute(
        select(
            Crop.id, Crop.name, Pest.id, Pest.name, Product.name,
            Registration.dilution_text, Registration.phi_days,
        )
        .join(Registration, Registration.crop_id == Crop.id)
        .join(Pest, Pest.id == Registration.pest_id)
        .join(Product, Product.id == Registration.product_id)
        .join(ProductIngredient, ProductIngredient.product_id == Product.id)
        .where(ProductIngredient.ingredient_id == ingredient_id)
        .order_by(Crop.name, Pest.name)
        .limit(limit)
    ).all()

    return [
        {
            "crop_id": c_id, "crop": c_name,
            "pest_id": p_id, "pest": p_name,
            "product": prod, "dilution": dil, "phi_days": phi,
        }
        for c_id, c_name, p_id, p_name, prod, dil, phi in rows
    ]
