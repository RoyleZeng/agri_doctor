"""農事日誌 API：記錄「實際做了什麼」。

掛在 /planning 底下，沿用農場工作區的 session cookie 與 access_farmer 權限。

幾條刻意的規則（改之前先看理由）：
* 施藥（spray）存檔時同時寫一筆 application_records——診療開單的抗藥性輪替檢查從那張表讀歷史，
  日誌不寫進去，輪替檢查就只看得到空白。
* 作用機制只有兩個來源：處方快照，或記錄時從藥劑登記挑選存下的快照。手動輸入的藥名不反查登記——
  中文藥名做字串比對會把單劑配成複方（見 tests/test_matching.py），寧可留空讓輪替檢查回「無法判斷」，
  也不要餵錯的機制。沒挑登記的施藥，事後可以「補選登記」回填。
* 從規劃事項記錄時把事項標完成，但不改 basis：basis 回答「日期是誰定的」（範本估計／使用者調整），
  跟「做完了沒」是兩件事。「已記錄」由是否有對應日誌推導（event_id 唯一）。
* 24 小時內可改可刪；之後只能改備註、補照片、補選登記。紀錄要交給產銷履歷與補助查核，不能事後消失或改寫。
"""
from __future__ import annotations

import calendar
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field, model_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.v1.consult import resolve_crop
from app.api.v1.planning import Input, access_farmer, access_plan, current_user, protect_write, serialize_plan
from app.core.config import settings
from app.db.session import get_db
from app.models import Pest
from app.models.consult import ApplicationRecord, ConsultCase, PrescriptionItem
from app.models.farm_log import FarmLogPhoto, FarmWorkLog
from app.models.planning import FarmAccount, FarmEvent, FarmPlan
from app.models.precision import FarmField, Recommendation
from app.precision import service as precision_service
from app.services.consult import json_snapshot, source_row, taipei_today
from app.services.farm_log import (COMPLIANCE_RULE_VERSION, EDIT_WINDOW, KIND_LABELS, as_utc, compliance, default_title,
                                  plain_number, within_edit_window)
from app.services.farm_media import MEDIA_TYPES, MediaError, photo_keys, process_image, resolve_key, write_atomic

router = APIRouter(prefix="/planning", tags=["farm logs"], dependencies=[Depends(protect_write)])

Kind = Literal["spray", "fertilize", "irrigate", "fieldwork", "harvest", "tillage", "scout", "other"]
PAGE_SIZE = 50
# 規劃事項類別 → 記錄時預設的日誌類型。生育時期與病蟲害巡查窗口不是作業，不在這裡。
EVENT_KIND_TO_LOG = {"care": "fieldwork", "nutrition": "fertilize"}
# 草稿處方醫師還會改，只有已儲存或已開立的處方才能記成「已執行」。
PRESCRIPTION_READY = ("saved", "issued")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LogBody(Input):
    done_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    field_text: str = Field(default="", max_length=160)
    crop_text: str = Field(default="", max_length=80)
    title: str = Field(default="", max_length=200)
    material_name: str = Field(default="", max_length=200)
    material_supplier: str = Field(default="", max_length=120)
    material_lot: str = Field(default="", max_length=80)
    dilution: str = Field(default="", max_length=40)
    amount_value: str = Field(default="", max_length=40)
    amount_unit: str = Field(default="", max_length=20)
    area_text: str = Field(default="", max_length=60)
    yield_value: str = Field(default="", max_length=40)
    yield_unit: str = Field(default="", max_length=20)
    yield_destination: str = Field(default="", max_length=120)
    labor_text: str = Field(default="", max_length=120)
    pest_id: int | None = Field(default=None, gt=0)
    pest_text: str = Field(default="", max_length=160)
    note: str = Field(default="", max_length=4000)
    registration_id: int | None = Field(default=None, gt=0)


class LogCreate(LogBody):
    done_on: date
    kind: Kind
    plan_id: str | None = Field(default=None, max_length=36)
    event_id: str | None = Field(default=None, max_length=36)
    prescription_item_id: str | None = Field(default=None, max_length=36)
    # 「已執行，記到日誌」：存檔成功把這則建議標成已執行並留下 work_log_id，決策才有紀錄可以追溯。
    recommendation_id: str | None = Field(default=None, max_length=36)


class LogPatch(LogBody):
    done_on: date | None = None
    kind: Kind | None = None

    @model_validator(mode="after")
    def required_not_cleared(self):
        if any(name in self.model_fields_set and getattr(self, name) is None for name in ("done_on", "kind")):
            raise ValueError("作業日期與類型不能清空")
        return self


LINK_FIELDS = ("plan_id", "event_id", "prescription_item_id")
# registration_id 不是單純照抄的欄位：換登記要重新抓快照，另外處理。
EDITABLE = tuple(name for name in LogPatch.model_fields if name != "registration_id")
OUTPUT_KEYS = ("id", "farmer_id", *LINK_FIELDS, "recommendation_id", "registration_id", "registration_snapshot",
               "field_id", "phi_days", "phi_source",
               *EDITABLE, "compliance", "created_by")
# 資料庫欄位長度；帶入的來源值（處方、登記）可能比使用者輸入上限長，超過不能靜默截斷成另一種藥。
COLUMN_LIMITS = {"material_name": 200, "dilution": 40}


def prescription_visible(user, case, farmer_id) -> bool:
    # 農民看得到自己農場的所有處方；植物醫生只看自己開的，不讀其他醫師替同一農場開的單。
    return case.farmer_id == farmer_id and (user.role != "doctor" or case.doctor_id == user.id)


def ready_prescription(db, user, farmer_id, item_id):
    item = db.get(PrescriptionItem, item_id)
    case = db.get(ConsultCase, item.case_id) if item else None
    if not case or not prescription_visible(user, case, farmer_id):
        raise HTTPException(404, "找不到此處方，或處方不屬於這個農場")
    if case.status not in PRESCRIPTION_READY:
        raise HTTPException(422, "處方仍是草稿，醫師儲存或開立後才能記錄為已執行")
    return item, case


_RECOMMENDATION_LABELS = {"proposed": "建議中", "accepted": "已接受", "done": "已執行", "dismissed": "已略過", "expired": "已過期"}
_RECOMMENDATION_KIND_TO_LOG = {"irrigate": "irrigate", "vrt_fertilizer": "fertilize"}


