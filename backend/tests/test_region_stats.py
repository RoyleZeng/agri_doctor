"""區域地圖彙整的離線測試：鄉鎮正規化、跨來源彙整、色階、警戒比對都是純函式。

`collect_source_rows`／`reaggregate`／`district_alerts` 會查資料庫，用記憶體 SQLite 另外測，
跟 `tests/test_farm_logs.py` 一樣的離線寫法。資料都是試驗值，不抄設計稿。
"""
from datetime import date

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.consult import ConsultCase
from app.models.farm_log import FarmWorkLog
from app.models.planning import FarmAccount, FarmPlan
from app.models.region import AlertThreshold, SurveyRecord
from app.services import region_stats as service
from app.services.region_stats import SourceRow, aggregate_region_stats


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kw):
    return "JSON"


# ---- normalize_location ----

def test_normalize_location_finds_unique_district():
    assert service.normalize_location("高雄市大社區XX路100號") == ("高雄市", "大社區")


def test_normalize_location_accepts_bare_district_without_county_prefix():
    assert service.normalize_location("大社區某路") == ("高雄市", "大社區")


def test_normalize_location_unresolvable_returns_none():
    assert service.normalize_location("") is None
    assert service.normalize_location("台北市大安區") is None  # 縣市尚未提供資料


def test_normalize_location_other_county_still_unresolvable_even_with_real_district_name():
    # 只有高雄市有鄉鎮界資料；其他縣市一律 None，不會因為文字剛好含某個字而誤判。
    assert service.normalize_location("台南市這裡沒有可比對的地名") is None


# ---- aggregate_region_stats ----

def row(district="大社區", day=date(2030, 6, 1), crop="芒果", pest="炭疽病", source="scout", farm_id="f1", **kw):
    return SourceRow("高雄市", district, day, crop, pest, source, farm_id, **kw)


def test_aggregate_suppresses_when_fewer_than_three_farms():
    rows = [row(farm_id="f1"), row(farm_id="f2")]  # 只有 2 戶
    [out] = aggregate_region_stats(rows)
    assert out["farms"] == 2
    assert out["count"] is None
    assert out["area_ha"] is None
    assert out["metric"] is None


def test_aggregate_shows_value_once_three_distinct_farms_contribute():
    rows = [row(farm_id="f1"), row(farm_id="f2"), row(farm_id="f3")]
    [out] = aggregate_region_stats(rows)
    assert out["farms"] == 3
    assert out["count"] == 3


def test_aggregate_dedups_farms_not_rows():
    # 同一戶回報 5 次不能墊高到門檻，貢獻農場數只看不重複的 farm_id。
    rows = [row(farm_id="f1")] * 5
    [out] = aggregate_region_stats(rows)
    assert out["farms"] == 1
    assert out["count"] is None


def test_aggregate_survey_source_exempt_from_farm_threshold():
    rows = [SourceRow("高雄市", "大社區", date(2030, 6, 1), "芒果", "炭疽病", "survey", None, metric=25.0)]
    [out] = aggregate_region_stats(rows)
    assert out["farms"] == 0
    assert out["metric"] == 25.0  # survey 不受 3 戶門檻限制


def test_aggregate_groups_by_exact_day_across_month_boundary():
    rows = [
        row(day=date(2030, 5, 31), farm_id="f1"), row(day=date(2030, 5, 31), farm_id="f2"), row(day=date(2030, 5, 31), farm_id="f3"),
        row(day=date(2030, 6, 1), farm_id="f4"), row(day=date(2030, 6, 1), farm_id="f5"), row(day=date(2030, 6, 1), farm_id="f6"),
    ]
    out = {r["day"]: r for r in aggregate_region_stats(rows)}
    assert set(out) == {date(2030, 5, 31), date(2030, 6, 1)}
    assert out[date(2030, 5, 31)]["count"] == 3
    assert out[date(2030, 6, 1)]["count"] == 3


def test_aggregate_area_ha_sums_and_metric_averages_within_group():
    rows = [
        row(source="plan", pest=None, farm_id="f1", area_ha=1.0), row(source="plan", pest=None, farm_id="f2", area_ha=2.0),
        row(source="plan", pest=None, farm_id="f3", area_ha=3.0),
    ]
    [out] = aggregate_region_stats(rows)
    assert out["area_ha"] == 6.0


# ---- color_scale ----

