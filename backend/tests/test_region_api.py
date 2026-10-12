"""區域地圖 API 的離線測試，重用 `test_farm_logs.py` 的 fixture；三種角色各一組：
植物醫生（doctor）、主管機關／改良場（agency）、農民（farmer）。
"""
import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import planning
from app.api.v1 import region as region_api
from app.models.planning import FarmAccount, FarmSession
from app.models.region import AlertThreshold, RegionDailyStat
from tests.test_farm_logs import TODAY, system  # noqa: F401  共用離線 API fixture

REGION = "/api/v1/region"


@pytest.fixture
def region(system, monkeypatch):
    app, factory, clients, clock = system
    app.include_router(region_api.router, prefix="/api/v1")
    monkeypatch.setattr(region_api, "taipei_today", lambda: TODAY)

    with factory() as db:
        farmer = db.get(FarmAccount, "farmer")
        farmer.location, farmer.share_region = "高雄市大社區", True
        stranger = db.get(FarmAccount, "stranger")
        stranger.location = "高雄市楠梓區"  # 沒同意彙整，也用來測「看不到別人的鄉鎮」
        db.add(FarmAccount(id="agency", username="agency", name="agency", role="agency", password_hash="unused"))
        db.flush()
        db.add(FarmSession(token_hash=planning.digest("agency-token"), account_id="agency",
                           expires_at=int(time.time()) + 10000))
        db.add_all([
            RegionDailyStat(id="s1", county="高雄市", district="大社區", day=TODAY, crop_text="芒果",
                            pest_text="炭疽病", source="scout", count=5, farms=4),
            RegionDailyStat(id="s2", county="高雄市", district="楠梓區", day=TODAY, crop_text="水稻",
                            pest_text=None, source="plan", area_ha=2.0, farms=4),
            AlertThreshold(id="t1", pest_text="炭疽病", metric="reports_30d", warn_value=3.0, basis="示範，待高改場提供"),
        ])
        db.commit()

    client = TestClient(app, headers={"X-Planning-Request": "1"})
    client.cookies.set(planning.COOKIE, "agency-token", path="/api/v1")
    clients["agency"] = client
    return app, factory, clients, clock


# ---------------------------------------------------------------------------
# 植物醫生：看得到全縣市、districts/{district} 多一段已連結農場、能登錄調查與重新彙整。
# ---------------------------------------------------------------------------

def test_geo_endpoint_returns_38_kaohsiung_districts(region):
    _, _, clients, _ = region
    r = clients["doc"].get(f"{REGION}/geo")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "FeatureCollection"
    assert len(body["features"]) == 38


def test_doctor_sees_countywide_summary(region):
    _, _, clients, _ = region
    r = clients["doc"].get(f"{REGION}/summary")
    assert r.status_code == 200
    assert r.json()["county"] == "高雄市"


def test_doctor_sees_linked_farms_on_district_detail(region):
    _, _, clients, _ = region
    r = clients["doc"].get(f"{REGION}/districts/大社區")
    assert r.status_code == 200
    body = r.json()
    assert "linked_farms" in body
    assert [f["id"] for f in body["linked_farms"]] == ["farmer"]


def test_doctor_can_post_survey(region):
    _, _, clients, _ = region
    r = clients["doc"].post(f"{REGION}/surveys", json=dict(
        district="大社區", surveyed_on="2030-03-10", crop_text="芒果", pest_text="東方果實蠅",
        trap_count=12.0, traps=1))
    assert r.status_code == 200, r.text


def test_doctor_can_trigger_reaggregate(region):
    _, _, clients, _ = region
    r = clients["doc"].post(f"{REGION}/reaggregate")
    assert r.status_code == 200, r.text
    # removed 也要回：來源被撤回而清掉的列數要說得出來，不能默默消失。
    assert set(r.json()) == {"rows", "removed"}


def test_doctor_can_export_xlsx(region):
    _, _, clients, _ = region
    r = clients["doc"].get(f"{REGION}/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")