def load_recommendation(db, user, farmer_id, recommendation_id):
    """驗證建議屬於這個農場；只有農民本人能操作，跟接受、略過同一條規則。不查狀態或類型，留給呼叫端依情境判斷。"""
    if user.role != "admin" and user.id != farmer_id:
        raise HTTPException(403, "建議的執行紀錄只能由農民本人建立")
    row = db.scalar(select(Recommendation).where(Recommendation.id == recommendation_id).with_for_update())
    field = db.get(FarmField, row.field_id) if row else None
    if not row or not field or field.farmer_id != farmer_id:
        raise HTTPException(404, "找不到此建議")
    return row, field


def resolve_recommendation(db, user, farmer_id, recommendation_id, log_kind):
    """記錄「已執行」時用：還沒被執行過，而且日誌類型跟建議相符（irrigate→irrigate、vrt_fertilizer→fertilize）。"""
    row, field = load_recommendation(db, user, farmer_id, recommendation_id)
    if row.status not in ("proposed", "accepted"):
        raise HTTPException(409, f"這則建議目前是「{_RECOMMENDATION_LABELS[row.status]}」，不能標記已執行")
    if log_kind != _RECOMMENDATION_KIND_TO_LOG[row.kind]:
        raise HTTPException(422, "由建議帶入的紀錄類型不符，請確認是補灌還是施肥建議")
    return row, field


def resolve_links(db, user, farmer_id, payload):
    plan = event = item = None
    if payload.event_id:
        event = db.get(FarmEvent, payload.event_id)
        if not event:
            raise HTTPException(404, "找不到此規劃事項")
        plan = access_plan(db, user, event.plan_id)
        if plan.farmer_id != farmer_id:
            raise HTTPException(404, "找不到此規劃事項")
        if payload.plan_id and payload.plan_id != plan.id:
            raise HTTPException(422, "規劃事項不屬於指定的規劃")
        if event.kind not in EVENT_KIND_TO_LOG:
            # 與規劃 API 同一條規則：生育階段與病蟲害觀察窗口不是「做完」的作業。巡田觀察請直接記 scout，不帶事項。
            raise HTTPException(422, "生育階段與病蟲害觀察不能記為作業完成；巡田觀察請記成「巡田」類型")
        if plan.archived:
            raise HTTPException(409, "這一期規劃已封存，請先恢復規劃再記錄事項")
        if db.scalar(select(FarmWorkLog.id).where(FarmWorkLog.event_id == event.id)):
            raise HTTPException(409, "此規劃事項已有農事紀錄，請到日誌頁補充")
    elif payload.plan_id:
        plan = access_plan(db, user, payload.plan_id)
        if plan.farmer_id != farmer_id:
            raise HTTPException(404, "找不到此規劃")
    if payload.prescription_item_id:
        item, _ = ready_prescription(db, user, farmer_id, payload.prescription_item_id)
    return plan, event, item


def clean_codes(codes) -> list[str]:
    return [code for code in codes if isinstance(code, str) and code] if isinstance(codes, list) else []


def capture_registration(db, registration_id: int, crop_text: str) -> dict:
    """挑選登記當下的快照。來源每週更新，已存的日誌不能因為上游改版而換了作用機制或採收期。

    挑到的登記若核准於別的作物或含禁用成分，照樣存：日誌記的是事實，擋下來農民就只能不記。
    但把 crop_matches、banned 一起存，讓畫面當場標出來。
    """
    reg, result, row = source_row(db, registration_id)
    crop_id = resolve_crop(db, crop_text) if crop_text else None
    product_text = " ".join(part for part in (row.concentration, row.product_name, row.formulation) if part)
    return json_snapshot({
        "registration_id": reg.id, "registration_snapshot_id": reg.snapshot_id,
        "crop": result.crop, "pest": result.pest, "product_text": product_text,
        "dilution": row.dilution_text, "dose": row.dose_per_hectare, "phi_days": row.phi_days,
        "moa_codes": row.moa_codes, "banned": row.banned,
        # 作物名稱對不到標準作物時是 None（不知道），不是 False（確定不符）。
        "crop_matches": None if crop_id is None else crop_id == reg.crop_id,
        "captured_at": utcnow(),
    })


def moa_codes_for(db, log) -> list[str] | None:
    """施藥紀錄該用的作用機制。回 None 代表來源已不存在，保留施藥紀錄裡既有的值。"""
    if log.prescription_item_id:
        item = db.get(PrescriptionItem, log.prescription_item_id)
        return clean_codes((item.source_values or {}).get("moa_codes")) if item else None
    if log.registration_snapshot:
        return clean_codes(log.registration_snapshot.get("moa_codes"))
    return []


def snapshot_pest(db, values):
    if values["pest_id"] is None:
        return
    pest = db.get(Pest, values["pest_id"])
    if not pest:
        raise HTTPException(422, "找不到此病蟲害，請重新搜尋")
    values["pest_text"] = values["pest_text"] or pest.name


def check_values(values):
    if not values["field_text"]:
        raise HTTPException(422, "請填寫田區，或從規劃帶入")
    if values["kind"] == "spray" and not values["material_name"]:
        raise HTTPException(422, "施藥紀錄請填寫藥劑名稱，輪替檢查才讀得到")
    if values["done_on"] > taipei_today():
        raise HTTPException(422, "作業日期不能晚於今天；還沒做的事請記在規劃")
    if values["done_on"].year < 2000:
        raise HTTPException(422, "請輸入 2000 年以後的日期")
    for name, limit in COLUMN_LIMITS.items():
        if len(values[name] or "") > limit:
            raise HTTPException(422, f"帶入的{'藥劑名稱' if name == 'material_name' else '稀釋倍數'}超過欄位長度，請手動確認後填寫")


def apply_registration(db, log_values, registration_id):
    """選了登記就抓快照；藥名、稀釋倍數、針對的病蟲害沒填的從登記帶入，填了的尊重使用者寫法。"""
    snapshot = capture_registration(db, registration_id, log_values["crop_text"])
    log_values["material_name"] = log_values["material_name"] or snapshot["product_text"]
    log_values["dilution"] = log_values["dilution"] or (snapshot["dilution"] or "")
    if log_values["pest_id"] is None and not log_values["pest_text"] and snapshot.get("pest"):
        log_values["pest_id"] = snapshot["pest"]["id"]
    return snapshot


def resolve_field_id(db, farmer_id, field_text) -> str | None:
    """依名稱連到田區主檔。同名不只一筆就不猜，留 None 讓安全採收期核對退回名稱比對；真實田區優先於示範田區。"""
    from app.models.precision import FarmField

    rows = db.execute(select(FarmField.id, FarmField.is_demo)
                      .where(FarmField.farmer_id == farmer_id, FarmField.name == field_text)).all()
    pool = [row.id for row in rows if not row.is_demo] or [row.id for row in rows]
    return pool[0] if len(pool) == 1 else None


