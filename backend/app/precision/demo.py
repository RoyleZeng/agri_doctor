"""精準模式示範資料產生器。

    python -m app.precision.demo --username <測試帳號>

* 冪等：每次先刪掉這個帳號 is_demo=true 的列再重建，真實資料一列都不碰；主鍵用帳號＋名稱推導，重建後網址不變。
* 用 random.Random(帳號 id) 產生數值：同一個帳號每次畫面都一樣，展示時不會忽高忽低。
* 6×4 格、A/B/C 三區（長勢高／中／低）、5 站（4 個土壤站＋1 個氣象站）、30 天每小時讀數：
  C 區土壤水分由 42% 緩降到 19%，中間穿插 6 場雨，最後 5 天無雨；NDVI 與上季產量圖層 A>B>C。
  跑完規則引擎剛好產出「補灌 C 區」與「變率施肥」兩筆建議。
* 示範田區用固定的降雨預報（service.DEMO_RAIN）：假讀數配真實天氣，展示結果會隨當天下不下雨而變。
"""
from __future__ import annotations

import argparse
import math
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import delete, func, insert, select

from app.models.planning import FarmAccount, FarmPlan
from app.models.precision import (FarmField, FarmZone, FieldLayer, Recommendation, SensorGroup, SensorReading,
                                  SensorStation)
from app.precision import service

GRID_COLS, GRID_ROWS = 6, 4
DAYS = 30
DRY_TAIL_DAYS = 5
RAIN_EVENTS = 6
DEFAULT_AREA_TEXT = "0.36 公頃"
# 分區刻意不等大（A 10 格、B 6 格、C 8 格）：各區面積相同時，變率施肥與均一施的總量會剛好一樣，
# 處方表的「對照均一施」就看不出差別。
ZONES = (
    ("A", "高", tuple((col, row) for col in (0, 1) for row in range(GRID_ROWS)) + ((2, 0), (2, 1))),
    ("B", "中", ((2, 2), (2, 3)) + tuple((3, row) for row in range(GRID_ROWS))),
    ("C", "低", tuple((col, row) for col in (4, 5) for row in range(GRID_ROWS))),
)
# (代號, 種類, 分區, 格子欄, 格子列)
STATIONS = (("S-A1", "soil", "A", 0, 1), ("S-B1", "soil", "B", 2, 2), ("S-C1", "soil", "C", 4, 1),
            ("S-C2", "soil", "C", 5, 3), ("W-1", "weather", None, 3, 0))
SOIL_METRICS = {"soil_moisture": "%", "soil_temp": "°C", "ec": "dS/m"}
WEATHER_METRICS = {"air_temp": "°C", "humidity": "%", "rain": "mm"}
# 土壤水分：30 天前 → 現在。A、B 最後三天仍高於 30%，只有 C 會低於門檻。
MOISTURE = {"A": (45.0, 34.5), "B": (44.0, 32.5), "C": (42.0, 19.0)}
NDVI = {"A": 0.78, "B": 0.64, "C": 0.49}
YIELD_PREV = {"A": 5.2, "B": 4.4, "C": 3.1}  # 公噸／公頃
DEMO_TABLES = (Recommendation, FieldLayer, SensorReading, SensorStation, SensorGroup, FarmZone, FarmField)


@dataclass
class DemoResult:
    field_id: str
    counts: dict
    recommendations: list = field(default_factory=list)
    notes: list = field(default_factory=list)


def demo_id(account_id: str, *parts: str) -> str:
    return str(uuid5(NAMESPACE_URL, "agri-doctor-demo:" + ":".join((account_id, *parts))))


def area_ha(text: str) -> float | None:
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*公頃\s*$", text or "")
    return float(match.group(1)) if match else None


