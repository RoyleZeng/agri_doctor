"""精準模式規則引擎（v1，刻意簡單）。

純函式：不查資料庫、不看時鐘。讀數、圖層、上次施肥日、降雨預報與「現在」都由呼叫端傳進來，
同樣的輸入永遠得到同樣的建議，離線測試與重播都靠這一點。門檻與對照表全部在 rules/<version>.json。

立場與整個專案一樣：只產生「建議」，並把依據（讀數 id、圖層 id、門檻值）寫進 evidence 讓人追溯；
資料不足或沒有依據時寧可不建議並說明原因（notes），不猜、不把缺值當 0。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from statistics import mean

RULES_DIR = Path(__file__).parent / "rules"


@lru_cache(maxsize=8)
def _load(version: str) -> str:
    return (RULES_DIR / f"{version}.json").read_text(encoding="utf-8")


def load_rules(version: str = "v1") -> dict:
    # 每次回傳新的字典：呼叫端改了也不會污染快取。
    return json.loads(_load(version))


@dataclass(frozen=True)
class Zone:
    code: str
    label: str = ""
    area_ha: float | None = None


@dataclass(frozen=True)
class Reading:
    id: int
    zone_code: str | None
    metric: str
    ts: datetime
    value: float


@dataclass(frozen=True)
class Layer:
    id: str
    kind: str
    captured_on: date
    zone_values: dict
    source_text: str = ""


@dataclass(frozen=True)
class RainForecast:
    """未來 48 小時降雨。雨量與機率各自可缺；氣象署縣市預報只有機率，沒有雨量。"""

    total_mm: float | None = None
    max_probability_pct: float | None = None
    source: str = ""


@dataclass(frozen=True)
class FieldInput:
    field_name: str
    zones: tuple[Zone, ...]
    readings: tuple[Reading, ...]
    layers: tuple[Layer, ...]
    last_fertilized_on: date | None
    rain: RainForecast | None
    now: datetime
    # 「距上次施肥幾天」用台北日曆日算；由呼叫端決定今天是哪一天，引擎不自己看時鐘。
    today: date


@dataclass
class Proposal:
    kind: str
    title: str
    rationale: str
    five_r: dict
    payload: dict
    evidence: dict
    expires_at: datetime
    rule_version: str


@dataclass
class Note:
    kind: str
    reason: str
    message: str
    zones: list[str] = field(default_factory=list)


def analyze(data: FieldInput, rules: dict) -> tuple[list[Proposal], list[Note]]:
    proposals: list[Proposal] = []
    notes: list[Note] = []
    for rule in (irrigate, vrt_fertilizer):
        proposal, rule_notes = rule(data, rules)
        notes.extend(rule_notes)
        if proposal:
            proposals.append(proposal)
    return proposals, notes


# ---- 補灌 ----

def _codes(zones) -> str:
    return "、".join(zones)


def irrigate(data: FieldInput, rules: dict) -> tuple[Proposal | None, list[Note]]:
    cfg = rules["irrigate"]
    notes: list[Note] = []
    start = data.now - timedelta(hours=cfg["window_hours"])
    dry = []
    for zone in data.zones:
        rows = sorted((r for r in data.readings
                       if r.zone_code == zone.code and r.metric == cfg["metric"] and start < r.ts <= data.now),
                      key=lambda r: (r.ts, r.id))
        if len(rows) < cfg["min_readings"]:
            notes.append(Note("irrigate", "insufficient_data", (
                f"{zone.code} 區近 {cfg['window_hours']} 小時土壤水分只有 {len(rows)} 筆讀數"
                f"（至少要 {cfg['min_readings']} 筆），不判斷是否需要補灌。"), [zone.code]))
            continue
        average = mean(r.value for r in rows)
        if average < cfg["threshold_pct"]:
            dry.append((zone, rows, average))
    if not dry:
        return None, notes

    codes = [zone.code for zone, _, _ in dry]
    rain = data.rain
    if rain is None or (rain.total_mm is None and rain.max_probability_pct is None):
        # 沒有預報就不知道會不會下雨；把缺值當成「不會下雨」等於替農民賭天氣。
        notes.append(Note("irrigate", "forecast_unavailable",
                          f"{_codes(codes)} 區土壤偏乾，但無法取得未來 48 小時降雨預報，不產生補灌建議；請確認天氣後自行判斷。",
                          codes))
        return None, notes
    if rain.total_mm is not None:
        wait = rain.total_mm >= cfg["rain_skip_mm_48h"]
        rain_text = f"未來 48 小時預報雨量 {rain.total_mm:g} mm"
    else:
        wait = rain.max_probability_pct >= cfg["rain_skip_probability_pct"]
        rain_text = f"未來 48 小時降雨機率最高 {rain.max_probability_pct:g}%"
    if wait:
        notes.append(Note("irrigate", "rain_expected", f"{_codes(codes)} 區土壤偏乾，但{rain_text}，先等雨，不建議補灌。", codes))
        return None, notes

    zones_payload, evidence_zones, parts = [], {}, []
    for zone, rows, average in dry:
        latest = rows[-1]
        deficit = max(0.0, cfg["threshold_pct"] - latest.value)
        mm = math.ceil(deficit * cfg["mm_per_pct"] / cfg["round_mm"]) * cfg["round_mm"]
        mm = min(cfg["max_mm"], max(cfg["round_mm"], mm))
        volume = round(mm * zone.area_ha * 10, 1) if zone.area_ha is not None else None  # 1 mm × 1 公頃 = 10 m³
        zones_payload.append(dict(code=zone.code, label=zone.label, mm=mm, avg_pct=round(average, 1),
                                  latest_pct=round(latest.value, 1), area_ha=zone.area_ha, volume_m3=volume))
        evidence_zones[zone.code] = dict(reading_ids=[r.id for r in rows], reading_count=len(rows),
                                         avg_pct=round(average, 2), latest_reading_id=latest.id,
                                         latest_pct=round(latest.value, 2), latest_at=latest.ts.isoformat())
        parts.append(f"{zone.code} 區土壤水分 {latest.value:.0f}%，近 {cfg['window_hours']} 小時平均 {average:.0f}%，"
                     f"低於 {cfg['threshold_pct']:g}%")
    volumes = [z["volume_m3"] for z in zones_payload]
    total_volume = round(sum(volumes), 1) if all(v is not None for v in volumes) else None
    rate = "；".join(f"{z['code']} 區約 {z['mm']} mm" + (f"（約 {z['volume_m3']:g} m³）" if z["volume_m3"] is not None else "")
                    for z in zones_payload)
    place = "、".join(f"{z['code']} 區" + (f"（{z['area_ha']:g} 公頃）" if z["area_ha"] is not None else "") for z in zones_payload)
    rationale = "；".join(parts) + f"。{rain_text}，建議補灌：{rate}。"
    return Proposal(
        kind="irrigate",
        title=f"補灌 {_codes(codes)} 區",
        rationale=rationale,
        five_r=dict(material=cfg["material"], rate=rate, place=place, time=cfg["timing"], method=cfg["method"]),
        payload=dict(zones=zones_payload, total_volume_m3=total_volume, warnings=[]),
        evidence=dict(rule="irrigate", metric=cfg["metric"], threshold_pct=cfg["threshold_pct"],
                      window_hours=cfg["window_hours"], min_readings=cfg["min_readings"],
                      window=[start.isoformat(), data.now.isoformat()], mm_per_pct=cfg["mm_per_pct"],
                      rain=dict(total_mm=rain.total_mm, max_probability_pct=rain.max_probability_pct, source=rain.source,
                                skip_mm_48h=cfg["rain_skip_mm_48h"], skip_probability_pct=cfg["rain_skip_probability_pct"]),
                      zones=evidence_zones),
        expires_at=data.now + timedelta(hours=cfg["expires_hours"]),
        rule_version=rules["version"],
    ), notes


# ---- 變率施肥 ----

def vrt_fertilizer(data: FieldInput, rules: dict) -> tuple[Proposal | None, list[Note]]:
    cfg = rules["vrt_fertilizer"]
    layers = [layer for layer in data.layers if layer.kind == cfg["layer"] and layer.captured_on <= data.today]
    layer = max(layers, key=lambda l: (l.captured_on, l.id), default=None)
    if layer is None:
        return None, [Note("vrt_fertilizer", "no_layer", "沒有 NDVI 圖層，不產生變率施肥建議。")]
    age = (data.today - layer.captured_on).days
    if age > cfg["max_layer_age_days"]:
        return None, [Note("vrt_fertilizer", "stale_layer", (
            f"最近一次 NDVI 是 {age} 天前（超過 {cfg['max_layer_age_days']} 天），作物狀況可能已經不同，不產生變率施肥建議。"))]
    values = layer.zone_values or {}
    missing = [zone.code for zone in data.zones
               if not isinstance(values.get(zone.code), (int, float)) or isinstance(values.get(zone.code), bool)]
    if not data.zones or missing:
        return None, [Note("vrt_fertilizer", "incomplete_layer",
                           f"NDVI 圖層缺少 {_codes(missing) or '分區'} 的數值，無法比較分區差異，不產生變率施肥建議。", missing)]
    days_since = (data.today - data.last_fertilized_on).days if data.last_fertilized_on else None
    if days_since is not None and days_since < cfg["min_days_since_fertilizer"]:
        return None, [Note("vrt_fertilizer", "recent_fertilizer",
                           f"距上次施肥 {days_since} 天，未滿 {cfg['min_days_since_fertilizer']} 天，不建議再施。")]

    classes = cfg["classes"]
    ranked = sorted(data.zones, key=lambda zone: (-float(values[zone.code]), zone.code))
    base = cfg["base_rate_kg_per_ha"]
    zones_payload = []
    for rank, zone in enumerate(ranked):
        # 依 NDVI 排名切三分位：長勢好的分區少施、差的多施；分區數不是 3 的倍數時，前段歸高。
        klass = classes[min(len(classes) - 1, rank * len(classes) // len(ranked))]
        rate = round(base * klass["multiplier"] / cfg["round_kg"]) * cfg["round_kg"]
        amount = round(rate * zone.area_ha, 1) if zone.area_ha is not None else None
        zones_payload.append(dict(code=zone.code, label=zone.label, ndvi=float(values[zone.code]), class_key=klass["key"],
                                  class_label=klass["label"], multiplier=klass["multiplier"], rate_kg_per_ha=rate,
                                  area_ha=zone.area_ha, amount_kg=amount))
    zones_payload.sort(key=lambda z: z["code"])
    known = all(z["amount_kg"] is not None for z in zones_payload)
    total = round(sum(z["amount_kg"] for z in zones_payload), 1) if known else None
    uniform = round(base * sum(z["area_ha"] for z in zones_payload), 1) if known else None
    difference = round(total - uniform, 1) if known else None

    rates = "／".join(f"{z['code']} {z['rate_kg_per_ha']}" for z in zones_payload) + " 公斤/公頃"
    ndvi_text = "、".join(f"{z['code']} 區 {z['ndvi']:.2f}" for z in zones_payload)
    fert_text = "系統內查無施肥紀錄" if days_since is None else f"距上次施肥 {days_since} 天"
    # 這句是可操作的提醒（怎麼修正），要讓前端當警示行單獨呈現，不能埋在 rationale 一整段裡。
    warnings = ["系統內查無施肥紀錄——若近 14 天施過肥，請先記到日誌再重新分析"] if days_since is None else []
    if known and difference == 0:
        total_text = f"總量約 {total:g} 公斤，與均一施相同，差別在施用位置。"
    elif known:
        compare = "少" if difference < 0 else "多"
        total_text = f"總量約 {total:g} 公斤，比均一施 {uniform:g} 公斤{compare} {abs(difference):g} 公斤。"
    else:
        total_text = "部分分區面積未填，無法估算總量。"
    rationale = (f"{layer.captured_on.month}/{layer.captured_on.day} NDVI：{ndvi_text}；{fert_text}。"
                 f"依長勢調整各區施用率（{rates}），{total_text}{cfg['disclaimer']}")
    return Proposal(
        kind="vrt_fertilizer",
        title=f"變率施肥（依 NDVI 分 {len(zones_payload)} 區）",
        rationale=rationale,
        five_r=dict(material=cfg["material"], rate=rates, place=f"{_codes(z['code'] for z in zones_payload)} 分區（見處方表）",
                    time=cfg["timing"], method=cfg["method"]),
        payload=dict(layer_id=layer.id, captured_on=layer.captured_on.isoformat(), base_rate_kg_per_ha=base,
                     zones=zones_payload, total_kg=total, uniform_total_kg=uniform, difference_kg=difference,
                     disclaimer=cfg["disclaimer"], warnings=warnings),
        evidence=dict(rule="vrt_fertilizer", layer_id=layer.id, layer_kind=layer.kind,
                      captured_on=layer.captured_on.isoformat(), layer_source=layer.source_text, layer_age_days=age,
                      max_layer_age_days=cfg["max_layer_age_days"], zone_values={k: values[k] for k in sorted(values)},
                      classes=classes, base_rate_kg_per_ha=base,
                      min_days_since_fertilizer=cfg["min_days_since_fertilizer"],
                      last_fertilized_on=data.last_fertilized_on.isoformat() if data.last_fertilized_on else None,
                      days_since_fertilizer=days_since),
        expires_at=data.now + timedelta(days=cfg["expires_days"]),
        rule_version=rules["version"],
    ), []
