"""工作區的公開行情與成本參考；僅傳作物搜尋詞，不傳農民身分或田區資料給上游。"""
from fastapi import APIRouter, Depends, Query
from app.api.v1.planning import current_user
from app.services import moa
from app.db.session import get_db
from sqlalchemy.orm import Session

router = APIRouter(prefix='/economics', tags=['農場行情與成本'], dependencies=[Depends(current_user)])


@router.get('/market')
def market(crop: str = Query(min_length=1, max_length=60, pattern=r"\S"), days: int = Query(default=7, ge=1, le=14), db: Session = Depends(get_db)):
    return moa.market(crop.strip(), days, db=db)


@router.get('/costs')
def costs(crop: str = Query(default='', max_length=60), db: Session = Depends(get_db)):
    return moa.costs(crop.strip(), db=db)
