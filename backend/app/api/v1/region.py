"""區域作物與病蟲害地圖 API。

權限（跟 `docs/farm-log-and-precision.md` 第三部分的角色表一致）：
* 農民只看得到自己所在鄉鎮的匯總，query 裡的 county/district 一律被覆蓋成自己的。
* 植物醫生與主管機關／改良場看得到全縣市；`districts/{district}` 多一段「本鄉鎮已連結農場」
  只有植物醫生看得到（機關與農民都看不到）。
* 主動調查登錄（`POST /surveys`）與報表匯出只開放植物醫生／機關；農民只讀。
* 永遠不把個別農場的資料往外送——彙整結果本身已經在 `region_daily_stats` 套過 `farms < 3` 抑制，
  這裡的角色檢查是「誰能看彙整後的畫面」，不是又一層資料過濾。
"""
# 這個模組**刻意不用** `from __future__ import annotations`：那會讓所有型別註記變成字串
# （ForwardRef），而 FastAPI 解析「普通 class 當 Depends」（下面的 Filters）時還原不回來，
# 會在請求當下丟 PydanticUserError 變成 500。Python 3.11+ 本來就支援 `X | None` 的執行期註記。
# 踩過一次：離線測試跑 3.11 沒事，映像檔的 3.12 一打開區域地圖就 500。

from datetime import date
from io import BytesIO
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.planning import DoctorFarmer, FarmAccount, Input, access_farmer, current_user, protect_write, role_required
from app.db.session import get_db
from app.models.region import AlertThreshold, RegionDailyStat, SurveyRecord
from app.services import region_stats as service
from app.services.consult import json_snapshot, taipei_today

router = APIRouter(prefix="/region", tags=["region map"], dependencies=[Depends(protect_write)])

DEFAULT_COUNTY = "高雄市"
REPORT_PAGE_SIZE = 50


DISTRICT_MISSING_MESSAGE = "您的農場地區尚未填寫鄉鎮，填寫後就能看到所在鄉鎮的彙整。"


def resolve_scope(user, county: str | None, district: str | None) -> tuple[str, str | None] | None:
    """農民只能看自己的鄉鎮；植物醫生／機關可以自己選（預設高雄市、不限鄉鎮）。

    農民的地區欄位解析不出鄉鎮時回 `None`，由呼叫端決定要擋還是回「尚未提供」——
    `/summary` 選擇回 200 `available=False`（見該函式的註解），其餘端點照舊 422。
    """
    if user.role == "farmer":
        from app.services.weather import county_from_location
        county = county_from_location(user.location)
        if county and not service.load_districts(county):
            return county, None
        return service.normalize_location(user.location) or None
    return county or DEFAULT_COUNTY, district


def scoped_county_district(user, county: str | None, district: str | None) -> tuple[str, str | None]:
    scope = resolve_scope(user, county, district)
    if scope is None:
        raise HTTPException(422, DISTRICT_MISSING_MESSAGE)
    return scope


def query_rows(db: Session, *, county: str, district: str | None, date_from: date | None, date_to: date | None,
                crop: str | None, pest: str | None, source: str | None) -> list[RegionDailyStat]:
    stmt = select(RegionDailyStat).where(RegionDailyStat.county == county)
    if district:
        stmt = stmt.where(RegionDailyStat.district == district)
    if date_from:
        stmt = stmt.where(RegionDailyStat.day >= date_from)
    if date_to:
        stmt = stmt.where(RegionDailyStat.day <= date_to)
    if crop:
        stmt = stmt.where(RegionDailyStat.crop_text == crop)
    if pest:
        stmt = stmt.where(RegionDailyStat.pest_text == pest)
    if source:
        stmt = stmt.where(RegionDailyStat.source == source)
    return db.scalars(stmt).all()


