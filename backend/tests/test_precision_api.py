"""精準模式 API 與示範產生器的離線測試（記憶體 SQLite，沿用農事日誌的 fixture）。

降雨預報一律 stub，不連氣象署；時間由 fixture 的 clock 控制。
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, update

from app.api.v1 import precision as precision_api
from app.models.consult import PrescriptionItem
from app.models.farm_log import FarmWorkLog
from app.models.planning import FarmAccount, FarmEvent, FarmPlan
from app.models.precision import FarmField, FarmZone, FieldLayer, Recommendation, SensorReading, SensorStation
from app.precision import demo, service
from app.precision.rules import RainForecast
from app.services.farm_log_export import phi_checks
from tests.test_farm_logs import TODAY, URL, spray, system  # noqa: F401  共用同一套離線 API fixture

LOGS = "/api/v1/planning/farmers/farmer/logs"
PREFILL = "/api/v1/planning/farmers/farmer/logs/prefill"

RECS = "/api/v1/precision/farmers/farmer/recommendations"
TITLES = ["補灌 C 區", "變率施肥（依 NDVI 分 3 區）"]


@pytest.fixture
def precision(system, monkeypatch):
    app, factory, clients, clock = system
    app.include_router(precision_api.router, prefix="/api/v1")
    monkeypatch.setattr(precision_api, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(precision_api, "taipei_today", lambda: TODAY)
    forecast = {"value": RainForecast(max_probability_pct=10, source="試驗預報"), "calls": []}

    def fake_rain(farmer, now):
        forecast["calls"].append(farmer.id)
        return forecast["value"]

    monkeypatch.setattr(service, "rain_forecast_48h", fake_rain)
    return app, factory, clients, clock, forecast


def run_demo(factory, clock, username="farmer"):
    with factory() as db:
        account = db.get(FarmAccount, username)
        result = demo.generate_demo(db, account, now=clock["now"], today=TODAY)
        db.commit()
    return result


def count(factory, model, *where):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(model).where(*where))


def real_field(factory, name="試驗田乙", moisture=None, now=None):
    """真實（非示範）田區；給 moisture 就放一個 C 區土壤站與 72 小時讀數。"""
    with factory() as db:
        field = FarmField(farmer_id="farmer", name=name, area_text="0.2 公頃", is_demo=False)
        db.add(field)
        db.flush()
        if moisture is not None:
            zone = FarmZone(field_id=field.id, code="C", label="低", cells=[[0, 0]], area_ha=0.2)
            db.add(zone)
            db.flush()
            station = SensorStation(field_id=field.id, zone_id=zone.id, code="S-1", kind="soil")
            db.add(station)
            db.flush()
            db.add_all([SensorReading(station_id=station.id, ts=now - timedelta(hours=h), metric="soil_moisture",
                                      value=moisture, unit="%") for h in range(72)])
        db.commit()
        return field.id


def test_demo_generator_is_idempotent_and_only_touches_its_own_demo_rows(precision):
    _, factory, _, clock, forecast = precision
    real_id = real_field(factory, moisture=35, now=clock["now"])
    run_demo(factory, clock, "stranger")  # 別的帳號的示範資料
    stranger_rows = count(factory, SensorReading, SensorReading.station_id.in_(
        select(SensorStation.id).join(FarmField).where(FarmField.farmer_id == "stranger")))

    first = run_demo(factory, clock)
    assert sorted(first.recommendations) == sorted(TITLES) and first.notes == []
    assert first.counts == {"recommendations": 2, "field_layers": 2, "sensor_readings": 30 * 24 * 15, "sensor_stations": 5,
                            "sensor_groups": 2, "farm_zones": 3, "farm_fields": 1}
    with factory() as db:
        ndvi_first = db.scalar(select(FieldLayer.zone_values).where(FieldLayer.field_id == first.field_id, FieldLayer.kind == "ndvi"))

    clock["now"] += timedelta(hours=1)
    second = run_demo(factory, clock)
    assert second.counts == first.counts and second.field_id == first.field_id
    assert sorted(second.recommendations) == sorted(TITLES)
    with factory() as db:
        ndvi_second = db.scalar(select(FieldLayer.zone_values).where(FieldLayer.field_id == second.field_id, FieldLayer.kind == "ndvi"))
        assert ndvi_first == ndvi_second and ndvi_first["A"] > ndvi_first["B"] > ndvi_first["C"]  # 同一個種子，數值一樣
        assert db.get(FarmField, real_id) is not None
    assert count(factory, SensorReading, SensorReading.is_demo.is_(False)) == 72  # 真實讀數一列都沒少
    assert count(factory, SensorReading, SensorReading.station_id.in_(
        select(SensorStation.id).join(FarmField).where(FarmField.farmer_id == "stranger"))) == stranger_rows
    assert forecast["calls"] == []  # 示範田區用固定預報，不叫天氣服務


def test_recommendations_endpoint_shows_both_with_evidence(precision):
    _, factory, clients, clock, _ = precision
    run_demo(factory, clock)
    body = clients["farmer"].get(RECS).json()
    items = {item["kind"]: item for item in body["items"]}
    assert sorted(item["title"] for item in body["items"]) == sorted(TITLES)
    assert all(item["status"] == "proposed" and item["is_demo"] and item["rule_version"] == "rules/v1" for item in body["items"])

    water = items["irrigate"]
    zone = water["evidence"]["zones"]["C"]
    assert water["evidence"]["threshold_pct"] == 30 and zone["avg_pct"] < 30 and len(zone["reading_ids"]) == 144
    assert water["evidence"]["rain"]["source"].startswith("示範資料") and water["field_name"] == "試驗田甲"
    assert water["expires_at"].startswith((clock["now"] + timedelta(hours=48)).strftime("%Y-%m-%dT%H"))
    with factory() as db:
        cited = db.scalars(select(SensorReading.metric).where(SensorReading.id.in_(zone["reading_ids"]))).all()
    assert set(cited) == {"soil_moisture"}  # evidence 引用的讀數確實存在，且是土壤水分

    vrt = items["vrt_fertilizer"]
    assert {z["code"]: z["rate_kg_per_ha"] for z in vrt["payload"]["zones"]} == {"A": 340, "B": 400, "C": 460}
    assert vrt["rationale"].endswith("數值為估算，實際請與植物醫生或改良場確認。")
    assert vrt["evidence"]["days_since_fertilizer"] is None

    assert clients["doc"].get(RECS).status_code == 200  # 已連結的植物醫生看得到
    for outsider in ("stranger", "unlinked"):
        assert clients[outsider].get(RECS).status_code == 404
        assert clients[outsider].get(f"/api/v1/precision/recommendations/{water['id']}").status_code == 404
    assert clients["farmer"].get(RECS, params={"status": "accepted"}).json()["items"] == []
    assert clients["farmer"].get(RECS, params={"status": "maybe"}).status_code == 422


def test_rerun_updates_instead_of_stacking(precision):
    _, factory, clients, clock, forecast = precision
    result = run_demo(factory, clock)
    before = {item["kind"]: item for item in clients["farmer"].get(RECS).json()["items"]}
    clock["now"] += timedelta(hours=1)
    response = clients["doc"].post(f"/api/v1/precision/fields/{result.field_id}/analyze")
    assert response.status_code == 200, response.text
    after = {item["kind"]: item for item in response.json()["items"]}
    assert {k: v["id"] for k, v in after.items()} == {k: v["id"] for k, v in before.items()}
    # 重跑不延後過期時間：proposed_at／expires_at 維持第一次提出的時間。
    assert all(after[k]["proposed_at"] == before[k]["proposed_at"] and after[k]["expires_at"] == before[k]["expires_at"] for k in after)
    assert count(factory, Recommendation) == 2 and forecast["calls"] == []


def test_real_fields_use_the_weather_forecast_and_wait_for_rain(precision):
    _, factory, clients, clock, forecast = precision
    field_id = real_field(factory, moisture=18, now=clock["now"])
    url = f"/api/v1/precision/fields/{field_id}/analyze"
    forecast["value"] = RainForecast(max_probability_pct=80, source="試驗預報")
    waited = clients["farmer"].post(url).json()
    assert waited["items"] == [] and any(note["reason"] == "rain_expected" and "等雨" in note["message"] for note in waited["notes"])
    assert forecast["calls"] == ["farmer"]
    forecast["value"] = None  # 取不到預報：不當成不會下雨
    assert any(note["reason"] == "forecast_unavailable" for note in clients["farmer"].post(url).json()["notes"])
    forecast["value"] = RainForecast(max_probability_pct=20, source="試驗預報")
    proposed = clients["farmer"].post(url).json()["items"]
    assert [item["title"] for item in proposed] == ["補灌 C 區"] and not proposed[0]["is_demo"]
    assert clients["stranger"].post(url).status_code == 404


def test_decisions_are_farmer_only_and_follow_the_lifecycle(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    items = {item["kind"]: item["id"] for item in clients["farmer"].get(RECS).json()["items"]}
    water, vrt = (f"/api/v1/precision/recommendations/{items[kind]}" for kind in ("irrigate", "vrt_fertilizer"))
    farmer, doctor = clients["farmer"], clients["doc"]

    assert doctor.post(water + "/accept").status_code == 403
    assert doctor.post(vrt + "/dismiss", json={"reason": "試驗"}).status_code == 403
    accepted = farmer.post(water + "/accept")
    assert accepted.status_code == 200 and (accepted.json()["status"], accepted.json()["decided_by"]) == ("accepted", "farmer")
    again = farmer.post(water + "/accept")
    assert again.status_code == 409 and "已接受" in again.json()["detail"]
    assert farmer.post(water + "/dismiss").status_code == 409
    assert farmer.post(water + "/done").json()["status"] == "done"
    dismissed = farmer.post(vrt + "/dismiss", json={"reason": "試驗：先觀察一週"}).json()
    assert (dismissed["status"], dismissed["status_note"]) == ("dismissed", "試驗：先觀察一週")
    assert farmer.post(vrt + "/done").status_code == 409

    # 舊建議已不是 proposed，重跑會提出新的，而不是改寫已決定的那一筆。
    clock["now"] += timedelta(hours=1)
    fresh = {item["kind"]: item for item in farmer.post(f"/api/v1/precision/fields/{result.field_id}/analyze").json()["items"]}
    assert set(fresh) == {"irrigate", "vrt_fertilizer"} and fresh["irrigate"]["id"] != items["irrigate"]
    assert count(factory, Recommendation) == 4

    clock["now"] += timedelta(hours=49)  # 補灌 48 小時過期，施肥 14 天還沒
    statuses = {(item["id"]): item["status"] for item in farmer.get(RECS).json()["items"]}
    assert statuses[fresh["irrigate"]["id"]] == "expired" and statuses[fresh["vrt_fertilizer"]["id"]] == "proposed"
    expired = farmer.post(f"/api/v1/precision/recommendations/{fresh['irrigate']['id']}/accept")
    assert expired.status_code == 409 and "過期" in expired.json()["detail"]
    with factory() as db:
        assert db.get(Recommendation, fresh["irrigate"]["id"]).status == "expired"


def test_phi_snapshot_survives_prescription_deletion_and_logs_link_to_fields(precision):
    _, factory, clients, _, _ = precision
    farmer = clients["farmer"]
    field_id = real_field(factory, name="試驗田甲")
    with factory() as db:
        db.get(PrescriptionItem, "rx").phi_days = 7
        db.commit()
    rx_log = farmer.post(URL, json=spray(prescription_item_id="rx", material_name="")).json()
    assert (rx_log["phi_days"], rx_log["phi_source"], rx_log["field_id"]) == (7, "prescription", field_id)
    registered = farmer.post(URL, json=spray(registration_id=1)).json()
    assert (registered["phi_days"], registered["phi_source"]) == (4, "registration")
    typed = farmer.post(URL, json=spray(field_text="沒有主檔的田")).json()
    assert (typed["phi_days"], typed["phi_source"], typed["field_id"]) == (None, "none", None)
    changed = farmer.patch(f"/api/v1/planning/logs/{registered['id']}", json=dict(kind="fertilize")).json()
    assert (changed["phi_days"], changed["phi_source"]) == (None, "none")

    with factory() as db:
        db.delete(db.get(PrescriptionItem, "rx"))
        db.commit()
        log = db.get(FarmWorkLog, rx_log["id"])
        assert log.prescription_item_id is None and (log.phi_days, log.phi_source) == (7, "prescription")
    harvest = dict(kind="harvest", done_on="2030-03-10", field_text="試驗田甲", crop_text="試驗作物甲", area_text="0.4 公頃",
                   yield_value="3", yield_unit="公斤")
    assert farmer.post(URL, json=harvest).status_code == 201
    preview = farmer.get("/api/v1/planning/farmers/farmer/logs/export/preview",
                         params={"from": "2030-03-01", "to": "2030-03-10"}).json()
    assert "未達安全採收期 7 天" in preview["phi"]["violations"][0]["message"]


def test_phi_check_prefers_field_id_over_names():
    harvest = dict(id="h", done_on=date(2030, 3, 10), field_text="東側一區", field_id="f1", plan_id=None, crop_text="")
    renamed = dict(id="s", done_on=date(2030, 3, 9), field_text="東區一", field_id="f1", plan_id=None, material_name="試驗藥劑",
                   phi_days=5, phi_source="registration")
    assert phi_checks([harvest], [renamed])[0]["status"] == "violation"  # 名稱寫法不同，但是同一塊田
    other = renamed | dict(field_text="東側一區", field_id="f2")
    assert phi_checks([harvest], [other])[0]["status"] == "no_spray"  # 名稱相同，但主檔說是不同田區


# --------------------------------------------------------------------------
# 接受 → 規劃事項；已執行 → 農事日誌；重新分析撤回不再成立的建議
# --------------------------------------------------------------------------

def test_accept_creates_a_farm_event_in_the_matching_plan(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    items = {item["kind"]: item for item in clients["farmer"].get(RECS).json()["items"]}
    water = items["irrigate"]
    response = clients["farmer"].post(f"/api/v1/precision/recommendations/{water['id']}/accept")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "accepted" and body["event_id"]
    with factory() as db:
        event = db.get(FarmEvent, body["event_id"])
        plan = db.get(FarmPlan, "plan")
        assert (event.plan_id, event.kind, event.title) == ("plan", "fieldwork", water["title"])
        assert (event.start_on, event.end_on) == (TODAY, TODAY)
        assert (event.updated_by, event.basis, event.field_id, event.completed) == (
            "精準模式建議", "user_added", result.field_id, False)
        assert plan.version == 2  # 新增事項照既有規則升版本
        row = db.get(Recommendation, water["id"])
        assert (row.status, row.event_id, row.decided_by) == ("accepted", event.id, "farmer")

    vrt = items["vrt_fertilizer"]
    fert = clients["farmer"].post(f"/api/v1/precision/recommendations/{vrt['id']}/accept").json()
    with factory() as db:
        assert db.get(FarmEvent, fert["event_id"]).kind == "nutrition"


def test_accept_without_a_matching_plan_explains_why(precision):
    _, factory, clients, clock, forecast = precision
    field_id = real_field(factory, name="沒有規劃的田", moisture=18, now=clock["now"])
    forecast["value"] = RainForecast(max_probability_pct=10, source="試驗預報")
    analyzed = clients["farmer"].post(f"/api/v1/precision/fields/{field_id}/analyze").json()
    rid = analyzed["items"][0]["id"]
    response = clients["farmer"].post(f"/api/v1/precision/recommendations/{rid}/accept")
    assert response.status_code == 422 and "沒有規劃的田" in response.json()["detail"]
    with factory() as db:
        assert db.get(Recommendation, rid).status == "proposed"  # 失敗不改狀態


def test_done_prefill_and_log_creation_mark_the_recommendation_done(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    farmer = clients["farmer"]
    items = {item["kind"]: item for item in farmer.get(RECS).json()["items"]}
    water = items["irrigate"]

    pre = farmer.get(PREFILL, params={"recommendation_id": water["id"]}).json()
    assert (pre["kind"], pre["field_text"], pre["recommendation_id"]) == ("irrigate", "試驗田甲", water["id"])
    assert pre["material_name"] == "灌溉水" and pre["amount_unit"] == "立方公尺" and pre["amount_value"]
    assert pre["recommendation"]["title"] == water["title"]

    created = farmer.post(LOGS, json=dict(kind="irrigate", done_on=TODAY.isoformat(), field_text=pre["field_text"],
                                          amount_value=pre["amount_value"], amount_unit=pre["amount_unit"],
                                          recommendation_id=water["id"]))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["recommendation_id"] == water["id"] and body["field_id"] == result.field_id
    assert body["recommendation"] == {"id": water["id"], "status": "done", "work_log_id": body["id"]}

    with factory() as db:
        row = db.get(Recommendation, water["id"])
        assert (row.status, row.work_log_id, row.decided_by) == ("done", body["id"], "farmer")

    again = farmer.post(LOGS, json=dict(kind="irrigate", done_on=TODAY.isoformat(), field_text="試驗田甲",
                                        recommendation_id=water["id"]))
    assert again.status_code == 409 and "已執行" in again.json()["detail"]

    vrt = items["vrt_fertilizer"]
    mismatched = farmer.post(LOGS, json=dict(kind="irrigate", done_on=TODAY.isoformat(), field_text="試驗田甲",
                                             recommendation_id=vrt["id"]))
    assert mismatched.status_code == 422

    doctor_blocked = clients["doc"].post(LOGS, json=dict(kind="fertilize", done_on=TODAY.isoformat(), field_text="試驗田甲",
                                                          material_name="x", recommendation_id=vrt["id"]))
    assert doctor_blocked.status_code == 403
    prefill_blocked = clients["doc"].get(PREFILL, params={"recommendation_id": vrt["id"]})
    assert prefill_blocked.status_code == 403


def test_vrt_withdrawn_when_a_fertilize_log_is_recorded_within_fourteen_days(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    farmer = clients["farmer"]
    before = {item["kind"]: item["id"] for item in farmer.get(RECS).json()["items"]}
    fertilized_on = (TODAY - timedelta(days=5)).isoformat()
    logged = farmer.post(LOGS, json=dict(kind="fertilize", done_on=fertilized_on, field_text="試驗田甲",
                                         material_name="試驗肥料"))
    assert logged.status_code == 201, logged.text

    clock["now"] += timedelta(hours=1)
    body = farmer.post(f"/api/v1/precision/fields/{result.field_id}/analyze").json()
    assert [item["kind"] for item in body["items"]] == ["irrigate"]  # 補灌條件沒變，繼續在案
    withdrawn = {item["kind"]: item for item in body["withdrawn"]}
    assert set(withdrawn) == {"vrt_fertilizer"}
    assert withdrawn["vrt_fertilizer"]["id"] == before["vrt_fertilizer"] and withdrawn["vrt_fertilizer"]["status"] == "expired"
    assert "距上次施肥 5 天" in withdrawn["vrt_fertilizer"]["expired_reason"]
    with factory() as db:
        assert db.get(Recommendation, before["vrt_fertilizer"]).status == "expired"
        assert db.get(Recommendation, before["irrigate"]).status == "proposed"  # 沒被動到


def test_irrigate_withdrawn_when_soil_recovers_and_reason_cites_rain(precision):
    _, factory, clients, clock, _ = precision
    field_id = real_field(factory, name="沒有規劃的田", moisture=18, now=clock["now"])
    with factory() as db:
        station = SensorStation(field_id=field_id, code="W-1", kind="weather")
        db.add(station)
        db.flush()
        db.add(SensorReading(station_id=station.id, ts=clock["now"] - timedelta(hours=6), metric="rain", value=18.0, unit="mm"))
        db.commit()
    first = clients["farmer"].post(f"/api/v1/precision/fields/{field_id}/analyze").json()
    assert [item["kind"] for item in first["items"]] == ["irrigate"]
    rid = first["items"][0]["id"]

    with factory() as db:
        soil_stations = select(SensorStation.id).where(SensorStation.field_id == field_id, SensorStation.kind == "soil")
        db.execute(update(SensorReading).where(SensorReading.metric == "soil_moisture",
                                               SensorReading.station_id.in_(soil_stations)).values(value=40.0))
        db.commit()
    clock["now"] += timedelta(hours=1)
    second = clients["farmer"].post(f"/api/v1/precision/fields/{field_id}/analyze").json()
    assert second["items"] == []
    withdrawn = second["withdrawn"][0]
    assert (withdrawn["id"], withdrawn["status"]) == (rid, "expired")
    assert "18 mm" in withdrawn["expired_reason"] and "C 區水分回升至 40%" in withdrawn["expired_reason"]
    assert "閾值 30%" in withdrawn["expired_reason"]
    with factory() as db:
        assert db.get(Recommendation, rid).expired_reason == withdrawn["expired_reason"]


# --------------------------------------------------------------------------
# 儀表板：總覽、圖層、30 天圖、精準模式開關
# --------------------------------------------------------------------------

def test_overview_returns_field_detail_stations_and_recommendations(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    farmer = clients["farmer"]
    body = farmer.get("/api/v1/precision/farmers/farmer/overview").json()
    assert body["precision_enabled"] is False
    assert [f["id"] for f in body["fields"]] == [result.field_id]
    field = body["field"]
    assert field["id"] == result.field_id and field["is_demo"] is True
    assert {z["code"] for z in field["zones"]} == {"A", "B", "C"}
    assert {g["name"] for g in field["groups"]} == {"土壤組（示範）", "氣象站（示範）"}
    station = next(s for s in field["stations"] if s["code"] == "S-C1")
    assert station["zone_code"] == "C" and "soil_moisture" in station["latest"]
    assert 1 <= len(station["sparkline"]["soil_moisture"]) <= 24  # 只給近 24 小時
    assert field["sensor_age_minutes"] is not None and field["sensor_age_minutes"] < 60
    assert field["ndvi_captured_on"] is not None
    assert {item["kind"] for item in body["recommendations"]} == {"irrigate", "vrt_fertilizer"}
    assert body["rules"]["irrigate"]["threshold_pct"] == 30
    assert "round_kg" not in body["rules"]["vrt_fertilizer"]  # 內部係數不外流

    assert farmer.get("/api/v1/precision/farmers/farmer/overview", params={"field_id": "nope"}).status_code == 404
    assert clients["stranger"].get("/api/v1/precision/farmers/farmer/overview").status_code == 404
    assert clients["doc"].get("/api/v1/precision/farmers/farmer/overview").status_code == 200


def test_field_layer_endpoint_returns_latest_ndvi(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    url = f"/api/v1/precision/fields/{result.field_id}/layers"
    body = clients["farmer"].get(f"{url}/ndvi").json()
    assert body["field"] == {"grid_cols": 6, "grid_rows": 4}
    assert body["layer"]["kind"] == "ndvi" and set(body["layer"]["zone_values"]) == {"A", "B", "C"}
    assert len(body["layer"]["cell_values"]) == 24
    assert clients["farmer"].get(f"{url}/ph").json()["layer"] is None
    assert clients["farmer"].get(f"{url}/bogus").status_code == 422
    assert clients["stranger"].get(f"{url}/ndvi").status_code == 404


def test_chart_endpoint_returns_series_rain_and_growth_stage_band(precision):
    _, factory, clients, clock, _ = precision
    result = run_demo(factory, clock)
    url = f"/api/v1/precision/fields/{result.field_id}/chart"
    body = clients["farmer"].get(url, params={"metric": "soil_moisture", "hours": 24}).json()
    assert body["unit"] == "%" and body["threshold_pct"] == 30
    assert set(body["series"]) == {"A", "B", "C"} and len(body["series"]["C"]) == 24
    assert len(body["rain"]) == 24  # 氣象站每小時一筆
    assert [s["title"] for s in body["stages"]] == ["試驗生育期"]  # 與規劃事項同田區名稱比對到
    assert clients["farmer"].get(url, params={"metric": "bogus"}).status_code == 422


def test_toggle_precision_enabled_is_farmer_only(precision):
    _, factory, clients, clock, _ = precision
    farmer = clients["farmer"]
    assert farmer.get("/api/v1/planning/me").json()["precision_enabled"] is False
    on = farmer.patch("/api/v1/precision/me", json={"enabled": True})
    assert on.status_code == 200 and on.json()["precision_enabled"] is True
    assert farmer.get("/api/v1/planning/me").json()["precision_enabled"] is True
    off = farmer.patch("/api/v1/precision/me", json={"enabled": False})
    assert off.json()["precision_enabled"] is False
    assert clients["doc"].patch("/api/v1/precision/me", json={"enabled": True}).status_code == 403