# ---------------------------------------------------------------------------
# 主管機關／改良場：跟植物醫生一樣看全縣市、能登錄調查與匯出，但看不到已連結農場。
# ---------------------------------------------------------------------------

def test_agency_sees_countywide_summary(region):
    _, _, clients, _ = region
    r = clients["agency"].get(f"{REGION}/summary")
    assert r.status_code == 200
    assert r.json()["county"] == "高雄市"


def test_agency_district_detail_has_no_linked_farms(region):
    _, _, clients, _ = region
    r = clients["agency"].get(f"{REGION}/districts/大社區")
    assert r.status_code == 200
    assert "linked_farms" not in r.json()


def test_agency_can_post_survey(region):
    _, _, clients, _ = region
    r = clients["agency"].post(f"{REGION}/surveys", json=dict(
        district="楠梓區", surveyed_on="2030-03-10", crop_text="水稻", pest_text="稻熱病",
        trap_count=5.0, traps=1))
    assert r.status_code == 200, r.text


def test_agency_can_export_xlsx(region):
    _, _, clients, _ = region
    r = clients["agency"].get(f"{REGION}/export.xlsx")
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# 農民：只看得到自己所在鄉鎮，看不到別人、不能登錄調查或匯出報表。
# ---------------------------------------------------------------------------

def test_farmer_summary_scoped_to_own_district(region):
    _, _, clients, _ = region
    r = clients["farmer"].get(f"{REGION}/summary")
    assert r.status_code == 200
    assert r.json()["kpi"]["count"] is not None  # 大社區當天有 4 戶貢獻，未被抑制


def test_farmer_districts_list_only_contains_own_district(region):
    _, _, clients, _ = region
    r = clients["farmer"].get(f"{REGION}/districts")
    assert r.status_code == 200
    body = r.json()["districts"]
    assert [d["district"] for d in body] == ["大社區"]


def test_farmer_cannot_view_other_district_detail(region):
    _, _, clients, _ = region
    r = clients["farmer"].get(f"{REGION}/districts/楠梓區")
    assert r.status_code == 404


def test_farmer_cannot_post_survey(region):
    _, _, clients, _ = region
    r = clients["farmer"].post(f"{REGION}/surveys", json=dict(
        district="大社區", surveyed_on="2030-03-10", crop_text="芒果", pest_text="東方果實蠅",
        trap_count=12.0, traps=1))
    assert r.status_code == 403


def test_farmer_cannot_export_xlsx(region):
    _, _, clients, _ = region
    r = clients["farmer"].get(f"{REGION}/export.xlsx")
    assert r.status_code == 403


def test_farmer_cannot_trigger_reaggregate(region):
    _, _, clients, _ = region
    r = clients["farmer"].post(f"{REGION}/reaggregate")
    assert r.status_code == 403