def phi_snapshot(kind, item=None, registration_snapshot=None) -> tuple[int | None, str]:
    """安全採收期快照，來源與作用機制同一條路：處方優先，其次挑選的登記；都沒有是 none，不填 0。"""
    if kind != "spray":
        return None, "none"
    if item is not None:
        return item.phi_days, "prescription"
    if registration_snapshot:
        return registration_snapshot.get("phi_days"), "registration"
    return None, "none"


def sync_application_record(db, log):
    record = db.scalar(select(ApplicationRecord).where(ApplicationRecord.work_log_id == log.id))
    if log.kind != "spray":
        if record:
            db.delete(record)
        return
    codes = moa_codes_for(db, log)
    if record is None:
        item = db.get(PrescriptionItem, log.prescription_item_id) if log.prescription_item_id else None
        record = ApplicationRecord(work_log_id=log.id, farmer_id=log.farmer_id, case_id=item.case_id if item else None,
                                   moa_codes=codes or [], note="")
        db.add(record)
    elif codes is not None:
        record.moa_codes = codes
    # 施藥紀錄是日誌的投影：日期、田區、藥名跟著日誌走；超過可修改期限後日誌不會再變，這裡也不會。
    record.plan_id = log.plan_id
    record.field_text = log.field_text
    record.product_text = log.material_name
    record.applied_on = log.done_on


PHOTO_KEYS = ("id", "log_id", "bytes", "width", "height", "taken_at_source", "lat", "lng", "caption", "sort_order")


def in_trash(photo, now) -> bool:
    return photo.deleted_at is not None and now - as_utc(photo.deleted_at) < timedelta(days=settings.media_trash_days)


def photo_output(photo):
    url = f"/api/v1/planning/media/{photo.id}"
    return {key: getattr(photo, key) for key in PHOTO_KEYS} \
        | dict(taken_at=as_utc(photo.taken_at), created_at=as_utc(photo.created_at), url=url, thumb_url=f"{url}?variant=thumb")


def trashed_output(photo):
    # 保留期內的照片列出來給日誌卡「還原」；保留期對使用者看不見就等於沒有。只給縮圖，原檔在還原前看不到。
    deleted_at = as_utc(photo.deleted_at)
    output = photo_output(photo)
    del output["url"]
    return output | dict(deleted_at=deleted_at, restorable_until=deleted_at + timedelta(days=settings.media_trash_days))


def outputs(db, logs):
    ids = [log.id for log in logs]
    records = {work_log_id: (record_id, codes) for work_log_id, record_id, codes in db.execute(
        select(ApplicationRecord.work_log_id, ApplicationRecord.id, ApplicationRecord.moa_codes)
        .where(ApplicationRecord.work_log_id.in_(ids)))} if ids else {}
    now = utcnow()
    photos, trashed = defaultdict(list), defaultdict(list)
    if ids:
        for photo in db.scalars(select(FarmLogPhoto).where(FarmLogPhoto.log_id.in_(ids))
                                .order_by(FarmLogPhoto.sort_order, FarmLogPhoto.created_at, FarmLogPhoto.id)):
            if photo.deleted_at is None:
                photos[photo.log_id].append(photo_output(photo))
            elif in_trash(photo, now):
                trashed[photo.log_id].append(trashed_output(photo))
    author_ids = {log.created_by for log in logs}
    authors = {row.id: row for row in db.execute(
        select(FarmAccount.id, FarmAccount.name, FarmAccount.role).where(FarmAccount.id.in_(author_ids)))} if author_ids else {}
    rx_ids = {log.prescription_item_id for log in logs if log.prescription_item_id}
    prescriptions = {row.item_id: dict(case_id=row.case_id, case_no=row.case_no, status=row.status) for row in db.execute(
        select(PrescriptionItem.id.label("item_id"), ConsultCase.id.label("case_id"), ConsultCase.case_no, ConsultCase.status)
        .join(ConsultCase, ConsultCase.id == PrescriptionItem.case_id).where(PrescriptionItem.id.in_(rx_ids)))} if rx_ids else {}
    result = []
    for log in logs:
        record_id, codes = records.get(log.id, (None, []))
        author = authors.get(log.created_by)
        result.append(
            {key: getattr(log, key) for key in OUTPUT_KEYS}
            | dict(kind_label=KIND_LABELS[log.kind], application_record_id=record_id, moa_codes=codes or [],
                   photos=photos[log.id], trashed_photos=trashed[log.id],
                   prescription=prescriptions.get(log.prescription_item_id),
                   # 植物醫生替農場記的紀錄要看得出是誰寫的，農民才知道為什麼自己改不了。
                   created_by_name=author.name if author else "", created_by_role=author.role if author else "",
                   created_at=as_utc(log.created_at), updated_at=as_utc(log.updated_at),
                   editable_until=as_utc(log.created_at) + EDIT_WINDOW,
                   locked=not within_edit_window(log.created_at, now)))
    return json_snapshot(result)


def owned_log(db, user, log_id):
    log = db.get(FarmWorkLog, log_id)
    if not log:
        raise HTTPException(404, "找不到此農事紀錄")
    access_farmer(db, user, log.farmer_id)
    if user.role != "admin" and user.id not in (log.farmer_id, log.created_by):
        # 植物醫生可以替連結的農場記錄，但別人記的紀錄只有農民本人或原記錄者能改。
        raise HTTPException(403, "只有農民本人或原記錄者可以修改這筆紀錄")
    return log


@router.get("/farmers/{farmer_id}/logs")
def list_logs(farmer_id: str, date_from: date | None = Query(None, alias="from"),
              date_to: date | None = Query(None, alias="to"), kind: Kind | None = None,
              field: str | None = Query(None, max_length=160), page: int = Query(1, ge=1, le=10000),
              db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "起始日期不能晚於結束日期")
    stmt = select(FarmWorkLog).where(FarmWorkLog.farmer_id == farmer_id)
    if date_from:
        stmt = stmt.where(FarmWorkLog.done_on >= date_from)
    if date_to:
        stmt = stmt.where(FarmWorkLog.done_on <= date_to)
    if kind:
        stmt = stmt.where(FarmWorkLog.kind == kind)
    if field and field.strip():
        stmt = stmt.where(FarmWorkLog.field_text == field.strip())
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    logs = db.scalars(stmt.order_by(FarmWorkLog.done_on.desc(), FarmWorkLog.done_time.desc().nulls_last(),
                                    FarmWorkLog.created_at.desc(), FarmWorkLog.id)
                      .offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE)).all()
    return dict(total=total, page=page, page_size=PAGE_SIZE, items=outputs(db, logs))


