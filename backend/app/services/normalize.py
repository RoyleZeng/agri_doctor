"""把 source_records 的原始列正規化成 canonical 實體。

成分是三源相接的樞紐，取名策略如下：
    英文名  <- 食藥署「國際普通名稱」，同時也是 PPM 英文成分組合的字串
              （PPM 的 "(tecloftalam + kasugamycin)" 拆開就對得上）
    中文名  <- 食藥署「普通名稱」
食藥署因此是成分命名的權威來源，PPM 只負責提供「這支藥含哪些成分」。
對不上的一律進 match_issues，不靜默丟掉。

作物類別對照：食藥署的「作物類別」欄同時混著具體作物（柿、梨、大豆）
與大類（米類、包葉菜類），所以查 MRL 時先用作物本名，查不到再退到
PPM 描述抽出來的大類。
"""

from __future__ import annotations

import logging
import hashlib
import json
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    ActiveIngredient, Crop, CropAlias, CropFdaCategory, IngredientNameMap, KdaisRecommendation,
    MrlLimit, Pest, PestAlias, PestKind, PestProfile, Product, ProductIngredient,
    Registration, SourceName, SourceRecord, SourceSnapshot,
)
from app.services.phi_extract import extract_mix_warnings, extract_phi_days
from app.services.seed_data import seed_crop_aliases, seed_ingredient_groups

log = logging.getLogger(__name__)

_DISEASE_HINTS = ("病", "腐", "萎凋", "銹", "黴", "疫")
_INSECT_HINTS = ("蟲", "蟎", "蝨", "蠅", "蛾", "蟬", "薊馬", "介殼", "螟", "蜱")
_WEED_HINTS = ("雜草", "草害", "除草")


def _norm(text: str | None) -> str:
    return re.sub(r"\s+", "", (text or "")).strip()


def guess_pest_kind(name: str) -> PestKind:
    if any(h in name for h in _WEED_HINTS):
        return PestKind.WEED
    if any(h in name for h in _INSECT_HINTS):
        return PestKind.INSECT
    if any(h in name for h in _DISEASE_HINTS):
        return PestKind.DISEASE
    return PestKind.OTHER


def active_snapshot(db: Session, source: SourceName) -> SourceSnapshot | None:
    return db.scalars(
        select(SourceSnapshot)
        .where(SourceSnapshot.source == source, SourceSnapshot.is_active.is_(True))
        .order_by(SourceSnapshot.id.desc())
    ).first()


def _records(db: Session, snap: SourceSnapshot, kind: str):
    return db.scalars(
        select(SourceRecord).where(
            SourceRecord.snapshot_id == snap.id, SourceRecord.record_kind == kind
        )
    )


# --------------------------------------------------------------------------
# get-or-create helpers
# --------------------------------------------------------------------------