class Filters:
    # 型別註記**不能**是字串（ForwardRef）：這個模組有 `from __future__ import annotations`，
    # 而 FastAPI 解析「普通 class 當 Depends」的參數時無法還原 Annotated 裡的 ForwardRef，
    # 會在請求當下丟 PydanticUserError 變成 500。用 Optional[...] 寫成執行期就存在的實體型別。
    # （踩過一次：離線測試跑 Python 3.11 沒事，映像檔的 3.12 一打開區域地圖就 500。）
    def __init__(self, county: Optional[str] = Query(None, max_length=12),
                 district: Optional[str] = Query(None, max_length=20),
                 date_from: Optional[date] = Query(None, alias="from"),
                 date_to: Optional[date] = Query(None, alias="to"),
                 crop: Optional[str] = Query(None, max_length=80),
                 pest: Optional[str] = Query(None, max_length=160),
                 source: Optional[Literal["consult", "scout", "plan", "survey"]] = Query(None)):
        self.county, self.district = county, district
        self.date_from, self.date_to, self.crop, self.pest, self.source = date_from, date_to, crop, pest, source


@router.get("/geo")
def geo(county: str | None = Query(None, max_length=12), user=Depends(current_user)):
    """鄉鎮界 GeoJSON，前端疊在 OSM 底圖上畫色階；農民也看得到（畫的還是只有自己那格會有數字）。"""
    resolved_county, _ = scoped_county_district(user, county, None)
    return dict(type="FeatureCollection", features=service.features_for_county(resolved_county))


@router.get("/summary")
def summary(filters: Filters = Depends(), db: Session = Depends(get_db), user=Depends(current_user)):
    # 「鄉鎮還沒填」不是請求錯誤，是這個帳號還沒設定完，回 422 會讓前端畫成紅字錯誤。
    # 這裡回 200 + available=False + reason，前端據此畫「去農場設定填寫鄉鎮」的空狀態。
    # 前端是這一頁的第一支請求：available=False 就不會再打 districts／geo，省掉兩個必然失敗的 422。
    scope = resolve_scope(user, filters.county, filters.district)
    if scope is None:
        return dict(county=DEFAULT_COUNTY, available=False, reason="district_missing",
                    message=DISTRICT_MISSING_MESSAGE,
                    kpi=dict(count=None, area_ha=None, metric=None), top_crops=[], alerts=[], legend=[])
    county, district = scope
    if not service.load_districts(county):
        return dict(county=county, available=False, reason="county_unsupported",
                    message="尚未提供這個縣市的區域地圖資料",
                    kpi=dict(count=None, area_ha=None, metric=None), top_crops=[], alerts=[], legend=[])
    rows = query_rows(db, county=county, district=district, date_from=filters.date_from, date_to=filters.date_to,
                       crop=filters.crop, pest=filters.pest, source=filters.source)
    kpi = service.summarize_district_rows(rows)

    crop_totals: dict[str, int] = {}
    for row in rows:
        if row.count is not None:
            crop_totals[row.crop_text] = crop_totals.get(row.crop_text, 0) + row.count
    top_crops = [dict(crop_text=crop, count=count) for crop, count in
                 sorted(crop_totals.items(), key=lambda item: item[1], reverse=True)[:5]]

    districts = [district] if district else list(service.load_districts(county))
    today = taipei_today()
    alerts = []
    for d in districts:
        for a in service.district_alerts(db, county, d, today):
            alerts.append(dict(district=d, **a))

    district_totals = {}
    for row in rows:
        if row.count is not None:
            district_totals[row.district] = district_totals.get(row.district, 0) + row.count
    legend = service.color_scale(list(district_totals.values()))

    return json_snapshot(dict(county=county, available=True, reason=None, kpi=kpi, top_crops=top_crops,
                              alerts=alerts, legend=legend))


@router.get("/districts")
def districts(filters: Filters = Depends(), db: Session = Depends(get_db), user=Depends(current_user)):
    county, forced_district = scoped_county_district(user, filters.county, filters.district)
    names = [forced_district] if forced_district else list(service.load_districts(county))
    if not names:
        return json_snapshot(dict(county=county, available=False, districts=[]))
    today = taipei_today()
    out = []
    for name in names:
        rows = query_rows(db, county=county, district=name, date_from=filters.date_from, date_to=filters.date_to,
                           crop=filters.crop, pest=filters.pest, source=filters.source)
        summarized = service.summarize_district_rows(rows)
        alert = bool(service.district_alerts(db, county, name, today))
        out.append(dict(district=name, alert=alert, **summarized))
    return json_snapshot(dict(county=county, available=True, districts=out))


