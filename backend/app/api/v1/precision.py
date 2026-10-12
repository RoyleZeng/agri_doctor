"""精準模式 API。

權限沿用農場工作區：農民本人與已連結的植物醫生都能看、能要求重新分析；
接受、標記已執行、略過只有農民本人能按——建議會變成農民的待辦與紀錄，決定權在農民；
植物醫生看得到卡片，但這三個動作一律回 403，前端對應顯示「由農民決定」。
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.planning import Input, access_farmer, current_user, protect_write, public_user, role_required
from app.db.session import get_db
from app.models.precision import FarmField, Recommendation
from app.precision import rules as engine
from app.precision import service
from app.services.consult import json_snapshot, taipei_today

router = APIRouter(prefix="/precision", tags=["precision"], dependencies=[Depends(protect_write)])

STATUS_LABELS = {"proposed": "建議中", "accepted": "已接受", "done": "已執行", "dismissed": "已略過", "expired": "已過期"}
# 展示層要的門檻，只挑會顯示給農民看的欄位；json 其餘內部係數（round_kg、mm_per_pct…）不外流。
RULES_OUTPUT_KEYS = {
    "irrigate": ("metric", "threshold_pct", "window_hours", "rain_skip_mm_48h", "rain_skip_probability_pct"),
    "vrt_fertilizer": ("min_days_since_fertilizer", "max_layer_age_days", "classes"),
}
OUTPUT_KEYS = ("id", "field_id", "kind", "title", "rationale", "five_r", "payload", "evidence", "proposed_at", "expires_at",
               "event_id", "work_log_id", "rule_version", "decided_by", "decided_at", "status_note", "expired_reason",
               "is_demo", "created_at", "updated_at")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DismissBody(Input):
    reason: str = Field(default="", max_length=500)


class PrecisionToggle(Input):
    farmer_id: str | None = Field(default=None, max_length=36)
    enabled: bool


def rules_output(rules: dict) -> dict:
    return {kind: {key: rules[kind][key] for key in keys} for kind, keys in RULES_OUTPUT_KEYS.items()}


def output(row: Recommendation, field: FarmField, now: datetime) -> dict:
    status = service.effective_status(row, now)
    return json_snapshot({key: getattr(row, key) for key in OUTPUT_KEYS} | dict(
        status=status, status_label=STATUS_LABELS[status], field_name=field.name))


def owned_field(db, user, field_id: str) -> FarmField:
    field = db.get(FarmField, field_id)
    if not field:
        raise HTTPException(404, "找不到此田區")
    access_farmer(db, user, field.farmer_id)
    return field


@router.patch("/me")
def toggle_precision(payload: PrecisionToggle, db: Session = Depends(get_db), user=Depends(current_user)):
    role_required(user, "farmer")
    target = user
    if payload.farmer_id and payload.farmer_id != user.id:
        role_required(user, "admin")
        target = access_farmer(db, user, payload.farmer_id)
    target.precision_enabled = payload.enabled
    db.commit()
    return public_user(target)


@router.get("/farmers/{farmer_id}/overview")
def overview(farmer_id: str, field_id: str | None = Query(None, max_length=36), db: Session = Depends(get_db),
            user=Depends(current_user)):
    """儀表板一次載入要的東西：田區清單（給地點 chip）、選定田區的詳情、該田區的建議、門檻。"""
    farmer = access_farmer(db, user, farmer_id)
    now = utcnow()
    fields = service.field_list(db, farmer_id)
    field_summaries = [dict(id=f.id, name=f.name, area_text=f.area_text, is_demo=f.is_demo) for f in fields]
    if field_id:
        selected = next((f for f in fields if f.id == field_id), None)
        if selected is None:
            raise HTTPException(404, "找不到此田區")
    else:
        selected = fields[0] if fields else None
    recommendations = []
    if selected:
        # GET 不寫資料庫：過期只在讀取時當場算（service.effective_status），時間到了才在下次「重新分析」寫回。
        rows = db.scalars(select(Recommendation).where(Recommendation.field_id == selected.id)
                          .order_by(Recommendation.proposed_at.desc(), Recommendation.kind)).all()
        recommendations = [output(row, selected, now) for row in rows]
    return json_snapshot(dict(
        precision_enabled=farmer.precision_enabled, fields=field_summaries,
        field=service.field_detail(db, selected, now=now) if selected else None,
        recommendations=recommendations, rules=rules_output(engine.load_rules()),
    ))


@router.get("/fields/{field_id}/layers/{kind}")
def field_layer(field_id: str, kind: str, db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if kind not in ("ndvi", "soil_moisture", "ph", "yield_prev"):
        raise HTTPException(422, "圖層種類只能是 ndvi、soil_moisture、ph、yield_prev")
    layer = service.latest_layer(db, field.id, kind)
    return json_snapshot(dict(
        field=dict(grid_cols=field.grid_cols, grid_rows=field.grid_rows),
        layer=None if layer is None else dict(id=layer.id, kind=layer.kind, captured_on=layer.captured_on,
                                              source_text=layer.source_text, cell_values=layer.cell_values,
                                              zone_values=layer.zone_values)))


@router.get("/fields/{field_id}/chart")
def chart(field_id: str, metric: str = Query("soil_moisture", max_length=24), hours: int = Query(720, ge=1, le=24 * 60),
          db: Session = Depends(get_db), user=Depends(current_user)):
    field = owned_field(db, user, field_id)
    if metric not in service.CHART_METRICS:
        raise HTTPException(422, f"metric 只能是 {'、'.join(service.CHART_METRICS)}")
    return json_snapshot(service.chart_data(db, field, metric=metric, hours=hours, now=utcnow(), rules=engine.load_rules()))


@router.get("/farmers/{farmer_id}/recommendations")
def list_recommendations(farmer_id: str, status: str | None = Query(None, max_length=80),
                         field_id: str | None = Query(None, max_length=36),
                         db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    wanted = {part.strip() for part in status.split(",") if part.strip()} if status else None
    if wanted and wanted - set(STATUS_LABELS):
        raise HTTPException(422, "狀態只能是 proposed、accepted、done、dismissed、expired")
    fields = {field.id: field for field in db.scalars(select(FarmField).where(FarmField.farmer_id == farmer_id))}
    if field_id and field_id not in fields:
        raise HTTPException(404, "找不到此田區")
    ids = [field_id] if field_id else list(fields)
    rows = db.scalars(select(Recommendation).where(Recommendation.field_id.in_(ids))
                      .order_by(Recommendation.proposed_at.desc(), Recommendation.kind)).all() if ids else []
    now = utcnow()
    items = [output(row, fields[row.field_id], now) for row in rows]
    return dict(items=[item for item in items if not wanted or item["status"] in wanted])


@router.get("/recommendations/{recommendation_id}")
def get_recommendation(recommendation_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    row = db.get(Recommendation, recommendation_id)
    if not row:
        raise HTTPException(404, "找不到此建議")
    return output(row, owned_field(db, user, row.field_id), utcnow())


@router.post("/fields/{field_id}/analyze")
def analyze(field_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """手動「重新分析」：同田區同種建議只更新不疊加；前提不再成立的建議會被提前撤回並附原因；
    完全沒有建議的原因（等雨、資料不足…）一併回傳，畫面上不能什麼都沒發生。"""
    field = owned_field(db, user, field_id)
    now = utcnow()
    saved, notes, withdrawn = service.analyze_field(db, field, now=now, today=taipei_today())
    db.commit()
    return dict(items=[output(row, field, now) for row in saved], notes=[asdict(note) for note in notes],
               withdrawn=[output(w.recommendation, field, now) for w in withdrawn])


def locked_recommendation(db, user, recommendation_id: str) -> tuple[Recommendation, FarmField]:
    row = db.scalar(select(Recommendation).where(Recommendation.id == recommendation_id).with_for_update())
    if not row:
        raise HTTPException(404, "找不到此建議")
    field = owned_field(db, user, row.field_id)
    if user.role != "admin" and user.id != field.farmer_id:
        raise HTTPException(403, "建議只能由農民本人接受、標記已執行或略過")
    return row, field


def guard_freshness(db, row: Recommendation, now: datetime) -> None:
    if service.effective_status(row, now) == "expired" and row.status == "proposed":
        row.status = "expired"
        row.expired_reason = row.expired_reason or service.GENERIC_EXPIRY_REASON
        row.updated_at = now
        db.commit()
        raise HTTPException(409, "這則建議已過期，田間狀況可能已經改變，請重新分析")


@router.post("/recommendations/{recommendation_id}/accept")
def accept(recommendation_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """接受：在對應的作物規劃裡建一個今天的待辦事項，來源標「精準模式建議」。"""
    row, field = locked_recommendation(db, user, recommendation_id)
    now = utcnow()
    guard_freshness(db, row, now)
    if row.status != "proposed":
        raise HTTPException(409, f"這則建議目前是「{STATUS_LABELS[row.status]}」，不能接受")
    event = service.accept_recommendation(db, user, row, field, now, taipei_today())
    if event is None:
        raise HTTPException(422, f"找不到「{field.name}」對應的單一作物規劃，請先在「我的農場」建立或確認規劃後再接受建議")
    db.commit()
    return output(row, field, now) | dict(event_id=event.id)


@router.post("/recommendations/{recommendation_id}/done")
def done(recommendation_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """直接標記已執行，不建立農事日誌。正常流程請改用「已執行，記到日誌」——存檔時帶 recommendation_id
    會自動把這則建議標成已執行並留下 work_log_id，才有紀錄可以追溯。"""
    row, field = locked_recommendation(db, user, recommendation_id)
    now = utcnow()
    guard_freshness(db, row, now)
    if row.status not in ("proposed", "accepted"):
        raise HTTPException(409, f"這則建議目前是「{STATUS_LABELS[row.status]}」，不能標記已執行")
    row.status = "done"
    row.decided_by = user.id
    row.decided_at = now
    row.updated_at = now
    db.commit()
    return output(row, field, now)


@router.post("/recommendations/{recommendation_id}/dismiss")
def dismiss(recommendation_id: str, payload: DismissBody | None = None, db: Session = Depends(get_db),
            user=Depends(current_user)):
    row, field = locked_recommendation(db, user, recommendation_id)
    now = utcnow()
    guard_freshness(db, row, now)
    if row.status != "proposed":
        raise HTTPException(409, f"這則建議目前是「{STATUS_LABELS[row.status]}」，不能略過")
    row.status = "dismissed"
    row.decided_by = user.id
    row.decided_at = now
    row.updated_at = now
    row.status_note = (payload.reason if payload else "").strip()
    db.commit()
    return output(row, field, now)
