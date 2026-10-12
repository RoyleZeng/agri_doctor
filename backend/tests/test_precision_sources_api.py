"""資料來源頁 API 的離線測試：田區 CRUD、感測群組與站點、data_sources、CSV 讀數上傳、
無人機任務（CSV 路徑）、GIS 邊界匯入。沿用農事日誌／精準模式共用的離線 fixture。
"""
from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.api.v1 import data_sources as sources_api
from app.api.v1 import precision as precision_api
from app.models.precision import DataSource, FarmField, FarmZone, FieldBoundary, FieldLayer, SensorGroup, SensorStation, UavMission
from app.precision import service
from app.precision.crypto import MASK, decrypt
from app.precision.geo import wgs84_to_twd97
from app.precision.rules import RainForecast
from tests.test_farm_logs import TODAY, system  # noqa: F401  共用同一套離線 API fixture

BASE = "/api/v1/precision"


@pytest.fixture
def wired(system, monkeypatch):  # noqa: F811
    app, factory, clients, clock = system
    app.include_router(precision_api.router, prefix="/api/v1")
    app.include_router(sources_api.router, prefix="/api/v1")
    monkeypatch.setattr(precision_api, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(sources_api, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(sources_api, "taipei_today", lambda: TODAY)
    monkeypatch.setattr(service, "rain_forecast_48h", lambda farmer, now: RainForecast(max_probability_pct=10, source="試驗預報"))
    return app, factory, clients, clock


def make_field(clients, name="東側一區", grid_cols=2, grid_rows=2, area_text="0.4 公頃"):
    response = clients["farmer"].post(f"{BASE}/farmers/farmer/fields",
                                      json=dict(name=name, area_text=area_text, grid_cols=grid_cols, grid_rows=grid_rows))
    assert response.status_code == 201, response.text
    return response.json()


def make_zones(factory, field_id):
    with factory() as db:
        db.add_all([FarmZone(field_id=field_id, code="A", label="高", cells=[[0, 0], [0, 1]], area_ha=0.2),
                    FarmZone(field_id=field_id, code="B", label="低", cells=[[1, 0], [1, 1]], area_ha=0.2)])
        db.commit()
        return {zone.code: zone.id for zone in db.scalars(select(FarmZone).where(FarmZone.field_id == field_id))}


# --------------------------------------------------------------------------
# 田區 CRUD
# --------------------------------------------------------------------------

def test_field_crud_is_farmer_only_and_protects_demo_fields(wired):
    _, factory, clients, _ = wired
    field = make_field(clients)
    assert clients["farmer"].get(f"{BASE}/farmers/farmer/fields").json()["items"][0]["id"] == field["id"]
    assert clients["doc"].post(f"{BASE}/farmers/farmer/fields", json=dict(name="x")).status_code == 403
    assert clients["stranger"].post(f"{BASE}/farmers/farmer/fields", json=dict(name="x")).status_code == 404

    patched = clients["farmer"].patch(f"{BASE}/fields/{field['id']}", json=dict(area_text="0.5 公頃"))
    assert patched.status_code == 200 and patched.json()["area_text"] == "0.5 公頃"
    assert clients["doc"].patch(f"{BASE}/fields/{field['id']}", json=dict(name="x")).status_code == 403

    with factory() as db:
        demo_field = FarmField(farmer_id="farmer", name="示範田區", is_demo=True)
        db.add(demo_field)
        db.commit()
        demo_id = demo_field.id
    assert clients["farmer"].patch(f"{BASE}/fields/{demo_id}", json=dict(name="x")).status_code == 409
    assert clients["farmer"].delete(f"{BASE}/fields/{demo_id}").status_code == 409

    assert clients["farmer"].delete(f"{BASE}/fields/{field['id']}").status_code == 200
    assert clients["farmer"].get(f"{BASE}/farmers/farmer/fields").json()["items"] == [
        {"id": demo_id, "name": "示範田區", "area_text": "", "grid_cols": 6, "grid_rows": 4, "is_demo": True}]


def test_shrinking_the_grid_is_blocked_when_a_station_or_zone_would_fall_outside(wired):
    _, factory, clients, _ = wired
    field = make_field(clients, grid_cols=4, grid_rows=4)
    group = clients["farmer"].post(f"{BASE}/fields/{field['id']}/sensor-groups", json=dict(name="土壤組")).json()
    clients["farmer"].post(f"{BASE}/sensor-groups/{group['id']}/stations",
                           json=dict(code="S-1", kind="soil", grid_col=3, grid_row=0))
    blocked = clients["farmer"].patch(f"{BASE}/fields/{field['id']}", json=dict(grid_cols=2))
    assert blocked.status_code == 409 and "超出" in blocked.json()["detail"]  # 站點在 col=3，縮到 2 欄會超出
    shrink_rows_only = clients["farmer"].patch(f"{BASE}/fields/{field['id']}", json=dict(grid_rows=1))
    assert shrink_rows_only.status_code == 200  # 站點在 row=0，縮 rows 不影響它


# --------------------------------------------------------------------------
# 感測群組與站點
# --------------------------------------------------------------------------

def test_sensor_groups_and_stations_crud(wired):
    _, factory, clients, _ = wired
    field = make_field(clients)
    created = clients["farmer"].post(f"{BASE}/fields/{field['id']}/sensor-groups",
                                     json=dict(name="土壤組 A", metrics=["soil_moisture"]))
    assert created.status_code == 201, created.text
    group = created.json()
    assert group["stations"] == [] and group["is_demo"] is False
    assert clients["doc"].post(f"{BASE}/fields/{field['id']}/sensor-groups", json=dict(name="x")).status_code == 403

    station = clients["farmer"].post(f"{BASE}/sensor-groups/{group['id']}/stations",
                                     json=dict(code="S-1", kind="soil", grid_col=0, grid_row=0)).json()
    listed = clients["farmer"].get(f"{BASE}/fields/{field['id']}/sensor-groups").json()["items"][0]
    assert listed["stations"] == [{"id": station["id"], "code": "S-1", "kind": "soil", "zone_id": None, "grid_col": 0, "grid_row": 0}]

    assert clients["stranger"].delete(f"{BASE}/stations/{station['id']}").status_code == 404
    assert clients["farmer"].delete(f"{BASE}/stations/{station['id']}").status_code == 200
    assert clients["farmer"].delete(f"{BASE}/sensor-groups/{group['id']}").status_code == 200
    assert clients["farmer"].get(f"{BASE}/fields/{field['id']}/sensor-groups").json()["items"] == []


# --------------------------------------------------------------------------
# data_sources：連線設定、加密、測試連線
# --------------------------------------------------------------------------

def test_data_source_secrets_are_encrypted_masked_and_not_reencrypted_on_noop_patch(wired):
    _, factory, clients, _ = wired
    created = clients["farmer"].post(f"{BASE}/farmers/farmer/data-sources",
                                     json=dict(kind="sensor_http", label="廠商 API",
                                              config={"endpoint": "https://vendor.invalid", "api_key": "raw-secret-1"}))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["config"]["api_key"] == MASK and body["config"]["endpoint"] == "https://vendor.invalid"
    assert body["status"] == "never"
    with factory() as db:
        stored = db.get(DataSource, body["id"]).config["api_key"]
        assert stored != "raw-secret-1" and decrypt(stored) == "raw-secret-1"

    # PATCH 只改 label，config 原樣送回遮罩值：不能把遮罩字串當成新密碼重新加密。
    noop = clients["farmer"].patch(f"{BASE}/data-sources/{body['id']}",
                                   json=dict(label="廠商 API v2", config={"endpoint": "https://vendor.invalid", "api_key": MASK}))
    assert noop.json()["label"] == "廠商 API v2"
    with factory() as db:
        assert decrypt(db.get(DataSource, body["id"]).config["api_key"]) == "raw-secret-1"

    changed = clients["farmer"].patch(f"{BASE}/data-sources/{body['id']}", json=dict(config={"api_key": "raw-secret-2"}))
    with factory() as db:
        row = db.get(DataSource, body["id"])
        assert decrypt(row.config["api_key"]) == "raw-secret-2" and row.config["endpoint"] == "https://vendor.invalid"

    assert clients["doc"].delete(f"{BASE}/data-sources/{body['id']}").status_code == 403
    assert clients["farmer"].delete(f"{BASE}/data-sources/{body['id']}").status_code == 200
    assert clients["farmer"].get(f"{BASE}/farmers/farmer/data-sources").json()["items"] == []


def test_test_connection_is_honest_about_what_actually_works(wired):
    _, factory, clients, _ = wired

    def make(kind):
        return clients["farmer"].post(f"{BASE}/farmers/farmer/data-sources", json=dict(kind=kind, label=kind, config={})).json()

    for kind in ("sensor_csv", "uav_upload", "gis_file"):
        source = make(kind)
        tested = clients["farmer"].post(f"{BASE}/data-sources/{source['id']}/test").json()
        assert tested["status"] == "connected" and tested["last_sync_at"] is not None

    for kind in ("sensor_http", "sensor_mqtt", "gis_wms"):
        source = make(kind)
        tested = clients["farmer"].post(f"{BASE}/data-sources/{source['id']}/test").json()
        assert tested["status"] == "error" and "尚未支援" in tested["last_error"]

    weather_source = make("weather")
    no_county = clients["farmer"].post(f"{BASE}/data-sources/{weather_source['id']}/test").json()
    assert no_county["status"] == "error" and "縣市" in no_county["last_error"]


# --------------------------------------------------------------------------
# CSV 讀數上傳
# --------------------------------------------------------------------------

def upload_csv(client, url, text, filename="readings.csv"):
    return client.post(url, files={"file": (filename, text.encode("utf-8"), "text/csv")})


def test_csv_reading_upload_matches_station_codes_dedupes_and_triggers_analysis(wired):
    _, factory, clients, clock = wired
    field = make_field(clients)
    zones = make_zones(factory, field["id"])
    group = clients["farmer"].post(f"{BASE}/fields/{field['id']}/sensor-groups", json=dict(name="土壤組")).json()
    source = clients["farmer"].post(f"{BASE}/farmers/farmer/data-sources",
                                    json=dict(kind="sensor_csv", label="CSV 上傳", config={})).json()
    with factory() as db:
        db.get(SensorGroup, group["id"]).source_id = source["id"]
        db.commit()
    clients["farmer"].post(f"{BASE}/sensor-groups/{group['id']}/stations",
                           json=dict(code="S-1", kind="soil", zone_id=zones["A"], grid_col=0, grid_row=0))

    # 72 小時內只有這個分區的讀數，剛好滿足 min_readings（36 筆），另一分區沒有讀數（insufficient_data）。
    csv_text = "station_code,metric,ts,value,unit\n" + "\n".join(
        f"S-1,soil_moisture,2030-03-{9 - h // 24:02d}T{(h % 24):02d}:00:00+00:00,18,%" for h in range(40)) + "\n"
    url = f"{BASE}/sensor-groups/{group['id']}/readings"
    first = upload_csv(clients["farmer"], url, csv_text)
    assert first.status_code == 201, first.text
    assert first.json() == {"inserted": 40, "skipped_duplicates": 0, "total_in_file": 40}

    again = upload_csv(clients["farmer"], url, csv_text)
    assert again.json() == {"inserted": 0, "skipped_duplicates": 40, "total_in_file": 40}

    unknown = upload_csv(clients["farmer"], url, "station_code,metric,ts,value\nS-2,soil_moisture,2030-03-09T00:00,18\n")
    assert unknown.status_code == 422 and "S-2" in unknown.json()["detail"]

    not_csv = clients["farmer"].post(url, files={"file": ("readings.txt", b"x", "text/plain")})
    assert not_csv.status_code == 422

    with factory() as db:
        assert db.get(DataSource, source["id"]).status == "connected"
    recs = clients["farmer"].get(f"{BASE}/farmers/farmer/recommendations").json()["items"]
    assert any(r["kind"] == "irrigate" for r in recs)  # 上傳後觸發了重新分析


# --------------------------------------------------------------------------
# 無人機任務（CSV 路徑）
# --------------------------------------------------------------------------

def test_uav_ndvi_upload_creates_a_layer_and_rgb_does_not(wired):
    _, factory, clients, clock = wired
    field = make_field(clients)
    make_zones(factory, field["id"])
    url = f"{BASE}/fields/{field['id']}/uav"

    ndvi_csv = "col,row,value\n0,0,0.75\n0,1,0.71\n1,0,0.45\n1,1,0.41\n"
    ndvi = clients["farmer"].post(url, data=dict(flown_on=str(TODAY), product="ndvi"),
                                  files={"file": ("ndvi.csv", ndvi_csv.encode(), "text/csv")})
    assert ndvi.status_code == 201, ndvi.text
    body = ndvi.json()
    assert (body["status"], body["product_label"]) == ("done", "NDVI")
    with factory() as db:
        layer = db.scalar(select(FieldLayer).where(FieldLayer.field_id == field["id"], FieldLayer.kind == "ndvi"))
        assert layer.zone_values == {"A": 0.73, "B": 0.43} and layer.captured_on == TODAY
        assert "無人機NDVI" in layer.source_text

    rgb = clients["farmer"].post(url, data=dict(flown_on=str(TODAY), product="rgb"),
                                 files={"file": ("rgb.csv", b"col,row,value\n0,0,1\n", "text/csv")})
    assert rgb.status_code == 201 and rgb.json()["status"] == "done"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FieldLayer)
                         .where(FieldLayer.field_id == field["id"])) == 1  # RGB 沒有多出一筆圖層

    future = clients["farmer"].post(url, data=dict(flown_on=str(TODAY + timedelta(days=1)), product="ndvi"),
                                    files={"file": ("x.csv", ndvi_csv.encode(), "text/csv")})
    assert future.status_code == 422

    out_of_range = clients["farmer"].post(url, data=dict(flown_on=str(TODAY), product="ndvi"),
                                          files={"file": ("bad.csv", b"col,row,value\n5,5,0.5\n", "text/csv")})
    assert out_of_range.status_code == 422
    missions = clients["farmer"].get(url).json()["items"]
    failed = next(m for m in missions if m["status"] == "failed")
    assert "超出" in failed["note"]
    assert clients["doc"].post(url, data=dict(flown_on=str(TODAY), product="ndvi"),
                               files={"file": ("x.csv", ndvi_csv.encode(), "text/csv")}).status_code == 403