def clear_demo(db, farmer_id: str) -> None:
    fields = select(FarmField.id).where(FarmField.farmer_id == farmer_id, FarmField.is_demo.is_(True))
    stations = select(SensorStation.id).where(SensorStation.field_id.in_(fields))
    # 子表先刪，只刪 is_demo 的列；不依賴資料庫的 CASCADE（SQLite 測試環境不一定開外鍵）。
    db.execute(delete(SensorReading).where(SensorReading.station_id.in_(stations), SensorReading.is_demo.is_(True)))
    for model in (Recommendation, FieldLayer, SensorStation, SensorGroup, FarmZone):
        db.execute(delete(model).where(model.field_id.in_(fields), model.is_demo.is_(True)))
    db.execute(delete(FarmField).where(FarmField.farmer_id == farmer_id, FarmField.is_demo.is_(True)))


def demo_counts(db, farmer_id: str) -> dict:
    fields = select(FarmField.id).where(FarmField.farmer_id == farmer_id, FarmField.is_demo.is_(True))
    stations = select(SensorStation.id).where(SensorStation.field_id.in_(fields))
    counts = {}
    for model in DEMO_TABLES:
        stmt = select(func.count()).select_from(model).where(model.is_demo.is_(True))
        if model is FarmField:
            stmt = stmt.where(FarmField.farmer_id == farmer_id)
        elif model is SensorReading:
            stmt = stmt.where(SensorReading.station_id.in_(stations))
        else:
            stmt = stmt.where(model.field_id.in_(fields))
        counts[model.__tablename__] = db.scalar(stmt)
    return counts


def rain_series(rng: random.Random, hours: int) -> list[float]:
    rain = [0.0] * hours
    days = rng.sample(range(1, DAYS - DRY_TAIL_DAYS), RAIN_EVENTS)
    for day in days:
        start = day * 24 + rng.randrange(24)
        duration = rng.randint(2, 4)
        total = rng.uniform(6, 22)
        for hour in range(start, min(hours, start + duration)):
            rain[hour] += round(total / duration, 1)
    return rain


