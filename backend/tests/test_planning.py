"""Offline API and provenance tests; never connect to the user's PostgreSQL database."""
import hashlib
from datetime import date, timedelta
from pathlib import Path
import time
from xml.etree import ElementTree

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import planning as module
from app.db.base import Base
from app.db.session import get_db
from app.models.farm_content import FarmContent, FarmContentRelease
from app.models.farm_log import FARM_LOG_TABLES
from app.models.planning import PLANNING_TABLES, FarmAccount, FarmEvent, FarmInvite, FarmPlan, FarmSession
from app.services.farm_templates import TEMPLATES, generate_events, source_index


@pytest.fixture
def system():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    # 規劃事項的「已記錄」要查農事日誌表，所以一併建立（SQLite 未開外鍵檢查，不需要其他外鍵目的表）。
    Base.metadata.create_all(engine, tables=PLANNING_TABLES + FARM_LOG_TABLES + [FarmContent.__table__, FarmContentRelease.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(module.router, prefix="/api/v1")
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    module._attempts.clear()
    yield app, factory
    engine.dispose()


def account(system, name, role="farmer"):
    client = TestClient(system[0], headers={"X-Planning-Request": "1"})
    response = client.post("/api/v1/planning/register", json=dict(username=name, password="test-password-123", name=name, role=role, farm_name="測試農場"))
    assert response.status_code == 200, response.text
    return client, response.json()


def create_plan(client, **changes):
    payload = dict(crop_key="rice", crop_name="水稻", field_name="甲區", planted_on="2026-12-15", end_on="2027-04-14") | changes
    response = client.post("/api/v1/planning/plans", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def edit_payload(plan, event, **changes):
    return {k: event[k] for k in ("kind", "title", "start_on", "end_on", "detail", "note", "completed")} | dict(version=plan["version"]) | changes


def link(farmer, doctor):
    code = farmer.post("/api/v1/planning/invite").json()["code"]
    response = doctor.post("/api/v1/planning/links", json={"code": code})
    assert response.status_code == 200, response.text
    return code


def test_auth_is_required_csrf_password_hash_and_logout(system):
    anon = TestClient(system[0])
    assert anon.get("/api/v1/planning/me").status_code == 401
    assert anon.post("/api/v1/planning/register", json={}).status_code == 403
    client, farmer = account(system, "farmer_a")
    assert client.get("/api/v1/planning/me").status_code == 200
    assert "password_hash" not in farmer and "username" not in farmer
    with system[1]() as db:
        assert db.scalar(select(FarmAccount)).password_hash != "test-password-123"
        session = db.scalar(select(FarmSession))
        assert session.token_hash != client.cookies.get(module.COOKIE)
    wrong = client.post("/api/v1/planning/login", json=dict(username="farmer_a", password="wrong-password"))
    assert wrong.status_code == 401
    client.post("/api/v1/planning/logout")
    assert client.get("/api/v1/planning/me").status_code == 401
    assert client.post("/api/v1/planning/login", json=dict(username="FARMER_A", password="test-password-123")).status_code == 200


def test_farmer_isolation_and_unlinked_doctor(system):
    farmer, owner = account(system, "farmer_a")
    other, _ = account(system, "farmer_b")
    doctor, _ = account(system, "doctor_a", "doctor")
    plan = create_plan(farmer)
    for unauthorized in [other, doctor]:
        assert unauthorized.get(f"/api/v1/planning/farmers/{owner['id']}").status_code == 404
        assert unauthorized.put(f"/api/v1/planning/plans/{plan['id']}/events/{plan['events'][0]['id']}", json=edit_payload(plan, plan["events"][0])).status_code == 404
        assert unauthorized.post(f"/api/v1/planning/farmers/{owner['id']}/notes", json={"body": "unauthorized"}).status_code == 404
    assert doctor.post("/api/v1/planning/plans", json=dict(crop_name="水稻", field_name="甲", planted_on="2026-01-01", end_on="2026-05-01")).status_code == 403


def test_link_replay_revoke_and_doctor_changes_persist(system):
    farmer, owner = account(system, "farmer_a")
    doctor, doc = account(system, "doctor_a", "doctor")
    plan = create_plan(farmer)
    code = link(farmer, doctor)
    assert doctor.post("/api/v1/planning/links", json={"code": code}).status_code == 400
    assert doctor.get("/api/v1/planning/links").json()[0]["crops"] == ["水稻"]
    event = next(e for e in plan["events"] if e["kind"] == "care")
    response = doctor.put(f"/api/v1/planning/plans/{plan['id']}/events/{event['id']}", json=edit_payload(plan, event, note="已確認田水"))
    assert response.status_code == 200
    persisted = farmer.get(f"/api/v1/planning/farmers/{owner['id']}").json()["plans"][0]
    assert next(e for e in persisted["events"] if e["id"] == event["id"])["note"] == "已確認田水"
    assert doctor.post(f"/api/v1/planning/farmers/{owner['id']}/notes", json={"body": "下次追蹤甲區"}).status_code == 200
    assert farmer.get(f"/api/v1/planning/farmers/{owner['id']}").json()["notes"][0]["author"] == doc["name"]
    assert doctor.patch(f"/api/v1/planning/plans/{plan['id']}", json={"version": 2, "archived": True}).status_code == 403
    farmer.delete(f"/api/v1/planning/links/{doc['id']}")
    assert doctor.get(f"/api/v1/planning/farmers/{owner['id']}").status_code == 404
    assert farmer.get(f"/api/v1/planning/farmers/{owner['id']}").json()["notes"][0]["body"] == "下次追蹤甲區"


def test_expired_invite_and_replacement(system):
    farmer, _ = account(system, "farmer_a")
    doctor, _ = account(system, "doctor_a", "doctor")
    old = farmer.post("/api/v1/planning/invite").json()["code"]
    new = farmer.post("/api/v1/planning/invite").json()["code"]
    assert doctor.post("/api/v1/planning/links", json={"code": old}).status_code == 400
    with system[1]() as db:
        invite = db.get(FarmInvite, module.digest(new))
        invite.expires_at = int(time.time()) - 1
        db.commit()
    assert doctor.post("/api/v1/planning/links", json={"code": new}).status_code == 400


def test_stale_write_dates_archive_and_restore(system):
    farmer, owner = account(system, "farmer_a")
    plan = create_plan(farmer)
    event = next(e for e in plan["events"] if e["kind"] == "care")
    url = f"/api/v1/planning/plans/{plan['id']}/events/{event['id']}"
    assert farmer.put(url, json=edit_payload(plan, event, start_on="2026-01-01")).status_code == 422
    assert farmer.put(url, json=edit_payload(plan, event, end_on="2026-12-14")).status_code == 422
    response = farmer.put(url, json=edit_payload(plan, event, note="第一次記錄"))
    assert response.status_code == 200
    assert farmer.put(url, json=edit_payload(plan, event, note="舊畫面覆寫")).status_code == 409
    latest = response.json()
    assert farmer.patch(f"/api/v1/planning/plans/{plan['id']}", json={"version": latest["version"], "archived": True}).status_code == 200
    assert farmer.put(url, json=edit_payload(latest, event, version=3)).status_code == 409
    restored = farmer.patch(f"/api/v1/planning/plans/{plan['id']}", json={"version": 3, "archived": False}).json()
    assert not restored["archived"]
    assert next(e for e in restored["events"] if e["id"] == event["id"])["note"] == "第一次記錄"


def test_event_cannot_be_moved_to_another_plan(system):
    farmer, _ = account(system, "farmer_a")
    p1, p2 = create_plan(farmer), create_plan(farmer, field_name="乙區")
    assert farmer.put(f"/api/v1/planning/plans/{p2['id']}/events/{p1['events'][0]['id']}", json=edit_payload(p2, p1["events"][0])).status_code == 404


def test_custom_crop_empty_not_guessed_and_custom_events(system):
    farmer, _ = account(system, "farmer_a")
    plan = create_plan(farmer, crop_key=None, crop_name="芭樂")
    assert plan["events"] == [] and plan["sources"] == [] and plan["template_version"] is None
    result = farmer.post(f"/api/v1/planning/plans/{plan['id']}/events", json=dict(version=1, kind="care", title="檢查田區", start_on=plan["planted_on"], end_on=plan["planted_on"]))
    assert result.status_code == 200
    assert result.json()["events"][0]["basis"] == "user_added"


@pytest.mark.parametrize("days", [14, 15, 75, 120, 150, 365, 730])
@pytest.mark.parametrize("key", ["rice", "tomato", "cabbage"])
def test_generated_dates_are_contiguous_bounded_and_cross_year_safe(days, key):
    start = date(2027, 12, 30)
    end = start + timedelta(days=days)
    events = generate_events(key, start, end)
    assert all(start <= e["start_on"] <= e["end_on"] <= end for e in events)
    stages = [e for e in events if e["kind"] == "stage"]
    assert stages[0]["start_on"] == start and stages[-1]["end_on"] == end
    assert all(a["end_on"] + timedelta(days=1) == b["start_on"] for a, b in zip(stages, stages[1:]))


def test_original_source_hashes_and_references_are_complete():
    index = {s["id"]: s for s in source_index()}
    assert len(index) == 6
    root = Path(__file__).resolve().parents[1]
    for source in index.values():
        assert source["status"] == "ok"
        assert source["url"].startswith("https://")
        assert hashlib.sha256((root / source["snapshot"]).read_bytes()).hexdigest() == source["sha256"]
    for template in TEMPLATES:
        assert all(set(e["source_ids"]) <= index.keys() for e in template["events"])


def test_source_refresh_does_not_rewrite_existing_plans(system, monkeypatch):
    farmer, user = account(system, "farmer_a")
    plan = create_plan(farmer)
    from app.services import farm_content
    monkeypatch.setattr(farm_content, "source_index", lambda: [])
    persisted = farmer.get(f"/api/v1/planning/farmers/{user['id']}").json()["plans"][0]
    assert persisted["sources"] == plan["sources"] and persisted["events"] == plan["events"]
    response = farmer.post("/api/v1/planning/plans", json=dict(crop_key="rice", crop_name="水稻", field_name="甲", planted_on="2026-01-01", end_on="2026-05-01"))
    assert response.status_code == 503


def test_demo_has_no_database_side_effects(system):
    client = TestClient(system[0])
    response = client.get("/api/v1/planning/example")
    assert response.status_code == 200
    assert response.json()["farmer"]["id"] == "demo"
    with system[1]() as db:
        assert db.scalar(select(FarmAccount)) is None
        assert db.scalar(select(FarmPlan)) is None


@pytest.mark.parametrize("changes", [{"field_name": "   "}, {"end_on": "2026-01-01"}, {"role": "doctor"}, {"crop_key": "unknown"}, {"planted_on": "not-a-date"}])
def test_invalid_plan_is_rejected(system, changes):
    farmer, _ = account(system, "farmer_a")
    response = farmer.post("/api/v1/planning/plans", json=dict(crop_key="rice", crop_name="水稻", field_name="甲", planted_on="2026-01-01", end_on="2026-05-01") | changes)
    assert response.status_code == 422


def test_export_filters_and_blocks_other_farmers(system):
    farmer, owner = account(system, "farmer_a")
    other, _ = account(system, "farmer_b")
    rice = create_plan(farmer)
    create_plan(farmer, crop_key="tomato", crop_name="番茄")
    url = f"/api/v1/planning/farmers/{owner['id']}/diagram.svg?plan_ids={rice['id']}&kinds=stage,pest"
    assert other.get(url).status_code == 404
    result = farmer.get(url)
    assert result.status_code == 200 and result.headers["content-type"].startswith("image/svg+xml")
    assert result.headers["cache-control"] == "no-store"
    tree = ElementTree.fromstring(result.text)
    assert "稻熱病" in result.text and "番茄" not in result.text
    assert "農事管理" not in result.text
    assert farmer.get(url.replace(rice['id'], 'unknown')).status_code == 422


def test_export_escapes_user_content(system):
    farmer, owner = account(system, "farmer_a")
    plan = create_plan(farmer, field_name='<script>alert("x")</script> & 甲\u0001')
    result = farmer.get(f"/api/v1/planning/farmers/{owner['id']}/diagram.svg?plan_ids={plan['id']}")
    tree = ElementTree.fromstring(result.text)
    assert not tree.findall('.//{http://www.w3.org/2000/svg}script')
    assert '<script>' not in result.text and '&lt;script&gt;' in result.text


def test_password_whitespace_is_not_silently_removed(system):
    client = TestClient(system[0], headers={"X-Planning-Request": "1"})
    assert client.post('/api/v1/planning/register', json=dict(username='space_user', password=' a-strong-password ', name='test', role='farmer')).status_code == 200
    client.post('/api/v1/planning/logout')
    assert client.post('/api/v1/planning/login', json=dict(username='space_user', password='a-strong-password')).status_code == 401
    assert client.post('/api/v1/planning/login', json=dict(username='space_user', password=' a-strong-password ')).status_code == 200


def test_weather_county_only_owner_can_save_and_rejects_unknown(system):
    farmer, farmer_data=account(system,'weather-farmer')
    doctor,_=account(system,'weather-doctor','doctor')
    url='/api/v1/planning/weather-county'
    assert TestClient(system[0]).patch(url,json={'county':'臺南市'}).status_code in (401,403)
    assert doctor.patch(url,json={'county':'臺南市'}).status_code==403
    assert farmer.patch(url,json={'county':'未知縣'}).status_code==422
    response=farmer.patch(url,json={'county':'臺南市'})
    assert response.status_code==200 and response.json()['weather_county']=='臺南市'
    other,_=account(system,'weather-other')
    assert other.get('/api/v1/planning/me').json()['weather_county'] is None


# ---- 代建農民帳號（第四輪第 5 步） ----

def create_farmer(client, **changes):
    payload = dict(name="陳示範", farm_name="示範農場", location="高雄市大社區") | changes
    return client.post("/api/v1/planning/farmers", json=payload)


def test_only_doctor_or_admin_can_create_a_farmer_account(system):
    farmer, _ = account(system, "self-farmer")
    assert create_farmer(farmer).status_code == 403
    doctor, _ = account(system, "doc-a", role="doctor")
    assert create_farmer(doctor).status_code == 201


def test_created_farmer_is_linked_and_gets_a_one_time_password(system):
    doctor, _ = account(system, "doc-b", role="doctor")
    body = create_farmer(doctor).json()
    assert body["role"] == "farmer"
    assert body["must_change_password"] is True
    assert len(body["one_time_password"]) >= 10
    assert body["otp_expires_at"]
    # 自動連到建立者，而且標成 created（農民管理要顯示「你建立的」）。
    links = doctor.get("/api/v1/planning/links").json()
    assert [l["id"] for l in links] == [body["id"]]
    assert links[0]["link_source"] == "created"
    assert links[0]["invite"]["state"] == "pending"


def test_one_time_password_is_never_readable_again(system):
    """明文只在建立那一次的回應裡出現；之後任何查詢都拿不到。"""
    doctor, _ = account(system, "doc-c", role="doctor")
    body = create_farmer(doctor).json()
    dumped = str(doctor.get("/api/v1/planning/links").json())
    assert body["one_time_password"] not in dumped


def test_location_is_required_because_region_and_weather_need_it(system):
    doctor, _ = account(system, "doc-d", role="doctor")
    assert create_farmer(doctor, location="").status_code == 422


def test_created_farmer_must_change_password_then_the_flag_clears(system):
    doctor, _ = account(system, "doc-e", role="doctor")
    body = create_farmer(doctor).json()
    farmer = TestClient(system[0], headers={"X-Planning-Request": "1"})
    login = farmer.post("/api/v1/planning/login",
                        json=dict(username=body["username"], password=body["one_time_password"]))
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    changed = farmer.post("/api/v1/planning/me/password", json=dict(password="farmer-own-password"))
    assert changed.status_code == 200 and changed.json()["must_change_password"] is False
    # 舊的一次性密碼從此失效。
    again = TestClient(system[0], headers={"X-Planning-Request": "1"})
    assert again.post("/api/v1/planning/login",
                      json=dict(username=body["username"], password=body["one_time_password"])).status_code == 401


def test_reset_otp_refuses_once_the_farmer_owns_the_password(system):
    """農民設過自己的密碼之後，別人不可以再替他重設——那是帳號易主。"""
    doctor, _ = account(system, "doc-f", role="doctor")
    body = create_farmer(doctor).json()
    # 還沒改密碼：可以重發，而且是新的一組。
    first = doctor.post(f"/api/v1/planning/farmers/{body['id']}/reset-otp")
    assert first.status_code == 200
    assert first.json()["one_time_password"] != body["one_time_password"]

    farmer = TestClient(system[0], headers={"X-Planning-Request": "1"})
    farmer.post("/api/v1/planning/login",
                json=dict(username=body["username"], password=first.json()["one_time_password"]))
    farmer.post("/api/v1/planning/me/password", json=dict(password="farmer-own-password"))
    assert doctor.post(f"/api/v1/planning/farmers/{body['id']}/reset-otp").status_code == 409


def test_reset_otp_is_scoped_to_your_own_linked_farmers(system):
    doctor, _ = account(system, "doc-g", role="doctor")
    other, _ = account(system, "doc-h", role="doctor")
    body = create_farmer(doctor).json()
    assert other.post(f"/api/v1/planning/farmers/{body['id']}/reset-otp").status_code == 403
    farmer_client, _ = account(system, "nosy-farmer")
    assert farmer_client.post(f"/api/v1/planning/farmers/{body['id']}/reset-otp").status_code == 403


def test_reset_otp_kills_existing_sessions(system):
    """重發等於換密碼：拿著舊密碼登入的人要被踢出去。"""
    doctor, _ = account(system, "doc-i", role="doctor")
    body = create_farmer(doctor).json()
    farmer = TestClient(system[0], headers={"X-Planning-Request": "1"})
    farmer.post("/api/v1/planning/login", json=dict(username=body["username"], password=body["one_time_password"]))
    assert farmer.get("/api/v1/planning/me").status_code == 200
    doctor.post(f"/api/v1/planning/farmers/{body['id']}/reset-otp")
    assert farmer.get("/api/v1/planning/me").status_code == 401


def test_farmer_can_unlink_a_doctor_who_created_the_account(system):
    """代建不等於永久授權：農民本人隨時可以解除連結。"""
    doctor, _ = account(system, "doc-j", role="doctor")
    body = create_farmer(doctor).json()
    farmer = TestClient(system[0], headers={"X-Planning-Request": "1"})
    farmer.post("/api/v1/planning/login", json=dict(username=body["username"], password=body["one_time_password"]))
    farmer.post("/api/v1/planning/me/password", json=dict(password="farmer-own-password"))
    assert farmer.delete(f"/api/v1/planning/links/{doctor.get('/api/v1/planning/me').json()['id']}").status_code == 200
    assert doctor.get("/api/v1/planning/links").json() == []


def test_doctor_cannot_assign_the_account_to_someone_else(system):
    """只有 admin 能指定 doctor_id；植物醫生一律連到自己。"""
    doctor, _ = account(system, "doc-k", role="doctor")
    other, other_me = account(system, "doc-l", role="doctor")
    body = create_farmer(doctor, doctor_id=other_me["id"]).json()
    assert [l["id"] for l in doctor.get("/api/v1/planning/links").json()] == [body["id"]]
    assert other.get("/api/v1/planning/links").json() == []


def test_usernames_do_not_collide(system):
    doctor, _ = account(system, "doc-m", role="doctor")
    first = create_farmer(doctor, name="Chen Ming").json()["username"]
    second = create_farmer(doctor, name="Chen Ming").json()["username"]
    assert first != second
    # 指定重複的帳號要擋下來，不要靜默改成別的。
    assert create_farmer(doctor, username=first).status_code == 409