class EntityCache:
    """一輪正規化裡重複查同一個作物/害物很多次，快取省掉大量 round trip。"""

    def __init__(self, db: Session) -> None:
        self.db = db
        self._crops: dict[str, Crop] = {}
        self._pests: dict[tuple[str, bool], Pest] = {}
        self._ingredients: dict[str, ActiveIngredient] = {}
        self._products: dict[tuple, Product] = {}
        # 同一個 transaction 裡已排隊但還沒 flush 的別名，SELECT 查不到，
        # 所以自己記一份，否則同一頁的「別名」欄與列表頁的別名會插入兩次。
        self._crop_aliases: set[tuple[int, str]] = set()
        self._pest_aliases: set[tuple[int, str]] = set()
        self.product_ingredients: set[tuple[int, int]] = set()
        self.crop_categories: set[tuple[int, str]] = set()

    # -- 作物 --------------------------------------------------------------
    def crop(self, name: str, **fields) -> Crop | None:
        key = _norm(name)
        if not key:
            return None
        if key in self._crops:
            crop = self._crops[key]
        else:
            crop = self.db.scalars(select(Crop).where(Crop.name == name)).first()
            if crop is None:
                alias = self.db.scalars(
                    select(CropAlias).where(CropAlias.alias == name)
                ).first()
                crop = alias.crop if alias else None
            if crop is None:
                crop = Crop(name=name)
                self.db.add(crop)
                self.db.flush()
            self._crops[key] = crop

        for attr, value in fields.items():
            if value and not getattr(crop, attr, None):
                setattr(crop, attr, value)
        return crop

    def add_crop_alias(self, crop: Crop, alias: str, source: SourceName) -> None:
        alias = (alias or "").strip()
        if not alias or alias == crop.name:
            return
        key = (crop.id, alias)
        if key in self._crop_aliases:
            return
        exists = self.db.scalars(
            select(CropAlias).where(CropAlias.crop_id == crop.id, CropAlias.alias == alias)
        ).first()
        self._crop_aliases.add(key)
        if exists is None:
            self.db.add(CropAlias(crop_id=crop.id, alias=alias, source=source))

    # -- 害物 --------------------------------------------------------------
    def pest(self, name: str, *, authoritative: bool = False, **fields) -> Pest | None:
        if not _norm(name):
            return None
        key = (_norm(name), authoritative)
        if key in self._pests:
            pest = self._pests[key]
        else:
            pest = self.db.scalars(select(Pest).where(Pest.name == name)).first()
            if pest is None and not authoritative:
                alias = self.db.scalars(
                    select(PestAlias).where(PestAlias.alias == name)
                ).first()
                pest = alias.pest if alias else None
            if pest is None:
                pest = Pest(name=name, kind=guess_pest_kind(name))
                self.db.add(pest)
                self.db.flush()
            self._pests[key] = pest

        for attr, value in fields.items():
            if value and not getattr(pest, attr, None):
                setattr(pest, attr, value)
        return pest

    def add_pest_alias(self, pest: Pest, alias: str, source: SourceName) -> None:
        alias = (alias or "").strip()
        if not alias or alias == pest.name:
            return
        key = (pest.id, alias)
        if key in self._pest_aliases:
            return
        exists = self.db.scalars(
            select(PestAlias).where(PestAlias.pest_id == pest.id, PestAlias.alias == alias)
        ).first()
        self._pest_aliases.add(key)
        if exists is None:
            self.db.add(PestAlias(pest_id=pest.id, alias=alias, source=source))

    # -- 成分 --------------------------------------------------------------
    def ingredient_by_english(self, english: str) -> ActiveIngredient | None:
        key = _norm(english).lower()
        if not key:
            return None
        if key in self._ingredients:
            return self._ingredients[key]
        row = self.db.scalars(
            select(ActiveIngredient).where(
                ActiveIngredient.english_name.ilike(english.strip())
            )
        ).first()
        if row is not None:
            self._ingredients[key] = row
        return row

    def upsert_ingredient(
        self, *, english: str | None, chinese: str | None
    ) -> ActiveIngredient | None:
        english = (english or "").strip()
        chinese = (chinese or "").strip()
        if not english and not chinese:
            return None

        row = self.ingredient_by_english(english) if english else None
        if row is None and chinese:
            row = self.db.scalars(
                select(ActiveIngredient).where(ActiveIngredient.name == chinese)
            ).first()
        if row is None:
            row = ActiveIngredient(name=chinese or english, english_name=english or None)
            self.db.add(row)
            self.db.flush()
        else:
            if english and not row.english_name:
                row.english_name = english
            if chinese and (not row.name or row.name == row.english_name):
                row.name = chinese

        if english:
            self._ingredients[_norm(english).lower()] = row
        return row

    # -- 產品 --------------------------------------------------------------
    def product(
        self, *, name: str, concentration: str | None, formulation: str | None,
        english_composition: str | None, moa_text: str | None,
    ) -> Product | None:
        if not name:
            return None
        key = (name, formulation, concentration)
        if key in self._products:
            return self._products[key]

        row = self.db.scalars(
            select(Product).where(
                Product.name == name,
                Product.formulation.is_(formulation) if formulation is None
                else Product.formulation == formulation,
                Product.concentration_text.is_(concentration) if concentration is None
                else Product.concentration_text == concentration,
            )
        ).first()
        if row is None:
            row = Product(
                name=name,
                concentration_text=concentration,
                formulation=formulation,
                english_composition=english_composition,
                moa_text=moa_text,
            )
            self.db.add(row)
            self.db.flush()
        else:
            row.english_composition = english_composition
            row.moa_text = moa_text

        self._products[key] = row
        return row


