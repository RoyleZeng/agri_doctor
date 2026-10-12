"""農事日誌 API 的離線測試：記憶體 SQLite，不連 PostgreSQL、不碰官方來源。

資料一律用試驗值；設計稿上的藥名、數字、日期是示意，不抄進測試。
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.v1 import farm_logs, planning
from app.core.config import settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (ActiveIngredient, Crop, Pest, Product, ProductIngredient, Registration, SnapshotStatus,
                        SourceName, SourceSnapshot)
from app.models.consult import ApplicationRecord, ConsultCase, PrescriptionItem
from app.models.farm_log import FarmLogPhoto, FarmWorkLog
from app.models.planning import DoctorFarmer, FarmAccount, FarmEvent, FarmPlan, FarmSession
from app.services.farm_log import COMPLIANCE_RULE_VERSION, compliance, default_title, within_edit_window


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kw):
    return "JSON"


TODAY = date(2030, 3, 10)
START = datetime(2030, 3, 10, 1, 0, tzinfo=timezone.utc)
URL = "/api/v1/planning/farmers/farmer/logs"


@pytest.fixture
def system(monkeypatch, tmp_path):
    # 照片寫到每個測試自己的暫存目錄，不碰 repo 的 data/media。
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    app = FastAPI()
    for router in (planning.router, farm_logs.router):
        app.include_router(router, prefix="/api/v1")

    def session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    clock = {"now": START}
    monkeypatch.setattr(farm_logs, "utcnow", lambda: clock["now"])
    monkeypatch.setattr(farm_logs, "taipei_today", lambda: TODAY)

    accounts = [("farmer", "farmer"), ("stranger", "farmer"), ("doc", "doctor"), ("doc2", "doctor"), ("unlinked", "doctor")]
    with factory() as db:
        for account_id, role in accounts:
            db.add(FarmAccount(id=account_id, username=account_id, name=account_id, role=role, password_hash="unused"))
        db.flush()
        for account_id, _ in accounts:
            db.add(FarmSession(token_hash=planning.digest(account_id + "-token"), account_id=account_id,
                               expires_at=int(time.time()) + 10000))
        db.add_all([DoctorFarmer(doctor_id="doc", farmer_id="farmer"), DoctorFarmer(doctor_id="doc2", farmer_id="farmer")])
        db.add_all([
            FarmPlan(id="plan", farmer_id="farmer", crop_name="試驗作物甲", field_name="試驗田甲", area="0.4 公頃",
                     planted_on=date(2030, 1, 1), end_on=date(2030, 6, 30)),
            FarmPlan(id="plan-b", farmer_id="farmer", crop_name="試驗作物乙", field_name="試驗田乙",
                     planted_on=date(2030, 1, 1), end_on=date(2030, 6, 30)),
            FarmPlan(id="other-plan", farmer_id="stranger", crop_name="試驗作物丙", field_name="試驗田丙",
                     planted_on=date(2030, 1, 1), end_on=date(2030, 6, 30)),
        ])
        db.flush()
        window = dict(start_on=date(2030, 3, 5), end_on=date(2030, 3, 15))
        db.add_all([
            FarmEvent(id="care", plan_id="plan", kind="care", title="試驗作業甲", **window),
            FarmEvent(id="stage", plan_id="plan", kind="stage", title="試驗生育期", **window),
            FarmEvent(id="care-b", plan_id="plan-b", kind="care", title="試驗作業乙", **window),
            FarmEvent(id="other-care", plan_id="other-plan", kind="care", title="試驗作業丙", **window),
            Pest(id=1, name="試驗害蟲甲"),
            ConsultCase(id="case", doctor_id="doc", farmer_id="farmer", farmer_text="試驗農民", case_no="T-1", crop_text="試驗作物甲",
                        field_text="試驗田甲", status="saved"),
            ConsultCase(id="other-case", doctor_id="doc", farmer_id="stranger", farmer_text="其他農民", case_no="T-2", crop_text="試驗作物丙",
                        status="saved"),
            ConsultCase(id="draft-case", doctor_id="doc", farmer_id="farmer", farmer_text="試驗農民", case_no="T-3", crop_text="試驗作物甲"),
            ConsultCase(id="doc2-case", doctor_id="doc2", farmer_id="farmer", farmer_text="試驗農民", case_no="T-4", crop_text="試驗作物甲",
                        status="issued"),
        ])
        db.flush()
        prescription = dict(ingredient_text="試驗成分", moa_text="FRAC 7 | IRAC 8", dilution="2345", dose="0.4公升",
                            source_values={"moa_codes": ["FRAC 7", "IRAC 8"]}, checks=[])
        db.add_all([
            PrescriptionItem(id="rx", case_id="case", product_text="13% 試驗藥劑甲 試驗劑型", **prescription),
            PrescriptionItem(id="other-rx", case_id="other-case", product_text="13% 試驗藥劑甲 試驗劑型", **prescription),
            PrescriptionItem(id="draft-rx", case_id="draft-case", product_text="13% 試驗藥劑甲 試驗劑型", **prescription),
            PrescriptionItem(id="doc2-rx", case_id="doc2-case", product_text="13% 試驗藥劑甲 試驗劑型", **prescription),
        ])
        # 藥劑登記：與規劃作物名稱相同的標準作物，另一作物的登記，以及不是正式快照的登記。
        now = datetime.now(timezone.utc)
        db.add_all([Crop(id=1, name="試驗作物甲"), Crop(id=2, name="試驗作物乙"), Pest(id=2, name="試驗病害乙"),
                    SourceSnapshot(id=1, source=SourceName.PPM, status=SnapshotStatus.SUCCESS, is_active=True, row_count=3, started_at=now),
                    SourceSnapshot(id=2, source=SourceName.PPM, status=SnapshotStatus.SUCCESS, is_active=False, row_count=1, started_at=now),
                    SourceSnapshot(id=3, source=SourceName.FDA, status=SnapshotStatus.SUCCESS, is_active=True, row_count=1, started_at=now),
                    ActiveIngredient(id=1, name="試驗成分甲", english_name="synthetic-alpha"),
                    Product(id=1, name="試驗單劑", concentration_text="13%", formulation="試驗劑型",
                            english_composition="synthetic-alpha", moa_text="FRAC 7 | IRAC 8")])
        db.flush()
        db.add(ProductIngredient(product_id=1, ingredient_id=1))
        for reg_id, crop_id, snapshot_id in [(1, 1, 1), (2, 2, 1), (3, 1, 2)]:
            db.add(Registration(id=reg_id, crop_id=crop_id, pest_id=2, product_id=1, snapshot_id=snapshot_id,
                                usage_hash=f"{reg_id:064d}", dilution_text="2345", dose_per_hectare="0.42公升", phi_days=4))
        db.commit()

    clients = {}
    for account_id, _ in accounts:
        client = TestClient(app, headers={"X-Planning-Request": "1"})
        client.cookies.set(planning.COOKIE, account_id + "-token", path="/api/v1")
        clients[account_id] = client
    yield app, factory, clients, clock
    engine.dispose()


def spray(**changes):
    return dict(kind="spray", done_on="2030-03-09", field_text="試驗田甲", material_name="試驗藥劑乙", dilution="1000",
                amount_value="0.3", amount_unit="公升", area_text="0.4 公頃") | changes


def count(factory, model):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(model))


def test_logs_require_login_csrf_and_farm_access(system):
    app, factory, clients, _ = system
    assert TestClient(app).get(URL).status_code == 401
    assert clients["farmer"].post(URL, json=spray(), headers={"X-Planning-Request": "0"}).status_code == 403
    for outsider in ("stranger", "unlinked"):
        assert clients[outsider].get(URL).status_code == 404
        assert clients[outsider].post(URL, json=spray()).status_code == 404
    created = clients["doc"].post(URL, json=spray())
    assert created.status_code == 201, created.text
    assert created.json()["created_by"] == "doc"
    assert clients["farmer"].get(URL).json()["total"] == 1
    assert clients["stranger"].get("/api/v1/planning/farmers/stranger/logs").json()["total"] == 0


def test_spray_from_prescription_writes_application_record_with_snapshot_moa(system):
    _, factory, clients, _ = system
    response = clients["doc"].post(URL, json=dict(kind="spray", done_on="2030-03-09", plan_id="plan", prescription_item_id="rx"))
    assert response.status_code == 201, response.text
    log = response.json()
    assert (log["material_name"], log["dilution"], log["field_text"], log["crop_text"]) == \
        ("13% 試驗藥劑甲 試驗劑型", "2345", "試驗田甲", "試驗作物甲")
    assert log["title"] == "施藥 · 13% 試驗藥劑甲 試驗劑型 2345 倍"
    with factory() as db:
        record = db.scalar(select(ApplicationRecord))
        assert record.id == log["application_record_id"] and record.work_log_id == log["id"]
        assert record.moa_codes == ["FRAC 7", "IRAC 8"]
        assert (record.farmer_id, record.plan_id, record.case_id, record.field_text, record.product_text, record.applied_on) == \
            ("farmer", "plan", "case", "試驗田甲", "13% 試驗藥劑甲 試驗劑型", date(2030, 3, 9))
    # 處方之後被改，已存的日誌與施藥紀錄仍是存檔當下的快照。
    with factory() as db:
        item = db.get(PrescriptionItem, "rx")
        item.product_text, item.source_values = "上游改名的藥劑", {"moa_codes": ["FRAC 99"]}
        db.commit()
    assert clients["farmer"].get(URL).json()["items"][0]["material_name"] == "13% 試驗藥劑甲 試驗劑型"
    with factory() as db:
        assert db.scalar(select(ApplicationRecord)).moa_codes == ["FRAC 7", "IRAC 8"]


def test_manual_spray_does_not_guess_moa_and_other_kinds_write_no_application_record(system):
    _, factory, clients, _ = system
    assert clients["farmer"].post(URL, json=spray()).status_code == 201
    with factory() as db:
        record = db.scalar(select(ApplicationRecord))
        # 手動輸入的藥名不反查登記：字串比對會把單劑配成複方，寧可空著也不餵錯的作用機制。
        assert record.moa_codes == [] and record.case_id is None
    for kind in ("fertilize", "irrigate", "harvest", "scout"):
        response = clients["farmer"].post(URL, json=dict(kind=kind, done_on="2030-03-09", field_text="試驗田甲"))
        assert response.status_code == 201, response.text
        assert response.json()["application_record_id"] is None
    assert count(factory, ApplicationRecord) == 1


def test_event_log_completes_event_keeps_basis_bumps_version_and_blocks_duplicates(system):
    _, factory, clients, _ = system
    farmer = clients["farmer"]
    response = farmer.post(URL, json=dict(kind="fieldwork", done_on="2030-03-09", event_id="care", labor_text="2 人"))
    assert response.status_code == 201, response.text
    log = response.json()
    assert (log["plan_id"], log["event_id"], log["field_text"], log["crop_text"], log["area_text"], log["title"]) == \
        ("plan", "care", "試驗田甲", "試驗作物甲", "0.4 公頃", "田間作業 · 試驗作業甲")
    with factory() as db:
        completed = db.get(FarmEvent, "care")
        # basis 記的是日期與內容的出處，記錄完成不應把「範本估計」蓋掉。
        assert completed.completed and completed.basis == "template_estimate" and completed.updated_by == "farmer"
        assert db.get(FarmPlan, "plan").version == 2
    again = dict(kind="fieldwork", done_on="2030-03-09")
    assert farmer.post(URL, json=again | dict(event_id="care")).status_code == 409
    assert farmer.post(URL, json=again | dict(event_id="stage")).status_code == 422
    assert farmer.post(URL, json=again | dict(event_id="care-b", plan_id="plan")).status_code == 422
    assert farmer.post(URL, json=again | dict(event_id="other-care")).status_code == 404
    assert farmer.post(URL, json=again | dict(event_id="missing")).status_code == 404
    assert count(factory, FarmWorkLog) == 1


@pytest.mark.parametrize("changes,status", [
    (dict(done_on="2030-03-11"), 422),                       # 還沒發生的事屬於規劃
    (dict(material_name=""), 422),                           # 施藥沒有藥名，輪替檢查讀不到
    (dict(field_text=""), 422),                              # 沒有田區也沒有規劃可帶
    (dict(done_time="25:00"), 422),
    (dict(kind="unknown"), 422),
    (dict(kind="fertilize", prescription_item_id="rx"), 422),
    (dict(prescription_item_id="other-rx"), 404),            # 別的農場的處方
    (dict(plan_id="other-plan"), 404),
    (dict(pest_id=999), 422),
    (dict(recommendation_id="made-up"), 404),                # 建議不存在（第 6 步：精準模式「已執行」流程見 test_precision_api.py）
])
def test_invalid_logs_are_rejected_without_writes(system, changes, status):
    _, factory, clients, _ = system
    response = clients["farmer"].post(URL, json=spray() | changes)
    assert response.status_code == status, response.text
    assert count(factory, FarmWorkLog) == 0
    assert count(factory, ApplicationRecord) == 0


def test_compliance_is_computed_and_stored_at_save(system):
    _, _, clients, _ = system
    fertilize = clients["farmer"].post(URL, json=dict(kind="fertilize", done_on="2030-03-09", field_text="試驗田甲",
                                                      material_name="試驗肥料", amount_value="20", amount_unit="公斤"))
    stored = fertilize.json()["compliance"]
    assert stored["rule_version"] == COMPLIANCE_RULE_VERSION
    assert stored["missing"] == ["material_supplier", "material_lot", "area_text"]
    harvest = clients["farmer"].post(URL, json=dict(kind="harvest", done_on="2030-03-09", plan_id="plan",
                                                    yield_value="120", yield_unit="公斤"))
    assert harvest.json()["compliance"]["missing"] == []


@pytest.mark.parametrize("kind,values,missing", [
    ("spray", {}, ["material_name", "material_supplier", "material_lot", "amount_value", "amount_unit", "area_text"]),
    ("harvest", {"crop_text": "試驗作物", "yield_value": "1"}, ["area_text", "yield_unit"]),
    ("irrigate", {}, []),
    ("fertilize", {"material_name": "  "}, ["material_name", "material_supplier", "material_lot", "amount_value", "amount_unit", "area_text"]),
])
def test_compliance_rules(kind, values, missing):
    assert compliance(kind, values)["missing"] == missing


def test_default_title_and_edit_window():
    assert default_title("spray", material_name="試驗藥劑", dilution="1,000") == "施藥 · 試驗藥劑 1,000 倍"
    assert default_title("spray", material_name="試驗藥劑", dilution="1000倍") == "施藥 · 試驗藥劑 1000倍"
    assert default_title("fieldwork", fallback="試驗作業") == "田間作業 · 試驗作業"
    assert default_title("irrigate") == "灌溉"
    stored = datetime(2030, 1, 1, 0, 0)  # SQLite 讀回沒有時區
    assert within_edit_window(stored, datetime(2030, 1, 2, 0, 0, tzinfo=timezone.utc))
    assert not within_edit_window(stored, datetime(2030, 1, 2, 0, 1, tzinfo=timezone.utc))


def test_edit_window_syncs_application_record_then_locks_to_note_only(system):
    _, factory, clients, clock = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray()).json()
    url = f"/api/v1/planning/logs/{log['id']}"
    edited = farmer.patch(url, json=dict(done_on="2030-03-08", field_text="試驗田乙", material_name="試驗藥劑丙", title=""))
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "施藥 · 試驗藥劑丙 1000 倍"
    with factory() as db:
        record = db.scalar(select(ApplicationRecord))
        assert (record.applied_on, record.field_text, record.product_text) == (date(2030, 3, 8), "試驗田乙", "試驗藥劑丙")
    assert farmer.patch(url, json=dict(kind="fertilize")).status_code == 200
    assert count(factory, ApplicationRecord) == 0
    assert farmer.patch(url, json=dict(kind="spray")).status_code == 200
    assert count(factory, ApplicationRecord) == 1
    assert farmer.patch(url, json=dict(done_on=None)).status_code == 422

    clock["now"] = START + timedelta(hours=24, minutes=1)
    assert farmer.get(URL).json()["items"][0]["locked"]
    assert farmer.patch(url, json=dict(dilution="500")).status_code == 409
    assert farmer.delete(url).status_code == 409
    noted = farmer.patch(url, json=dict(note="更正說明"))
    assert noted.status_code == 200
    assert noted.json()["note"] == "更正說明" and noted.json()["dilution"] == "1000"


def test_delete_within_window_removes_application_record_but_event_stays_completed(system):
    _, factory, clients, _ = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray(event_id="care")).json()
    assert count(factory, ApplicationRecord) == 1
    assert farmer.delete(f"/api/v1/planning/logs/{log['id']}").status_code == 200
    assert count(factory, FarmWorkLog) == 0 and count(factory, ApplicationRecord) == 0
    with factory() as db:
        # 刪的是記錯的紀錄，不代表工作沒做；事項留在已完成、未記錄，讓人補記。
        assert db.get(FarmEvent, "care").completed
    assert farmer.post(URL, json=spray(event_id="care")).status_code == 201


def test_only_farmer_or_original_author_can_modify(system):
    _, _, clients, _ = system
    log = clients["doc"].post(URL, json=spray()).json()
    url = f"/api/v1/planning/logs/{log['id']}"
    assert clients["doc2"].patch(url, json=dict(note="越權")).status_code == 403
    assert clients["doc2"].delete(url).status_code == 403
    assert clients["stranger"].patch(url, json=dict(note="越權")).status_code == 404
    assert clients["unlinked"].delete(url).status_code == 404
    assert clients["farmer"].patch(url, json=dict(note="農民補充")).status_code == 200
    assert clients["doc"].patch(url, json=dict(note="醫師補充")).status_code == 200


def test_list_filters_orders_and_scopes_to_farm(system):
    _, _, clients, _ = system
    farmer = clients["farmer"]
    for payload in [spray(done_on="2030-03-01"),
                    dict(kind="irrigate", done_on="2030-03-05", field_text="試驗田乙"),
                    spray(done_on="2030-03-05", done_time="06:30"),
                    spray(done_on="2030-03-05", done_time="17:00")]:
        assert farmer.post(URL, json=payload).status_code == 201
    assert clients["stranger"].post("/api/v1/planning/farmers/stranger/logs", json=spray(field_text="試驗田丙")).status_code == 201
    body = farmer.get(URL).json()
    assert body["total"] == 4
    # 同一天先排有時間的（晚到早），沒填時間的排後面；PostgreSQL 與 SQLite 的 NULL 排序不同，要明寫。
    assert [(i["done_on"], i["done_time"]) for i in body["items"]] == \
        [("2030-03-05", "17:00"), ("2030-03-05", "06:30"), ("2030-03-05", None), ("2030-03-01", None)]
    assert farmer.get(URL, params={"kind": "spray", "from": "2030-03-02"}).json()["total"] == 2
    assert farmer.get(URL, params={"field": "試驗田乙"}).json()["total"] == 1
    assert farmer.get(URL, params={"to": "2030-03-01"}).json()["total"] == 1
    assert farmer.get(URL, params={"from": "2030-03-06", "to": "2030-03-01"}).status_code == 422


def test_scout_pest_name_is_snapshotted(system):
    _, factory, clients, _ = system
    log = clients["farmer"].post(URL, json=dict(kind="scout", done_on="2030-03-09", field_text="試驗田甲", pest_id=1)).json()
    assert log["pest_text"] == "試驗害蟲甲"
    with factory() as db:
        db.get(Pest, 1).name = "上游改名的害蟲"
        db.commit()
    assert clients["farmer"].get(URL).json()["items"][0]["pest_text"] == "試驗害蟲甲"


# --------------------------------------------------------------------------
# 照片上傳與受權限保護的 media 端點
# --------------------------------------------------------------------------

MEDIA = Path(__file__).parent / "fixtures" / "media"


def upload(client, log_id, name="exif_gps_rotated.jpg", **data):
    content = (MEDIA / name).read_bytes()
    return client.post(f"/api/v1/planning/logs/{log_id}/photos", files={"file": (name, content, "application/octet-stream")},
                       data=data)


def media_files():
    root = settings.media_root
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_photo_upload_is_listed_and_served_only_to_people_who_can_see_the_farm(system):
    app, factory, clients, _ = system
    log = clients["farmer"].post(URL, json=spray()).json()
    response = upload(clients["farmer"], log["id"], caption="試驗說明")
    assert response.status_code == 201, response.text
    photo = response.json()
    assert (photo["width"], photo["height"], photo["caption"], photo["sort_order"]) == (40, 64, "試驗說明", 0)
    assert photo["lat"] == pytest.approx(22.7584722) and photo["taken_from_exif"] and photo["taken_at_source"] == "exif"
    assert photo["taken_at"].startswith("2030-03-08T22:15:30")
    # 存放路徑依農場與上傳年月分，原檔與縮圖各一個。
    stored = media_files()
    assert len(stored) == 2 and all(p.startswith("farmer/2030/03/") for p in stored)

    listed = clients["farmer"].get(URL).json()["items"][0]["photos"]
    assert [p["id"] for p in listed] == [photo["id"]] and "storage_key" not in listed[0]
    assert listed[0]["taken_at_source"] == "exif"  # 來源存進資料庫，列表讀到的跟上傳當下一致

    original = clients["doc"].get(photo["url"])
    assert original.status_code == 200
    assert original.content == (MEDIA / "exif_gps_rotated.jpg").read_bytes()
    assert original.headers["content-type"] == "image/jpeg"
    assert original.headers["cache-control"] == "private, no-cache"
    assert original.headers["x-content-type-options"] == "nosniff"
    thumb = clients["farmer"].get(photo["thumb_url"])
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/webp"

    assert TestClient(app).get(photo["url"]).status_code == 401
    for outsider in ("stranger", "unlinked"):
        assert clients[outsider].get(photo["url"]).status_code == 404
    assert clients["farmer"].get(photo["url"] + "?variant=raw").status_code == 422
    # 解除連結後植物醫生就看不到，即使之前看過同一張。
    with factory() as db:
        db.delete(db.get(DoctorFarmer, ("doc", "farmer")))
        db.commit()
    assert clients["doc"].get(photo["url"]).status_code == 404


def test_heic_upload_is_stored_and_served_as_jpeg(system):
    _, _, clients, _ = system
    log = clients["farmer"].post(URL, json=spray()).json()
    photo = upload(clients["farmer"], log["id"], "phone.heic").json()
    assert (photo["lat"], photo["lng"]) == (-23.5, -46.25)
    assert any(p.endswith(".jpg") for p in media_files())
    served = clients["farmer"].get(photo["url"])
    assert served.headers["content-type"] == "image/jpeg" and served.content[:3] == b"\xff\xd8\xff"


@pytest.mark.parametrize("name,status", [("not_an_image.jpg", 415), ("animated.gif", 415)])
def test_invalid_upload_writes_nothing(system, name, status):
    _, factory, clients, _ = system
    log = clients["farmer"].post(URL, json=spray()).json()
    response = upload(clients["farmer"], log["id"], name)
    assert response.status_code == status, response.text
    assert count(factory, FarmLogPhoto) == 0 and media_files() == []


def test_upload_limits_permissions_and_csrf(system, monkeypatch):
    _, factory, clients, _ = system
    log = clients["doc"].post(URL, json=spray()).json()
    assert upload(clients["doc2"], log["id"]).status_code == 403
    assert upload(clients["stranger"], log["id"]).status_code == 404
    content = (MEDIA / "no_exif.png").read_bytes()
    no_csrf = clients["farmer"].post(f"/api/v1/planning/logs/{log['id']}/photos",
                                     files={"file": ("a.png", content, "image/png")}, headers={"X-Planning-Request": "0"})
    assert no_csrf.status_code == 403
    assert upload(clients["farmer"], "missing-log").status_code == 404

    monkeypatch.setattr(settings, "media_max_bytes", 500)
    assert upload(clients["farmer"], log["id"], "large.jpg").status_code == 413
    monkeypatch.setattr(settings, "media_max_bytes", 10 * 1024 * 1024)
    monkeypatch.setattr(settings, "media_max_photos_per_log", 1)
    assert upload(clients["farmer"], log["id"], "no_exif.png").status_code == 201
    assert upload(clients["farmer"], log["id"], "no_exif.png").status_code == 409
    assert count(factory, FarmLogPhoto) == 1


def test_photos_can_be_added_after_edit_window_and_deletion_is_soft(system):
    _, factory, clients, clock = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray()).json()
    clock["now"] = START + timedelta(days=3)
    # 先存日誌、照片之後補：超過 24 小時一樣要能補上。
    first = upload(farmer, log["id"], "no_exif.png")
    assert first.status_code == 201
    assert first.json()["taken_at"].startswith("2030-03-13T01:00") and not first.json()["taken_from_exif"] and first.json()["taken_at_source"] == "upload"
    second = upload(farmer, log["id"]).json()
    assert second["sort_order"] == 1

    url = f"/api/v1/planning/logs/{log['id']}/photos/{first.json()['id']}"
    assert clients["stranger"].delete(url).status_code == 404
    assert farmer.delete(url).status_code == 200
    assert farmer.delete(url).status_code == 404
    assert farmer.get(first.json()["url"]).status_code == 404
    assert [p["id"] for p in farmer.get(URL).json()["items"][0]["photos"]] == [second["id"]]
    with factory() as db:
        assert db.get(FarmLogPhoto, first.json()["id"]).deleted_at is not None
    assert len(media_files()) == 4  # 軟刪不動實體檔，交給清理排程
    other = farmer.post(URL, json=spray()).json()
    assert farmer.delete(f"/api/v1/planning/logs/{other['id']}/photos/{second['id']}").status_code == 404


def test_deleting_log_within_window_removes_photo_rows(system):
    _, factory, clients, _ = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray()).json()
    photo = upload(farmer, log["id"]).json()
    assert farmer.delete(f"/api/v1/planning/logs/{log['id']}").status_code == 200
    assert count(factory, FarmLogPhoto) == 0
    assert farmer.get(photo["url"]).status_code == 404


def test_trashed_photos_are_listed_with_thumbnail_and_restorable_within_trash_period(system):
    _, _, clients, clock = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray()).json()
    photo = upload(farmer, log["id"], "no_exif.png").json()
    base = f"/api/v1/planning/logs/{log['id']}/photos/{photo['id']}"
    assert farmer.post(base + "/restore").status_code == 404  # 沒刪過的照片不能還原
    assert farmer.delete(base).status_code == 200

    item = farmer.get(URL).json()["items"][0]
    assert item["photos"] == []
    trashed = item["trashed_photos"][0]
    assert trashed["id"] == photo["id"] and trashed["taken_at_source"] == "upload" and "url" not in trashed
    assert trashed["restorable_until"].startswith("2030-04-09T01:00")
    # 保留期內給縮圖讓人認得要還原哪一張；原檔還原前看不到，別人也看不到縮圖。
    assert farmer.get(trashed["thumb_url"]).status_code == 200
    assert farmer.get(photo["url"]).status_code == 404
    assert clients["stranger"].get(trashed["thumb_url"]).status_code == 404
    assert clients["doc2"].post(base + "/restore").status_code == 403

    clock["now"] = START + timedelta(days=29)  # 日誌早已鎖定，照片仍可還原
    restored = farmer.post(base + "/restore")
    assert restored.status_code == 200 and restored.json()["id"] == photo["id"]
    assert farmer.get(photo["url"]).status_code == 200
    body = farmer.get(URL).json()["items"][0]
    assert [p["id"] for p in body["photos"]] == [photo["id"]] and body["trashed_photos"] == []


def test_restore_refused_when_log_is_full_files_are_gone_or_trash_period_passed(system, monkeypatch):
    _, factory, clients, clock = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray()).json()
    first = upload(farmer, log["id"], "no_exif.png").json()
    second = upload(farmer, log["id"], "no_exif.png").json()
    url = lambda p: f"/api/v1/planning/logs/{log['id']}/photos/{p['id']}"
    assert farmer.delete(url(first)).status_code == 200
    assert farmer.delete(url(second)).status_code == 200

    # 還原不能繞過每筆紀錄的張數上限。
    monkeypatch.setattr(settings, "media_max_photos_per_log", 1)
    assert upload(farmer, log["id"], "no_exif.png").status_code == 201
    assert farmer.post(url(first) + "/restore").status_code == 409
    monkeypatch.setattr(settings, "media_max_photos_per_log", 20)

    with factory() as db:
        key = db.get(FarmLogPhoto, first["id"]).storage_key
    (settings.media_root / key).unlink()
    assert farmer.post(url(first) + "/restore").status_code == 409
    with factory() as db:
        assert db.get(FarmLogPhoto, first["id"]).deleted_at is not None

    clock["now"] = START + timedelta(days=30)
    assert farmer.post(url(second) + "/restore").status_code == 409
    assert farmer.get(URL).json()["items"][0]["trashed_photos"] == []
    assert farmer.get(second["thumb_url"]).status_code == 404


# --------------------------------------------------------------------------
# 藥劑登記挑選、補選登記、記錄者、預填、可帶入處方、本月摘要
# --------------------------------------------------------------------------

def record_moa(factory, log_id=None):
    with factory() as db:
        stmt = select(ApplicationRecord)
        return db.scalar(stmt.where(ApplicationRecord.work_log_id == log_id) if log_id else stmt).moa_codes


def test_spray_with_registration_snapshots_moa_phi_and_fills_blank_fields(system):
    _, factory, clients, _ = system
    response = clients["farmer"].post(URL, json=dict(kind="spray", done_on="2030-03-09", plan_id="plan", registration_id=1))
    assert response.status_code == 201, response.text
    log = response.json()
    snapshot = log["registration_snapshot"]
    assert (log["material_name"], log["dilution"], log["pest_text"]) == ("13% 試驗單劑 試驗劑型", "2345", "試驗病害乙")
    assert snapshot["moa_codes"] == ["FRAC 7", "IRAC 8"] and snapshot["phi_days"] == 4
    assert snapshot["crop_matches"] is True and snapshot["banned"] is False
    assert log["moa_codes"] == ["FRAC 7", "IRAC 8"] and record_moa(factory) == ["FRAC 7", "IRAC 8"]
    # 使用者自己寫的藥名不被登記蓋掉。
    typed = clients["farmer"].post(URL, json=spray(registration_id=1, material_name="農民寫的商品名"))
    assert typed.json()["material_name"] == "農民寫的商品名"
    # 正規化刪掉登記列：外鍵清空，但當時的快照與作用機制都還在。
    with factory() as db:
        db.delete(db.get(Registration, 1))
        db.commit()
    kept = clients["farmer"].get(URL).json()["items"][1]
    assert kept["registration_id"] is None and kept["registration_snapshot"]["moa_codes"] == ["FRAC 7", "IRAC 8"]


def test_registration_for_other_crop_is_recorded_but_flagged_and_invalid_combinations_rejected(system):
    _, _, clients, _ = system
    farmer = clients["farmer"]
    other = farmer.post(URL, json=spray(plan_id="plan", registration_id=2))
    assert other.status_code == 201 and other.json()["registration_snapshot"]["crop_matches"] is False
    unknown_crop = farmer.post(URL, json=spray(crop_text="沒有對應的作物", registration_id=1))
    assert unknown_crop.json()["registration_snapshot"]["crop_matches"] is None
    assert farmer.post(URL, json=spray(kind="fertilize", registration_id=1)).status_code == 422
    assert farmer.post(URL, json=spray(prescription_item_id="rx", registration_id=1)).status_code == 422
    assert farmer.post(URL, json=spray(registration_id=3)).status_code == 409   # 不是正式快照
    assert farmer.post(URL, json=spray(registration_id=999)).status_code == 409


def test_backfill_registration_after_lock_only_once_and_updates_application_record(system):
    _, factory, clients, clock = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray()).json()
    rx = farmer.post(URL, json=spray(prescription_item_id="rx", material_name="")).json()
    assert log["moa_codes"] == [] and record_moa(factory, log["id"]) == []
    url = f"/api/v1/planning/logs/{log['id']}"
    clock["now"] = START + timedelta(days=2)
    assert farmer.patch(url, json=dict(registration_id=None)).status_code == 409
    filled = farmer.patch(url, json=dict(registration_id=1))
    assert filled.status_code == 200, filled.text
    body = filled.json()
    # 鎖定後補選只補依據，不改寫當時記的藥名與倍數。
    assert body["moa_codes"] == ["FRAC 7", "IRAC 8"] and body["material_name"] == "試驗藥劑乙" and body["dilution"] == "1000"
    assert record_moa(factory, log["id"]) == ["FRAC 7", "IRAC 8"]
    assert farmer.patch(url, json=dict(registration_id=2)).status_code == 409
    # 處方來的施藥已有作用機制來源，鎖定後也不能再補選登記。
    assert farmer.patch(f"/api/v1/planning/logs/{rx['id']}", json=dict(registration_id=1)).status_code == 409


def test_within_window_registration_can_change_clear_and_is_dropped_for_other_kinds(system):
    _, factory, clients, _ = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=spray(registration_id=1)).json()
    url = f"/api/v1/planning/logs/{log['id']}"
    cleared = farmer.patch(url, json=dict(registration_id=None)).json()
    assert cleared["registration_snapshot"] is None and cleared["moa_codes"] == [] and record_moa(factory) == []
    assert farmer.patch(url, json=dict(registration_id=1)).json()["moa_codes"] == ["FRAC 7", "IRAC 8"]
    changed = farmer.patch(url, json=dict(kind="fertilize")).json()
    assert changed["registration_snapshot"] is None and changed["application_record_id"] is None


def test_outputs_show_author_and_prescription_case(system):
    _, _, clients, _ = system
    assert clients["doc"].post(URL, json=spray(prescription_item_id="rx", material_name="")).status_code == 201
    item = clients["farmer"].get(URL).json()["items"][0]
    assert (item["created_by"], item["created_by_name"], item["created_by_role"]) == ("doc", "doc", "doctor")
    assert item["prescription"] == dict(case_id="case", case_no="T-1", status="saved")


def test_prescription_must_be_saved_and_visible_to_the_recorder(system):
    _, _, clients, _ = system
    assert clients["farmer"].post(URL, json=spray(prescription_item_id="draft-rx")).status_code == 422
    # 其他醫師替同一農場開的單：植物醫生看不到，農民看得到。
    assert clients["doc"].post(URL, json=spray(prescription_item_id="doc2-rx")).status_code == 404
    assert clients["farmer"].post(URL, json=spray(prescription_item_id="doc2-rx")).status_code == 201

    listed = clients["farmer"].get("/api/v1/planning/farmers/farmer/prescriptions").json()
    assert {row["id"] for row in listed} == {"rx", "doc2-rx"}
    assert next(row for row in listed if row["id"] == "doc2-rx")["logged_count"] == 1
    rx = next(row for row in listed if row["id"] == "rx")
    assert (rx["case_no"], rx["moa_codes"], rx["phi_days"], rx["logged_count"]) == ("T-1", ["FRAC 7", "IRAC 8"], None, 0)
    assert [row["id"] for row in clients["doc"].get("/api/v1/planning/farmers/farmer/prescriptions").json()] == ["rx"]
    assert clients["stranger"].get("/api/v1/planning/farmers/farmer/prescriptions").status_code == 404


def test_prefill_for_event_scout_band_prescription_and_plan(system):
    _, factory, clients, _ = system
    farmer = clients["farmer"]
    base = "/api/v1/planning/farmers/farmer/logs/prefill"
    care = farmer.get(base, params={"event_id": "care"}).json()
    assert (care["kind"], care["event_id"], care["plan_id"], care["field_text"], care["crop_id"], care["title"]) == \
        ("fieldwork", "care", "plan", "試驗田甲", 1, "試驗作業甲")
    assert care["event"]["log_id"] is None
    with factory() as db:
        db.add(FarmEvent(id="pest-band", plan_id="plan", kind="pest", title="試驗害蟲甲", start_on=date(2030, 3, 1), end_on=date(2030, 3, 20)))
        db.commit()
    scout = farmer.get(base, params={"event_id": "pest-band"}).json()
    # 巡查色帶：記成巡田觀察、預填病蟲害名，但不連結事項（窗口是提醒，不是待辦）。
    assert (scout["kind"], scout["event_id"], scout["pest_text"], scout["plan_id"]) == ("scout", None, "試驗害蟲甲", "plan")
    assert farmer.get(base, params={"event_id": "stage"}).status_code == 422
    rx = farmer.get(base, params={"prescription_item_id": "rx"}).json()
    assert (rx["kind"], rx["material_name"], rx["dilution"], rx["field_text"], rx["prescription"]["dose"]) == \
        ("spray", "13% 試驗藥劑甲 試驗劑型", "2345", "試驗田甲", "0.4公升")
    assert farmer.get(base, params={"plan_id": "plan-b"}).json()["crop_id"] == 2
    assert farmer.get(base, params={"event_id": "other-care"}).status_code == 404
    assert farmer.get(base, params={"prescription_item_id": "draft-rx"}).status_code == 422
    assert clients["stranger"].get(base, params={"plan_id": "plan"}).status_code == 404


def test_scout_log_does_not_complete_pest_band(system):
    _, factory, clients, _ = system
    with factory() as db:
        db.add(FarmEvent(id="pest-band", plan_id="plan", kind="pest", title="試驗害蟲甲", start_on=date(2030, 3, 1), end_on=date(2030, 3, 20)))
        db.commit()
    response = clients["farmer"].post(URL, json=dict(kind="scout", done_on="2030-03-09", plan_id="plan", pest_text="試驗害蟲甲", note="巡了，沒看到"))
    assert response.status_code == 201 and response.json()["event_id"] is None
    with factory() as db:
        assert not db.get(FarmEvent, "pest-band").completed


def test_event_log_returns_updated_plan_and_workspace_links_log(system):
    _, _, clients, _ = system
    farmer = clients["farmer"]
    log = farmer.post(URL, json=dict(kind="fieldwork", done_on="2030-03-09", event_id="care")).json()
    plan = log["plan"]
    assert plan["version"] == 2
    event = next(e for e in plan["events"] if e["id"] == "care")
    assert event["completed"] and event["log_id"] == log["id"] and event["log_done_on"] == "2030-03-09"
    workspace = farmer.get("/api/v1/planning/farmers/farmer").json()
    listed = next(e for p in workspace["plans"] if p["id"] == "plan" for e in p["events"] if e["id"] == "care")
    assert listed["log_id"] == log["id"]
    assert "plan" not in farmer.post(URL, json=spray()).json()


def test_archived_plan_event_error_explains_restore(system):
    _, factory, clients, _ = system
    with factory() as db:
        db.get(FarmPlan, "plan").archived = True
        db.commit()
    response = clients["farmer"].post(URL, json=dict(kind="fieldwork", done_on="2030-03-09", event_id="care"))
    assert response.status_code == 409 and "恢復規劃" in response.json()["detail"]


def test_monthly_summary_counts_compliance_yields_and_plan_compare(system):
    _, _, clients, _ = system
    farmer = clients["farmer"]
    for payload in [
        spray(done_on="2030-03-02"),
        dict(kind="harvest", done_on="2030-03-03", plan_id="plan", yield_value="1,200", yield_unit="公斤"),
        dict(kind="harvest", done_on="2030-03-04", plan_id="plan", yield_value="30", yield_unit="公斤"),
        dict(kind="harvest", done_on="2030-03-05", plan_id="plan", yield_value="約兩籃", yield_unit="籃"),
        dict(kind="fertilize", done_on="2030-03-06", field_text="試驗田乙", material_name="試驗肥料"),
        dict(kind="fieldwork", done_on="2030-03-07", event_id="care"),
        dict(kind="irrigate", done_on="2030-02-27", field_text="上個月的田"),
    ]:
        assert farmer.post(URL, json=payload).status_code == 201, payload
    log = farmer.get(URL, params={"kind": "spray"}).json()["items"][0]
    assert upload(farmer, log["id"], "no_exif.png").status_code == 201

    body = farmer.get("/api/v1/planning/farmers/farmer/logs/summary", params={"month": "2030-03"}).json()
    assert (body["total"], body["photo_count"], body["spray_count"]) == (6, 1, 1)
    assert body["by_kind"] == {"spray": 1, "harvest": 3, "fertilize": 1, "fieldwork": 1}
    assert body["yields"] == [{"unit": "公斤", "total": 1230.0}] and body["unparsed_yields"] == 1
    assert body["compliance"]["complete"] == 4
    assert {row["title"] for row in body["compliance"]["incomplete"]} == {"施藥 · 試驗藥劑乙 1000 倍", "施肥 · 試驗肥料"}
    # care 已記錄；care-b（另一期規劃、同一窗口）還沒記。
    assert (body["plan_compare"]["total"], body["plan_compare"]["logged"]) == (2, 1)
    assert [row["event_id"] for row in body["plan_compare"]["unlogged"]] == ["care-b"]
    assert "上個月的田" in body["fields"] and "試驗田乙" in body["fields"]
    assert farmer.get("/api/v1/planning/farmers/farmer/logs/summary", params={"month": "2030-13"}).status_code == 422
    assert clients["stranger"].get("/api/v1/planning/farmers/farmer/logs/summary").status_code == 404
