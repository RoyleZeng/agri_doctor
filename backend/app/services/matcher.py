"""★ 三源比對 —— 這個系統的核心。

一句話說明流程：
    以「植保系統的核准清單」為合法性底線，
    逐支藥去問食藥署「這個成分在這個作物上的容許量是多少」，
    再問高改場「你們實務上推不推薦這支」，
    三邊對不起來的地方一律標出來，不藏。

刻意的設計取捨
--------------
* 不自動修正衝突。系統只負責把「誰說了什麼」攤開，判斷留給植物醫生。
* 高改場有列但植保系統查無核准 (KDAIS_ONLY) 標成 critical ——
  這代表可能要開一支沒登記的藥，是三種衝突裡最危險的。
* 禁用農藥直接擋下來，不管其他兩源怎麼說。
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ActiveIngredient, Confidence, Crop, CropAlias, CropFdaCategory, IssueType,
    KdaisDocument, KdaisRecommendation,
    MatchIssue, MrlLimit, Pest, PestProfile, Product, Registration, Severity,
    SourceName, SourceRecord, SourceSnapshot,
)
from app.services.phi_extract import split_precautions
from app.services.resistance import RotationAdvice, build_rotation_advice
from app.services.coverage import pair_coverage, snapshot_coverage

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# 回傳結構
# --------------------------------------------------------------------------

@dataclass
class MrlView:
    ingredient_id: int | None = None
    ingredient_name: str = ""
    ingredient_english: str | None = None
    limit_ppm: Decimal | None = None
    limit_text: str | None = None
    matched_category: str | None = None   # 實際命中的作物類別
    match_basis: str | None = None        # crop_name / crop_alias / fda_category（+group）
    group_name: str | None = None         # 以群組容許量命中時，群組名稱
    note: str | None = None
    found: bool = False


@dataclass
class IssueView:
    issue_type: str
    severity: str
    summary: str
    detail: dict = field(default_factory=dict)


@dataclass
class RowSourceInfo:
    """這一列在某個來源裡「有沒有出現、是哪個版本」。

    版本的定義三源不同，介面上必須講清楚，不能混為一談：

        高改場    有真正的文件版本 —— PDF 標題裡的修訂日期，逐份不同。
        植保系統  網頁沒有公告版本，只能用我們的爬取時間當代表。
        食藥署    同上。容許量表本身沒有生效日期欄位。

    所以 `version` 只在高改場會有值；另外兩源一律是 None，
    前端顯示「無版本號，以抓取時間為準」，不要拿抓取時間假裝成官方版本。
    """

    present: bool = False
    version: str | None = None          # 官方文件版本（目前只有高改場有）
    version_label: str | None = None    # 給人看的版本說明，例如「114.12.22 修訂」
    document: str | None = None         # 來源文件名稱
    fetched_at: str | None = None       # 我們抓到這份資料的時間
    snapshot_id: int | None = None
    detail: str | None = None           # 這一列在該來源的涵蓋狀況摘要
    url: str | None = None


@dataclass
class ComparisonRow:
    registration_id: int
    product_id: int
    product_name: str
    concentration: str | None
    formulation: str | None
    english_composition: str | None

    dilution_text: str | None
    dilution_value: int | None
    dose_per_hectare: str | None
    application_method: str | None
    precautions: list[str]
    phi_days: int | None
    mix_warnings: list[str]

    moa_codes: list[str]
    ppm_mrl_text: str | None
    source_url: str | None

    mrls: list[MrlView] = field(default_factory=list)
    kdais_recommended: bool = False
    kdais_entries: list[dict] = field(default_factory=list)
    banned: bool = False
    issues: list[IssueView] = field(default_factory=list)
    # 三源涵蓋狀況：ppm / fda / kdais
    sources: dict[str, RowSourceInfo] = field(default_factory=dict)


@dataclass
class ComparisonResult:
    crop: dict
    pest: dict
    profile: dict | None
    rows: list[ComparisonRow]
    rotation: RotationAdvice
    issues: list[IssueView]
    sources: dict
    coverage: dict


# --------------------------------------------------------------------------
# MRL 查詢
# --------------------------------------------------------------------------

def _crop_lookup_keys(db: Session, crop: Crop) -> list[tuple[str, str]]:
    """查 MRL 時要試的作物類別鍵，由具體到概括。

    食藥署的「作物類別」欄混著具體作物（柿、梨、大豆）與大類（米類、
    包葉菜類），所以先用作物本名，再用別名，最後才退到 PPM 描述裡
    「適用之大類容許量為X」抽出來的大類。
    """
    keys: list[tuple[str, str]] = []
    seen: set[str] = set()

    def push(value: str | None, basis: str) -> None:
        value = (value or "").strip()
        if value and value not in seen:
            seen.add(value)
            keys.append((value, basis))

    push(crop.name, "crop_name")
    for alias in db.scalars(select(CropAlias).where(CropAlias.crop_id == crop.id)):
        push(alias.alias, "crop_alias")
    # 由具體到概括：容許量欄註記的「米」會排在描述抽出的「米類」前面。
    for row in db.scalars(
        select(CropFdaCategory)
        .where(CropFdaCategory.crop_id == crop.id)
        .order_by(CropFdaCategory.priority)
    ):
        push(row.category, f"fda_category:{row.source_note}")
    push(crop.fda_category, "fda_category")
    return keys


def lookup_mrl(db: Session, ingredient: ActiveIngredient, crop: Crop) -> MrlView:
    view = MrlView(
        ingredient_id=ingredient.id,
        ingredient_name=ingredient.name,
        ingredient_english=ingredient.english_name,
    )
    crop_keys = _crop_lookup_keys(db, crop)

    # 先查成分本身；查不到再退到它所屬的群組（例如二硫代胺基甲酸鹽類）。
    candidates: list[tuple[ActiveIngredient, bool]] = [(ingredient, False)]
    if ingredient.mrl_group is not None:
        candidates.append((ingredient.mrl_group, True))

    for candidate, via_group in candidates:
        for key, basis in crop_keys:
            row = db.scalars(
                select(MrlLimit).where(
                    MrlLimit.ingredient_id == candidate.id,
                    MrlLimit.crop_category == key,
                )
            ).first()
            if row is None:
                continue
            view.limit_ppm = row.limit_ppm
            view.limit_text = row.limit_text
            view.note = row.note
            view.matched_category = row.crop_category
            view.match_basis = f"{basis}+group" if via_group else basis
            view.group_name = candidate.name if via_group else None
            view.found = True
            return view
    return view


def banned_ingredient_names(db: Session) -> set[str]:
    """公告禁用農藥的中英文名（小寫）—— 推薦前的硬性閘門。"""
    from app.services.normalize import active_snapshot

    snap = active_snapshot(db, SourceName.FDA)
    if snap is None:
        return set()

    names: set[str] = set()
    records = db.scalars(
        select(SourceRecord).where(
            SourceRecord.snapshot_id == snap.id,
            SourceRecord.record_kind == "fda_banned",
        )
    )
    for rec in records:
        from app.services.source_updates import date_from_notice
        from datetime import datetime
        from zoneinfo import ZoneInfo
        effective = date_from_notice(rec.payload.get("banned_sale_date"))
        if effective and effective > datetime.now(ZoneInfo("Asia/Taipei")).date():
            continue
        for key in ("name", "english_name"):
            value = (rec.payload.get(key) or "").strip().lower()
            if value:
                names.add(value)
    return names


def future_ban_notices(db):
    from app.services.normalize import active_snapshot
    from app.services.source_updates import date_from_notice
    from datetime import datetime
    from zoneinfo import ZoneInfo
    snap = active_snapshot(db, SourceName.FDA)
    if not snap:
        return {}
    notices = {}
    for rec in db.scalars(select(SourceRecord).where(SourceRecord.snapshot_id == snap.id,
            SourceRecord.record_kind == "fda_banned")):
        effective = date_from_notice(rec.payload.get("banned_sale_date"))
        if effective and effective > datetime.now(ZoneInfo("Asia/Taipei")).date():
            for key in ("name", "english_name"):
                name = (rec.payload.get(key) or "").strip().lower()
                if name:
                    notices[name] = {"effective_date": effective.isoformat(), "original": rec.payload, "url": rec.source_url}
    return notices


# --------------------------------------------------------------------------
# 高改場比對
# --------------------------------------------------------------------------

# 高改場摘要表把含量、藥名、劑型、標記符號全寫在同一欄
# （「10%克枯爛可濕性粉劑○S」），植保系統則是分欄的，比對前要先攤平。
_NOISE = re.compile(r"[\s○◎●☆★△▲※\-—－()（）,，、]")

# 同一個劑型兩邊用字不同：植保系統寫「可溼性粉劑」，高改場寫「可濕性粉劑」。
# 作物名的差異（檬果／芒果）由 crop_aliases 處理，不在這裡混進來。
_CHAR_VARIANTS = str.maketrans({"溼": "濕"})


def _core_name(text: str | None) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    return _NOISE.sub("", text).translate(_CHAR_VARIANTS).lower()


def _product_keys(product: Product) -> list[str]:
    """由嚴到寬的比對鍵。

    ⚠️ 不要退回「只用藥名做子字串比對」。實測過：植保系統的「克枯爛」會
    命中高改場的「14%嘉賜克枯爛可濕性粉劑」—— 那是含量、成分都不同的
    複方藥，結果憑空生出「稀釋倍數不一致 1,000 vs 1,500」的假衝突。
    含量與劑型必須一起比，才分得開單劑與複方。
    """
    name = _core_name(product.name)
    if not name:
        return []

    conc = _core_name(product.concentration_text)
    form = _core_name(product.formulation)

    keys: list[str] = []
    if conc and form:
        keys.append(conc + name + form)
    if form:
        # 劑型不同就是不同產品（水懸劑 vs 可濕性粉劑），所以劑型一定要帶上；
        # 只放寬含量，因為兩邊偶爾一邊寫「10%」一邊寫「100 g/L」。
        keys.append(name + form)
    elif conc:
        keys.append(conc + name)
    return keys


_CJK = re.compile(r"[\u3400-\u9fff]")


def _contains_at_name_boundary(haystack: str, needle: str) -> bool:
    """needle 必須出現在 haystack 裡，而且前面不能再接一個中文字。

    中文沒有詞界，所以單純的 `in` 會把複方當成單劑：
        「克枯爛可濕性粉劑」 in 「14%嘉賜克枯爛可濕性粉劑」 -> True（錯）
    但那是「嘉賜克枯爛」，是不同的藥。檢查前一個字元：
    正確的情形前面是含量或字串開頭（「10%克枯爛…」），
    錯誤的情形前面是中文字（「嘉賜」的「賜」），據此擋掉。
    """
    start = 0
    while (idx := haystack.find(needle, start)) != -1:
        if idx == 0 or not _CJK.match(haystack[idx - 1]):
            return True
        start = idx + 1
    return False


def _kdais_matches(
    kdais_rows: list[KdaisRecommendation], product: Product
) -> list[KdaisRecommendation]:
    """植保系統的「含量+藥名+劑型」應該是高改場那一長串的子字串。"""
    keys = _product_keys(product)
    if not keys:
        return []

    for key in keys:      # 由嚴到寬，第一個有命中的就採用
        matches = [
            row for row in kdais_rows
            if _contains_at_name_boundary(_core_name(row.product_text), key)
        ]
        if matches:
            return matches
    return []


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------

def compare(db: Session, crop_id: int, pest_id: int) -> ComparisonResult:
    crop = db.get(Crop, crop_id)
    pest = db.get(Pest, pest_id)
    if crop is None or pest is None:
        raise ValueError("找不到作物或病蟲害")

    registrations = db.scalars(
        select(Registration)
        .where(Registration.crop_id == crop_id, Registration.pest_id == pest_id)
        .options(selectinload(Registration.product).selectinload(Product.ingredients))
        .order_by(Registration.id)
    ).all()

    # 未經人工校對的低信心列不併入比對，只在衝突區提示需要校對。
    kdais_all = db.scalars(
        select(KdaisRecommendation).where(
            KdaisRecommendation.crop_id == crop_id,
            KdaisRecommendation.pest_id == pest_id,
            KdaisRecommendation.snapshot_id == select(SourceSnapshot.id).where(
                SourceSnapshot.source == SourceName.KDAIS, SourceSnapshot.is_active.is_(True)).order_by(SourceSnapshot.id.desc()).limit(1).scalar_subquery(),
        )
    ).all()
    kdais_usable = [
        r for r in kdais_all if r.confidence in (Confidence.HIGH, Confidence.VERIFIED)
    ]

    banned = banned_ingredient_names(db)
    upcoming_bans = future_ban_notices(db)
    snapshots = _active_snapshots(db)
    coverage = pair_coverage(db, crop_id, pest_id)
    rows: list[ComparisonRow] = []
    global_issues: list[IssueView] = []
    matched_kdais_ids: set[int] = set()

    for reg in registrations:
        product = reg.product
        row = ComparisonRow(
            registration_id=reg.id,
            product_id=product.id,
            product_name=product.name,
            concentration=product.concentration_text,
            formulation=product.formulation,
            english_composition=product.english_composition,
            dilution_text=reg.dilution_text,
            dilution_value=reg.dilution_value,
            dose_per_hectare=reg.dose_per_hectare,
            application_method=reg.application_method,
            precautions=split_precautions(reg.precautions),
            phi_days=reg.phi_days,
            mix_warnings=list(reg.mix_warnings or []),
            moa_codes=(product.moa_text or "").split(" | ") if product.moa_text else [],
            ppm_mrl_text=reg.ppm_mrl_text,
            source_url=reg.source_url,
        )

        _fill_mrls(db, row, product, crop, banned)
        for link in product.ingredients:
            ingredient = link.ingredient
            notice = (upcoming_bans.get((ingredient.name or "").lower()) or upcoming_bans.get((ingredient.english_name or "").lower())) if ingredient else None
            if notice:
                row.issues.append(IssueView(issue_type=IssueType.BAN_ANNOUNCED.value, severity=Severity.WARNING.value,
                    summary=f"成分「{ingredient.name}」已公告於 {notice['effective_date']} 禁止銷售／使用，請核對過渡期規定。", detail=notice))
        _fill_kdais(row, kdais_usable, product, matched_kdais_ids)
        _fill_row_sources(row, reg, snapshots)
        rows.append(row)

    # 高改場有推薦、但植保系統這組作物病蟲害查無核准 —— 最危險的一種不一致。
    for kd in kdais_usable:
        if kd.id in matched_kdais_ids:
            continue
        global_issues.append(
            IssueView(
                issue_type=IssueType.KDAIS_ONLY.value if coverage["can_assert_page_absence"] else IssueType.PPM_COVERAGE_GAP.value,
                severity=Severity.CRITICAL.value if coverage["can_assert_page_absence"] else Severity.WARNING.value,
                summary=(
                    f"高改場摘要表列了「{kd.product_text}」，"
                    + (f"但已收錄植保頁面未對上「{crop.name}／{pest.name}」的產品，請人工核對名稱、含量與劑型"
                       if coverage["can_assert_page_absence"] else "植保資料尚未完整驗證，暫時無法確認是否對應核准登記")
                ),
                detail={
                    "product_text": kd.product_text,
                    "dilution": kd.dilution_text,
                    "phi": kd.phi_text,
                    "pdf_url": kd.pdf_url,
                    "pdf_page": kd.pdf_page,
                    "revision_date": kd.revision_date,
                    "coverage": coverage["state"],
                },
            )
        )

    gaps = [i for i in global_issues if i.issue_type == IssueType.PPM_COVERAGE_GAP.value]
    if gaps:
        global_issues = [i for i in global_issues if i.issue_type != IssueType.PPM_COVERAGE_GAP.value]
        global_issues.append(IssueView(issue_type=IssueType.PPM_COVERAGE_GAP.value,
            severity=Severity.WARNING.value,
            summary=f"高改場有 {len(gaps)} 筆用藥尚無法完整比對；此組合的植保收錄完整性待確認，不能據此判定無核准登記。",
            detail={"count":len(gaps),"coverage":coverage["state"],"products":[i.detail for i in gaps]}))

    pending = [r for r in kdais_all if r.confidence == Confidence.LOW]
    if pending:
        global_issues.append(
            IssueView(
                issue_type=IssueType.KDAIS_PENDING_REVIEW.value,
                severity=Severity.INFO.value,
                summary=(
                    f"高改場另有 {len(pending)} 列 PDF 解析結果信心不足，"
                    "尚未納入比對，請至校對介面確認"
                ),
                detail={"count": len(pending)},
            )
        )

    rotation = build_rotation_advice(
        [(r.product_id, r.product_name, r.moa_codes) for r in rows if not r.banned]
    )

    profile = db.scalars(
        select(PestProfile).where(
            PestProfile.crop_id == crop_id, PestProfile.pest_id == pest_id
        )
    ).first()

    return ComparisonResult(
        crop={
            "id": crop.id, "name": crop.name,
            "scientific_name": crop.scientific_name,
            # 舊的單一欄位只存植保系統描述抽到的大類，常常是空的；
            # 真正在查 MRL 的是 crop_fda_categories，所以一起帶出去，
            # 前端才不會在明明查得到容許量時還顯示「作物類別未對應」。
            "fda_category": crop.fda_category,
            "fda_categories": [c for c, _ in _crop_lookup_keys(db, crop)[1:]],
            "aliases": [a.alias for a in db.scalars(
                select(CropAlias).where(CropAlias.crop_id == crop.id)
            )],
            "description": crop.description,
        },
        pest={
            "id": pest.id, "name": pest.name, "kind": pest.kind.value,
            "scientific_name": pest.scientific_name,
            "english_name": pest.english_name,
        },
        profile=None if profile is None else {
            "symptoms": profile.symptoms,
            "transmission": profile.transmission,
            "cultural_control": profile.cultural_control,
        },
        rows=rows,
        rotation=rotation,
        issues=global_issues,
        sources=source_freshness(db),
        coverage=coverage,
    )


def _fill_mrls(
    db: Session, row: ComparisonRow, product: Product, crop: Crop, banned: set[str],
) -> None:
    for link in sorted(product.ingredients, key=lambda x: x.position):
        ingredient = link.ingredient
        if ingredient is None:
            continue

        if (
            (ingredient.name or "").lower() in banned
            or (ingredient.english_name or "").lower() in banned
        ):
            row.banned = True
            row.issues.append(
                IssueView(
                    issue_type=IssueType.BANNED_INGREDIENT.value,
                    severity=Severity.CRITICAL.value,
                    summary=f"成分「{ingredient.name}」在食藥署公告禁用農藥名單中",
                    detail={"ingredient": ingredient.name},
                )
            )

        view = lookup_mrl(db, ingredient, crop)
        row.mrls.append(view)

        if not view.found:
            if not ingredient.english_name and not ingredient.name:
                continue
            severity = (
                Severity.WARNING.value
                if ingredient.english_name
                else Severity.INFO.value
            )
            row.issues.append(
                IssueView(
                    issue_type=IssueType.MRL_MISSING.value,
                    severity=severity,
                    summary=(
                        f"本站食藥署快照未找到成分「{ingredient.name}」在「{crop.name}」"
                        f"（大類：{crop.fda_category or '未對應'}）的殘留容許量，需核對來源完整性與類別對應"
                    ),
                    detail={
                        "ingredient": ingredient.name,
                        "ingredient_english": ingredient.english_name,
                        "crop": crop.name,
                        "fda_category": crop.fda_category,
                        "ppm_says": row.ppm_mrl_text,
                    },
                )
            )
            continue

        # 植保系統頁面上自己也印了容許量；跟食藥署現行值對不起來就標出來。
        if row.ppm_mrl_text and view.limit_ppm is not None:
            if not _ppm_text_agrees(row.ppm_mrl_text, view.limit_ppm):
                row.issues.append(
                    IssueView(
                        issue_type=IssueType.MRL_MISMATCH.value,
                        severity=Severity.WARNING.value,
                        summary=(
                            f"植保系統顯示的容許量與食藥署現行值不一致"
                            f"（食藥署：{view.limit_text} ppm / {view.matched_category}）"
                        ),
                        detail={
                            "ingredient": ingredient.name,
                            "ppm_text": row.ppm_mrl_text,
                            "fda_limit": str(view.limit_ppm),
                            "fda_category": view.matched_category,
                        },
                    )
                )


def _ppm_text_agrees(ppm_text: str, fda_limit: Decimal) -> bool:
    """PPM 的容許量欄是「嘉賜黴素0.2(米) 克枯爛0.2(米)」這種多值字串，
    只要裡面出現食藥署的數值就當一致，避免製造大量假警報。"""
    numbers = {n for n in re.findall(r"\d+(?:\.\d+)?", ppm_text or "")}
    target = str(fda_limit.normalize())
    return target in numbers or f"{fda_limit:.1f}" in numbers or f"{fda_limit:g}" in numbers


def _fill_kdais(
    row: ComparisonRow,
    kdais_rows: list[KdaisRecommendation],
    product: Product,
    matched_ids: set[int],
) -> None:
    matches = _kdais_matches(kdais_rows, product)
    if matches:
        row.kdais_recommended = True
        for kd in matches:
            matched_ids.add(kd.id)
            row.kdais_entries.append(
                {
                    "document_title": kd.document_title,
                    "product_text": kd.product_text,
                    "dilution": kd.dilution_text,
                    "phi": kd.phi_text,
                    "tolerance": kd.tolerance_text,
                    "moa": kd.moa_text,
                    "dose": kd.dose_text,
                    "remark": kd.remark,
                    "pdf_url": kd.pdf_url,
                    "pdf_page": kd.pdf_page,
                    "revision_date": kd.revision_date,
                    "confidence": kd.confidence.value,
                }
            )
            _compare_kdais_fields(row, kd)
    elif kdais_rows:
        # 這個作物病蟲害有摘要表，但這支藥不在上面。
        row.issues.append(
            IssueView(
                issue_type=IssueType.KDAIS_NOT_RECOMMENDED.value,
                severity=Severity.INFO.value,
                summary="合法核准，但高改場摘要表未列入推薦",
                detail={"product": product.name},
            )
        )


def _compare_kdais_fields(row: ComparisonRow, kd: KdaisRecommendation) -> None:
    """同一支藥兩邊的稀釋倍數／安全採收期對不起來時標出來。"""
    if row.dilution_value and kd.dilution_text:
        nums = re.findall(r"[\d,]+", kd.dilution_text)
        values = {int(n.replace(",", "")) for n in nums if n.replace(",", "").isdigit()}
        if values and row.dilution_value not in values:
            row.issues.append(
                IssueView(
                    issue_type=IssueType.DILUTION_MISMATCH.value,
                    severity=Severity.WARNING.value,
                    summary=(
                        f"稀釋倍數不一致：植保系統 {row.dilution_text} 倍 / "
                        f"高改場 {kd.dilution_text} 倍"
                    ),
                    detail={"ppm": row.dilution_text, "kdais": kd.dilution_text},
                )
            )

    if row.phi_days is not None and kd.phi_text:
        nums = re.findall(r"\d+", kd.phi_text)
        if nums and str(row.phi_days) not in nums:
            row.issues.append(
                IssueView(
                    issue_type=IssueType.PHI_MISMATCH.value,
                    severity=Severity.WARNING.value,
                    summary=(
                        f"安全採收期不一致：植保系統 {row.phi_days} 天 / "
                        f"高改場 {kd.phi_text}"
                    ),
                    detail={"ppm": row.phi_days, "kdais": kd.phi_text},
                )
            )


def _active_snapshots(db: Session) -> dict[SourceName, SourceSnapshot | None]:
    from app.services.normalize import active_snapshot

    return {source: active_snapshot(db, source) for source in SourceName}


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _fill_row_sources(
    row: ComparisonRow,
    reg: Registration,
    snapshots: dict[SourceName, SourceSnapshot | None],
) -> None:
    """標出這一列在三個來源各自的涵蓋狀況與版本。

    植物醫生第一個要問的是「這條建議是誰說的、資料多新」，所以每一列都要
    自己帶著答案，不能只靠頁尾一個全域的更新時間。
    """
    ppm_snap = snapshots.get(SourceName.PPM)
    fda_snap = snapshots.get(SourceName.FDA)
    kdais_snap = snapshots.get(SourceName.KDAIS)

    # --- 植保系統：這一列本來就是從核准登記長出來的，一定有 ---
    row.sources["ppm"] = RowSourceInfo(
        present=True,
        version=None,                       # 網頁沒有公告版本號
        version_label="無版本號，以抓取時間為準",
        document="植物保護資訊系統 核准使用範圍",
        fetched_at=_iso(ppm_snap.finished_at) if ppm_snap else None,
        snapshot_id=ppm_snap.id if ppm_snap else None,
        detail="有核准登記",
        url=reg.source_url,
    )

    # --- 食藥署：看這支藥的成分查到幾個容許量 ---
    total = len(row.mrls)
    found = sum(1 for m in row.mrls if m.found)
    if total == 0:
        detail = "此藥劑未對應到任何成分"
    elif found == 0:
        detail = f"{total} 個成分皆查無容許量"
    elif found < total:
        detail = f"{found}/{total} 個成分查到容許量"
    else:
        detail = f"{found} 個成分皆有容許量"
        via_group = [m.group_name for m in row.mrls if m.found and m.group_name]
        if via_group:
            detail += f"（{'、'.join(via_group)} 以群組容許量計）"

    row.sources["fda"] = RowSourceInfo(
        present=found > 0,
        version=None,                       # 容許量表沒有生效日期欄位
        version_label="無版本號，以抓取時間為準",
        document="食藥署 農藥殘留容許量標準",
        fetched_at=_iso(fda_snap.finished_at) if fda_snap else None,
        snapshot_id=fda_snap.id if fda_snap else None,
        detail=detail,
    )

    # --- 高改場：唯一有真正文件版本的來源 ---
    entries = row.kdais_entries
    revisions = sorted({e["revision_date"] for e in entries if e.get("revision_date")})
    documents = sorted({e["document_title"] for e in entries if e.get("document_title")})
    row.sources["kdais"] = RowSourceInfo(
        present=bool(entries),
        version="、".join(revisions) or None,
        # version 已經是日期本身，label 只說明這個版本號是什麼，不要重複日期。
        version_label="PDF 摘要表修訂日期" if revisions
        else ("摘要表未列此藥劑" if kdais_snap else "尚未取得高改場資料"),
        document="；".join(documents) or None,
        fetched_at=_iso(kdais_snap.finished_at) if kdais_snap else None,
        snapshot_id=kdais_snap.id if kdais_snap else None,
        detail=f"摘要表列了 {len(entries)} 列" if entries else "摘要表未列此藥劑",
        url=entries[0].get("pdf_url") if entries else None,
    )


def source_freshness(db: Session) -> dict:
    """每個來源最後一次成功更新的時間 —— 前端頁尾要顯示。"""
    from app.services.normalize import active_snapshot

    out: dict[str, dict] = {}
    labels = {
        SourceName.PPM: "植物保護資訊系統",
        SourceName.FDA: "食藥署農藥殘留容許量",
        SourceName.KDAIS: "高改場作物安全用藥資訊",
    }
    # 只有高改場的來源文件本身帶版本（PDF 修訂日期），另外兩源沒有公告版本號。
    versioned = {SourceName.KDAIS}

    for source, label in labels.items():
        snap = active_snapshot(db, source)
        out[source.value] = {
            "label": label,
            "fetched_at": snap.finished_at.isoformat() if snap and snap.finished_at else None,
            "row_count": snap.row_count if snap else 0,
            "status": snap.status.value if snap else "never",
            "snapshot_id": snap.id if snap else None,
            "has_document_version": source in versioned,
            "complete": bool(snap and (snap.meta or {}).get("complete")),
        }
        if source == SourceName.PPM:
            out[source.value]["coverage"] = snapshot_coverage(db, snap)

    # 高改場逐份文件都有修訂日期，列出來讓人知道現在用的是哪一版。
    documents = db.scalars(
        select(KdaisDocument).order_by(KdaisDocument.crop_text, KdaisDocument.title)
    ).all()
    out[SourceName.KDAIS.value]["documents"] = [
        {
            "title": d.title,
            "crop_text": d.crop_text,
            "revision_date": d.revision_date,
            "pdf_url": d.pdf_url,
        }
        for d in documents
    ]
    kd_snap = active_snapshot(db, SourceName.KDAIS)
    if kd_snap and (kd_snap.meta or {}).get("complete"):
        out[SourceName.KDAIS.value]["documents"] = [
            {key: r.payload.get(key) for key in ("title", "crop_text", "revision_date", "pdf_url")}
            for r in db.scalars(select(SourceRecord).where(SourceRecord.snapshot_id == kd_snap.id,
                SourceRecord.record_kind == "kdais_document"))]
    return out


# --------------------------------------------------------------------------
# 全庫掃描：把衝突寫進 match_issues，供「衝突總覽」頁使用
# --------------------------------------------------------------------------

def scan_all_issues(db: Session, *, commit: bool = True) -> int:
    db.query(MatchIssue).filter(MatchIssue.resolved.is_(False)).delete()
    db.flush()

    pairs = db.execute(select(Registration.crop_id, Registration.pest_id).union(
        select(KdaisRecommendation.crop_id, KdaisRecommendation.pest_id).where(
            KdaisRecommendation.crop_id.is_not(None), KdaisRecommendation.pest_id.is_not(None)))).all()

    count = 0
    for crop_id, pest_id in pairs:
        try:
            result = compare(db, crop_id, pest_id)
        except ValueError:
            continue

        for issue in result.issues:
            db.add(_to_model(issue, crop_id, pest_id, None))
            count += 1
        for row in result.rows:
            for issue in row.issues:
                db.add(_to_model(issue, crop_id, pest_id, row.registration_id))
                count += 1

    if commit:
        db.commit()
    else:
        db.flush()
    log.info("scan_all_issues: %d issues", count)
    return count


def _to_model(
    issue: IssueView, crop_id: int, pest_id: int, registration_id: int | None
) -> MatchIssue:
    issue_type = IssueType(issue.issue_type)
    return MatchIssue(
        issue_type=issue_type,
        severity=Severity(issue.severity),
        crop_id=crop_id,
        pest_id=pest_id,
        registration_id=registration_id,
        summary=issue.summary,
        detail=issue.detail,
    )