_ALIAS_SPLIT = re.compile(r"[、,，;；/]+")


def split_aliases(text: str | None) -> list[str]:
    """「稻泥蟲、揹屎蟲」-> ['稻泥蟲', '揹屎蟲']"""
    if not text:
        return []
    return [a.strip() for a in _ALIAS_SPLIT.split(text) if a.strip()]


_COMPOSITION_SPLIT = re.compile(r"\s*\+\s*|\s*、\s*|\s*,\s*")


def split_composition(english_composition: str | None) -> list[str]:
    """「tecloftalam + kasugamycin」-> ['tecloftalam', 'kasugamycin']"""
    if not english_composition:
        return []
    text = english_composition.strip().strip("()（）")
    return [p.strip() for p in _COMPOSITION_SPLIT.split(text) if p.strip()]


# --------------------------------------------------------------------------
# 食藥署 -> 成分與 MRL（先跑，因為 PPM 要靠成分表對接）
# --------------------------------------------------------------------------

@dataclass
class NormalizeResult:
    ingredients: int = 0
    mrl_limits: int = 0
    crops: int = 0
    pests: int = 0
    products: int = 0
    registrations: int = 0
    kdais_rows: int = 0
    unmatched_ingredients: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.unmatched_ingredients is None:
            self.unmatched_ingredients = []


def normalize_fda(db: Session, cache: EntityCache, result: NormalizeResult) -> None:
    snap = active_snapshot(db, SourceName.FDA)
    if snap is None:
        log.warning("沒有 active 的食藥署 snapshot，跳過")
        return

    db.query(MrlLimit).delete()
    db.flush()

    seen: set[tuple] = set()
    for rec in _records(db, snap, "fda_mrl"):
        p = rec.payload
        ingredient = cache.upsert_ingredient(
            english=p.get("international_name"), chinese=p.get("common_name")
        )

        category = (p.get("crop_category") or "").strip()
        if not category:
            continue
        key = (ingredient.id if ingredient else None, category)
        if key in seen:
            continue
        seen.add(key)

        limit_text = (p.get("limit_text") or "").strip()
        db.add(
            MrlLimit(
                ingredient_id=ingredient.id if ingredient else None,
                snapshot_id=snap.id,
                international_name=p.get("international_name"),
                common_name=p.get("common_name"),
                crop_category=category,
                limit_ppm=_decimal_or_none(limit_text),
                limit_text=limit_text or None,
                note=p.get("note"),
            )
        )

    # 禁用農藥也建成分，讓比對階段查得到。
    for rec in _records(db, snap, "fda_banned"):
        p = rec.payload
        cache.upsert_ingredient(english=p.get("english_name"), chinese=p.get("name"))

    db.flush()


def _decimal_or_none(text: str):
    from decimal import Decimal, InvalidOperation

    m = re.search(r"\d+(?:\.\d+)?", text or "")
    if not m:
        return None
    try:
        return Decimal(m.group(0))
    except InvalidOperation:
        return None


# --------------------------------------------------------------------------
# PPM -> 作物 / 害物 / 產品 / 核准用藥
# --------------------------------------------------------------------------