# --------------------------------------------------------------------------
# GIS 邊界
# --------------------------------------------------------------------------

def test_boundary_import_converts_twd97_marks_outside_cells_and_can_be_replaced(wired):
    _, factory, clients, _ = wired
    field = make_field(clients, grid_cols=4, grid_rows=4)
    url = f"{BASE}/fields/{field['id']}/boundary"
    assert clients["farmer"].get(url).json()["boundary"] is None

    square = {"type": "Polygon", "coordinates": [[[121.5, 24.0], [121.504, 24.0], [121.504, 24.004], [121.5, 24.004], [121.5, 24.0]]]}
    created = clients["farmer"].post(url, json=dict(geojson=square, crs="EPSG:4326", source="upload"))
    assert created.status_code == 201, created.text
    assert created.json()["outside_cells"] == []  # 整塊都在邊界內

    # TWD97 反算公式只在中央經線附近準確，測試座標要落在台灣真實範圍內，不能用 (0,0) 這種玩具座標。
    base_lon, base_lat, size = 121.5, 24.0, 0.004
    triangle_4326 = [[base_lon, base_lat], [base_lon + size, base_lat], [base_lon, base_lat + size], [base_lon, base_lat]]
    triangle_twd97 = {"type": "Polygon", "coordinates": [[list(wgs84_to_twd97(lon, lat)) for lon, lat in triangle_4326]]}
    replaced = clients["farmer"].post(url, json=dict(geojson=triangle_twd97, crs="EPSG:3826", source="upload"))
    assert replaced.status_code in (200, 201)
    body = replaced.json()
    assert [3, 3] in body["outside_cells"] and [0, 0] not in body["outside_cells"]
    lon, lat = body["geojson"]["coordinates"][0][0][0]
    assert abs(lon - base_lon) < 1e-4 and abs(lat - base_lat) < 1e-4  # 存回來的座標已經是 4326，不是 TWD97 的公尺數字

    with factory() as db:
        assert db.scalar(select(func.count()).select_from(FieldBoundary)
                         .where(FieldBoundary.field_id == field["id"])) == 1  # 覆蓋，不是疊加

    bad_geometry = clients["farmer"].post(url, json=dict(geojson={"type": "Point", "coordinates": [0, 0]}))
    assert bad_geometry.status_code == 422
    bad_crs = clients["farmer"].post(url, json=dict(geojson=square, crs="EPSG:9999"))
    assert bad_crs.status_code == 422

    assert clients["doc"].delete(url).status_code == 403
    assert clients["farmer"].delete(url).status_code == 200
    assert clients["farmer"].get(url).json()["boundary"] is None
    assert clients["farmer"].delete(url).status_code == 404