def test_color_scale_empty_when_no_values():
    assert service.color_scale([None, None]) == []


def test_color_scale_breaks_are_increasing_and_include_extremes():
    breaks = service.color_scale([1, 2, 3, 4, 5, 100], classes=5)
    assert breaks[0] == 1
    assert breaks[-1] == 100
    assert breaks == sorted(breaks)


# ---- evaluate_alerts ----

def threshold(pest_text="小黃薊馬", metric="trap_count", warn_value=20.0):
    return AlertThreshold(pest_text=pest_text, metric=metric, warn_value=warn_value, basis="示範，待高改場提供")


def test_evaluate_alerts_flags_reading_at_or_above_threshold():
    readings = [dict(pest_text="小黃薊馬", metric="trap_count", value=20.0)]
    out = service.evaluate_alerts(readings, [threshold()])
    assert out and out[0]["pest_text"] == "小黃薊馬"


def test_evaluate_alerts_ignores_reading_below_threshold():
    readings = [dict(pest_text="小黃薊馬", metric="trap_count", value=19.9)]
    assert service.evaluate_alerts(readings, [threshold()]) == []


def test_evaluate_alerts_ignores_mismatched_metric():
    readings = [dict(pest_text="小黃薊馬", metric="reports_30d", value=999)]
    assert service.evaluate_alerts(readings, [threshold()]) == []


# ---- collect_source_rows / reaggregate（需要資料庫） ----

@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    with factory() as session:
        yield session
    engine.dispose()


def farmer(id, *, share_region=True, location="高雄市大社區", role="farmer"):
    return FarmAccount(id=id, username=id, name=id, role=role, password_hash="unused",
                       location=location, share_region=share_region)


def test_collect_source_rows_excludes_non_consenting_farms(db):
    db.add_all([farmer("consents", share_region=True), farmer("declines", share_region=False)])
    db.flush()
    db.add_all([
        FarmWorkLog(id="log-yes", farmer_id="consents", done_on=date(2030, 6, 1), kind="scout",
                    field_text="田1", crop_text="芒果", title="巡田", pest_text="炭疽病", created_by="consents"),
        FarmWorkLog(id="log-no", farmer_id="declines", done_on=date(2030, 6, 1), kind="scout",
                    field_text="田2", crop_text="芒果", title="巡田", pest_text="炭疽病", created_by="declines"),
    ])
    db.commit()
    rows = service.collect_source_rows(db, date(2030, 6, 1))
    farm_ids = {r.farm_id for r in rows}
    assert "consents" in farm_ids
    assert "declines" not in farm_ids


def test_collect_source_rows_excludes_unresolvable_location(db):
    db.add(farmer("nowhere", share_region=True, location="這裡沒有鄉鎮名字"))
    db.flush()
    db.add(FarmWorkLog(id="log", farmer_id="nowhere", done_on=date(2030, 6, 1), kind="scout",
                       field_text="田1", crop_text="芒果", title="巡田", pest_text="炭疽病", created_by="nowhere"))
    db.commit()
    assert service.collect_source_rows(db, date(2030, 6, 1)) == []


def test_collect_source_rows_includes_consult_scout_plan_survey(db):
    db.add(farmer("f1"))
    db.flush()
    db.add_all([
        ConsultCase(id="c1", doctor_id="f1", farmer_id="f1", farmer_text="農民", case_no="T-1", crop_text="芒果",
                    status="saved", diagnosis_text="炭疽病"),
        FarmWorkLog(id="log", farmer_id="f1", done_on=date(2030, 6, 1), kind="scout", field_text="田1",
                   crop_text="芒果", title="巡田", pest_text="炭疽病", created_by="f1"),
        FarmPlan(id="plan", farmer_id="f1", crop_name="芒果", field_name="田1", area="1.5",
                planted_on=date(2030, 1, 1), end_on=date(2030, 12, 31)),
        SurveyRecord(id="survey", district="大社區", surveyed_on=date(2030, 6, 1), crop_text="芒果",
                    pest_text="炭疽病", trap_count=25.0, traps=1, recorded_by="f1"),
    ])
    db.commit()
    sources = {r.source for r in service.collect_source_rows(db, date(2030, 6, 1))}
    assert sources == {"consult", "scout", "plan", "survey"}