def normalize_ppm(db: Session, cache: EntityCache, result: NormalizeResult) -> None:
    snap = active_snapshot(db, SourceName.PPM)
    if snap is None:
        log.warning("沒有 active 的植保系統 snapshot，跳過")
        return

    if (snap.meta or {}).get('complete') is True:
        # Rebuild source-generated aliases from the verified full capture. Old
        # list-page parsing could mistake dosage cells for pest aliases. Retain
        # the previous values in snapshot metadata; manual aliases are untouched.
        previous_aliases = list(db.scalars(select(PestAlias).where(PestAlias.source == SourceName.PPM)))
        meta = dict(snap.meta or {})
        if 'previous_ppm_aliases' not in meta:
            meta['previous_ppm_aliases'] = [{'pest_id': a.pest_id, 'alias': a.alias} for a in previous_aliases]
            snap.meta = meta
        for alias in previous_aliases:
            db.delete(alias)
        db.flush()
        cache._pest_aliases.clear()
        cache._pests.clear()

    db.query(Registration).delete()
    db.query(PestProfile).delete()
    db.query(ProductIngredient).delete()
    cache.product_ingredients.clear()
    db.query(CropFdaCategory).filter(CropFdaCategory.priority < 3).delete()
    db.flush()

    # 同一頁上偶爾會把同一列印兩次（完全一樣的藥劑、倍數、施藥量）。
    # 唯一鍵擋下來會讓整個正規化中斷，所以先在程式層去重。
    seen_registrations: set[tuple] = set()

    for rec in _records(db, snap, "ppm_pest_detail"):
        p = rec.payload
        list_row = p.get("list_row") or {}

        crop_name = p.get("crop_name") or list_row.get("crop")
        pest_name = p.get("pest_name") or list_row.get("pest")
        if not crop_name or not pest_name:
            continue

        crop = cache.crop(
            crop_name,
            scientific_name=p.get("crop_scientific_name"),
            description=p.get("crop_description"),
            fda_category=p.get("fda_category"),
        )
        pest = cache.pest(
            pest_name,
            authoritative=True,
            scientific_name=p.get("pest_scientific_name"),
            english_name=p.get("pest_english_name"),
        )
        if crop is None or pest is None:
            continue

        # 作物 -> 食藥署作物類別的候選，由具體到概括。
        _add_crop_category(db, cache, crop, crop.name, 0, "crop_name")
        if p.get("fda_category"):
            _add_crop_category(db, cache, crop, p["fda_category"], 2, "ppm_description")

        for alias in p.get("pest_aliases") or []:
            cache.add_pest_alias(pest, alias, SourceName.PPM)
        # 列表頁的「中文別名」欄是整串（「稻泥蟲、揹屎蟲」），要拆開，
        # 否則會多存一筆合併字串，顯示別名時看起來像重複。
        for alias in split_aliases(list_row.get("alias")):
            cache.add_pest_alias(pest, alias, SourceName.PPM)

        _upsert_profile(db, crop.id, pest.id, p)

        for drug in p.get("drugs") or []:
            product = cache.product(
                name=drug.get("product_name") or "",
                concentration=drug.get("concentration"),
                formulation=drug.get("formulation"),
                english_composition=drug.get("english_composition"),
                moa_text=" | ".join(drug.get("moa_codes") or []) or None,
            )
            if product is None:
                continue

            _link_ingredients(db, cache, product, drug, result)

            for category in extract_tolerance_categories(drug.get("tolerance_text")):
                _add_crop_category(db, cache, crop, category, 1, "ppm_tolerance_note")

            usage_hash = hashlib.sha256(json.dumps([drug.get(k) for k in (
                "dilution_text", "dose_per_hectare", "application_method", "precautions")],
                ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            reg_key = (crop.id, pest.id, product.id, usage_hash)
            if reg_key in seen_registrations:
                continue
            seen_registrations.add(reg_key)

            precautions = drug.get("precautions")
            db.add(
                Registration(
                    crop_id=crop.id,
                    pest_id=pest.id,
                    product_id=product.id,
                    snapshot_id=snap.id,
                    usage_hash=usage_hash,
                    dose_per_hectare=drug.get("dose_per_hectare"),
                    dilution_text=drug.get("dilution_text"),
                    dilution_value=_dilution_value(drug.get("dilution_text")),
                    application_method=drug.get("application_method"),
                    precautions=precautions,
                    phi_days=extract_phi_days(precautions),
                    mix_warnings=extract_mix_warnings(precautions) or None,
                    ppm_mrl_text=drug.get("tolerance_text"),
                    source_url=rec.source_url,
                )
            )

        rec.crop_id, rec.pest_id = crop.id, pest.id

    db.flush()


# 植保系統容許量欄長這樣：「嘉賜黴素0.2(旱稻) 克枯爛0.2(米) 嘉賜黴素0.2(米)」
# 括號裡就是食藥署那邊用的作物類別，拿來當對照資料比人工維護可靠。
_TOLERANCE_CATEGORY_RE = re.compile(r"[（(]([^（()）]{1,12})[)）]")


def extract_tolerance_categories(tolerance_text: str | None) -> set[str]:
    if not tolerance_text:
        return set()
    return {
        m.strip() for m in _TOLERANCE_CATEGORY_RE.findall(tolerance_text) if m.strip()
    }


def _add_crop_category(
    db: Session, cache: "EntityCache", crop: Crop, category: str,
    priority: int, note: str,
) -> None:
    category = (category or "").strip()
    if not category:
        return
    key = (crop.id, category)
    if key in cache.crop_categories:
        return
    cache.crop_categories.add(key)
    exists = db.scalars(
        select(CropFdaCategory).where(
            CropFdaCategory.crop_id == crop.id, CropFdaCategory.category == category
        )
    ).first()
    if exists is None:
        db.add(
            CropFdaCategory(
                crop_id=crop.id, category=category,
                priority=priority, source_note=note,
            )
        )


def _dilution_value(text: str | None) -> int | None:
    m = re.search(r"[\d,]+", text or "")
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _upsert_profile(db: Session, crop_id: int, pest_id: int, payload: dict) -> None:
    profile = db.scalars(
        select(PestProfile).where(
            PestProfile.crop_id == crop_id, PestProfile.pest_id == pest_id
        )
    ).first()
    if profile is None:
        profile = PestProfile(crop_id=crop_id, pest_id=pest_id)
        db.add(profile)
    profile.symptoms = payload.get("symptoms") or profile.symptoms
    profile.transmission = payload.get("transmission") or profile.transmission
    profile.cultural_control = payload.get("cultural_control") or profile.cultural_control


def _link_ingredients(
    db: Session, cache: EntityCache, product: Product, drug: dict,
    result: NormalizeResult,
) -> None:
    """用英文成分組合把產品接到成分表。對不上的記下來，之後開 issue。"""
    names = split_composition(drug.get("english_composition"))
    if not names:
        return

    for position, english in enumerate(names):
        ingredient = cache.ingredient_by_english(english)
        if ingredient is None:
            # 食藥署那邊查無此成分名 —— 可能是拼法差異，也可能真的沒訂容許量。
            # 先建一個只有英文名的成分，讓比對階段能開 INGREDIENT_UNMATCHED。
            ingredient = cache.upsert_ingredient(english=english, chinese=None)
            if english not in result.unmatched_ingredients:
                result.unmatched_ingredients.append(english)
        if ingredient is None:
            continue

        link_key = (product.id, ingredient.id)
        if link_key in cache.product_ingredients:
            continue
        exists = db.scalars(
            select(ProductIngredient).where(
                ProductIngredient.product_id == product.id,
                ProductIngredient.ingredient_id == ingredient.id,
            )
        ).first()
        cache.product_ingredients.add(link_key)
        if exists is None:
            db.add(
                ProductIngredient(
                    product_id=product.id,
                    ingredient_id=ingredient.id,
                    position=position,
                )
            )


# --------------------------------------------------------------------------
# 高改場 -> 場方推薦
# --------------------------------------------------------------------------

def normalize_kdais(db: Session, cache: EntityCache, result: NormalizeResult) -> None:
    snap = active_snapshot(db, SourceName.KDAIS)
    if snap is None:
        log.warning("沒有 active 的高改場 snapshot，跳過")
        return

    # 人工校對過的列不能被重跑蓋掉。
    from app.models import Confidence

    def verified_key(row):
        return (row.document_title, row.revision_date, json.dumps(row.raw_row, sort_keys=True, ensure_ascii=False))
    verified = {verified_key(row): row for row in db.scalars(select(KdaisRecommendation).where(
        KdaisRecommendation.confidence == Confidence.VERIFIED))}

    db.query(KdaisRecommendation).filter(
        KdaisRecommendation.confidence != Confidence.VERIFIED
    ).delete()
    db.flush()

    for rec in _records(db, snap, "kdais_row"):
        p = rec.payload
        key = (p.get("document_title"), p.get("revision_date"), json.dumps(p.get("raw_row"), sort_keys=True, ensure_ascii=False))
        if key in verified:
            verified[key].snapshot_id = snap.id
            continue
        crop = cache.crop(p.get("crop_text") or "") if p.get("crop_text") else None
        pest = cache.pest(p.get("pest_text") or "") if p.get("pest_text") else None

        db.add(
            KdaisRecommendation(
                crop_id=crop.id if crop else None,
                pest_id=pest.id if pest else None,
                snapshot_id=snap.id,
                crop_text=p.get("crop_text"),
                pest_text=p.get("pest_text"),
                product_text=p.get("product_text"),
                moa_text=p.get("moa_text"),
                dilution_text=p.get("dilution_text"),
                dose_text=p.get("dose_text"),
                phi_text=p.get("phi_text"),
                tolerance_text=p.get("tolerance_text"),
                method_text=p.get("method_text"),
                remark=p.get("remark"),
                raw_row=p.get("raw_row"),
                document_title=p.get("document_title"),
                pdf_url=rec.source_url,
                pdf_page=p.get("page"),
                revision_date=p.get("revision_date"),
                confidence=Confidence.HIGH if p.get("confident") else Confidence.LOW,
            )
        )

    db.flush()


# --------------------------------------------------------------------------

def normalize_all(db: Session) -> NormalizeResult:
    """依序跑三源。食藥署必須先跑，因為它是成分命名的權威來源。"""
    result = NormalizeResult()
    cache = EntityCache(db)

    normalize_fda(db, cache, result)
    db.commit()

    normalize_ppm(db, cache, result)
    db.commit()

    # 別名要在 PPM 之後、KDAIS 之前種：作物本體由 PPM 建立，
    # 然後高改場的「芒果」才接得回植保系統的「檬果」。
    seed_crop_aliases(db)
    seed_ingredient_groups(db)
    db.commit()

    normalize_kdais(db, cache, result)
    db.commit()

    # 統計一律用實際列數，不用處理次數 —— 後者會把同一個成分重複算好幾遍。
    result.crops = db.query(Crop).count()
    result.pests = db.query(Pest).count()
    result.ingredients = db.query(ActiveIngredient).count()
    result.products = db.query(Product).count()
    result.registrations = db.query(Registration).count()
    result.mrl_limits = db.query(MrlLimit).count()
    result.kdais_rows = db.query(KdaisRecommendation).count()

    log.info(
        "normalize: %d 成分 / %d MRL / %d 作物 / %d 害物 / %d 核准用藥 / %d 高改場列",
        result.ingredients, result.mrl_limits, result.crops, result.pests,
        result.registrations, result.kdais_rows,
    )
    return result
