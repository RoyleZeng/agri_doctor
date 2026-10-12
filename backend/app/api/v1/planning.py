"""Authenticated farmer planning and consent-based plant-doctor workspaces."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, Query
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.planning import DoctorFarmer, FarmAccount, FarmEvent, FarmInvite, FarmNote, FarmPlan, FarmSession
from app.services import farmer_invite
from app.services.farm_templates import TEMPLATES, VERSION, catalog, generate_events, source_index
from app.services.farm_export import KINDS, render_svg

COOKIE = "agri_farm_session"


def protect_write(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-Planning-Request") != "1":
        raise HTTPException(403, "請從農場工作區送出操作")


router = APIRouter(prefix="/planning", tags=["farm planning"], dependencies=[Depends(protect_write)])


class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class Login(Input):
    username: str = Field(min_length=3, max_length=120)
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=10, max_length=128)

    @field_validator("username")
    @classmethod
    def username_normalized(cls, value):
        value = value.lower()
        if not re.fullmatch(r"[a-z0-9_.@+\-]+", value):
            raise ValueError("帳號請使用英文字母、數字或電子信箱")
        return value


class Register(Login):
    name: str = Field(min_length=1, max_length=80)
    # agency＝主管機關／改良場：只看區域地圖的鄉鎮匯總與匯出報表，沒有自己的農場，
    # farm_name／location 仍填但前端不會用到（跟農場工作區共用同一張表，不另開一張）。
    role: Literal["farmer", "doctor", "agency"]
    farm_name: str = Field(default="我的農場", min_length=1, max_length=120)
    location: str = Field(default="", max_length=120)


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(value, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.scrypt(value.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"{salt}:{hashed}"


def public_user(user):
    return {key: getattr(user, key) for key in
            ("id", "name", "role", "farm_name", "location", "weather_county", "precision_enabled", "share_region")} | {
        # 代建帳號首次登入要強制改密碼；前端靠這個旗標把人擋在改密碼畫面。
        "must_change_password": bool(getattr(user, "must_change_password", False))}


def current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get(COOKIE, "")
    session = db.get(FarmSession, digest(token)) if token else None
    if not session or session.expires_at < int(time.time()):
        raise HTTPException(401, "請先登入農場工作區")
    user = db.get(FarmAccount, session.account_id)
    if not user:
        raise HTTPException(401, "請重新登入")
    return user


def role_required(user, role):
    # role 可以是單一角色或一組角色（例如區域地圖的主動調查登錄開放植物醫生與機關）。
    allowed = role if isinstance(role, tuple) else (role,)
    if user.role != "admin" and user.role not in allowed:
        raise HTTPException(403, "此操作不適用於目前的帳號")


def access_farmer(db, user, farmer_id):
    farmer = db.get(FarmAccount, farmer_id)
    if not farmer or (farmer.role != "farmer" and not (user.role == "admin" and farmer.id == user.id)):
        raise HTTPException(404, "找不到此農場或尚未取得查看權限")
    if user.role != "admin" and farmer.id != user.id and not (user.role == "doctor" and db.get(DoctorFarmer, (user.id, farmer.id))):
        raise HTTPException(404, "找不到此農場或尚未取得查看權限")
    return farmer


def access_plan(db, user, plan_id, *, owner=False, version=None):
    plan = db.scalar(select(FarmPlan).where(FarmPlan.id == plan_id).with_for_update())
    if not plan:
        raise HTTPException(404, "找不到此規劃")
    access_farmer(db, user, plan.farmer_id)
    if owner and user.role != "admin" and user.id != plan.farmer_id:
        raise HTTPException(403, "請由農民本人管理作物規劃")
    if version is not None and version != plan.version:
        raise HTTPException(409, "規劃已被更新，請重新載入後再修改")
    return plan


_attempts = defaultdict(deque)


def throttle(request):
    now = time.monotonic()
    # Bounded, per-process guard; deploy shared rate limiting at the HTTPS proxy.
    for key in list(_attempts):
        if not _attempts[key] or _attempts[key][-1] < now - 60:
            del _attempts[key]
    key = request.client.host if request.client else "local"
    queue = _attempts[key]
    while queue and queue[0] < now - 60:
        queue.popleft()
    if len(queue) >= 15:
        raise HTTPException(429, "嘗試次數較多，請稍等一分鐘再試")
    queue.append(now)


def set_session_cookie(response, token):
    # 診療與農場共用登入；清除舊路徑避免瀏覽器送出兩個同名 cookie。
    response.delete_cookie(COOKIE, path="/api/v1/planning")
    response.set_cookie(COOKIE, token, max_age=604800, httponly=True,
        secure=settings.planning_cookie_secure, samesite="strict", path="/api/v1")


def new_session(db, user, response, request):
    old_token = request.cookies.get(COOKIE)
    if old_token:
        db.execute(delete(FarmSession).where(FarmSession.token_hash == digest(old_token)))
    db.execute(delete(FarmSession).where(FarmSession.expires_at < int(time.time())))
    token = secrets.token_urlsafe(32)
    db.add(FarmSession(token_hash=digest(token), account_id=user.id, expires_at=int(time.time()) + 604800))
    db.commit()
    set_session_cookie(response, token)
    return public_user(user)


@router.get("/catalog")
def get_catalog(db: Session = Depends(get_db)):
    from app.services.farm_content import published_templates
    data = catalog()
    data['crops'] = [{k: v for k, v in t.items() if k not in ('events', 'sources')} for t in published_templates(db)]
    return data


class WeatherCounty(Input):
    farmer_id: str | None = Field(default=None, max_length=36)
    county: str = Field(max_length=12)


@router.patch("/weather-county")
def save_weather_county(payload: WeatherCounty, db: Session = Depends(get_db), user=Depends(current_user)):
    from app.services.weather import COUNTIES
    role_required(user, "farmer")
    if payload.county not in COUNTIES:
        raise HTTPException(422, "請選擇臺灣縣市")
    target = user
    if payload.farmer_id and payload.farmer_id != user.id:
        role_required(user, "admin")
        target = access_farmer(db, user, payload.farmer_id)
    target.weather_county = payload.county
    db.commit()
    return public_user(target)


class ShareRegion(Input):
    enabled: bool


class FarmLocation(Input):
    location: str = Field(min_length=1, max_length=120)


@router.patch("/me/location")
def save_location(payload: FarmLocation, db: Session = Depends(get_db), user=Depends(current_user)):
    from app.services.region_stats import load_districts
    from app.services.weather import county_from_location

    # 地區會決定農民能看哪個鄉鎮，僅接受本人設定，不接受指定別人的 account_id。
    role_required(user, "farmer")
    location = payload.location.replace("台", "臺").replace(" ", "")
    county = county_from_location(location)
    district = location.removeprefix(county) if county else ""
    if not county or not location.startswith(county) or not re.fullmatch(r"[\u4e00-\u9fff]{1,8}[區鄉鎮市]", district):
        raise HTTPException(422, "請填寫縣市與鄉鎮市區，例如：高雄市大社區；不需填門牌")
    districts = load_districts(county)
    if districts and district not in districts:
        raise HTTPException(422, "鄉鎮市區與縣市不符，請確認名稱")
    # 尚無界線檔的縣市仍可存所在地，地圖應回「尚未提供」，不能假裝能顯示。
    user.location = location
    db.commit()
    return public_user(user)


@router.patch("/me/share-region")
def save_share_region(payload: ShareRegion, db: Session = Depends(get_db), user=Depends(current_user)):
    # 只有農民本人能改；同意才會被算進區域彙整，預設不同意。
    role_required(user, "farmer")
    user.share_region = payload.enabled
    db.commit()
    return public_user(user)


@router.get("/example")
def example():
    """Explicitly synthetic preview; no account or database writes."""
    today = datetime.now(ZoneInfo("Asia/Taipei")).date()
    plans = []
    for index, template in enumerate(TEMPLATES):
        start = today - timedelta(days=[40, 25, 10][index])
        end = start + timedelta(days=template["days"])
        events = [e | dict(id=f"demo-{index}-{i}", completed=False, note="", updated_by="系統參考範本", basis="template_estimate", log_id=None, log_done_on=None) for i, e in enumerate(generate_events(template["key"], start, end))]
        ids = {s for e in events for s in e["source_ids"]}
        plans.append(dict(id=f"demo-{index}", farmer_id="demo", crop_key=template["key"], crop_name=template["name"], field_name=["溪畔一區", "溫室二區", "菜園三區"][index], variety="示範品種", area=["0.8 公頃", "0.2 公頃", "0.3 公頃"][index], planted_on=start, end_on=end, notes="示範資料，不屬於真實農民", archived=False, version=1, template_version=VERSION, sources=[s for s in source_index() if s["id"] in ids], events=events))
    return dict(farmer=dict(id="demo", name="示範農友", role="farmer", farm_name="青禾示範農場", location="臺灣 · 操作示範"), plans=plans, notes=[])


@router.post("/register")
def register(payload: Register, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(request)
    user = FarmAccount(**payload.model_dump(exclude={"password"}), password_hash=password_hash(payload.password))
    db.add(user)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "此帳號已被使用，請登入或更換帳號")
    return new_session(db, user, response, request)


@router.post("/login")
def login(payload: Login, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(request)
    user = db.scalar(select(FarmAccount).where(FarmAccount.username == payload.username))
    stored = user.password_hash if user else ("0" * 32 + ":" + "0" * 128)
    if not hmac.compare_digest(password_hash(payload.password, stored.split(":")[0]), stored) or not user:
        raise HTTPException(401, "帳號或密碼不正確")
    return new_session(db, user, response, request)


@router.get("/me")
def me(request: Request, response: Response, user=Depends(current_user)):
    set_session_cookie(response, request.cookies[COOKIE])
    return public_user(user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    db.execute(delete(FarmSession).where(FarmSession.token_hash == digest(request.cookies.get(COOKIE, ""))))
    db.commit()
    response.delete_cookie(COOKIE, path="/api/v1/planning")
    response.delete_cookie(COOKIE, path="/api/v1")
    return {"ok": True}


@router.post("/invite")
def invite(db: Session = Depends(get_db), user=Depends(current_user)):
    role_required(user, "farmer")
    db.execute(delete(FarmInvite).where(FarmInvite.farmer_id == user.id))
    code = secrets.token_urlsafe(18)
    expires = int(time.time()) + 86400
    db.add(FarmInvite(code_hash=digest(code), farmer_id=user.id, expires_at=expires))
    db.commit()
    return dict(code=code, expires_at=expires)


class Link(Input):
    code: str = Field(min_length=12, max_length=100)


@router.post("/links")
def link_farmer(payload: Link, db: Session = Depends(get_db), user=Depends(current_user)):
    role_required(user, "doctor")
    invite = db.scalar(select(FarmInvite).where(FarmInvite.code_hash == digest(payload.code)).with_for_update())
    if not invite or invite.expires_at < int(time.time()):
        raise HTTPException(400, "連結碼無效、已使用或已過期，請農民重新產生")
    farmer_id = invite.farmer_id
    if not db.get(DoctorFarmer, (user.id, farmer_id)):
        db.add(DoctorFarmer(doctor_id=user.id, farmer_id=farmer_id))
    db.delete(invite)
    db.commit()
    return public_user(db.get(FarmAccount, farmer_id))


@router.get("/links")
def links(db: Session = Depends(get_db), user=Depends(current_user)):
    if user.role == "admin":
        users = db.scalars(select(FarmAccount).where(FarmAccount.role == "farmer")).all()
    elif user.role == "farmer":
        users = db.scalars(select(FarmAccount).join(DoctorFarmer, FarmAccount.id == DoctorFarmer.doctor_id).where(DoctorFarmer.farmer_id == user.id)).all()
    else:
        users = db.scalars(select(FarmAccount).join(DoctorFarmer, FarmAccount.id == DoctorFarmer.farmer_id).where(DoctorFarmer.doctor_id == user.id)).all()
    now = datetime.now(ZoneInfo("UTC"))
    result = []
    for account in users:
        plans = db.scalars(select(FarmPlan).where(FarmPlan.farmer_id == account.id, FarmPlan.archived.is_(False))).all()
        link = db.get(DoctorFarmer, (user.id, account.id)) if user.role in ("doctor", "admin") else None
        result.append(public_user(account) | dict(
            crops=[p.crop_name for p in plans], plan_count=len(plans),
            # 「你建立的」與邀請狀態；農民自己註冊的連結是 code，不顯示這些。
            link_source=link.source if link else "code",
            invite=farmer_invite.invite_state(account, now) if account.role == "farmer" else None))
    return result


class CreateFarmer(Input):
    name: str = Field(min_length=1, max_length=80)
    farm_name: str = Field(default="我的農場", max_length=120)
    # 鄉鎮必填：區域地圖與天氣都靠它，事後才補等於這兩個功能先壞一段時間。
    location: str = Field(min_length=2, max_length=120)
    username: str | None = Field(default=None, max_length=120)
    crops: list[str] = Field(default_factory=list, max_length=12)
    #: 只有 admin 能指定；doctor 一律連到自己。
    doctor_id: str | None = Field(default=None, max_length=36)

    @field_validator("username")
    @classmethod
    def username_shape(cls, value):
        if value is None:
            return None
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9._-]{3,120}", value):
            raise ValueError("帳號請用 3 個字以上的英數字、句點、底線或減號")
        return value


def _issue_otp(account, now):
    """產生一次性密碼並寫進帳號。回傳明文，**只在這一次回應裡出現**，之後查不到。"""
    password = farmer_invite.one_time_password()
    account.password_hash = password_hash(password)
    account.must_change_password = True
    account.otp_expires_at = farmer_invite.otp_expiry(now)
    return password


@router.post("/farmers", status_code=201)
def create_farmer(payload: CreateFarmer, db: Session = Depends(get_db), user=Depends(current_user)):
    """植物醫生／管理者替農民建立帳號。

    建立之後自動連結到該植物醫生，但**農民本人隨時可以解除**——
    代建是為了讓不熟電腦的農民用得起來，不是取得永久授權。
    """
    if user.role not in ("doctor", "admin"):
        raise HTTPException(403, "只有植物醫生或管理者可以代建農民帳號")
    doctor_id = user.id if user.role == "doctor" else payload.doctor_id
    if doctor_id and doctor_id != user.id:
        doctor = db.get(FarmAccount, doctor_id)
        if not doctor or doctor.role != "doctor":
            raise HTTPException(422, "指定的植物醫生不存在")

    now = datetime.now(ZoneInfo("UTC"))
    taken = set(db.scalars(select(FarmAccount.username)).all())
    username = payload.username or farmer_invite.suggest_username(payload.name, taken, now)
    if username in taken:
        raise HTTPException(409, "此帳號已被使用，請換一個")

    farmer = FarmAccount(username=username, name=payload.name, role="farmer",
                         farm_name=payload.farm_name or "我的農場", location=payload.location,
                         created_by=user.id, password_hash="")
    password = _issue_otp(farmer, now)
    db.add(farmer)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "此帳號已被使用，請換一個")
    if doctor_id:
        db.add(DoctorFarmer(doctor_id=doctor_id, farmer_id=farmer.id, source="created", created_by=user.id))
    db.commit()
    return public_user(farmer) | dict(username=username, one_time_password=password,
                                      otp_expires_at=farmer.otp_expires_at,
                                      crops=payload.crops, plan_count=0)


@router.post("/farmers/{farmer_id}/reset-otp")
def reset_otp(farmer_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    """一次性密碼過期或弄丟時重發。**不重建帳號**——重建會把已經記的日誌與規劃丟掉。"""
    if user.role not in ("doctor", "admin"):
        raise HTTPException(403, "只有植物醫生或管理者可以重發一次性密碼")
    farmer = db.get(FarmAccount, farmer_id)
    if not farmer or farmer.role != "farmer":
        raise HTTPException(404, "找不到這個農民帳號")
    if user.role == "doctor" and not db.get(DoctorFarmer, (user.id, farmer_id)):
        raise HTTPException(403, "這位農民不在您的連結清單中")
    # 農民已經改過密碼＝這個帳號從此只屬於他；再發一次等於幫他換掉密碼。
    if not farmer.must_change_password:
        raise HTTPException(409, "這位農民已經設定過自己的密碼，請改用忘記密碼流程，不要由他人重設")
    now = datetime.now(ZoneInfo("UTC"))
    password = _issue_otp(farmer, now)
    # 舊的登入階段要一起失效，否則拿著舊密碼登入的人不會被踢出去。
    db.execute(delete(FarmSession).where(FarmSession.account_id == farmer.id))
    db.commit()
    return dict(username=farmer.username, one_time_password=password, otp_expires_at=farmer.otp_expires_at)


class NewPassword(Input):
    password: Annotated[str, StringConstraints(strip_whitespace=False)] = Field(min_length=10, max_length=128)


@router.post("/me/password")
def change_password(payload: NewPassword, request: Request, db: Session = Depends(get_db), user=Depends(current_user)):
    """改密碼。代建帳號首次登入走這一支，改完 `must_change_password` 才會關掉。"""
    user.password_hash = password_hash(payload.password)
    user.must_change_password = False
    user.otp_expires_at = None
    # 別的裝置上的階段一起失效；只留現在這一個。
    db.execute(delete(FarmSession).where(FarmSession.account_id == user.id,
                                         FarmSession.token_hash != digest(request.cookies.get(COOKIE, ""))))
    db.commit()
    return public_user(user)


@router.post("/farmers/{farmer_id}/welcome-card.pdf")
def welcome_card(farmer_id: str, payload: NewPassword, db: Session = Depends(get_db), user=Depends(current_user)):
    """一次性密碼的紙卡。

    用 POST 而不是 GET，而且密碼由呼叫端傳回來：明文我們沒有存（只存雜湊），
    所以這支只是把剛剛回傳給植物醫生的那組字排版成 A6，不是從資料庫讀出來的。
    這樣「之後任何查詢都拿不到明文」這條才站得住。
    """
    if user.role not in ("doctor", "admin"):
        raise HTTPException(403, "只有植物醫生或管理者可以列印帳號小卡")
    farmer = db.get(FarmAccount, farmer_id)
    if not farmer or farmer.role != "farmer":
        raise HTTPException(404, "找不到這個農民帳號")
    if user.role == "doctor" and not db.get(DoctorFarmer, (user.id, farmer_id)):
        raise HTTPException(403, "這位農民不在您的連結清單中")
    from app.services.welcome_card import render_welcome_card
    pdf = render_welcome_card(farmer=farmer, username=farmer.username, password=payload.password,
                              doctor_name=user.name, expires_at=farmer.otp_expires_at)
    name = quote(f"{farmer.name}-農場帳號.pdf")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f"attachment; filename=welcome-card.pdf; filename*=UTF-8''{name}"})


@router.delete("/links/{other_id}")
def unlink(other_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    key = (user.id, other_id) if user.role == "doctor" else (other_id, user.id)
    link = db.get(DoctorFarmer, key)
    if link:
        db.delete(link)
        db.commit()
    return {"ok": True}


class PlanInput(Input):
    farmer_id: str | None = Field(default=None, max_length=36)
    crop_key: str | None = Field(default=None, max_length=40)
    crop_name: str = Field(min_length=1, max_length=80)
    field_name: str = Field(min_length=1, max_length=100)
    variety: str = Field(default="", max_length=80)
    area: str = Field(default="", max_length=60)
    planted_on: date
    end_on: date
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def dates_valid(self):
        if not 14 <= (self.end_on - self.planted_on).days <= 730:
            raise ValueError("規劃長度須為14～730天")
        if self.planted_on.year < 2000 or self.end_on.year > 2100:
            raise ValueError("請輸入2000～2100年間的日期")
        return self


def serialize_plan(db, plan):
    keys = ("id", "farmer_id", "crop_key", "crop_name", "field_name", "variety", "area", "planted_on", "end_on", "notes", "archived", "version", "template_version", "sources")
    events = db.scalars(select(FarmEvent).where(FarmEvent.plan_id == plan.id).order_by(FarmEvent.start_on, FarmEvent.id)).all()
    ekeys = ("id", "kind", "title", "start_on", "end_on", "detail", "source_ids", "media", "completed", "note", "updated_by", "basis")
    # 「已記錄」由是否有對應日誌推導，不另存旗標：只有一個事實來源，標籤不會跟日誌對不上。
    from app.models.farm_log import FarmWorkLog
    ids = [e.id for e in events]
    # 事項的「已記錄」由是否有日誌推導；一併給作業日期，複合圖上的標籤才能直接跳到那筆紀錄所在的月份。
    logged = {event_id: (log_id, done_on) for event_id, log_id, done_on in db.execute(
        select(FarmWorkLog.event_id, FarmWorkLog.id, FarmWorkLog.done_on).where(FarmWorkLog.event_id.in_(ids)))} if ids else {}
    return {k: getattr(plan, k) for k in keys} | dict(events=[
        {k: getattr(e, k) for k in ekeys} | dict(log_id=logged.get(e.id, (None, None))[0], log_done_on=logged.get(e.id, (None, None))[1])
        for e in events])


@router.post("/plans")
def create_plan(payload: PlanInput, db: Session = Depends(get_db), user=Depends(current_user)):
    role_required(user, "farmer")
    from app.services.farm_content import published_templates
    template = next((t for t in published_templates(db) if t["key"] == payload.crop_key), None)
    if payload.crop_key and not template:
        raise HTTPException(422, "尚無此作物的參考範本，請選擇自訂作物")
    farmer_id = payload.farmer_id or user.id
    if farmer_id != user.id and user.role != "admin":
        raise HTTPException(403, "只有管理者能替其他農民建立規劃")
    access_farmer(db, user, farmer_id)
    data = payload.model_dump(exclude={"farmer_id"})
    if template:
        data["crop_name"] = template["name"]
    events = generate_events(payload.crop_key, payload.planted_on, payload.end_on, template)
    used_ids = {s for e in events for s in e["source_ids"]}
    sources = [s for s in (template["sources"] if template else []) if s["id"] in used_ids and s["status"] == "ok"]
    if template and len(sources) != len(used_ids):
        raise HTTPException(503, "此作物的來源資料尚未準備完成，請稍後再試或建立自訂規劃")
    plan = FarmPlan(**data, farmer_id=farmer_id, template_version=template["version"] if template else None, sources=sources)
    db.add(plan)
    db.flush()
    for event in events:
        db.add(FarmEvent(plan_id=plan.id, **event))
    db.commit()
    return serialize_plan(db, plan)


@router.get("/farmers/{farmer_id}")
def workspace(farmer_id: str, db: Session = Depends(get_db), user=Depends(current_user)):
    farmer = access_farmer(db, user, farmer_id)
    plans = db.scalars(select(FarmPlan).where(FarmPlan.farmer_id == farmer_id).order_by(FarmPlan.planted_on, FarmPlan.id)).all()
    notes = db.execute(select(FarmNote, FarmAccount.name, FarmAccount.role).join(FarmAccount, FarmNote.author_id == FarmAccount.id).where(FarmNote.farmer_id == farmer_id).order_by(FarmNote.created_at.desc()).limit(100)).all()
    # 區域地圖達警戒的病蟲害回饋給複合圖的巡查色帶；抓不到這個農場的鄉鎮就是空清單，不是錯誤。
    from app.services import region_stats
    from app.services.consult import taipei_today
    resolved = region_stats.normalize_location(farmer.location)
    region_alerts = region_stats.district_alerts(db, *resolved, taipei_today()) if resolved else []
    return dict(farmer=public_user(farmer), plans=[serialize_plan(db, p) for p in plans],
                notes=[dict(id=n.id, body=n.body, created_at=n.created_at, author=name, role=role) for n, name, role in notes],
                region_alerts=region_alerts)


def diagram_response(data, plan_ids, kinds):
    ids = set(plan_ids.split(","))
    selected = [p for p in data["plans"] if p["id"] in ids]
    categories = set(kinds.split(","))
    if not selected or len(selected) != len(ids) or not categories or not categories <= KINDS.keys():
        raise HTTPException(422, "請選擇有效的作物與管理類別")
    return Response(render_svg(data["farmer"]["farm_name"], selected, categories), media_type="image/svg+xml", headers={"Content-Disposition": 'attachment; filename="farm-plan.svg"', "Cache-Control": "no-store"})


@router.get("/farmers/{farmer_id}/diagram.svg")
def export_diagram(farmer_id: str, plan_ids: str = Query(max_length=5000), kinds: str = "stage,care,nutrition,pest", db: Session = Depends(get_db), user=Depends(current_user)):
    # Same authorization as the workspace; never export arbitrary plan IDs across owners.
    return diagram_response(workspace(farmer_id, db, user), plan_ids, kinds)


@router.get("/example.svg")
def export_example(plan_ids: str = Query(max_length=5000), kinds: str = "stage,care,nutrition,pest"):
    return diagram_response(example(), plan_ids, kinds)


class Archive(Input):
    version: int = Field(ge=1)
    archived: bool


@router.patch("/plans/{plan_id}")
def archive_plan(plan_id: str, payload: Archive, db: Session = Depends(get_db), user=Depends(current_user)):
    plan = access_plan(db, user, plan_id, owner=True, version=payload.version)
    plan.archived = payload.archived
    plan.version += 1
    db.commit()
    return serialize_plan(db, plan)


class EventInput(Input):
    version: int = Field(ge=1)
    kind: Literal["stage", "care", "nutrition", "pest"]
    title: str = Field(min_length=1, max_length=120)
    start_on: date
    end_on: date
    detail: str = Field(default="", max_length=3000)
    note: str = Field(default="", max_length=2000)
    completed: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        if self.end_on < self.start_on:
            raise ValueError("結束日期不能早於開始日期")
        if self.kind in ("stage", "pest") and self.completed:
            raise ValueError("生育階段與病蟲害觀察不能標記為作業完成")
        return self


def validate_event(plan, event):
    if plan.archived:
        raise HTTPException(409, "請先恢復已封存的規劃再修改")
    if event.start_on < plan.planted_on or event.end_on > plan.end_on:
        raise HTTPException(422, "事項日期須位於本期規劃的起訖日期內")


@router.post("/plans/{plan_id}/events")
def add_event(plan_id: str, payload: EventInput, db: Session = Depends(get_db), user=Depends(current_user)):
    plan = access_plan(db, user, plan_id, version=payload.version)
    validate_event(plan, payload)
    if payload.kind in ("stage", "pest") or payload.completed:
        raise HTTPException(422, "建議內容請至作物內容後台維護；完成作業請記錄農事日誌")
    db.add(FarmEvent(plan_id=plan.id, **payload.model_dump(exclude={"version"}), updated_by=user.name, basis="user_added", source_ids=[]))
    plan.version += 1
    db.commit()
    return serialize_plan(db, plan)


@router.put("/plans/{plan_id}/events/{event_id}")
def update_event(plan_id: str, event_id: str, payload: EventInput, db: Session = Depends(get_db), user=Depends(current_user)):
    plan = access_plan(db, user, plan_id, version=payload.version)
    validate_event(plan, payload)
    event = db.get(FarmEvent, event_id)
    if not event or event.plan_id != plan.id:
        raise HTTPException(404, "找不到此事項")
    if event.kind in ("stage", "pest") or payload.kind in ("stage", "pest"):
        raise HTTPException(403, "生育與巡查建議為本期快照，請至作物內容後台維護新版")
    if payload.completed != event.completed:
        raise HTTPException(409, "完成作業請記錄農事日誌；既有紀錄請到日誌頁管理")
    content_changed = any(getattr(event, key) != getattr(payload, key) for key in ("title", "kind", "detail", "start_on", "end_on"))
    for key, value in payload.model_dump(exclude={"version"}).items():
        setattr(event, key, value)
    event.updated_by = user.name
    if content_changed and event.basis == "template_estimate":
        event.basis = "user_adjusted"
    plan.version += 1
    db.commit()
    return serialize_plan(db, plan)


class NoteInput(Input):
    body: str = Field(min_length=1, max_length=2000)


@router.post("/farmers/{farmer_id}/notes")
def add_note(farmer_id: str, payload: NoteInput, db: Session = Depends(get_db), user=Depends(current_user)):
    access_farmer(db, user, farmer_id)
    db.add(FarmNote(farmer_id=farmer_id, author_id=user.id, body=payload.body, created_at=int(time.time())))
    db.commit()
    return {"ok": True}