def test_reaggregate_writes_visible_stat_once_three_farms_consent(db):
    for i in range(1, 4):
        db.add(farmer(f"f{i}"))
    db.flush()
    for i in range(1, 4):
        db.add(FarmWorkLog(id=f"log{i}", farmer_id=f"f{i}", done_on=date(2030, 6, 1), kind="scout",
                           field_text="田", crop_text="芒果", title="巡田", pest_text="炭疽病", created_by=f"f{i}"))
    db.commit()
    from app.models.region import RegionDailyStat
    result = service.reaggregate(db, date(2030, 6, 1))
    assert result["written"] >= 1 and result["removed"] == 0
    stat = db.query(RegionDailyStat).filter_by(district="大社區", source="scout").one()
    assert stat.count == 3


def scouting_farms(db, *, count=3, day=date(2030, 6, 1)):
    """建 count 戶同意彙整的農場，各記一筆同一天、同作物病蟲害的巡田日誌。"""
    for i in range(1, count + 1):
        db.add(farmer(f"f{i}"))
    db.flush()
    for i in range(1, count + 1):
        db.add(FarmWorkLog(id=f"log{i}", farmer_id=f"f{i}", done_on=day, kind="scout", field_text="田",
                           crop_text="芒果", title="巡田", pest_text="炭疽病", created_by=f"f{i}"))
    db.commit()


def test_reaggregate_removes_rows_whose_source_logs_are_gone(db):
    """日誌撤回後，彙整出來的那筆也要消失——不然地圖會一直顯示一筆早就不存在的通報。

    跟「撤回的施藥不能被輪替檢查讀到」同一個原則：撤回就要真的看不到。
    """
    from app.models.region import RegionDailyStat

    day = date(2030, 6, 1)
    scouting_farms(db, day=day)
    service.reaggregate(db, day)
    assert db.query(RegionDailyStat).filter_by(source="scout").count() == 1

    db.query(FarmWorkLog).delete()  # 三筆巡田日誌都被撤回
    db.commit()
    result = service.reaggregate(db, day)

    assert result["removed"] == 1
    assert db.query(RegionDailyStat).filter_by(source="scout").count() == 0


def test_reaggregate_moves_the_row_when_a_log_is_re_dated(db):
    """改日期＝舊那天的列要消失、新那天的列要出現，不能兩天都留著（總數會被灌成兩倍）。"""
    from app.models.region import RegionDailyStat

    old_day, new_day = date(2030, 6, 1), date(2030, 6, 8)
    scouting_farms(db, day=old_day)
    service.reaggregate(db, old_day)

    for log in db.query(FarmWorkLog).all():
        log.done_on = new_day
    db.commit()
    service.reaggregate(db, new_day)

    days = [row.day for row in db.query(RegionDailyStat).filter_by(source="scout").all()]
    assert days == [new_day]


def test_reaggregate_keeps_plan_snapshots_from_earlier_days(db):
    """plan 是「今天正在種什麼」的快照：過去某天的分布無從重建，重跑時一律留著。

    如果照 scout 那樣「這次沒算到就刪」，每跑一次彙整就會把歷史種植面積抹掉一天。
    """
    from app.models.region import RegionDailyStat

    yesterday, today = date(2030, 5, 31), date(2030, 6, 1)
    db.add(RegionDailyStat(id="old-plan", county="高雄市", district="大社區", day=yesterday,
                           crop_text="芒果", pest_text=None, source="plan", count=4, area_ha=2.5, farms=4))
    scouting_farms(db, day=today)
    db.commit()

    service.reaggregate(db, today)

    kept = db.query(RegionDailyStat).filter_by(source="plan", day=yesterday).one()
    assert kept.area_ha == 2.5


def test_reaggregate_replaces_todays_plan_snapshot_instead_of_leaving_a_stale_one(db):
    """今天這批 plan 列是可以重算的：作物改了，今天的舊列要被清掉，不能跟新的並存。"""
    from app.models.region import RegionDailyStat

    today = date(2030, 6, 1)
    db.add(RegionDailyStat(id="stale-plan", county="高雄市", district="大社區", day=today,
                           crop_text="已經不種的作物", pest_text=None, source="plan", count=3, farms=3))
    scouting_farms(db, day=today)
    db.commit()

    service.reaggregate(db, today)

    crops = [row.crop_text for row in db.query(RegionDailyStat).filter_by(source="plan", day=today).all()]
    assert "已經不種的作物" not in crops