@router.get("/districts/{district}")
def district_detail(district: str, county: str | None = Query(None, max_length=12),
                     date_from: date | None = Query(None, alias="from"), date_to: date | None = Query(None, alias="to"),
                     source: Literal["consult", "scout", "plan", "survey"] | None = Query(None),
                     db: Session = Depends(get_db), user=Depends(current_user)):
    # 路徑已經指定鄉鎮，這個端點的 query 不再收 district——同名的 query 參數在有 {district} 路徑時
    # 會跟 FastAPI 的路徑參數解析衝突，且本來就多餘。
    county, forced_district = scoped_county_district(user, county, None)
    if forced_district and forced_district != district:
        raise HTTPException(404, "找不到此鄉鎮或尚未取得查看權限")
    if district not in service.load_districts(county):
        raise HTTPException(404, "找不到此鄉鎮")
    rows = query_rows(db, county=county, district=district, date_from=date_from, date_to=date_to,
                       crop=None, pest=None, source=source)

    by_crop_pest: dict[tuple[str, str | None], list[RegionDailyStat]] = {}
    for row in rows:
        by_crop_pest.setdefault((row.crop_text, row.pest_text), []).append(row)
    items = [dict(crop_text=crop, pest_text=pest, **service.summarize_district_rows(group))
             for (crop, pest), group in by_crop_pest.items()]
    items.sort(key=lambda item: (item["count"] is None, -(item["count"] or 0)))

    crop_totals: dict[str, int] = {}
    for row in rows:
        if row.count is not None:
            crop_totals[row.crop_text] = crop_totals.get(row.crop_text, 0) + row.count
    top_crops = [dict(crop_text=crop, count=count) for crop, count in
                 sorted(crop_totals.items(), key=lambda item: item[1], reverse=True)[:5]]

    today = taipei_today()
    out = dict(county=county, district=district, items=items, top_crops=top_crops,
               alerts=service.district_alerts(db, county, district, today))

    if user.role == "doctor":
        # 只有植物醫生看得到「已連結農場」，而且只算已連結、地區可正規化到這個鄉鎮的農民——
        # 不是整個鄉鎮的農場數（那會等於把彙整資料又拆回個別農場），單純是「你可以直接開單的對象」。
        linked = db.execute(select(FarmAccount.id, FarmAccount.name, FarmAccount.farm_name, FarmAccount.location).join(
            DoctorFarmer, DoctorFarmer.farmer_id == FarmAccount.id).where(DoctorFarmer.doctor_id == user.id)).all()
        farms = [dict(id=fid, name=name, farm_name=farm_name) for fid, name, farm_name, location in linked
                 if service.normalize_location(location) == (county, district)]
        out["linked_farms"] = farms

    return json_snapshot(out)


@router.get("/reports")
def reports(filters: Filters = Depends(), page: int = Query(1, ge=1), db: Session = Depends(get_db),
            user=Depends(current_user)):
    county, district = scoped_county_district(user, filters.county, filters.district)
    stmt = select(RegionDailyStat).where(RegionDailyStat.county == county)
    if district:
        stmt = stmt.where(RegionDailyStat.district == district)
    if filters.date_from:
        stmt = stmt.where(RegionDailyStat.day >= filters.date_from)
    if filters.date_to:
        stmt = stmt.where(RegionDailyStat.day <= filters.date_to)
    if filters.crop:
        stmt = stmt.where(RegionDailyStat.crop_text == filters.crop)
    if filters.pest:
        stmt = stmt.where(RegionDailyStat.pest_text == filters.pest)
    if filters.source:
        stmt = stmt.where(RegionDailyStat.source == filters.source)
    stmt = stmt.order_by(RegionDailyStat.day.desc(), RegionDailyStat.id)
    rows = db.scalars(stmt.offset((page - 1) * REPORT_PAGE_SIZE).limit(REPORT_PAGE_SIZE + 1)).all()
    has_more = len(rows) > REPORT_PAGE_SIZE
    rows = rows[:REPORT_PAGE_SIZE]
    # 匯總後的列本身已經不含農場身分（連結到哪個農場已經在彙整時丟掉），照樣回傳即可。
    keys = ("district", "day", "crop_text", "pest_text", "source", "count", "area_ha", "metric", "farms")
    items = [{k: getattr(r, k) for k in keys} for r in rows]
    for item, row in zip(items, rows):
        if row.count is None and row.area_ha is None and row.metric is None:
            item["farms"] = None  # 樣本不足時連農場數本身都不外傳，避免反推
    return json_snapshot(dict(page=page, has_more=has_more, items=items))