def test_rotated_secret_key_surfaces_as_needs_reentry_and_is_not_clobbered_by_a_noop_patch(wired, monkeypatch):
    """換過 SECRET_KEY 之後：解不回來的密碼要顯示「需重新輸入」而不是「已設定」，
    而且使用者沒動那格、把提示文字原樣送回來時，不能把提示文字本身存成密碼。"""
    from app.precision import crypto

    _, factory, clients, _ = wired
    created = clients["farmer"].post(f"{BASE}/farmers/farmer/data-sources",
                                     json=dict(kind="sensor_http", label="廠商 API",
                                              config={"endpoint": "https://vendor.invalid", "api_key": "raw-secret-1"}))
    source_id = created.json()["id"]
    with factory() as db:
        old_token = db.get(DataSource, source_id).config["api_key"]

    crypto._fernet.cache_clear()
    monkeypatch.setattr(crypto.settings, "secret_key", type(crypto.settings.secret_key)("rotated-key-value"))
    crypto._fernet.cache_clear()
    try:
        listed = clients["farmer"].get(f"{BASE}/farmers/farmer/data-sources").json()
        assert listed["items"][0]["config"]["api_key"] == crypto.NEEDS_REENTRY
        assert listed["items"][0]["config"]["endpoint"] == "https://vendor.invalid"

        # 使用者只改名稱，密碼那格原樣送回提示文字：舊密文原樣保留，不會被提示文字或再加密一層蓋掉。
        clients["farmer"].patch(f"{BASE}/data-sources/{source_id}",
                                json=dict(label="改個名字", config={"api_key": crypto.NEEDS_REENTRY}))
        with factory() as db:
            assert db.get(DataSource, source_id).config["api_key"] == old_token

        # 真的重新輸入密碼：用新金鑰存得回去，畫面回到「已設定」。
        again = clients["farmer"].patch(f"{BASE}/data-sources/{source_id}", json=dict(config={"api_key": "raw-secret-3"}))
        assert again.json()["config"]["api_key"] == MASK
        with factory() as db:
            assert crypto.decrypt(db.get(DataSource, source_id).config["api_key"]) == "raw-secret-3"
    finally:
        crypto._fernet.cache_clear()


def test_data_sources_list_tells_the_page_whether_the_key_is_still_the_dev_default(wired, monkeypatch):
    # 啟動 log 的警告只有管理者看得到；真正在這頁輸入密碼的人要在畫面上看到。
    _, _, clients, _ = wired
    from app.api.v1 import data_sources as sources_api

    assert clients["farmer"].get(f"{BASE}/farmers/farmer/data-sources").json()["secret_key_is_default"] is True
    monkeypatch.setattr(type(sources_api.settings), "secret_key_is_default", property(lambda self: False))
    assert clients["farmer"].get(f"{BASE}/farmers/farmer/data-sources").json()["secret_key_is_default"] is False