@router.post("/farmers/{farmer_id}/logs", status_code=201)
def create_log(farmer_id: str, payload: LogCreate, db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    plan, event, item = resolve_links(db, user, farmer_id, payload)
    values = payload.model_dump(exclude={*LINK_FIELDS, "registration_id", "recommendation_id"})
    recommendation = recommendation_field = None
    if payload.recommendation_id:
        recommendation, recommendation_field = resolve_recommendation(db, user, farmer_id, payload.recommendation_id,
                                                                       values["kind"])
        values["field_text"] = values["field_text"] or recommendation_field.name
        values["area_text"] = values["area_text"] or recommendation_field.area_text
    if plan:
        values["field_text"] = values["field_text"] or plan.field_name
        values["crop_text"] = values["crop_text"] or plan.crop_name
        values["area_text"] = values["area_text"] or plan.area
    if item:
        if values["kind"] != "spray":
            raise HTTPException(422, "由處方帶入的紀錄類型須為施藥")
        if payload.registration_id is not None:
            raise HTTPException(422, "由處方帶入的紀錄已有處方快照，不需另選藥劑登記")
        values["material_name"] = values["material_name"] or item.product_text
        values["dilution"] = values["dilution"] or (item.dilution or "")
    snapshot = None
    if payload.registration_id is not None:
        if values["kind"] != "spray":
            raise HTTPException(422, "只有施藥紀錄可以選擇藥劑登記")
        snapshot = apply_registration(db, values, payload.registration_id)
    snapshot_pest(db, values)
    check_values(values)
    values["title"] = values["title"] or default_title(
        values["kind"], material_name=values["material_name"], dilution=values["dilution"],
        fallback=event.title if event else "")
    now = utcnow()
    phi_days, phi_source = phi_snapshot(values["kind"], item, snapshot)
    log = FarmWorkLog(farmer_id=farmer_id, plan_id=plan.id if plan else None, event_id=event.id if event else None,
                      prescription_item_id=item.id if item else None, created_by=user.id,
                      registration_id=payload.registration_id, registration_snapshot=snapshot,
                      recommendation_id=recommendation.id if recommendation else None,
                      field_id=recommendation_field.id if recommendation_field else resolve_field_id(db, farmer_id, values["field_text"]),
                      phi_days=phi_days, phi_source=phi_source,
                      created_at=now, updated_at=now, compliance=compliance(values["kind"], values), **values)
    db.add(log)
    if event:
        event.completed = True
        event.updated_by = user.name
        # 事項變了就升版本，舊畫面再存事項會被 409 擋下，不會把完成狀態蓋回未完成。
        plan.version += 1
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "此規劃事項已有農事紀錄，請到日誌頁補充")
    if recommendation:
        precision_service.mark_recommendation_done(recommendation, user, log.id, now)
    sync_application_record(db, log)
    db.commit()
    output = outputs(db, [log])[0]
    if event:
        # 事項完成、版本已升：一併回傳規劃，畫面就地更新複合圖，不整頁重載也不會拿舊版本去存。
        output["plan"] = json_snapshot(serialize_plan(db, plan))
    if recommendation:
        # 卡片就地變「已記錄」，不用再打一次 GET 才知道建議狀態變了。
        output["recommendation"] = dict(id=recommendation.id, status="done", work_log_id=log.id)
    return output


@router.patch("/logs/{log_id}")
def patch_log(log_id: str, payload: LogPatch, db: Session = Depends(get_db), user=Depends(current_user)):
    log = owned_log(db, user, log_id)
    changes = payload.model_dump(exclude_unset=True)
    registration_changed = "registration_id" in changes
    new_registration = changes.pop("registration_id", None)

    if not within_edit_window(log.created_at, utcnow()):
        # 鎖定後唯一能補的內容：還沒有任何作用機制來源的施藥，補選登記。這只是補上缺的依據，不改寫當時做了什麼。
        backfill = (registration_changed and new_registration is not None and log.kind == "spray"
                    and not log.registration_snapshot and not log.prescription_item_id)
        if set(changes) - {"note"} or (registration_changed and not backfill):
            raise HTTPException(409, "超過 24 小時的紀錄只能修改備註、補照片或補選藥劑登記；更正請寫在備註裡")
        if backfill:
            log.registration_snapshot = capture_registration(db, new_registration, log.crop_text)
            log.registration_id = new_registration
            log.phi_days, log.phi_source = phi_snapshot(log.kind, registration_snapshot=log.registration_snapshot)
            sync_application_record(db, log)
        if "note" in changes:
            log.note = changes["note"]
        if backfill or "note" in changes:
            log.updated_at = utcnow()
            db.commit()
        return outputs(db, [log])[0]

    values = {key: getattr(log, key) for key in EDITABLE} | changes
    if "pest_id" in changes and "pest_text" not in changes:
        values["pest_text"] = ""
    if log.prescription_item_id and values["kind"] != "spray":
        raise HTTPException(422, "由處方帶入的紀錄類型須為施藥")
    if registration_changed and new_registration is not None:
        if values["kind"] != "spray":
            raise HTTPException(422, "只有施藥紀錄可以選擇藥劑登記")
        if log.prescription_item_id:
            raise HTTPException(422, "由處方帶入的紀錄已有處方快照，不需另選藥劑登記")
        log.registration_snapshot = apply_registration(db, values, new_registration)
        log.registration_id = new_registration
    elif (registration_changed and new_registration is None) or values["kind"] != "spray":
        # 清掉登記，或改成非施藥：登記快照只對施藥有意義，留著會讓作用機制對不上類型。
        log.registration_id = None
        log.registration_snapshot = None
    snapshot_pest(db, values)
    check_values(values)
    if not values["title"]:
        # 送空標題代表「依內容重新產生」；沒送標題就保留原本的，不偷偷改掉使用者寫的字。
        event = db.get(FarmEvent, log.event_id) if log.event_id else None
        values["title"] = default_title(values["kind"], material_name=values["material_name"],
                                        dilution=values["dilution"], fallback=event.title if event else "")
    for key, value in values.items():
        setattr(log, key, value)
    if log.kind != "spray":
        log.phi_days, log.phi_source = None, "none"
    elif not log.prescription_item_id and (registration_changed or log.phi_source == "none"):
        # 處方的快照在建立時存下就不再改；登記換了或清掉，快照跟著換。
        log.phi_days, log.phi_source = phi_snapshot(log.kind, registration_snapshot=log.registration_snapshot)
    if "field_text" in changes:
        log.field_id = resolve_field_id(db, log.farmer_id, log.field_text)
    log.compliance = compliance(log.kind, values)
    log.updated_at = utcnow()
    sync_application_record(db, log)
    db.commit()
    return outputs(db, [log])[0]


