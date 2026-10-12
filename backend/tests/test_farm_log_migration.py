"""直接跑 0010～0015 的升降版：外鍵刪除政策、一個事項只能一筆日誌、加欄後既有資料不遺失、安全採收期回填、
sensor_groups 補上 data_sources 外鍵。

這些檔案在 0001 合併重建時被併入新的固定快照（見 alembic/versions/0001_initial.py 的說明），原始檔案
移到 versions_archive_pre_squash/，不再是真正會被 `alembic upgrade head` 執行的路徑。測試繼續留著、
指向存檔目錄：這裡驗證過的回填邏輯與資料保留行為是踩過的坑，之後如果對已有資料的正式環境要加欄位，
這裡的寫法可以直接參考。"""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

from app.models.farm_log import FARM_LOG_TABLES
from app.models.precision import DATA_SOURCE_TABLES, PRECISION_TABLES


def load(name):
    spec = spec_from_file_location(name, Path(__file__).parents[1] / f"alembic/versions_archive_pre_squash/{name}.py")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_farm_log_migrations_upgrade_downgrade_and_delete_policy():
    consult, farm_log, photo_source = load("0009_consult"), load("0010_farm_work_logs"), load("0011_photo_taken_at_source")
    registration = load("0012_work_log_registration")
    precision = load("0013_precision")
    expired_reason = load("0014_recommendation_expired_reason")
    data_sources = load("0015_data_sources")
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        # migration 不依賴正式資料，只需要外鍵目的表存在。
        for name in ("farm_accounts", "farm_plans", "farm_events", "crops", "pests", "registrations"):
            conn.execute(text(f"CREATE TABLE {name} (id INTEGER PRIMARY KEY)"))
        with Operations.context(MigrationContext.configure(conn)):
            consult.upgrade()
            # 正式庫的 application_records 已有資料；SQLite 加外鍵會整表重建，確認舊紀錄不會被弄丟。
            conn.execute(text("INSERT INTO application_records (id, farmer_id, field_text, product_text, moa_codes, applied_on, note)"
                              " VALUES ('old', 'f', '試驗田', '試驗藥劑', '[]', '2030-01-01', '')"))
            farm_log.upgrade()
            # 0011 之前就存在的照片：無法事後證明讀到的是 EXIF，一律補成 upload。
            conn.execute(text("INSERT INTO farm_work_logs (id, farmer_id, done_on, kind, field_text, crop_text, title,"
                              " material_name, material_supplier, material_lot, dilution, amount_value, amount_unit, area_text,"
                              " yield_value, yield_unit, yield_destination, labor_text, pest_text, note, compliance, created_by)"
                              " VALUES ('log', 'f', '2030-01-01', 'irrigate', '試驗田', '', '灌溉', '', '', '', '', '', '', '',"
                              " '', '', '', '', '', '', '{}', 'f')"))
            conn.execute(text("INSERT INTO farm_log_photos (id, log_id, storage_key, thumb_key, bytes, taken_at, caption, sort_order)"
                              " VALUES ('old-photo', 'log', 'a', 'b', 1, '2030-01-01', '', 0)"))
            photo_source.upgrade()
            registration.upgrade()
            # 0013 之前已存在的施藥：有登記快照的回填安全採收期，沒有依據的維持 none，不填 0。
            conn.execute(text("INSERT INTO farm_work_logs (id, farmer_id, done_on, kind, field_text, crop_text, title,"
                              " material_name, material_supplier, material_lot, dilution, amount_value, amount_unit, area_text,"
                              " yield_value, yield_unit, yield_destination, labor_text, pest_text, note, compliance, created_by,"
                              " registration_snapshot)"
                              " VALUES ('spray-reg', 'f', '2030-01-01', 'spray', '試驗田', '', '施藥', '試驗藥劑', '', '', '', '', '', '',"
                              " '', '', '', '', '', '', '{}', 'f', '{\"phi_days\": 6}'),"
                              " ('spray-typed', 'f', '2030-01-01', 'spray', '試驗田', '', '施藥', '手寫藥名', '', '', '', '', '', '',"
                              " '', '', '', '', '', '', '{}', 'f', NULL)"))
            precision.upgrade()
            expired_reason.upgrade()
            data_sources.upgrade()

            inspector = inspect(conn)
            backfilled = dict(conn.execute(text("SELECT id, phi_source || ':' || COALESCE(phi_days, '-') FROM farm_work_logs")).all())
            assert backfilled == {"log": "none:-", "spray-reg": "registration:6", "spray-typed": "none:-"}
            for table in FARM_LOG_TABLES + PRECISION_TABLES + DATA_SOURCE_TABLES:
                actual = {c["name"]: c for c in inspector.get_columns(table.name)}
                assert set(actual) == set(table.columns.keys()), table.name
                for column in table.columns:
                    assert actual[column.name]["nullable"] == column.nullable, (table.name, column.name)
                assert {i["name"] for i in inspector.get_indexes(table.name)} == {i.name for i in table.indexes}
            assert conn.execute(text("SELECT taken_at_source FROM farm_log_photos")).scalar_one() == "upload"
            assert any(c["name"] == "ck_log_photo_taken_at_source" for c in inspector.get_check_constraints("farm_log_photos"))

            # 上游刪除只清連結，日誌本身要留下來；帳號連結不設刪除行為。
            policy = {tuple(f["constrained_columns"]): f["options"].get("ondelete")
                      for f in inspector.get_foreign_keys("farm_work_logs")}
            for column in ("plan_id", "event_id", "prescription_item_id", "pest_id"):
                assert policy[(column,)] == "SET NULL", column
            assert policy[("farmer_id",)] is None and policy[("created_by",)] is None
            assert inspector.get_foreign_keys("farm_log_photos")[0]["options"]["ondelete"] == "CASCADE"
            work_log_fk = next(f for f in inspector.get_foreign_keys("application_records")
                               if f["constrained_columns"] == ["work_log_id"])
            assert work_log_fk["referred_table"] == "farm_work_logs"
            assert work_log_fk["options"]["ondelete"] == "CASCADE"
            assert next(i for i in inspector.get_indexes("farm_work_logs") if i["name"] == "ix_farm_work_logs_event_id")["unique"]
            assert any(c["name"] == "ck_work_log_kind" for c in inspector.get_check_constraints("farm_work_logs"))
            assert conn.execute(text("SELECT id FROM application_records")).scalars().all() == ["old"]
            reg_fk = next(f for f in inspector.get_foreign_keys("farm_work_logs") if f["constrained_columns"] == ["registration_id"])
            assert reg_fk["referred_table"] == "registrations" and reg_fk["options"]["ondelete"] == "SET NULL"
            for column, target in (("field_id", "farm_fields"), ("recommendation_id", "recommendations")):
                fk = next(f for f in inspector.get_foreign_keys("farm_work_logs") if f["constrained_columns"] == [column])
                assert fk["referred_table"] == target and fk["options"]["ondelete"] == "SET NULL", column
            event_fk = next(f for f in inspector.get_foreign_keys("farm_events") if f["constrained_columns"] == ["field_id"])
            assert event_fk["referred_table"] == "farm_fields" and event_fk["options"]["ondelete"] == "SET NULL"
            proposed = next(i for i in inspector.get_indexes("recommendations") if i["name"] == "uq_recommendation_proposed")
            assert proposed["unique"]
            assert "precision_enabled" in {c["name"] for c in inspector.get_columns("farm_accounts")}
            source_fk = next(f for f in inspector.get_foreign_keys("sensor_groups") if f["constrained_columns"] == ["source_id"])
            assert source_fk["referred_table"] == "data_sources" and source_fk["options"]["ondelete"] == "SET NULL"
            assert {i["name"] for i in inspector.get_indexes("sensor_groups")} == {"ix_sensor_groups_field_id", "ix_sensor_groups_source_id"}
            assert inspector.get_unique_constraints("field_boundaries")[0]["column_names"] == ["field_id"]

            data_sources.downgrade()
            inspector = inspect(conn)
            assert not {t.name for t in DATA_SOURCE_TABLES} & set(inspector.get_table_names())
            assert {i["name"] for i in inspector.get_indexes("sensor_groups")} == {"ix_sensor_groups_field_id"}

            expired_reason.downgrade()
            inspector = inspect(conn)
            assert "expired_reason" not in {c["name"] for c in inspector.get_columns("recommendations")}

            precision.downgrade()
            inspector = inspect(conn)
            assert not {t.name for t in PRECISION_TABLES} & set(inspector.get_table_names())
            assert not {"field_id", "phi_days", "phi_source"} & {c["name"] for c in inspector.get_columns("farm_work_logs")}
            assert "field_id" not in {c["name"] for c in inspector.get_columns("farm_events")}
            conn.execute(text("DELETE FROM farm_work_logs WHERE id LIKE 'spray-%'"))
            assert conn.execute(text("SELECT id FROM farm_work_logs")).scalars().all() == ["log"]

            registration.downgrade()
            inspector = inspect(conn)
            assert not {"registration_id", "registration_snapshot"} & {c["name"] for c in inspector.get_columns("farm_work_logs")}
            assert conn.execute(text("SELECT id FROM farm_work_logs")).scalars().all() == ["log"]

            photo_source.downgrade()
            inspector = inspect(conn)
            assert "taken_at_source" not in {c["name"] for c in inspector.get_columns("farm_log_photos")}
            assert conn.execute(text("SELECT id FROM farm_log_photos")).scalars().all() == ["old-photo"]

            farm_log.downgrade()
            inspector = inspect(conn)
            assert "work_log_id" not in {c["name"] for c in inspector.get_columns("application_records")}
            assert not {t.name for t in FARM_LOG_TABLES} & set(inspector.get_table_names())
            assert conn.execute(text("SELECT id FROM application_records")).scalars().all() == ["old"]
            consult.downgrade()
    engine.dispose()
