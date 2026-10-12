"""規則引擎的離線測試：給定讀數、圖層、施肥日與降雨預報 → 期望的建議與不建議的原因。

引擎是純函式，測試不碰資料庫也不看時鐘；數值都是試驗值，不抄設計稿。
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from app.precision.rules import FieldInput, Layer, RainForecast, Reading, Zone, analyze, load_rules

NOW = datetime(2030, 9, 13, 0, 0, tzinfo=timezone.utc)
TODAY = date(2030, 9, 13)
RULES = load_rules()
ZONES = (Zone("A", "高", 0.15), Zone("B", "中", 0.12), Zone("C", "低", 0.09))


def moisture(values, hours=72, offset=0, metric="soil_moisture"):
    """每區每小時一筆；values 可給常數或 lambda(小時數前) → 值。"""
    rows, next_id = [], 1
    for code, value in values.items():
        for h in range(offset, offset + hours):
            rows.append(Reading(id=next_id, zone_code=code, metric=metric, ts=NOW - timedelta(hours=h),
                                value=value(h) if callable(value) else value))
            next_id += 1
    return tuple(rows)


NDVI = Layer("layer-ndvi", "ndvi", TODAY - timedelta(days=3), {"A": 0.78, "B": 0.64, "C": 0.49}, "試驗多光譜")


def field(**changes):
    base = FieldInput(field_name="試驗田甲", zones=ZONES, readings=moisture({"A": 36, "B": 33, "C": lambda h: 21 + h * 0.05}),
                      layers=(NDVI,), last_fertilized_on=None, rain=RainForecast(total_mm=0, source="試驗預報"),
                      now=NOW, today=TODAY)
    return replace(base, **changes)


def by_kind(result):
    proposals, notes = result
    return {p.kind: p for p in proposals}, {(n.kind, n.reason): n for n in notes}


def test_dry_zone_and_fresh_ndvi_produce_irrigation_and_variable_rate():
    proposals, notes = by_kind(analyze(field(), RULES))
    assert set(proposals) == {"irrigate", "vrt_fertilizer"} and notes == {}

    water = proposals["irrigate"]
    zone = water.payload["zones"][0]
    assert water.title == "補灌 C 區" and [z["code"] for z in water.payload["zones"]] == ["C"]
    # 目前 21%：缺 9 個百分點 × 1.5 mm = 13.5 → 無條件進位到 5 的倍數 15 mm；0.09 公頃 × 15 mm = 13.5 m³。
    assert (zone["mm"], zone["latest_pct"], zone["volume_m3"]) == (15, 21.0, 13.5)
    evidence = water.evidence["zones"]["C"]
    assert water.evidence["threshold_pct"] == 30 and water.evidence["rain"]["total_mm"] == 0
    assert evidence["reading_count"] == 72 and len(evidence["reading_ids"]) == 72 and evidence["latest_reading_id"] in evidence["reading_ids"]
    assert "C 區土壤水分 21%" in water.rationale and "低於 30%" in water.rationale
    assert water.expires_at == NOW + timedelta(hours=48) and water.rule_version == "rules/v1"

    vrt = proposals["vrt_fertilizer"]
    rates = {z["code"]: z["rate_kg_per_ha"] for z in vrt.payload["zones"]}
    assert rates == {"A": 340, "B": 400, "C": 460}  # NDVI 高的少施、低的多施
    # 340×0.15 + 400×0.12 + 460×0.09 = 140.4；均一施 400×0.36 = 144。
    assert (vrt.payload["total_kg"], vrt.payload["uniform_total_kg"], vrt.payload["difference_kg"]) == (140.4, 144.0, -3.6)
    assert vrt.rationale.endswith("數值為估算，實際請與植物醫生或改良場確認。") and "少 3.6 公斤" in vrt.rationale
    assert "系統內查無施肥紀錄" in vrt.rationale and vrt.evidence["layer_id"] == "layer-ndvi"
    assert vrt.expires_at == NOW + timedelta(days=14) and set(vrt.five_r) == {"material", "rate", "place", "time", "method"}


def test_rain_on_the_way_means_wait_instead_of_irrigate():
    proposals, notes = by_kind(analyze(field(rain=RainForecast(total_mm=8, source="試驗預報")), RULES))
    assert "irrigate" not in proposals and "vrt_fertilizer" in proposals
    assert "等雨" in notes[("irrigate", "rain_expected")].message and notes[("irrigate", "rain_expected")].zones == ["C"]


def test_probability_only_forecast_uses_its_own_threshold():
    # 氣象署縣市預報只有降雨機率，沒有雨量：不把機率換算成 mm。
    waiting, notes = by_kind(analyze(field(rain=RainForecast(max_probability_pct=70)), RULES))
    assert "irrigate" not in waiting and ("irrigate", "rain_expected") in notes
    proposals, _ = by_kind(analyze(field(rain=RainForecast(max_probability_pct=40)), RULES))
    assert "降雨機率最高 40%" in proposals["irrigate"].rationale


def test_missing_forecast_is_not_treated_as_no_rain():
    for rain in (None, RainForecast()):
        proposals, notes = by_kind(analyze(field(rain=rain), RULES))
        assert "irrigate" not in proposals and ("irrigate", "forecast_unavailable") in notes


def test_no_ndvi_means_no_fertilizer_advice():
    proposals, notes = by_kind(analyze(field(layers=()), RULES))
    assert "vrt_fertilizer" not in proposals and ("vrt_fertilizer", "no_layer") in notes
    stale = replace(NDVI, captured_on=TODAY - timedelta(days=31))
    assert ("vrt_fertilizer", "stale_layer") in by_kind(analyze(field(layers=(stale,)), RULES))[1]
    partial = replace(NDVI, zone_values={"A": 0.78, "B": 0.64})
    _, notes = by_kind(analyze(field(layers=(partial,)), RULES))
    assert notes[("vrt_fertilizer", "incomplete_layer")].zones == ["C"]
    other_kind = replace(NDVI, kind="yield_prev")
    assert "vrt_fertilizer" not in by_kind(analyze(field(layers=(other_kind,)), RULES))[0]


def test_fertilized_within_fourteen_days_blocks_fertilizer_advice():
    proposals, notes = by_kind(analyze(field(last_fertilized_on=TODAY - timedelta(days=13)), RULES))
    assert "vrt_fertilizer" not in proposals and "13 天" in notes[("vrt_fertilizer", "recent_fertilizer")].message
    proposals, _ = by_kind(analyze(field(last_fertilized_on=TODAY - timedelta(days=14)), RULES))
    assert proposals["vrt_fertilizer"].evidence["days_since_fertilizer"] == 14


def test_only_the_window_counts_and_thin_data_is_not_judged():
    # 72 小時前很乾、這三天濕：不建議；時間在「現在」之後的讀數不算。
    old_dry = moisture({"C": 10}, hours=48, offset=72)
    recent_wet = moisture({"C": 40})
    future = tuple(replace(r, ts=NOW + timedelta(hours=1 + i), id=10_000 + i) for i, r in enumerate(moisture({"C": 5}, hours=10)))
    zones = (Zone("C", "低", 0.09),)
    proposals, _ = by_kind(analyze(field(zones=zones, readings=old_dry + recent_wet + future), RULES))
    assert "irrigate" not in proposals
    proposals, notes = by_kind(analyze(field(zones=zones, readings=moisture({"C": 15}, hours=20)), RULES))
    assert "irrigate" not in proposals and "20 筆" in notes[("irrigate", "insufficient_data")].message


def test_thresholds_come_from_the_rules_file_and_results_are_repeatable():
    looser = load_rules()
    looser["irrigate"]["threshold_pct"] = 20
    assert "irrigate" not in by_kind(analyze(field(), looser))[0]
    assert analyze(field(), RULES) == analyze(field(), RULES)
    very_dry = field(readings=moisture({"A": 36, "B": 33, "C": 2}))
    assert by_kind(analyze(very_dry, RULES))[0]["irrigate"].payload["zones"][0]["mm"] == 40  # 上限 40 mm
    assert load_rules()["irrigate"]["threshold_pct"] == 30  # 改副本不污染讀進來的規則
