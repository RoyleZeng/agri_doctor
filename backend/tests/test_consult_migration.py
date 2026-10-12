"""直接執行 migration 的升降版，避免只有 ORM 定義正確而資料庫腳本漏欄。

這兩個檔案（0009/0010）在 0001 合併重建時被併入新的固定快照（見 alembic/versions/0001_initial.py
的說明），原始檔案移到 versions_archive_pre_squash/，不再是真正會被 `alembic upgrade head` 執行的
路徑。測試繼續留著、指向存檔目錄：這裡驗證過的「加外鍵時 SQLite 整表重建不遺失既有資料」「SET NULL
政策」都是踩過的坑，之後如果對已有資料的正式環境要加欄位，這裡的寫法可以直接參考。"""
from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine,inspect,text
from app.db.base import Base
from app.models.consult import CONSULT_TABLES


def load(name):
    spec=spec_from_file_location(name,Path(__file__).parents[1]/f'alembic/versions_archive_pre_squash/{name}.py')
    module=module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_consult_migration_upgrade_downgrade_and_foreign_key_policy():
    # application_records 在 0010 加了 work_log_id；ORM 反映的是最新結構，所以一路升到 0010 再比對。
    migrations=[load('0009_consult'),load('0010_farm_work_logs')]
    engine=create_engine('sqlite://')
    with engine.begin() as conn:
        # migration 本身不依賴正式資料，僅需外鍵目的表存在。
        for name in ('farm_accounts','farm_plans','farm_events','crops','pests','registrations'):
            conn.execute(text(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY)'))
        with Operations.context(MigrationContext.configure(conn)):
            for migration in migrations:
                migration.upgrade()
            inspector=inspect(conn)
            for table in CONSULT_TABLES:
                actual={c['name']:c for c in inspector.get_columns(table.name)}
                assert set(actual)==set(table.columns.keys())
                for col in table.columns:
                    assert actual[col.name]['nullable']==col.nullable
                assert {i['name'] for i in inspector.get_indexes(table.name)}=={i.name for i in table.indexes}
            fk=next(f for f in inspector.get_foreign_keys('prescription_items') if f['constrained_columns']==['registration_id'])
            assert fk['options']['ondelete']=='SET NULL'
            assert any(c['name']=='ck_consult_status' for c in inspector.get_check_constraints('consult_cases'))
            for migration in reversed(migrations):
                migration.downgrade()
            assert not set(t.name for t in CONSULT_TABLES)&set(inspect(conn).get_table_names())
    engine.dispose()
