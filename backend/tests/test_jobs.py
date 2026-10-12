"""排程工作的離線冒煙測試：三個工作都要能在空資料庫上跑完並回報結果。

價值在於「壞掉的時候在這裡爆，而不是凌晨四點半在正式環境默默失敗」——這些工作平常沒人看著，
查詢寫錯、import 少一個，靠人工是不會發現的。實際排程設定見 docs/deployment.md。
"""
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import jobs
from app.core.config import settings
from app.db.base import Base
from app.models import *  # noqa: F401,F403 - 匯入以註冊所有 model


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def db(monkeypatch, tmp_path):
    # 照片清理會掃 media_root；指到每個測試自己的暫存目錄，不要碰 repo 的 data/media。
    monkeypatch.setattr(settings, "media_root", tmp_path / "media")
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)() as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("name", sorted(jobs.JOBS))
def test_job_runs_on_empty_database(db, name, monkeypatch):
    if name == 'moa-sync':
        # 排程 wrapper 的離線契約；下載與快照完整性另以 MockTransport 驗證。
        monkeypatch.setattr('app.services.moa_sync.sync', lambda db: {'fixture': 'ok'})
    message = jobs.JOBS[name](db)
    assert isinstance(message, str) and message


def test_unknown_job_name_is_a_usage_error():
    # 打錯名字要立刻以非零碼結束，不能被當成「跑過了」。
    assert jobs.main(["app.jobs", "no-such-job"]) == 2
    assert jobs.main(["app.jobs"]) == 2


def test_lambda_handler_rejects_unknown_job_name():
    # 排程設定打錯字要讓這次執行標記為失敗，不能靜悄悄回成功。
    with pytest.raises(ValueError, match="未知的排程工作"):
        jobs.lambda_handler({"job": "no-such-job"})
    with pytest.raises(ValueError):
        jobs.lambda_handler({})


def test_documented_job_names_match_the_registry():
    # docs/deployment.md 的 EventBridge 設定直接寫這三個名字；改名要同時改文件。
    assert set(jobs.JOBS) == {"media-purge", "region-aggregate", "precision-reanalyze", "moa-sync"}
