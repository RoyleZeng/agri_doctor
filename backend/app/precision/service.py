"""精準模式的資料存取與建議生命週期。

規則判斷全部在 rules.py（純函式）；這裡做三件事：把資料庫的讀數、圖層、施肥紀錄組成引擎輸入並把
建議寫回 recommendations，把「接受」／「已執行」轉成規劃事項／農事日誌的實際動作，以及組出儀表板要讀的資料。
時間一律由呼叫端傳入，測試與示範產生器才能固定時鐘。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from sqlalchemy import and_, func, or_, select, update

from app.models.farm_log import FarmWorkLog
from app.models.planning import FarmAccount, FarmEvent, FarmPlan
from app.models.precision import (FarmField, FarmZone, FieldBoundary, FieldLayer, Recommendation, SensorGroup,
                                  SensorReading, SensorStation)
from app.precision import rules as engine
from app.services import weather
from app.services.farm_log import as_utc

# 示範田區用固定的預報：示範讀數是假的，配上真實天氣會讓展示畫面隨當天下不下雨而變。
DEMO_RAIN = engine.RainForecast(total_mm=0.0, source="示範資料：未來 48 小時無降雨")
GENERIC_EXPIRY_REASON = "已超過建議有效期，田間狀況可能已經改變，請重新分析。"
GENERIC_WITHDRAWAL_REASON = "已不符合建議條件，請重新分析查看目前狀況。"
_UNSET = object()


def rain_forecast_48h(farmer: FarmAccount, now: datetime) -> engine.RainForecast | None:
    """中央氣象署縣市預報（services/weather.py）只有降雨機率、沒有雨量：取 48 小時內最高機率，不換算成 mm。

    沒設縣市、沒設授權碼或預報取不到都回 None，引擎會因此不產生補灌建議，而不是當成不會下雨。
    """
    county = farmer.weather_county or weather.county_from_location(farmer.location)
    if not county:
        return None
    try:
        result = weather.get_forecast(county, now=as_utc(now).astimezone(weather.TAIPEI))
    except ValueError:
        return None
    if result.get("status") != "ok":
        return None
    chances = [p["rain_probability"] for p in result.get("periods", []) if p.get("rain_probability") is not None]
    if not chances:
        return None
    return engine.RainForecast(max_probability_pct=max(chances), source=f"中央氣象署 {county} 縣市預報（降雨機率）")


def last_fertilized_on(db, field: FarmField, today: date) -> date | None:
    # 日誌有連到田區主檔就用 field_id；舊紀錄或還沒連上的，退回田區名稱快照比對。
    return db.scalar(select(func.max(FarmWorkLog.done_on)).where(
        FarmWorkLog.farmer_id == field.farmer_id, FarmWorkLog.kind == "fertilize", FarmWorkLog.done_on <= today,
        or_(FarmWorkLog.field_id == field.id, and_(FarmWorkLog.field_id.is_(None), FarmWorkLog.field_text == field.name))))


def rain_total(db, field: FarmField, *, start: datetime, end: datetime) -> float | None:
    """這段期間實際量到的降雨（氣象站 metric=rain），不是預報。查不到任何讀數回 None，不當成 0。"""
    rows = db.execute(select(SensorReading.value).join(SensorStation, SensorStation.id == SensorReading.station_id)
                      .where(SensorStation.field_id == field.id, SensorReading.metric == "rain",
                             SensorReading.ts > start, SensorReading.ts <= end)).scalars().all()
    return round(sum(rows), 1) if rows else None


def field_input(db, field: FarmField, *, now: datetime, today: date, rain, rules: dict) -> engine.FieldInput:
    zones = db.scalars(select(FarmZone).where(FarmZone.field_id == field.id).order_by(FarmZone.code)).all()
    codes = {zone.id: zone.code for zone in zones}
    cfg = rules["irrigate"]
    rows = db.execute(
        select(SensorReading.id, SensorStation.zone_id, SensorReading.metric, SensorReading.ts, SensorReading.value)
        .join(SensorStation, SensorStation.id == SensorReading.station_id)
        .where(SensorStation.field_id == field.id, SensorReading.metric == cfg["metric"],
               SensorReading.ts > now - timedelta(hours=cfg["window_hours"]), SensorReading.ts <= now)
        .order_by(SensorReading.ts, SensorReading.id)).all()
    layers = db.scalars(select(FieldLayer).where(FieldLayer.field_id == field.id, FieldLayer.captured_on <= today)
                        .order_by(FieldLayer.captured_on.desc()).limit(20)).all()
    return engine.FieldInput(
        field_name=field.name,
        zones=tuple(engine.Zone(zone.code, zone.label, zone.area_ha) for zone in zones),
        readings=tuple(engine.Reading(row.id, codes.get(row.zone_id), row.metric, as_utc(row.ts), row.value) for row in rows),
        layers=tuple(engine.Layer(layer.id, layer.kind, layer.captured_on, layer.zone_values or {}, layer.source_text)
                     for layer in layers),
        last_fertilized_on=last_fertilized_on(db, field, today),
        rain=rain,
        now=now,
        today=today,
    )


def expire_due(db, now: datetime, field_id: str | None = None) -> int:
    stmt = (update(Recommendation)
            .where(Recommendation.status == "proposed", Recommendation.expires_at <= now)
            .values(status="expired", updated_at=now, expired_reason=GENERIC_EXPIRY_REASON))
    if field_id:
        stmt = stmt.where(Recommendation.field_id == field_id)
    return db.execute(stmt).rowcount or 0


def effective_status(row: Recommendation, now: datetime) -> str:
    # 讀取時就把過期的建議當過期，不必等排程寫回；GET 不寫資料庫。
    if row.status == "proposed" and as_utc(row.expires_at) <= as_utc(now):
        return "expired"
    return row.status


def upsert_proposal(db, field: FarmField, proposal: engine.Proposal, now: datetime) -> Recommendation:
    row = db.scalar(select(Recommendation).where(Recommendation.field_id == field.id, Recommendation.kind == proposal.kind,
                                                 Recommendation.status == "proposed").with_for_update())
    if row is None:
        row = Recommendation(field_id=field.id, kind=proposal.kind, status="proposed", proposed_at=now,
                             expires_at=proposal.expires_at, is_demo=field.is_demo, created_at=now, status_note="",
                             expired_reason="")
        db.add(row)
    # 重跑只更新內容與依據；proposed_at 與 expires_at 維持第一次提出的時間，否則每小時重跑會讓建議永遠不過期。
    row.title = proposal.title
    row.rationale = proposal.rationale
    row.five_r = proposal.five_r
    row.payload = proposal.payload
    row.evidence = proposal.evidence
    row.rule_version = proposal.rule_version
    row.updated_at = now
    return row


def irrigate_withdrawal_reason(db, field: FarmField, data: engine.FieldInput, rules: dict, now: datetime,
                               old_payload: dict) -> str:
    """補灌建議撤回時的具體理由。引擎本身不知道「之前建議過」，這段比較只能放在有狀態的 service 層。"""
    cfg = rules["irrigate"]
    parts = []
    for zone in old_payload.get("zones", []):
        code = zone.get("code")
        rows = [r for r in data.readings if r.zone_code == code]
        if not rows:
            continue
        average = mean(r.value for r in rows)
        latest = max(rows, key=lambda r: r.ts)
        if average >= cfg["threshold_pct"]:
            parts.append(f"{code} 區水分回升至 {latest.value:.0f}%（近 {cfg['window_hours']} 小時平均 {average:.0f}%）")
    rain = rain_total(db, field, start=now - timedelta(hours=48), end=now)
    prefix = f"近 48 小時降雨 {rain:g} mm，" if rain else ""
    if parts:
        return f"{prefix}{'、'.join(parts)}，已高於閾值 {cfg['threshold_pct']:g}%，暫不需要補灌。"
    return f"{prefix}土壤水分已回到正常範圍，暫不需要補灌。" if prefix else "土壤水分已回到正常範圍，暫不需要補灌。"


def withdrawal_reason(db, field: FarmField, kind: str, data: engine.FieldInput, rules: dict, now: datetime,
                      notes: list[engine.Note], old_payload: dict) -> str:
    matching = list(dict.fromkeys(note.message for note in notes if note.kind == kind))
    if matching:
        return "；".join(matching)
    if kind == "irrigate":
        return irrigate_withdrawal_reason(db, field, data, rules, now, old_payload)
    return GENERIC_WITHDRAWAL_REASON


@dataclass
class Withdrawn:
    recommendation: Recommendation
    reason: str


def analyze_field(db, field: FarmField, *, now: datetime, today: date, rain=_UNSET, rules: dict | None = None):
    """跑一次規則引擎並寫回建議。

    回傳 (saved, notes, withdrawn)：saved 是這次產出或更新的建議；notes 是不建議的原因；
    withdrawn 是原本 proposed、這次前提不再成立而被提前撤回的建議（連同理由）——只動 proposed，
    accepted 已經是農民自己的待辦，系統不代為收回。呼叫端負責 commit。
    """
    rules = rules or engine.load_rules()
    if rain is _UNSET:
        farmer = db.get(FarmAccount, field.farmer_id)
        rain = DEMO_RAIN if field.is_demo else rain_forecast_48h(farmer, now)
    expire_due(db, now, field.id)
    db.flush()
    previous = {row.kind: row for row in db.scalars(
        select(Recommendation).where(Recommendation.field_id == field.id, Recommendation.status == "proposed"))}
    data = field_input(db, field, now=now, today=today, rain=rain, rules=rules)
    proposals, notes = engine.analyze(data, rules)
    kept = {proposal.kind for proposal in proposals}
    withdrawn = []
    for kind, row in previous.items():
        if kind in kept:
            continue
        reason = withdrawal_reason(db, field, kind, data, rules, now, notes, row.payload or {})
        row.status = "expired"
        row.expired_reason = reason
        row.updated_at = now
        withdrawn.append(Withdrawn(row, reason))
    saved = [upsert_proposal(db, field, proposal, now) for proposal in proposals]
    db.flush()
    return saved, notes, withdrawn


# ---- 接受 → 規劃事項；已執行 → 農事日誌（由呼叫端經 farm_logs.py 完成，這裡只轉狀態） ----

def matching_plan(db, field: FarmField) -> FarmPlan | None:
    """依田區名稱找回對應的作物規劃；找不到或不只一筆都回 None，不用猜的。"""
    rows = db.scalars(select(FarmPlan).where(FarmPlan.farmer_id == field.farmer_id, FarmPlan.field_name == field.name,
                                             FarmPlan.archived.is_(False))).all()
    return rows[0] if len(rows) == 1 else None


EVENT_KIND = {"irrigate": "fieldwork", "vrt_fertilizer": "nutrition"}


def accept_recommendation(db, user, row: Recommendation, field: FarmField, now: datetime, today: date) -> FarmEvent | None:
    """接受建議：在對應的作物規劃裡建一個今天的待辦事項。找不到唯一對應的規劃就回 None，由呼叫端決定怎麼回應。"""
    plan = matching_plan(db, field)
    if plan is None:
        return None
    detail = "；".join(f"{label}：{row.five_r.get(key, '')}" for key, label in
                       (("material", "資材"), ("rate", "用量"), ("time", "時間"), ("method", "方式")) if row.five_r.get(key))
    event = FarmEvent(plan_id=plan.id, kind=EVENT_KIND[row.kind], title=row.title, start_on=today, end_on=today,
                      detail=detail, note=row.rationale, updated_by="精準模式建議", basis="user_added", source_ids=[],
                      field_id=field.id)
    db.add(event)
    plan.version += 1
    db.flush()
    row.event_id = event.id
    row.status = "accepted"
    row.decided_by = user.id
    row.decided_at = now
    row.updated_at = now
    return event


def mark_recommendation_done(row: Recommendation, user, work_log_id: str, now: datetime) -> None:
    row.status = "done"
    row.work_log_id = work_log_id
    row.decided_by = user.id
    row.decided_at = now
    row.updated_at = now


# ---- 儀表板：田區清單、選定田區的詳情、圖層、30 天圖 ----

def field_list(db, farmer_id: str) -> list[FarmField]:
    return db.scalars(select(FarmField).where(FarmField.farmer_id == farmer_id)
                      .order_by(FarmField.is_demo.desc(), FarmField.created_at)).all()


def latest_layer(db, field_id: str, kind: str) -> FieldLayer | None:
    return db.scalar(select(FieldLayer).where(FieldLayer.field_id == field_id, FieldLayer.kind == kind)
                     .order_by(FieldLayer.captured_on.desc(), FieldLayer.id.desc()))


def sparkline(db, station_ids: list[str], metrics: list[str], now: datetime, hours: int = 24) -> dict:
    if not station_ids:
        return {}
    rows = db.execute(select(SensorReading.station_id, SensorReading.metric, SensorReading.ts, SensorReading.value,
                            SensorReading.unit)
                      .where(SensorReading.station_id.in_(station_ids), SensorReading.metric.in_(metrics),
                             SensorReading.ts > now - timedelta(hours=hours), SensorReading.ts <= now)
                      .order_by(SensorReading.ts)).all()
    points = defaultdict(lambda: defaultdict(list))
    for row in rows:
        points[row.station_id][row.metric].append(dict(ts=as_utc(row.ts), value=row.value, unit=row.unit))
    return points


def field_detail(db, field: FarmField, *, now: datetime) -> dict:
    zones = db.scalars(select(FarmZone).where(FarmZone.field_id == field.id).order_by(FarmZone.code)).all()
    groups = db.scalars(select(SensorGroup).where(SensorGroup.field_id == field.id)).all()
    stations = db.scalars(select(SensorStation).where(SensorStation.field_id == field.id)).all()
    zone_codes = {zone.id: zone.code for zone in zones}
    all_metrics = sorted({metric for group in groups for metric in group.metrics} | {"soil_moisture", "soil_temp", "ec",
                                                                                      "air_temp", "humidity", "rain"})
    points = sparkline(db, [s.id for s in stations], all_metrics, now)
    station_rows = []
    for station in stations:
        series = points.get(station.id, {})
        latest = {metric: values[-1] for metric, values in series.items() if values}
        station_rows.append(dict(id=station.id, code=station.code, kind=station.kind, group_id=station.group_id,
                                 zone_code=zone_codes.get(station.zone_id), grid_col=station.grid_col,
                                 grid_row=station.grid_row, source=station.source, latest=latest, sparkline=series))
    latest_ts = max((row["latest"][metric]["ts"] for row in station_rows for metric in row["latest"]), default=None)
    ndvi = latest_layer(db, field.id, "ndvi")
    boundary = db.scalar(select(FieldBoundary).where(FieldBoundary.field_id == field.id))
    return dict(
        id=field.id, name=field.name, area_text=field.area_text, grid_cols=field.grid_cols, grid_rows=field.grid_rows,
        is_demo=field.is_demo,
        zones=[dict(code=z.code, label=z.label, area_ha=z.area_ha, cells=z.cells) for z in zones],
        groups=[dict(id=g.id, name=g.name, metrics=g.metrics) for g in groups],
        stations=station_rows,
        sensor_age_minutes=int((now - latest_ts).total_seconds() // 60) if latest_ts else None,
        ndvi_captured_on=ndvi.captured_on if ndvi else None,
        boundary=dict(source=boundary.source, imported_at=boundary.imported_at, outside_cells=boundary.outside_cells)
        if boundary else None,
    )


def try_analyze(db, field: FarmField, *, now: datetime, today: date) -> None:
    """CSV／UAV 上傳後最好順便重新分析一次，但這不是上傳能不能成功的條件——分析失敗不能讓資料白傳了。"""
    try:
        analyze_field(db, field, now=now, today=today)
    except Exception:
        pass


CHART_METRICS = {"soil_moisture": ("%", "irrigate"), "soil_temp": ("°C", None), "ec": ("dS/m", None),
                 "air_temp": ("°C", None), "humidity": ("%", None)}


def chart_data(db, field: FarmField, *, metric: str, hours: int, now: datetime, rules: dict) -> dict:
    unit, threshold_rule = CHART_METRICS.get(metric, ("", None))
    zones = {zone.id: zone.code for zone in db.scalars(select(FarmZone).where(FarmZone.field_id == field.id))}
    start = now - timedelta(hours=hours)
    rows = db.execute(select(SensorStation.zone_id, SensorReading.ts, SensorReading.value)
                      .join(SensorStation, SensorStation.id == SensorReading.station_id)
                      .where(SensorStation.field_id == field.id, SensorReading.metric == metric,
                             SensorReading.ts > start, SensorReading.ts <= now)
                      .order_by(SensorReading.ts)).all()
    # 一個分區可能不只一個測站（示範田區 C 區就有兩個）；圖上一區只畫一條線，同一時間點取平均。
    buckets = defaultdict(lambda: defaultdict(list))
    for zone_id, ts, value in rows:
        code = zones.get(zone_id)
        if code:
            buckets[code][as_utc(ts)].append(value)
    series = {code: [dict(ts=ts, value=round(mean(values), 2)) for ts, values in sorted(points.items())]
             for code, points in buckets.items()}
    rain_rows = db.execute(select(SensorReading.ts, SensorReading.value).join(SensorStation, SensorStation.id == SensorReading.station_id)
                           .where(SensorStation.field_id == field.id, SensorReading.metric == "rain",
                                  SensorReading.ts > start, SensorReading.ts <= now)
                           .order_by(SensorReading.ts)).all()
    stages = []
    plan = matching_plan(db, field)
    if plan:
        stages = [dict(title=e.title, start_on=e.start_on, end_on=e.end_on) for e in db.scalars(
            select(FarmEvent).where(FarmEvent.plan_id == plan.id, FarmEvent.kind == "stage",
                                    FarmEvent.start_on <= now.date(), FarmEvent.end_on >= start.date())
            .order_by(FarmEvent.start_on))]
    return dict(metric=metric, unit=unit, threshold_pct=(rules.get(threshold_rule, {}).get("threshold_pct")
                                                          if threshold_rule else None),
               series={code: values for code, values in series.items()},
               rain=[dict(ts=as_utc(ts), value=value) for ts, value in rain_rows], stages=stages)