@router.get("/export.xlsx")
def export_xlsx(filters: Filters = Depends(), db: Session = Depends(get_db), user=Depends(current_user)):
    role_required(user, ("doctor", "agency"))
    county, district = scoped_county_district(user, filters.county, filters.district)
    rows = query_rows(db, county=county, district=district, date_from=filters.date_from, date_to=filters.date_to,
                       crop=filters.crop, pest=filters.pest, source=filters.source)
    rows = sorted(rows, key=lambda r: (r.district, r.day, r.crop_text))

    book = Workbook()
    sheet = book.active
    sheet.title = "區域彙整報表"
    headers = ["鄉鎮", "日期", "作物", "病蟲害", "來源", "件數", "面積(公頃)", "調查指標(每板隻數)", "貢獻農場數"]
    sheet.append(headers)
    source_labels = {"consult": "診斷案件", "scout": "農民巡田", "plan": "作物規劃", "survey": "主動調查"}
    for row in rows:
        insufficient = row.count is None and row.area_ha is None and row.metric is None
        sheet.append([
            row.district, row.day.isoformat(), row.crop_text, row.pest_text or "",
            source_labels.get(row.source, row.source),
            "樣本不足" if insufficient else row.count,
            "" if row.area_ha is None else row.area_ha,
            "" if row.metric is None else row.metric,
            "" if insufficient else row.farms,
        ])
    for i, _ in enumerate(headers, start=1):
        sheet.column_dimensions[sheet.cell(row=1, column=i).column_letter].width = 16

    buffer = BytesIO()
    book.save(buffer)
    # Content-Disposition 的 filename= 只能是 ASCII；中文縣市名放進 filename*=UTF-8'' 那段即可。
    ascii_name = "region-report.xlsx"
    local_name = quote(f"區域彙整報表_{county}.xlsx")
    return Response(buffer.getvalue(),
                     media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     headers={"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{local_name}",
                              "Cache-Control": "private, no-store"})


class SurveyInput(Input):
    district: str = Field(max_length=20)
    surveyed_on: date
    crop_text: str = Field(min_length=1, max_length=80)
    pest_text: str = Field(min_length=1, max_length=160)
    trap_count: float = Field(ge=0)
    traps: int = Field(default=1, ge=1)
    note: str = Field(default="", max_length=500)


@router.post("/surveys")
def create_survey(payload: SurveyInput, db: Session = Depends(get_db), user=Depends(current_user)):
    role_required(user, ("doctor", "agency"))
    if not service.county_for_district(payload.district):
        raise HTTPException(422, "尚未提供這個行政區的區域地圖資料")
    row = SurveyRecord(recorded_by=user.id, **payload.model_dump())
    db.add(row)
    db.commit()
    return dict(id=row.id)


@router.post("/reaggregate")
def reaggregate(db: Session = Depends(get_db), user=Depends(current_user)):
    """手動觸發彙整；排程化留給 EventBridge（見 docs/deployment.md），這裡先給人工按鈕與驗收腳本用。"""
    role_required(user, ("doctor", "agency"))
    result = service.reaggregate(db, taipei_today())
    # removed＝來源已經不在（日誌被撤回或改日期）而清掉的舊列；畫面要說得出來，不能默默消失。
    return dict(rows=result["written"], removed=result["removed"])