@router.delete("/logs/{log_id}")
def delete_log(log_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    log = owned_log(db, user, log_id)
    if not within_edit_window(log.created_at, utcnow()):
        raise HTTPException(409, "超過 24 小時的紀錄不能刪除；更正請修改備註")
    # 衍生的施藥紀錄一起刪，輪替檢查不能讀到已撤回的施藥；外鍵 CASCADE 是最後防線，這裡明確刪。
    db.execute(delete(ApplicationRecord).where(ApplicationRecord.work_log_id == log.id))
    # 撤回的是整筆紀錄，照片列跟著刪；實體檔沒有照片列指到，由清理排程當孤兒回收（services/farm_media.purge_media）。
    db.execute(delete(FarmLogPhoto).where(FarmLogPhoto.log_id == log.id))
    # 規劃事項維持已完成：刪的是記錯的紀錄，不代表工作沒做；事項回到「未記錄」讓人補記。
    db.delete(log)
    db.commit()
    return {"ok": True}


# --------------------------------------------------------------------------
# 表單預填、可帶入的處方、本月摘要
# --------------------------------------------------------------------------

@router.get("/farmers/{farmer_id}/logs/prefill")
def prefill(farmer_id: str, plan_id: str | None = Query(None, max_length=36),
            event_id: str | None = Query(None, max_length=36),
            prescription_item_id: str | None = Query(None, max_length=36),
            recommendation_id: str | None = Query(None, max_length=36),
            db: Session = Depends(get_db), user=Depends(current_user)):
    """四個入口共用同一張表單，這裡算好各入口的預設值；只讀，不改任何資料。"""
    access_farmer(db, user, farmer_id)
    out = dict(kind=None, plan_id=None, event_id=None, prescription_item_id=None, recommendation_id=None,
               field_text="", crop_text="", area_text="", amount_value="", amount_unit="", crop_id=None,
               plan_archived=False, title="", material_name="", dilution="",
               pest_text="", event=None, prescription=None, recommendation=None)
    plan = None
    if recommendation_id:
        recommendation, field = load_recommendation(db, user, farmer_id, recommendation_id)
        if recommendation.status not in ("proposed", "accepted"):
            raise HTTPException(409, f"這則建議目前是「{_RECOMMENDATION_LABELS[recommendation.status]}」，不能記錄")
        kind = _RECOMMENDATION_KIND_TO_LOG[recommendation.kind]
        material = (recommendation.five_r or {}).get("material", "")
        if recommendation.kind == "irrigate":
            total = recommendation.payload.get("total_volume_m3")
            zones = recommendation.payload.get("zones") or []
            amount_value, amount_unit = (str(total), "立方公尺") if total is not None else (
                (str(zones[0]["mm"]), "mm") if zones else ("", ""))
        else:
            total = recommendation.payload.get("total_kg")
            amount_value, amount_unit = (str(total), "公斤") if total is not None else ("", "")
        out.update(kind=kind, field_text=field.name, area_text=field.area_text, material_name=material,
                   amount_value=amount_value, amount_unit=amount_unit, recommendation_id=recommendation.id,
                   recommendation=dict(id=recommendation.id, kind=recommendation.kind, title=recommendation.title,
                                       rationale=recommendation.rationale, five_r=recommendation.five_r,
                                       payload=recommendation.payload))
        return json_snapshot(out)
    if event_id:
        event = db.get(FarmEvent, event_id)
        plan = db.get(FarmPlan, event.plan_id) if event else None
        if not event or not plan or plan.farmer_id != farmer_id:
            raise HTTPException(404, "找不到此規劃事項")
        log_id = db.scalar(select(FarmWorkLog.id).where(FarmWorkLog.event_id == event.id))
        out["event"] = dict(id=event.id, kind=event.kind, title=event.title, start_on=event.start_on,
                            end_on=event.end_on, completed=event.completed, log_id=log_id)
        if event.kind in EVENT_KIND_TO_LOG:
            out.update(kind=EVENT_KIND_TO_LOG[event.kind], event_id=event.id, title=event.title)
        elif event.kind == "pest":
            # 巡查色帶是提醒，不是待辦：記成巡田觀察並預填病蟲害名，但不連結事項、不動窗口狀態。
            out.update(kind="scout", pest_text=event.title)
        else:
            raise HTTPException(422, "生育時期是估計的階段，不是作業，沒有可以記錄的內容")
    elif plan_id:
        plan = db.get(FarmPlan, plan_id)
        if not plan or plan.farmer_id != farmer_id:
            raise HTTPException(404, "找不到此規劃")
    if plan:
        out.update(plan_id=plan.id, field_text=plan.field_name, crop_text=plan.crop_name, area_text=plan.area,
                   crop_id=resolve_crop(db, plan.crop_name), plan_archived=plan.archived)
    if prescription_item_id:
        item, case = ready_prescription(db, user, farmer_id, prescription_item_id)
        out.update(kind="spray", prescription_item_id=item.id, material_name=item.product_text, dilution=item.dilution or "")
        # 每公頃用量與實際用量意義不同，只當提示顯示，不塞進用量欄。
        out["prescription"] = dict(case_id=case.id, case_no=case.case_no, product_text=item.product_text,
                                   dose=item.dose, phi_days=item.phi_days,
                                   moa_codes=clean_codes((item.source_values or {}).get("moa_codes")))
        if not plan:
            out.update(field_text=case.field_text, crop_text=case.crop_text, area_text=case.area_text, crop_id=case.crop_id)
    return json_snapshot(out)


@router.get("/farmers/{farmer_id}/prescriptions")
def prescriptions(farmer_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """可以「帶入處方」的處方項目：已儲存或已開立，並附上已經記錄過幾次。"""
    access_farmer(db, user, farmer_id)
    stmt = (select(PrescriptionItem, ConsultCase)
            .join(ConsultCase, ConsultCase.id == PrescriptionItem.case_id)
            .where(ConsultCase.farmer_id == farmer_id, ConsultCase.status.in_(PRESCRIPTION_READY)))
    if user.role == "doctor":
        stmt = stmt.where(ConsultCase.doctor_id == user.id)
    rows = db.execute(stmt.order_by(ConsultCase.created_at.desc(), PrescriptionItem.sort_order, PrescriptionItem.id)
                      .limit(100)).all()
    ids = [item.id for item, _ in rows]
    logged = {item_id: (count, last) for item_id, count, last in db.execute(
        select(FarmWorkLog.prescription_item_id, func.count(FarmWorkLog.id), func.max(FarmWorkLog.done_on))
        .where(FarmWorkLog.prescription_item_id.in_(ids)).group_by(FarmWorkLog.prescription_item_id))} if ids else {}
    return json_snapshot([
        dict(id=item.id, case_id=case.id, case_no=case.case_no, status=case.status, issued_at=case.issued_at,
             crop_text=case.crop_text, field_text=case.field_text, product_text=item.product_text,
             dilution=item.dilution, dose=item.dose, phi_days=item.phi_days,
             moa_codes=clean_codes((item.source_values or {}).get("moa_codes")),
             logged_count=logged.get(item.id, (0, None))[0], last_logged_on=logged.get(item.id, (0, None))[1])
        for item, case in rows
    ])


@router.get("/farmers/{farmer_id}/logs/summary")
def summary(farmer_id: str, month: str | None = Query(None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
            db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    today = taipei_today()
    year, number = (int(part) for part in month.split("-")) if month else (today.year, today.month)
    start, end = date(year, number, 1), date(year, number, calendar.monthrange(year, number)[1])

    logs = db.scalars(select(FarmWorkLog).where(FarmWorkLog.farmer_id == farmer_id, FarmWorkLog.done_on >= start,
                                                FarmWorkLog.done_on <= end)
                      .order_by(FarmWorkLog.done_on.desc(), FarmWorkLog.id)).all()
    ids = [log.id for log in logs]
    photo_count = db.scalar(select(func.count()).select_from(FarmLogPhoto).where(
        FarmLogPhoto.log_id.in_(ids), FarmLogPhoto.deleted_at.is_(None))) if ids else 0

    yields: dict[str, float] = defaultdict(float)
    unparsed_yields = 0
    incomplete = []
    for log in logs:
        if log.kind == "harvest" and log.yield_value:
            value = plain_number(log.yield_value)
            if value is None or not log.yield_unit:
                unparsed_yields += 1
            else:
                yields[log.yield_unit] += value
        # 完整度讀存檔當下算好的結果，不用現在的規則重算；規則改版不讓舊紀錄突然變「不合格」。
        missing = (log.compliance or {}).get("missing") or []
        if missing:
            incomplete.append(dict(id=log.id, done_on=log.done_on, title=log.title, missing=missing))

    # 規劃對照只算已經開始的農事／肥培事項；還沒到的不算「未記錄」。
    events = db.execute(
        select(FarmEvent, FarmPlan).join(FarmPlan, FarmPlan.id == FarmEvent.plan_id)
        .where(FarmPlan.farmer_id == farmer_id, FarmPlan.archived.is_(False), FarmEvent.kind.in_(tuple(EVENT_KIND_TO_LOG)),
               FarmEvent.start_on <= min(end, today), FarmEvent.end_on >= start)
        .order_by(FarmEvent.start_on, FarmEvent.id)).all()
    event_ids = [event.id for event, _ in events]
    logged = dict(db.execute(select(FarmWorkLog.event_id, FarmWorkLog.id).where(FarmWorkLog.event_id.in_(event_ids))).all()) if event_ids else {}
    unlogged = [dict(event_id=event.id, plan_id=plan.id, title=event.title, kind=event.kind, start_on=event.start_on,
                     end_on=event.end_on, completed=event.completed, crop_name=plan.crop_name, field_name=plan.field_name)
                for event, plan in events if event.id not in logged]

    fields = set(db.scalars(select(FarmWorkLog.field_text).where(FarmWorkLog.farmer_id == farmer_id).distinct()))
    fields |= set(db.scalars(select(FarmPlan.field_name).where(FarmPlan.farmer_id == farmer_id, FarmPlan.archived.is_(False))))
    return json_snapshot(dict(
        month=f"{year:04d}-{number:02d}", start=start, end=end, total=len(logs),
        by_kind=dict(Counter(log.kind for log in logs)), photo_count=photo_count,
        spray_count=sum(1 for log in logs if log.kind == "spray"),
        yields=[dict(unit=unit, total=round(total, 3)) for unit, total in sorted(yields.items())],
        unparsed_yields=unparsed_yields,
        compliance=dict(complete=len(logs) - len(incomplete), incomplete=incomplete),
        plan_compare=dict(total=len(events), logged=len(events) - len(unlogged), unlogged=unlogged),
        fields=sorted(field for field in fields if field),
    ))


# --------------------------------------------------------------------------
# 照片：先存日誌再傳照片，網路差時照片可以之後補；超過可修改期限仍可補照片。
# --------------------------------------------------------------------------

@router.post("/logs/{log_id}/photos", status_code=201)
def upload_photo(log_id: str, file: UploadFile = File(...), caption: str = Form("", max_length=200),
                 db: Session = Depends(get_db), user=Depends(current_user)):
    # 刻意用同步函式：讀檔、解碼與同步資料庫連線都會阻塞，交給 FastAPI 的執行緒池，不卡住事件迴圈。
    log = owned_log(db, user, log_id)
    active = db.scalar(select(func.count()).select_from(FarmLogPhoto)
                       .where(FarmLogPhoto.log_id == log.id, FarmLogPhoto.deleted_at.is_(None)))
    if active >= settings.media_max_photos_per_log:
        raise HTTPException(409, f"每筆紀錄最多 {settings.media_max_photos_per_log} 張照片")
    # 只讀到上限多一個位元組：不信 Content-Length，超過就停，不把整個大檔讀進記憶體。
    data = file.file.read(settings.media_max_bytes + 1)
    if len(data) > settings.media_max_bytes:
        raise HTTPException(413, f"照片不能超過 {settings.media_max_bytes // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(422, "沒有收到照片內容")
    now = utcnow()
    try:
        processed = process_image(data, now=now, allowed=set(settings.media_allowed_formats),
                                  max_pixels=settings.media_max_pixels, thumb_px=settings.media_thumb_px)
        storage_key, thumb_key = photo_keys(log.farmer_id, now, processed.stored_ext)
        paths = [resolve_key(settings.media_root, storage_key), resolve_key(settings.media_root, thumb_key)]
    except MediaError as error:
        raise HTTPException(error.status, str(error))
    last = db.scalar(select(func.max(FarmLogPhoto.sort_order)).where(FarmLogPhoto.log_id == log.id))
    try:
        write_atomic(paths[0], processed.original)
        write_atomic(paths[1], processed.thumb)
        # bytes 記的是實際存下的原檔大小；HEIC 轉 JPEG 後會跟上傳的大小不同。
        photo = FarmLogPhoto(log_id=log.id, storage_key=storage_key, thumb_key=thumb_key, bytes=len(processed.original),
                             width=processed.width, height=processed.height, taken_at=processed.taken_at, taken_at_source=processed.taken_at_source,
                             lat=processed.lat, lng=processed.lng, caption=caption,
                             sort_order=(last if last is not None else -1) + 1, created_at=now)
        db.add(photo)
        db.commit()
    except Exception:
        # 資料庫沒寫成就把檔案收掉；檔案殘留也會被清理排程當孤兒回收，這裡只是不讓它等一天。
        db.rollback()
        for path in paths:
            path.unlink(missing_ok=True)
        raise
    # 相容欄位：讀資料庫存下的來源，不另外算一次，回應與之後列表永遠一致。
    return photo_output(photo) | dict(taken_from_exif=photo.taken_at_source == "exif")


@router.delete("/logs/{log_id}/photos/{photo_id}")
def delete_photo(log_id: str, photo_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    log = owned_log(db, user, log_id)
    photo = db.get(FarmLogPhoto, photo_id)
    if not photo or photo.log_id != log.id or photo.deleted_at is not None:
        raise HTTPException(404, "找不到此照片")
    # 只軟刪：照片是交給查核的證據，實體檔保留一段時間才由清理排程回收。
    photo.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@router.get("/media/{photo_id}")
def media(photo_id: str, variant: Literal["orig", "thumb"] = "orig", db: Session = Depends(get_db),
          user=Depends(current_user)):
    photo = db.get(FarmLogPhoto, photo_id)
    # 保留期內已刪除的照片只給縮圖，讓人認得出要還原哪一張；原檔還原前一律看不到。
    visible = photo is not None and (photo.deleted_at is None or (variant == "thumb" and in_trash(photo, utcnow())))
    log = db.get(FarmWorkLog, photo.log_id) if visible else None
    try:
        if not log:
            raise HTTPException(404)
        # 誰能看農場就能看照片；解除連結的植物醫生在這裡就被擋下。
        access_farmer(db, user, log.farmer_id)
        path = resolve_key(settings.media_root, photo.thumb_key if variant == "thumb" else photo.storage_key)
    except (HTTPException, MediaError):
        # 不存在、已刪除、沒有權限一律同一個回應，不透露這張照片是否存在。
        raise HTTPException(404, "找不到此照片")
    if not path.is_file() or path.suffix not in MEDIA_TYPES:
        raise HTTPException(404, "照片檔案不存在，可能已被清理")
    # private + no-cache：瀏覽器可以存，但每次都要回來問，解除連結後就拿不到。
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix], headers={
        "Cache-Control": "private, no-cache",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": f'inline; filename="{photo.id}{path.suffix}"',
    })


@router.post("/logs/{log_id}/photos/{photo_id}/restore")
def restore_photo(log_id: str, photo_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    log = owned_log(db, user, log_id)
    photo = db.get(FarmLogPhoto, photo_id)
    if not photo or photo.log_id != log.id or photo.deleted_at is None:
        raise HTTPException(404, "找不到可還原的照片")
    if not in_trash(photo, utcnow()):
        raise HTTPException(409, f"刪除超過 {settings.media_trash_days} 天的照片已無法還原")
    try:
        files_present = all(resolve_key(settings.media_root, key).is_file() for key in (photo.storage_key, photo.thumb_key))
    except MediaError:
        files_present = False
    if not files_present:
        # 保留期內照理不會被清，但檔案可能被人工搬走；還原成一張打不開的照片比不還原更糟。
        raise HTTPException(409, "照片檔案已不存在，無法還原")
    active = db.scalar(select(func.count()).select_from(FarmLogPhoto)
                       .where(FarmLogPhoto.log_id == log.id, FarmLogPhoto.deleted_at.is_(None)))
    if active >= settings.media_max_photos_per_log:
        raise HTTPException(409, f"每筆紀錄最多 {settings.media_max_photos_per_log} 張照片，請先刪除一張再還原")
    photo.deleted_at = None
    db.commit()
    return photo_output(photo)


# --------------------------------------------------------------------------
# 匯出農事紀錄表：欄位對齊產銷履歷驗證基準第 5、6 條。版面在 templates/farm_log_export.html。
# --------------------------------------------------------------------------

EXPORT_TYPES = {"pdf": "application/pdf",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
LOG_TEXT_KEYS = ("field_text", "crop_text", "title", "material_name", "material_supplier", "material_lot", "dilution",
                 "amount_value", "amount_unit", "area_text", "yield_value", "yield_unit", "yield_destination",
                 "labor_text", "pest_text", "note")


def prescription_phi(db, logs) -> dict:
    ids = {log.prescription_item_id for log in logs if log.prescription_item_id}
    if not ids:
        return {}
    rows = db.execute(select(PrescriptionItem.id, PrescriptionItem.phi_days, ConsultCase.case_no)
                      .join(ConsultCase, ConsultCase.id == PrescriptionItem.case_id).where(PrescriptionItem.id.in_(ids)))
    return {item_id: (phi_days, case_no) for item_id, phi_days, case_no in rows}


def phi_of(log, prescriptions) -> tuple[int | None, str | None, str]:
    """安全採收期讀日誌建立時存下的快照：處方項目日後被刪，已交出去的核對結果也不會變成「無法判斷」。
    診斷書編號只是顯示用，仍從處方查，查不到就不印。"""
    case_no = prescriptions[log.prescription_item_id][1] if log.prescription_item_id in prescriptions else ""
    source = log.phi_source if log.phi_source in ("prescription", "registration") else None
    return log.phi_days, source, case_no


def export_data(db, user, farmer_id, date_from, date_to, field, options):
    from app.services.farm_log_export import PHI_LOOKBACK, build_export

    access_farmer(db, user, farmer_id)
    if date_from > date_to:
        raise HTTPException(422, "起始日期不能晚於結束日期")
    if (date_to - date_from).days + 1 > settings.farm_log_export_max_days:
        raise HTTPException(422, f"一次最多匯出 {settings.farm_log_export_max_days} 天，請分段匯出")
    farmer = db.get(FarmAccount, farmer_id)
    field = (field or "").strip() or None
    stmt = select(FarmWorkLog).where(FarmWorkLog.farmer_id == farmer_id, FarmWorkLog.done_on >= date_from,
                                     FarmWorkLog.done_on <= date_to)
    if field:
        stmt = stmt.where(FarmWorkLog.field_text == field)
    logs = db.scalars(stmt).all()

    harvests = [log for log in logs if log.kind == "harvest"]
    sprays = []
    if options.phi and harvests:
        # 往期間之前找施藥：月初採收要看上個月底噴的藥，只看匯出期間會漏掉最要緊的那一筆。
        sprays = db.scalars(select(FarmWorkLog).where(
            FarmWorkLog.farmer_id == farmer_id, FarmWorkLog.kind == "spray",
            or_(FarmWorkLog.field_text.in_({log.field_text for log in harvests}),
                FarmWorkLog.field_id.in_({log.field_id for log in harvests if log.field_id})),
            FarmWorkLog.done_on >= min(log.done_on for log in harvests) - PHI_LOOKBACK,
            FarmWorkLog.done_on <= max(log.done_on for log in harvests))).all()
    prescriptions = prescription_phi(db, [*logs, *sprays])

    photos = defaultdict(list)
    if logs:
        for photo in db.scalars(select(FarmLogPhoto).where(FarmLogPhoto.log_id.in_([log.id for log in logs]),
                                                           FarmLogPhoto.deleted_at.is_(None))
                                .order_by(FarmLogPhoto.sort_order, FarmLogPhoto.created_at, FarmLogPhoto.id)):
            try:
                path = resolve_key(settings.media_root, photo.storage_key)
            except MediaError:
                path = None
            photos[photo.log_id].append(dict(id=photo.id, path=path, taken_at=photo.taken_at, caption=photo.caption,
                                             taken_at_source=photo.taken_at_source, lat=photo.lat, lng=photo.lng))
    authors = dict(db.execute(select(FarmAccount.id, FarmAccount.name)
                              .where(FarmAccount.id.in_({log.created_by for log in logs}))).all()) if logs else {}
    plan_ids = {log.plan_id for log in logs if log.plan_id}
    plans = {plan.id: dict(field_name=plan.field_name, crop_name=plan.crop_name, area=plan.area, variety=plan.variety)
             for plan in db.scalars(select(FarmPlan).where(FarmPlan.id.in_(plan_ids)))} if plan_ids else {}

    def snapshot(log):
        phi_days, phi_source, case_no = phi_of(log, prescriptions)
        return {key: getattr(log, key) or "" for key in LOG_TEXT_KEYS} | dict(
            id=log.id, kind=log.kind, done_on=log.done_on, done_time=log.done_time, created_at=as_utc(log.created_at),
            plan_id=log.plan_id, field_id=log.field_id, compliance=log.compliance or {}, phi_days=phi_days, phi_source=phi_source,
            case_no=case_no, created_by_name=authors.get(log.created_by, ""), photos=photos.get(log.id, []))

    def spray_ref(log):
        phi_days, phi_source, _ = phi_of(log, prescriptions)
        return dict(id=log.id, done_on=log.done_on, field_text=log.field_text, field_id=log.field_id, plan_id=log.plan_id,
                    material_name=log.material_name, phi_days=phi_days, phi_source=phi_source)

    return build_export(farm_name=farmer.farm_name or "", operator=farmer.name, start=date_from, end=date_to, field=field,
                        logs=[snapshot(log) for log in logs], sprays=[spray_ref(log) for log in sprays], plans=plans,
                        options=options, generated_on=taipei_today())


@router.get("/farmers/{farmer_id}/logs/export/preview")
def export_preview(farmer_id: str, date_from: date = Query(..., alias="from"), date_to: date = Query(..., alias="to"),
                   field: str | None = Query(None, max_length=160), db: Session = Depends(get_db),
                   user=Depends(current_user)):
    """匯出前的提醒：用目前的規則重算缺漏、核對安全採收期、照片是否超過上限。只讀，不產檔。"""
    from app.services.farm_log_export import ExportOptions

    data = export_data(db, user, farmer_id, date_from, date_to, field, ExportOptions())
    warnings = data["warnings"]
    slim = lambda item: {key: item[key] for key in ("harvest_id", "harvest_on", "field_text", "crop_text", "message")}  # noqa: E731
    return json_snapshot(dict(
        total=data["counts"]["logs"], photo_count=data["counts"]["photos"], max_photos=settings.media_export_max_photos,
        too_many_photos=data["counts"]["photos"] > settings.media_export_max_photos, fields=data["fields"],
        rule_version=COMPLIANCE_RULE_VERSION, rule_changed=warnings["rule_changed"], incomplete=warnings["incomplete"],
        phi=dict(checked=len(data["phi"]), violations=[slim(item) for item in warnings["phi_violations"]],
                 unknown=[slim(item) for item in warnings["phi_unknown"]]),
    ))


@router.get("/farmers/{farmer_id}/logs/export")
def export_logs(farmer_id: str, date_from: date = Query(..., alias="from"), date_to: date = Query(..., alias="to"),
                field: str | None = Query(None, max_length=160), format: Literal["pdf", "xlsx"] = "pdf",
                photos: bool = True, gps: bool = True, phi: bool = True,
                db: Session = Depends(get_db), user=Depends(current_user)):
    from app.services.farm_log_export import ExportOptions, render_export_pdf, render_export_xlsx

    options = ExportOptions(photos=photos, gps=gps, phi=phi)
    data = export_data(db, user, farmer_id, date_from, date_to, field, options)
    count = data["counts"]["photos"]
    if format == "pdf" and photos and count > settings.media_export_max_photos:
        # 照片全部內嵌進 PDF，數量沒有上限就會把記憶體與產檔時間撐爆；寧可請使用者分段。
        raise HTTPException(422, f"這段期間有 {count} 張照片，超過一次附在 PDF 的上限 {settings.media_export_max_photos} 張；"
                                 "請分期匯出（縮短期間）、選擇單一田區，或取消照片附錄")
    body = render_export_pdf(data) if format == "pdf" else render_export_xlsx(data)
    ascii_name = f"farm-log-{date_from:%Y%m%d}-{date_to:%Y%m%d}.{format}"
    local_name = quote(f"農事紀錄表_{date_from:%Y%m%d}-{date_to:%Y%m%d}.{format}")
    return Response(body, media_type=EXPORT_TYPES[format], headers={
        "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{local_name}",
        # 紀錄含田區位置與照片座標：不讓瀏覽器或中間代理快取。
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
    })
