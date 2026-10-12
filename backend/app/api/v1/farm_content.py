"""作物內容後台：獨立管理者權限、樂觀鎖與發布快照。"""
from datetime import datetime, timezone
from typing import Literal
import re
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.api.v1.planning import Input, current_user, protect_write, role_required
from app.db.session import get_db
from app.models.farm_content import FarmContent, FarmContentRelease
from app.services.farm_content import editor_rows
from app.services.farm_templates import TEMPLATES


def administrator(user=Depends(current_user)):
    role_required(user, 'admin')
    return user


router = APIRouter(prefix='/admin/farm-content', tags=['farm content'],
                   dependencies=[Depends(protect_write), Depends(administrator)])


def safe_url(value):
    parsed = urlparse(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('請使用不含帳密的 HTTPS 來源網址')
    return value


class Media(Input):
    kind: Literal['image', 'video']
    url: str = Field(min_length=1, max_length=2000)
    caption: str = Field(min_length=1, max_length=300)
    source_url: str = Field(min_length=1, max_length=2000)
    @field_validator('url')
    @classmethod
    def media_url(cls, value):
        # 只有版本控制內的教學示意素材可用相對網址，不接受任意本地檔案路徑。
        if re.fullmatch(r'/guidance-demo/[a-z0-9-]+\.(svg|webm)', value):
            return value
        return safe_url(value)

    _source = field_validator('source_url')(safe_url)


class Reference(Input):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=160)
    url: str = Field(min_length=1, max_length=2000)
    fetched_at: str = ''
    publication_date: str | None = None
    sha256: str = ''
    status: str = 'ok'
    # 舊的離線来源索引另存本地檔案位置，保留它們以免編輯草稿掉資料。
    model_config = {'extra': 'allow', 'str_strip_whitespace': True}
    _url = field_validator('url')(safe_url)


class ContentEvent(Input):
    kind: Literal['stage', 'care', 'nutrition', 'pest']
    title: str = Field(min_length=1, max_length=120)
    start: int = Field(ge=0, le=730)
    end: int = Field(ge=0, le=730)
    detail: str = Field(min_length=1, max_length=3000)
    source_ids: list[str] = Field(default_factory=list, max_length=30)
    media: list[Media] = Field(default_factory=list, max_length=12)


class Content(Input):
    key: str = Field(pattern=r'^[a-z][a-z0-9_-]{0,39}$')
    name: str = Field(min_length=1, max_length=80)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    emoji: str = Field(default='🌱', max_length=12)
    days: int = Field(ge=14, le=730)
    anchor: str = Field(default='種植日', min_length=1, max_length=30)
    scope: str = Field(min_length=1, max_length=1000)
    sources: list[Reference] = Field(default_factory=list, max_length=100)
    events: list[ContentEvent] = Field(min_length=1, max_length=150)

    @model_validator(mode='after')
    def coherent(self):
        ids = [s.id for s in self.sources]
        if len(ids) != len(set(ids)):
            raise ValueError('參考來源代碼不可重複')
        available = {s.id for s in self.sources if s.status == 'ok'}
        for e in self.events:
            if e.end < e.start or e.end > self.days:
                raise ValueError('事項日數須位於範本期間內，結束日不得早於開始日')
            if not set(e.source_ids) <= available:
                raise ValueError('事項參考來源不存在或尚未確認')
        return self


class Save(Input):
    revision: int = Field(ge=0)
    content: Content


class Publish(Input):
    revision: int = Field(ge=1)


@router.get('')
def index(db: Session = Depends(get_db)):
    return editor_rows(db)


@router.put('/{key}')
def save(key: str, payload: Save, db: Session = Depends(get_db)):
    if payload.content.key != key:
        raise HTTPException(422, '作物代碼不一致')
    row = db.scalar(select(FarmContent).where(FarmContent.key == key).with_for_update())
    if (row.revision if row else 0) != payload.revision:
        raise HTTPException(409, '範本已由其他人更新，請重新載入後再編輯')
    if row is None:
        row = FarmContent(key=key, revision=0, draft={})
        db.add(row)
    row.draft = payload.content.model_dump()
    row.revision += 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '範本已由其他人建立，請重新載入')
    return next(r for r in editor_rows(db) if r['key'] == key)


@router.post('/{key}/publish')
def publish(key: str, payload: Publish, db: Session = Depends(get_db), user=Depends(administrator)):
    row = db.scalar(select(FarmContent).where(FarmContent.key == key).with_for_update())
    if not row or row.revision != payload.revision:
        raise HTTPException(409, '請先儲存草稿，或重新載入最新版本')
    content = Content.model_validate(row.draft)
    if any(not e.source_ids for e in content.events):
        raise HTTPException(422, '發布前每項建議都必須附上參考來源')
    version = f'{key}.r{row.revision}'
    if row.published_version != version:
        row.published = content.model_dump()
        row.published_version = version
        db.add(FarmContentRelease(crop_key=key, revision=row.revision, content=row.published,
                                 published_by=user.id, published_at=datetime.now(timezone.utc).isoformat()))
        db.commit()
    return next(r for r in editor_rows(db) if r['key'] == key)


@router.get('/{key}/releases')
def releases(key: str, db: Session = Depends(get_db)):
    return [dict(revision=r.revision, published_by=r.published_by, published_at=r.published_at, content=r.content)
            for r in db.scalars(select(FarmContentRelease).where(FarmContentRelease.crop_key == key)
                                .order_by(FarmContentRelease.revision.desc())).all()]