def test_farmer_without_resolvable_location_gets_available_false_not_error(region):
    """鄉鎮沒填是「還沒設定」不是「請求錯誤」：/summary 回 200，前端才能畫成空狀態而不是紅字。"""
    app, factory, clients, clock = region
    with factory() as db:
        db.get(FarmAccount, "farmer").location = ""
        db.commit()
    r = clients["farmer"].get(f"{REGION}/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert body["reason"] == "district_missing"
    # 前端拿這句直接顯示，必須講得出「要去哪裡補」。
    assert "鄉鎮" in body["message"]
    # 空狀態也要有完整形狀，前端才不用到處防 undefined。
    assert body["kpi"] == dict(count=None, area_ha=None, metric=None)
    assert body["top_crops"] == [] and body["alerts"] == [] and body["legend"] == []


def test_farmer_without_resolvable_location_still_blocked_on_other_endpoints(region):
    """只有 /summary 放寬。其餘端點沒有「空狀態」可畫，照舊 422，不要回半套資料。"""
    app, factory, clients, clock = region
    with factory() as db:
        db.get(FarmAccount, "farmer").location = ""
        db.commit()
    for path in (f"{REGION}/districts", f"{REGION}/geo"):
        assert clients["farmer"].get(path).status_code == 422, path


def test_summary_marks_reason_none_when_available(region):
    _, _, clients, _ = region
    body = clients["doc"].get(f"{REGION}/summary").json()
    assert body["available"] is True and body["reason"] is None


def test_farmer_can_toggle_share_region(region):
    _, factory, clients, _ = region
    r = clients["farmer"].patch("/api/v1/planning/me/share-region", json=dict(enabled=False))
    assert r.status_code == 200
    assert r.json()["share_region"] is False
    with factory() as db:
        assert db.get(FarmAccount, "farmer").share_region is False


def test_doctor_cannot_toggle_share_region(region):
    _, _, clients, _ = region
    r = clients["doc"].patch("/api/v1/planning/me/share-region", json=dict(enabled=True))
    assert r.status_code == 403


def test_region_module_must_not_use_postponed_annotations():
    """`from __future__ import annotations` 會讓型別註記變成字串（ForwardRef），FastAPI 解析
    `Filters`（普通 class 當 Depends）時還原不回來，請求當下丟 PydanticUserError → 500。

    這個 bug **離線測試抓不到**：測試跑在 venv 的 Python 3.11 沒事，映像檔的 3.12 一打開區域地圖就 500
    （第 9 步端到端走查時踩到）。所以改成檢查模組本身有沒有那行 import——它會在模組命名空間綁定
    `annotations` 這個名字，與 Python 版本無關。
    """
    assert not hasattr(region_api, "annotations"), (
        "app/api/v1/region.py 不能有 `from __future__ import annotations`，"
        "否則 Filters 依賴會在 Python 3.12 的正式映像檔上 500（見本測試的說明）"
    )


def test_farmer_can_save_own_location_and_leave_empty_state(region):
    _, factory, clients, _ = region
    with factory() as db:
        db.get(FarmAccount, "farmer").location = ""
        db.commit()
    assert clients["farmer"].get(f"{REGION}/summary").json()["reason"] == "district_missing"
    r = clients["farmer"].patch('/api/v1/planning/me/location', json={'location': '高雄市 大社區'})
    assert r.status_code == 200
    assert r.json()['location'] == '高雄市大社區'
    assert clients['farmer'].get(f'{REGION}/summary').json()['available'] is True
    with factory() as db:
        assert db.get(FarmAccount, 'stranger').location == '高雄市楠梓區'


@pytest.mark.parametrize('who', ['doc', 'agency'])
def test_location_settings_only_for_farmer(region, who):
    _, _, clients, _ = region
    assert clients[who].patch('/api/v1/planning/me/location', json={'location': '高雄市大社區'}).status_code == 403


@pytest.mark.parametrize('location', ['', '   ', '高雄市', '高雄市測試區', '台北市大社區高雄市'])
def test_location_settings_reject_incomplete_or_ambiguous_value(region, location):
    _, factory, clients, _ = region
    assert clients['farmer'].patch('/api/v1/planning/me/location', json={'location': location}).status_code == 422
    with factory() as db:
        assert db.get(FarmAccount, 'farmer').location == '高雄市大社區'


def test_unsupported_county_is_saved_but_not_misreported_as_district_missing(region):
    _, _, clients, _ = region
    r = clients['farmer'].patch('/api/v1/planning/me/location', json={'location': '台北市中正區'})
    assert r.status_code == 200 and r.json()['location'] == '臺北市中正區'
    data = clients['farmer'].get(f'{REGION}/summary').json()
    assert not data['available'] and data['reason'] == 'county_unsupported'
    assert data['county'] == '臺北市'


def test_location_update_requires_session_and_rejects_extra_account_id(region):
    app, _, clients, _ = region
    assert TestClient(app, headers={'X-Planning-Request': '1'}).patch('/api/v1/planning/me/location', json={'location': '高雄市大社區'}).status_code == 401
    assert clients['farmer'].patch('/api/v1/planning/me/location', json={
        'location': '高雄市大社區', 'account_id': 'stranger'}).status_code == 422