def generate_demo(db, account: FarmAccount, *, now: datetime, today: date, rain=service.DEMO_RAIN) -> DemoResult:
    if account.role not in ("farmer", "admin"):
        raise ValueError("示範資料只能建在農民帳號")
    rng = random.Random(account.id)
    clear_demo(db, account.id)
    db.flush()

    plan = db.scalar(select(FarmPlan).where(FarmPlan.farmer_id == account.id, FarmPlan.archived.is_(False))
                     .order_by(FarmPlan.planted_on.desc(), FarmPlan.id))
    name = plan.field_name if plan else "示範田區"
    area_text = plan.area if plan and area_ha(plan.area) else DEFAULT_AREA_TEXT
    total_ha = area_ha(area_text)
    farm_field = FarmField(id=demo_id(account.id, "field"), farmer_id=account.id, name=name, area_text=area_text,
                           grid_cols=GRID_COLS, grid_rows=GRID_ROWS, is_demo=True, created_at=now, updated_at=now)
    db.add(farm_field)
    zones = {}
    for code, label, zone_cells in ZONES:
        cells = [list(cell) for cell in zone_cells]
        zones[code] = FarmZone(id=demo_id(account.id, "zone", code), field_id=farm_field.id, code=code, label=label,
                               cells=cells, area_ha=round(total_ha * len(cells) / (GRID_COLS * GRID_ROWS), 3), is_demo=True)
    groups = {
        "soil": SensorGroup(id=demo_id(account.id, "group", "soil"), field_id=farm_field.id, name="土壤組（示範）",
                            metrics=list(SOIL_METRICS), is_demo=True),
        "weather": SensorGroup(id=demo_id(account.id, "group", "weather"), field_id=farm_field.id, name="氣象站（示範）",
                               metrics=list(WEATHER_METRICS), is_demo=True),
    }
    stations = [SensorStation(id=demo_id(account.id, "station", code), field_id=farm_field.id, group_id=groups[kind].id,
                              zone_id=zones[zone].id if zone else None, code=code, kind=kind, grid_col=col, grid_row=row,
                              source="demo", is_demo=True)
                for code, kind, zone, col, row in STATIONS]
    db.add_all([*zones.values(), *groups.values()])
    db.flush()
    db.add_all(stations)
    db.flush()

    hours = DAYS * 24
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    times = [end - timedelta(hours=hours - 1 - index) for index in range(hours)]
    rainfall = rain_series(rng, hours)
    rows = []
    for station_index, (code, kind, zone, _, _) in enumerate(STATIONS):
        station_id = stations[station_index].id
        wetness = 0.0
        offset = rng.uniform(-0.6, 0.6)
        for index, ts in enumerate(times):
            local_hour = (ts.hour + 8) % 24
            daily = math.sin(2 * math.pi * (local_hour - 9) / 24)
            if kind == "soil":
                start, finish = MOISTURE[zone]
                # 雨水讓土壤水分跳升再慢慢回落（約 1.5 天衰減一半）；最後 5 天無雨，影響已消散。
                wetness = wetness * math.exp(-1 / 50) + rainfall[index] * 0.35
                base = start + (finish - start) * index / (hours - 1)
                values = {
                    "soil_moisture": min(60.0, max(5.0, base + wetness - 0.4 * daily + offset * 0.5 + rng.gauss(0, 0.25))),
                    "soil_temp": 25.5 + 2.2 * daily + rng.gauss(0, 0.2),
                    "ec": {"A": 1.05, "B": 0.95, "C": 0.8}[zone] + rng.gauss(0, 0.03),
                }
                units = SOIL_METRICS
            else:
                values = {
                    "air_temp": 27.0 + 4.5 * daily - (2.0 if rainfall[index] else 0.0) + rng.gauss(0, 0.3),
                    "humidity": min(100.0, 78.0 - 12.0 * daily + (15.0 if rainfall[index] else 0.0) + rng.gauss(0, 1.0)),
                    "rain": rainfall[index],
                }
                units = WEATHER_METRICS
            for metric, value in values.items():
                rows.append(dict(station_id=station_id, ts=ts, metric=metric, value=round(value, 2), unit=units[metric],
                                 is_demo=True))
    db.execute(insert(SensorReading), rows)

    for kind, captured_on, source_text, base_values, spread in (
        ("ndvi", today - timedelta(days=3), "無人機多光譜（示範）", NDVI, 0.03),
        ("yield_prev", today - timedelta(days=180), "上季採收紀錄（示範，公噸／公頃）", YIELD_PREV, 0.25),
    ):
        cell_values, zone_values = {}, {}
        for code, _, zone_cells in ZONES:
            values = []
            for col, row in zone_cells:
                value = round(base_values[code] + rng.uniform(-spread, spread), 3)
                cell_values[f"{col},{row}"] = value
                values.append(value)
            zone_values[code] = round(sum(values) / len(values), 3)
        db.add(FieldLayer(id=demo_id(account.id, "layer", kind), field_id=farm_field.id, kind=kind, captured_on=captured_on,
                          source_text=source_text, cell_values=cell_values, zone_values=zone_values, is_demo=True))
    db.flush()

    saved, notes, _ = service.analyze_field(db, farm_field, now=now, today=today, rain=rain)
    return DemoResult(field_id=farm_field.id, counts=demo_counts(db, account.id),
                      recommendations=[row.title for row in saved], notes=[note.message for note in notes])


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="產生精準模式示範資料（只刪建 is_demo=true 的列，可重複執行）")
    parser.add_argument("--username", required=True, help="測試用的農民帳號")
    args = parser.parse_args(argv)

    from app.db.session import SessionLocal
    from app.services.consult import taipei_today

    with SessionLocal() as db:
        account = db.scalar(select(FarmAccount).where(FarmAccount.username == args.username))
        if account is None:
            raise SystemExit(f"找不到帳號 {args.username}")
        result = generate_demo(db, account, now=datetime.now(timezone.utc), today=taipei_today())
        db.commit()
    print(f"示範田區 {result.field_id}")
    for table, count in result.counts.items():
        print(f"  {table}: {count}")
    print("建議：" + ("、".join(result.recommendations) or "（無）"))
    for note in result.notes:
        print("  不建議：" + note)


if __name__ == "__main__":
    main()
