"""資料來源頁：田區 CRUD、感測群組與站點、data_sources 連線設定、CSV 讀數上傳、
無人機任務（示範階段只吃已算好格子數值的 CSV）、GIS 邊界匯入。

現在只真的實作 CSV 這條路（見 `app/precision/sources.py` 的 `SensorSource` 介面說明）；
HTTP／MQTT／WMS 的「測試連線」誠實回「尚未支援」，不假裝連得上——這個專案的立場一貫是
「資料不足或做不到就說清楚，不猜、不裝」。
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.planning import Input, access_farmer, current_user, protect_write
from app.api.v1.precision import owned_field
from app.core.config import settings
from app.db.session import get_db
from app.models.planning import FarmAccount
from app.models.precision import (DATA_SOURCE_KINDS, UAV_PRODUCTS, DataSource, FarmField, FarmZone, FieldBoundary,
                                  FieldLayer, SensorGroup, SensorReading, SensorStation, UavMission)
from app.precision import geo, service
from app.precision.crypto import encrypt_config, mask_config
from app.precision.layers import parse_cell_csv, zone_averages
from app.services.farm_log import as_utc
from app.precision.sources import CsvUploadSource
from app.services import weather
from app.services.consult import json_snapshot, taipei_today

router = APIRouter(prefix="/precision", tags=["precision-sources"], dependencies=[Depends(protect_write)])

FILE_BASED_SOURCE_KINDS = ("sensor_csv", "uav_upload", "gis_file")
UAV_PRODUCT_LABELS = {"ndvi": "NDVI", "rgb": "RGB", "multispectral": "多光譜"}
# 無人機成果目前只有 NDVI／多光譜能直接變成分區數值圖層；RGB 是正射影像，示範階段僅供留存比對，不產生圖層。
UAV_LAYER_PRODUCTS = ("ndvi", "multispectral")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def read_csv_upload(file: UploadFile, *, max_bytes: int = 2 * 1024 * 1024) -> str:
    if file.filename and not file.filename.lower().endswith(".csv"):
        raise HTTPException(422, "示範階段僅支援 CSV 檔（.csv）；GeoTIFF 與其他格式留待之後")
    data = file.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"檔案不能超過 {max_bytes // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "沒有收到檔案內容")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(422, "CSV 請存成 UTF-8 編碼")


# --------------------------------------------------------------------------
# 田區 CRUD（地點切換用；示範田區歸產生器管理，這裡的異動只碰真實田區）
# --------------------------------------------------------------------------

class FieldInput(Input):
    name: str = Field(min_length=1, max_length=160)
    area_text: str = Field(default="", max_length=60)
    grid_cols: int = Field(default=6, ge=1, le=20)
    grid_rows: int = Field(default=4, ge=1, le=20)


class FieldPatch(Input):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    area_text: str | None = Field(default=None, max_length=60)
    grid_cols: int | None = Field(default=None, ge=1, le=20)
    grid_rows: int | None = Field(default=None, ge=1, le=20)


def field_output(field: FarmField) -> dict:
    return json_snapshot(dict(id=field.id, name=field.name, area_text=field.area_text, grid_cols=field.grid_cols,
                              grid_rows=field.grid_rows, is_demo=field.is_demo))


@router.get("/farmers/{farmer_id}/fields")
def list_fields(farmer_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    return dict(items=[field_output(f) for f in service.field_list(db, farmer_id)])


@router.post("/farmers/{farmer_id}/fields", status_code=201)
def create_field(farmer_id: str, payload: FieldInput, db: Session = Depends(get_db), user=Depends(current_user)):
    farmer = access_farmer(db, user, farmer_id)
    if user.role != "admin" and user.id != farmer.id:
        raise HTTPException(403, "田區只能由農民本人新增")
    field = FarmField(farmer_id=farmer_id, **payload.model_dump(), is_demo=False)
    db.add(field)
    db.commit()
    return field_output(field)


@router.patch("/fields/{field_id}")
def patch_field(field_id: str, payload: FieldPatch, db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "田區只能由農民本人修改")
    if field.is_demo:
        raise HTTPException(409, "示範田區由「重新產生示範資料」管理，這裡不能修改")
    changes = payload.model_dump(exclude_unset=True)
    grid_cols = changes.get("grid_cols", field.grid_cols)
    grid_rows = changes.get("grid_rows", field.grid_rows)
    if (grid_cols, grid_rows) != (field.grid_cols, field.grid_rows):
        stations = db.scalars(select(SensorStation).where(SensorStation.field_id == field.id)).all()
        zones = db.scalars(select(FarmZone).where(FarmZone.field_id == field.id)).all()
        out_of_range = any(s.grid_col is not None and s.grid_col >= grid_cols for s in stations) \
            or any(s.grid_row is not None and s.grid_row >= grid_rows for s in stations) \
            or any(col >= grid_cols or row >= grid_rows for zone in zones for col, row in zone.cells)
        if out_of_range:
            raise HTTPException(409, "已經有測站或分區的格子座標超出縮小後的範圍，請先調整那些格子再縮小格數")
    for key, value in changes.items():
        setattr(field, key, value)
    db.commit()
    return field_output(field)


@router.delete("/fields/{field_id}")
def delete_field(field_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "田區只能由農民本人刪除")
    if field.is_demo:
        raise HTTPException(409, "示範田區由「重新產生示範資料」管理，這裡不能刪除")
    db.delete(field)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# 感測群組與站點
# --------------------------------------------------------------------------

class GroupInput(Input):
    name: str = Field(min_length=1, max_length=80)
    source_id: str | None = Field(default=None, max_length=36)
    metrics: list[str] = Field(default_factory=list)


class StationInput(Input):
    code: str = Field(default="", max_length=20)
    kind: str = Field(pattern="^(soil|weather)$")
    zone_id: str | None = Field(default=None, max_length=36)
    grid_col: int | None = Field(default=None, ge=0)
    grid_row: int | None = Field(default=None, ge=0)


def group_output(db, group: SensorGroup) -> dict:
    stations = db.scalars(select(SensorStation).where(SensorStation.group_id == group.id)).all()
    return json_snapshot(dict(
        id=group.id, field_id=group.field_id, name=group.name, source_id=group.source_id, metrics=group.metrics,
        is_demo=group.is_demo, stations=[dict(id=s.id, code=s.code, kind=s.kind, zone_id=s.zone_id,
                                              grid_col=s.grid_col, grid_row=s.grid_row) for s in stations]))


def owned_group(db, user, group_id: str) -> tuple[SensorGroup, FarmField]:
    group = db.get(SensorGroup, group_id)
    if not group:
        raise HTTPException(404, "找不到此感測群組")
    return group, owned_field(db, user, group.field_id)


@router.get("/fields/{field_id}/sensor-groups")
def list_groups(field_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    owned_field(db, user, field_id)
    groups = db.scalars(select(SensorGroup).where(SensorGroup.field_id == field_id)).all()
    return dict(items=[group_output(db, g) for g in groups])


@router.post("/fields/{field_id}/sensor-groups", status_code=201)
def create_group(field_id: str, payload: GroupInput, db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "感測群組只能由農民本人新增")
    if payload.source_id and not db.get(DataSource, payload.source_id):
        raise HTTPException(404, "找不到此資料來源")
    group = SensorGroup(field_id=field_id, name=payload.name, source_id=payload.source_id, metrics=payload.metrics,
                       is_demo=False)
    db.add(group)
    db.commit()
    return group_output(db, group)


@router.delete("/sensor-groups/{group_id}")
def delete_group(group_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    group, field = owned_group(db, user, group_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "感測群組只能由農民本人刪除")
    db.delete(group)
    db.commit()
    return {"ok": True}


@router.post("/sensor-groups/{group_id}/stations", status_code=201)
def create_station(group_id: str, payload: StationInput, db: Session = Depends(get_db), user=Depends(current_user)):
    group, field = owned_group(db, user, group_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "站點只能由農民本人新增")
    if payload.zone_id:
        zone = db.get(FarmZone, payload.zone_id)
        if not zone or zone.field_id != field.id:
            raise HTTPException(404, "找不到此分區")
    station = SensorStation(field_id=field.id, group_id=group.id, code=payload.code, kind=payload.kind,
                            zone_id=payload.zone_id, grid_col=payload.grid_col, grid_row=payload.grid_row,
                            source="manual", is_demo=False)
    db.add(station)
    db.commit()
    return json_snapshot(dict(id=station.id, code=station.code, kind=station.kind, zone_id=station.zone_id,
                              grid_col=station.grid_col, grid_row=station.grid_row))


@router.delete("/stations/{station_id}")
def delete_station(station_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    station = db.get(SensorStation, station_id)
    if not station:
        raise HTTPException(404, "找不到此站點")
    field = owned_field(db, user, station.field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "站點只能由農民本人刪除")
    db.delete(station)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# data_sources：連線設定與測試
# --------------------------------------------------------------------------

class DataSourceInput(Input):
    kind: str = Field(pattern="^(" + "|".join(DATA_SOURCE_KINDS) + ")$")
    label: str = Field(min_length=1, max_length=120)
    config: dict = Field(default_factory=dict)


class DataSourcePatch(Input):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    config: dict | None = None


def source_output(source: DataSource) -> dict:
    return json_snapshot(dict(id=source.id, kind=source.kind, label=source.label, config=mask_config(source.config),
                              status=source.status, last_sync_at=source.last_sync_at, last_error=source.last_error,
                              created_at=source.created_at, updated_at=source.updated_at))


def owned_source(db, user, source_id: str) -> DataSource:
    source = db.get(DataSource, source_id)
    if not source:
        raise HTTPException(404, "找不到此資料來源")
    access_farmer(db, user, source.farmer_id)
    if user.role != "admin" and user.id != source.farmer_id:
        raise HTTPException(403, "資料來源只能由農民本人管理")
    return source


@router.get("/farmers/{farmer_id}/data-sources")
def list_sources(farmer_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    sources = db.scalars(select(DataSource).where(DataSource.farmer_id == farmer_id).order_by(DataSource.created_at)).all()
    # secret_key_is_default 一起回：這頁存的密碼就是用那把金鑰加密的，金鑰還是開發預設值時
    # 畫面要講出來——log 的警告只有管理者看得到，真正在這頁輸入密碼的人看不到。
    return dict(items=[source_output(s) for s in sources], secret_key_is_default=settings.secret_key_is_default)


@router.post("/farmers/{farmer_id}/data-sources", status_code=201)
def create_source(farmer_id: str, payload: DataSourceInput, db: Session = Depends(get_db), user=Depends(current_user)):
    farmer = access_farmer(db, user, farmer_id)
    if user.role != "admin" and user.id != farmer.id:
        raise HTTPException(403, "資料來源只能由農民本人新增")
    source = DataSource(farmer_id=farmer_id, kind=payload.kind, label=payload.label,
                       config=encrypt_config(payload.config), status="never", last_error="")
    db.add(source)
    db.commit()
    return source_output(source)


@router.patch("/data-sources/{source_id}")
def patch_source(source_id: str, payload: DataSourcePatch, db: Session = Depends(get_db), user=Depends(current_user)):
    source = owned_source(db, user, source_id)
    if payload.label is not None:
        source.label = payload.label
    if payload.config is not None:
        # 遮罩字串原樣送回代表「沒改」，這包含「需重新輸入」——畫面上那格顯示的是提示文字不是密碼，
        # 使用者沒動它就照樣送回來，不能把提示文字本身存成密碼。
        from app.precision.crypto import MASK, NEEDS_REENTRY
        merged = dict(source.config)
        for key, value in payload.config.items():
            if value in (MASK, NEEDS_REENTRY):
                continue
            merged[key] = value
        source.config = encrypt_config(merged)
    db.commit()
    return source_output(source)


@router.delete("/data-sources/{source_id}")
def delete_source(source_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    source = owned_source(db, user, source_id)
    db.delete(source)
    db.commit()
    return {"ok": True}


@router.post("/data-sources/{source_id}/test")
def test_source(source_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    source = owned_source(db, user, source_id)
    now = utcnow()
    if source.kind in FILE_BASED_SOURCE_KINDS:
        # 檔案型來源沒有「連得上連不上」這回事，測試就是確認設定存在、可以開始接收上傳。
        source.status, source.last_sync_at, source.last_error = "connected", now, ""
    elif source.kind == "weather":
        farmer = db.get(FarmAccount, source.farmer_id)
        county = farmer.weather_county or weather.county_from_location(farmer.location)
        if not county:
            source.status, source.last_error = "error", "尚未設定所在縣市，無法取得預報"
        else:
            result = weather.get_forecast(county, now=now)
            if result["status"] == "ok":
                source.status, source.last_sync_at, source.last_error = "connected", now, ""
            else:
                source.status, source.last_error = "error", result["message"]
    else:
        # sensor_http / sensor_mqtt / gis_wms：介面已經定義好（見 sources.py），但還沒有真的協定實作。
        source.status = "error"
        source.last_error = "即時連線尚未支援；示範階段請改用 CSV 上傳或檔案匯入。"
    db.commit()
    return source_output(source)


# --------------------------------------------------------------------------
# CSV 讀數上傳
# --------------------------------------------------------------------------

@router.post("/sensor-groups/{group_id}/readings", status_code=201)
def upload_readings(group_id: str, file: UploadFile = File(...), db: Session = Depends(get_db),
                    user=Depends(current_user)):
    group, field = owned_group(db, user, group_id)
    text = read_csv_upload(file)
    try:
        rows = CsvUploadSource(text).pull(None)
    except ValueError as error:
        raise HTTPException(422, str(error))
    stations = {s.code: s for s in db.scalars(select(SensorStation).where(SensorStation.group_id == group.id))}
    unknown = sorted({row.station_code for row in rows if row.station_code not in stations})
    if unknown:
        raise HTTPException(422, f"這個群組沒有站代碼「{'、'.join(unknown)}」，請先在「站點」新增或修正 CSV")

    # SQLite 的 DateTime(timezone=True) 不會真的存時區，讀回來一律是不帶時區的值——跟 as_utc() 在別處
    # 讀 created_at／expires_at 補時區是同一個坑。這裡不補的話，比對「這筆是不是已經上傳過」永遠比不出來，
    # 重送同一份 CSV 就會每次都想再插入一次，撞到 uq_sensor_reading。
    existing = {(sid, metric, as_utc(ts)) for sid, metric, ts in db.execute(
        select(SensorReading.station_id, SensorReading.metric, SensorReading.ts)
        .where(SensorReading.station_id.in_([s.id for s in stations.values()])))}
    inserted = 0
    for row in rows:
        station = stations[row.station_code]
        key = (station.id, row.metric, as_utc(row.ts))
        if key in existing:
            continue
        existing.add(key)
        db.add(SensorReading(station_id=station.id, ts=row.ts, metric=row.metric, value=row.value, unit=row.unit,
                             is_demo=False))
        inserted += 1

    now = utcnow()
    if group.source_id:
        source = db.get(DataSource, group.source_id)
        if source:
            source.status, source.last_sync_at, source.last_error = "connected", now, ""
    service.try_analyze(db, field, now=now, today=taipei_today())
    db.commit()
    return dict(inserted=inserted, skipped_duplicates=len(rows) - inserted, total_in_file=len(rows))


# --------------------------------------------------------------------------
# 無人機影像（示範階段：CSV 路徑）
# --------------------------------------------------------------------------

def uav_output(mission: UavMission) -> dict:
    return json_snapshot(dict(id=mission.id, field_id=mission.field_id, flown_on=mission.flown_on, product=mission.product,
                              product_label=UAV_PRODUCT_LABELS[mission.product], file_key=mission.file_key,
                              crs=mission.crs, status=mission.status, note=mission.note, is_demo=mission.is_demo,
                              created_at=mission.created_at))


@router.get("/fields/{field_id}/uav")
def list_uav_missions(field_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    owned_field(db, user, field_id)
    missions = db.scalars(select(UavMission).where(UavMission.field_id == field_id)
                          .order_by(UavMission.flown_on.desc(), UavMission.created_at.desc())).all()
    return dict(items=[uav_output(m) for m in missions])


@router.post("/fields/{field_id}/uav", status_code=201)
def upload_uav_mission(field_id: str, flown_on: date = Form(...), product: str = Form(...),
                       file: UploadFile = File(...), db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "無人機任務只能由農民本人上傳")
    if product not in UAV_PRODUCTS:
        raise HTTPException(422, f"product 只能是 {'、'.join(UAV_PRODUCTS)}")
    if flown_on > taipei_today():
        raise HTTPException(422, "飛行日期不能晚於今天")
    text = read_csv_upload(file)

    from app.services.farm_media import resolve_key, write_atomic
    from app.core.config import settings
    now = utcnow()
    key = f"{field.farmer_id}/uav/{field_id}/{now:%Y%m%d%H%M%S}_{file.filename or 'upload.csv'}"
    write_atomic(resolve_key(settings.media_root, key), text.encode("utf-8"))

    mission = UavMission(field_id=field_id, flown_on=flown_on, product=product, file_key=key, crs="EPSG:4326",
                        status="uploaded", note="", is_demo=False)
    db.add(mission)
    db.flush()

    if product in UAV_LAYER_PRODUCTS:
        try:
            cell_values = parse_cell_csv(text, field.grid_cols, field.grid_rows)
        except ValueError as error:
            mission.status = "failed"
            mission.note = str(error)
            db.commit()
            raise HTTPException(422, str(error))
        zones = db.scalars(select(FarmZone).where(FarmZone.field_id == field_id)).all()
        db.add(FieldLayer(field_id=field_id, kind="ndvi", captured_on=flown_on,
                          source_text=f"無人機{UAV_PRODUCT_LABELS[product]}／{file.filename or 'CSV'}",
                          cell_values=cell_values, zone_values=zone_averages(cell_values, zones), is_demo=False))
        mission.status = "done"
    else:
        mission.status = "done"
        mission.note = "RGB 為正射影像，僅供留存比對，示範階段不產生數值圖層。"

    service.try_analyze(db, field, now=now, today=taipei_today())
    db.commit()
    return uav_output(mission)


# --------------------------------------------------------------------------
# GIS 邊界（一律存 EPSG:4326）
# --------------------------------------------------------------------------

class BoundaryInput(Input):
    geojson: dict
    crs: str = Field(default="EPSG:4326", max_length=40)
    source: str = Field(default="upload", pattern="^(hand_grid|upload|cadastral_wms)$")

    @model_validator(mode="after")
    def not_empty(self):
        if not self.geojson:
            raise ValueError("請提供 GeoJSON 內容")
        return self


def boundary_output(boundary: FieldBoundary) -> dict:
    return json_snapshot(dict(id=boundary.id, field_id=boundary.field_id, geojson=boundary.geojson,
                              source=boundary.source, outside_cells=boundary.outside_cells,
                              imported_at=boundary.imported_at))


@router.get("/fields/{field_id}/boundary")
def get_boundary(field_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    owned_field(db, user, field_id)
    boundary = db.scalar(select(FieldBoundary).where(FieldBoundary.field_id == field_id))
    return dict(boundary=boundary_output(boundary) if boundary else None)


@router.post("/fields/{field_id}/boundary", status_code=201)
def import_boundary(field_id: str, payload: BoundaryInput, db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "邊界只能由農民本人匯入")
    try:
        rings = geo.polygon_rings(payload.geojson)
        converted = [geo.convert_ring(ring, payload.crs) for ring in rings]
    except ValueError as error:
        raise HTTPException(422, str(error))
    outside = geo.outside_cells(converted, field.grid_cols, field.grid_rows)
    stored_geojson = {"type": "MultiPolygon", "coordinates": [[ring] for ring in converted]}

    existing = db.scalar(select(FieldBoundary).where(FieldBoundary.field_id == field_id))
    now = utcnow()
    if existing:
        existing.geojson, existing.outside_cells = stored_geojson, outside
        existing.source, existing.imported_at = payload.source, now
        boundary = existing
    else:
        boundary = FieldBoundary(field_id=field_id, geojson=stored_geojson, outside_cells=outside,
                                 source=payload.source, imported_at=now)
        db.add(boundary)
    db.commit()
    return boundary_output(boundary)


@router.delete("/fields/{field_id}/boundary")
def delete_boundary(field_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "邊界只能由農民本人移除")
    boundary = db.scalar(select(FieldBoundary).where(FieldBoundary.field_id == field_id))
    if not boundary:
        raise HTTPException(404, "這個田區還沒有邊界")
    db.delete(boundary)
    db.commit()
    return {"ok": True}
