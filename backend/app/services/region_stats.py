"""區域作物與病蟲害地圖：鄉鎮正規化與跨來源彙整。

正規化、彙整、警戒比對都是純函式（離線可測）；`collect_source_rows`／`reaggregate`／
`district_alerts` 會查資料庫，測試要接假 session 或整合測試才能涵蓋。

彙整規則（跟隱私相關，改動前先想一遍為什麼）：
* 只算 `share_region=true` 的農場；`survey`（機關／植物醫生主動調查）沒有農場歸屬，不受此限。
* 同一鄉鎮＋同一天＋同一作物／病蟲害＋同一來源，貢獻的「不重複農場數」< 3 時整組 count／area_ha／
  metric 一律 None——用不重複農場數而不是列數，避免一戶高頻回報就撐過門檻，變相洩漏「其實只有這戶」。
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.consult import ConsultCase
from app.models.farm_log import FarmWorkLog
from app.models.planning import FarmAccount, FarmPlan
from app.models.region import AlertThreshold, RegionDailyStat, SurveyRecord
from app.services.farm_log import as_utc, plain_number

GEO_DIR = Path(__file__).resolve().parent.parent / "data" / "geo"
TAIPEI = ZoneInfo("Asia/Taipei")
REGION_UPSERT_KEYS = ("county", "district", "day", "crop_text", "pest_text", "source")
# 每次彙整都重算完整歷史的來源：這次沒算出來就是來源不在了，舊列可以刪。
REBUILDABLE_SOURCES = ("consult", "scout", "survey")
# 只能重算「今天」的來源：plan 是當下正在種什麼的快照，過去某天的分布無從重建，舊列一律留著。
SNAPSHOT_SOURCES = ("plan",)


# ---- 鄉鎮界資料（單一事實來源；新增縣市只要照 data/geo/README.md 加檔案，這裡不用改） ----

@lru_cache
def _all_features() -> tuple[dict, ...]:
    features: list[dict] = []
    for path in sorted(GEO_DIR.glob("*.geo.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        features.extend(data["features"])
    return tuple(features)


@lru_cache
def available_counties() -> tuple[str, ...]:
    seen: list[str] = []
    for feature in _all_features():
        county = feature["properties"]["county"]
        if county not in seen:
            seen.append(county)
    return tuple(seen)


@lru_cache
def load_districts(county: str) -> tuple[str, ...]:
    """該縣市的區名清單；沒有資料回空 tuple（地圖與彙整都當「尚未提供」，不是漏算）。"""
    return tuple(f["properties"]["district"] for f in _all_features() if f["properties"]["county"] == county)


def features_for_county(county: str) -> list[dict]:
    return [f for f in _all_features() if f["properties"]["county"] == county]


def county_for_district(district: str) -> str | None:
    for county in available_counties():
        if district in load_districts(county):
            return county
    return None


def normalize_location(location: str) -> tuple[str, str] | None:
    """從 `farm_accounts.location` 這種自由文字抓出 (縣市, 鄉鎮)，延伸 `weather.county_from_location`
    的「唯一命中才算數」作法。抓不到（尚未提供該縣市資料、或文字沒寫鄉鎮）回 None，該筆不計入彙整。
    """
    text = (location or "").replace("台", "臺")
    for county in available_counties():
        matches = [d for d in load_districts(county) if d in text]
        if len(matches) == 1:
            return county, matches[0]
    return None


# ---- 彙整：SourceRow → region_daily_stats ----

@dataclass(frozen=True)
class SourceRow:
    county: str
    district: str
    day: date
    crop_text: str
    pest_text: str | None
    source: str  # consult / scout / plan / survey
    farm_id: str | None  # survey 沒有農場歸屬，固定 None
    area_ha: float | None = None
    metric: float | None = None  # 只有 survey（每板隻數）會填


def _sum_or_none(values):
    values = [v for v in values if v is not None]
    return round(sum(values), 4) if values else None


def _avg_or_none(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 2) if values else None


def aggregate_region_stats(rows: list[SourceRow], *, min_farms: int = 3) -> list[dict]:
    groups: dict[tuple, list[SourceRow]] = defaultdict(list)
    for row in rows:
        groups[(row.county, row.district, row.day, row.crop_text, row.pest_text, row.source)].append(row)

    out = []
    for (county, district, day, crop_text, pest_text, source), group in groups.items():
        farms = len({r.farm_id for r in group if r.farm_id is not None})
        suppressed = source != "survey" and farms < min_farms
        out.append(dict(
            county=county, district=district, day=day, crop_text=crop_text, pest_text=pest_text, source=source,
            count=None if suppressed else len(group),
            area_ha=None if suppressed else _sum_or_none(r.area_ha for r in group),
            metric=None if suppressed else _avg_or_none(r.metric for r in group),
            farms=farms,
        ))
    return out


def collect_source_rows(db: Session, today: date) -> list[SourceRow]:
    """組出彙整用的原始列。四個來源共用同一套 SourceRow，但「天」的意義不同：
    consult／scout／survey 各用自己實際發生的日期；plan 沒有「哪天種的變化」可言，
    用彙整當天當快照，代表「現在正在種什麼」，不是歷史逐日分布。
    """
    consenting = db.execute(select(FarmAccount.id, FarmAccount.location).where(
        FarmAccount.role == "farmer", FarmAccount.share_region.is_(True))).all()
    farm_district: dict[str, tuple[str, str]] = {}
    for farm_id, location in consenting:
        resolved = normalize_location(location)
        if resolved:
            farm_district[farm_id] = resolved
    farm_ids = tuple(farm_district)

    rows: list[SourceRow] = []
    if farm_ids:
        for case in db.scalars(select(ConsultCase).where(
                ConsultCase.farmer_id.in_(farm_ids), ConsultCase.status.in_(("saved", "issued")),
                ConsultCase.diagnosis_text != "")):
            county, district = farm_district[case.farmer_id]
            day = as_utc(case.created_at).astimezone(TAIPEI).date()
            rows.append(SourceRow(county, district, day, case.crop_text, case.diagnosis_text, "consult", case.farmer_id))

        for log in db.scalars(select(FarmWorkLog).where(
                FarmWorkLog.farmer_id.in_(farm_ids), FarmWorkLog.kind == "scout",
                FarmWorkLog.pest_text != "", FarmWorkLog.crop_text != "")):
            county, district = farm_district[log.farmer_id]
            rows.append(SourceRow(county, district, log.done_on, log.crop_text, log.pest_text, "scout", log.farmer_id))

        for plan in db.scalars(select(FarmPlan).where(
                FarmPlan.farmer_id.in_(farm_ids), FarmPlan.archived.is_(False),
                FarmPlan.planted_on <= today, FarmPlan.end_on >= today)):
            county, district = farm_district[plan.farmer_id]
            rows.append(SourceRow(county, district, today, plan.crop_name, None, "plan", plan.farmer_id,
                                   area_ha=plain_number(plan.area)))

    for survey in db.scalars(select(SurveyRecord)):
        county = county_for_district(survey.district)
        if county:
            rows.append(SourceRow(county, survey.district, survey.surveyed_on, survey.crop_text, survey.pest_text,
                                   "survey", None, metric=survey.trap_count))
    return rows


def reaggregate(db: Session, today: date) -> dict:
    """手動「重新彙整」：整批 upsert ＋ 刪掉已經算不出來的舊列，回傳 {"written", "removed"}。

    **為什麼要刪**：日誌可以在 24 小時內撤回、也可以改日期。只 upsert 不刪的話，來源不在了，
    彙整出來的那筆還留著，地圖就會一直顯示一筆早就不存在的通報——跟「撤回的施藥不能被輪替檢查
    讀到」是同一個原則：撤回就要真的看不到。

    **能刪哪些**（別放寬）：只刪「這次有能力重算」的範圍。
    * `consult`／`scout`／`survey`：`collect_source_rows` 每次都重算完整歷史，所以這次沒算出來的
      就是真的沒有了，可以刪。
    * `plan`：是「今天正在種什麼」的快照，過去某一天種了什麼**無從重建**（規劃改了就改了）。
      所以只刪今天這批，更早的日子一律留著，否則一重跑就把歷史面積分布抹掉。
    """
    stats = aggregate_region_stats(collect_source_rows(db, today))
    fresh = {tuple(stat[key] for key in REGION_UPSERT_KEYS) for stat in stats}
    for stat in stats:
        existing = db.scalar(select(RegionDailyStat).where(*(
            getattr(RegionDailyStat, key) == stat[key] for key in REGION_UPSERT_KEYS)))
        if existing:
            for key in ("count", "area_ha", "metric", "farms"):
                setattr(existing, key, stat[key])
        else:
            db.add(RegionDailyStat(**stat))

    removed = 0
    for row in db.scalars(select(RegionDailyStat)).all():
        if tuple(getattr(row, key) for key in REGION_UPSERT_KEYS) in fresh:
            continue
        if row.source in REBUILDABLE_SOURCES or (row.source in SNAPSHOT_SOURCES and row.day == today):
            db.delete(row)
            removed += 1
    db.commit()
    return {"written": len(stats), "removed": removed}


# ---- 色階：切點由 API 算好回傳，前端不寫死 ----

def color_scale(values: list[float], classes: int = 5) -> list[float]:
    """回傳等分位切點（含頭尾）；沒有資料回空 list（前端全部畫灰色）。"""
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return []
    step = len(vals) / classes
    breaks = [vals[min(len(vals) - 1, round(step * i))] for i in range(classes + 1)]
    breaks[-1] = vals[-1]
    out: list[float] = []
    for b in breaks:
        if not out or b > out[-1]:
            out.append(b)
    return out


# ---- 「附近已達警戒」：給複合圖病蟲害巡查色帶用 ----

def evaluate_alerts(readings: list[dict], thresholds: list[AlertThreshold]) -> list[dict]:
    """readings 是已經彙整好的 {"pest_text","metric","value"}；純函式只比大小，不碰資料庫。"""
    out = []
    for t in thresholds:
        match = next((r for r in readings if r["pest_text"] == t.pest_text and r["metric"] == t.metric), None)
        if match and match["value"] >= t.warn_value:
            out.append(dict(pest_text=t.pest_text, metric=t.metric, value=match["value"],
                             warn_value=t.warn_value, basis=t.basis))
    return out


def collect_district_readings(db: Session, county: str, district: str, today: date, *, window_days: int = 30) -> list[dict]:
    since = today - timedelta(days=window_days)
    rows = db.scalars(select(RegionDailyStat).where(
        RegionDailyStat.county == county, RegionDailyStat.district == district,
        RegionDailyStat.day >= since, RegionDailyStat.day <= today,
        RegionDailyStat.source.in_(("scout", "survey")))).all()
    reports: dict[str, int] = defaultdict(int)
    trap_values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.pest_text:
            continue
        if row.source == "scout" and row.count is not None:
            reports[row.pest_text] += row.count
        elif row.source == "survey" and row.metric is not None:
            trap_values[row.pest_text].append(row.metric)
    readings = [dict(pest_text=p, metric="reports_30d", value=float(v)) for p, v in reports.items()]
    readings += [dict(pest_text=p, metric="trap_count", value=sum(vs) / len(vs)) for p, vs in trap_values.items()]
    return readings


def district_alerts(db: Session, county: str, district: str, today: date) -> list[dict]:
    thresholds = db.scalars(select(AlertThreshold)).all()
    if not thresholds:
        return []
    return evaluate_alerts(collect_district_readings(db, county, district, today), thresholds)


# ---- API 層共用的彙總（把已經套過 farms<3 抑制的多筆 row 併成一格地圖用的數字） ----

def summarize_district_rows(rows: list[RegionDailyStat]) -> dict:
    counted = [r.count for r in rows if r.count is not None]
    area_vals = [r.area_ha for r in rows if r.area_ha is not None]
    metric_vals = [r.metric for r in rows if r.metric is not None]
    count = sum(counted) if counted else None
    area_ha = round(sum(area_vals), 2) if area_vals else None
    metric = round(sum(metric_vals) / len(metric_vals), 2) if metric_vals else None
    has_data = count is not None or area_ha is not None or metric is not None
    # 有列但三個數字都是 None，且至少一列曾經有農場貢獻過（只是不到 3 戶）才算「樣本不足」；
    # 完全沒有列＝這格本來就沒人通報，前端畫法不同（無資料 vs 樣本不足），不能混為一談。
    sample_insufficient = (not has_data) and any(r.farms and r.source != "survey" for r in rows)
    return dict(count=count, area_ha=area_ha, metric=metric, sample_insufficient=sample_insufficient)
