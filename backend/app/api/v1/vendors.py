"""農藥廠商與許可證：只讀本地已發布快照，沒有隱藏的上游 API 呼叫。

為什麼要有這一頁：`PesticideDataQueryType` 早就同步到本地了，但只能在「農業資料庫」那個
通用瀏覽器裡一列一列翻。植物醫生真正會被問的是「我手上這罐為什麼不能用」「這家還有哪些藥」——
那是廠商導向的問法，通用瀏覽器答不了。

**邊界**：許可證回答的是「這支藥有沒有登記、登記在誰名下、到什麼時候」，
不是「這支藥能不能用在這個作物的這個病蟲害上」。後者要走三源比對。
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.planning import current_user
from app.db.session import get_db
from app.models.moa import MoaRecord, MoaSnapshot
from app.services.consult import taipei_today
from app.services.moa_catalog import normalize
from app.services.moa_store import DatabaseStore, aware, dataset_key
from app.services import pesticide_permits as permits

router = APIRouter(prefix='/vendors', tags=['農藥廠商查詢'], dependencies=[Depends(current_user)])

PERMIT_DATASET = 'PesticideDataQueryType'
#: 一次最多回幾列許可證；一家大廠可能有數百張，整批丟給前端只是把捲軸變長。
PAGE_SIZE = 100


def _snapshot(db: Session):
    store = DatabaseStore(db)
    state = store.state(dataset_key(PERMIT_DATASET, {}))
    snap = db.get(MoaSnapshot, state.active_snapshot_id) if state and state.active_snapshot_id else None
    return state, snap


def _source(db: Session) -> dict:
    """資料來源與最後同步時間。畫面一定要講得出這兩件事，不然使用者沒辦法判斷新不新。"""
    state, snap = _snapshot(db)
    now = datetime.now(timezone.utc)
    stale = bool(snap and (now >= aware(snap.refresh_after) or (state and state.last_error)))
    return dict(available=bool(snap), row_count=snap.row_count if snap else 0,
                fetched_at=aware(snap.fetched_at).isoformat() if snap else None,
                status=('stale' if stale else 'ok') if snap else 'not_synced',
                last_error=state.last_error if state else None,
                source_url='https://data.moa.gov.tw/api.aspx', dataset=PERMIT_DATASET)


def _rows(db: Session, needle: str = '') -> list[dict]:
    _, snap = _snapshot(db)
    if not snap:
        return []
    stmt = select(MoaRecord).where(MoaRecord.snapshot_id == snap.id)
    if needle:
        stmt = stmt.where(MoaRecord.search_text.contains(needle, autoescape=True))
    return [permits.permit_row(r.payload) for r in db.scalars(stmt.order_by(MoaRecord.ordinal)).all()]


@router.get('')
def search_vendors(q: str = Query(default='', max_length=120), limit: int = Query(default=20, ge=1, le=50),
                   db: Session = Depends(get_db)):
    """廠商 typeahead：名稱＋張數。

    廠商名**允許子字串比對**——這裡比的是公司名不是藥劑名，
    「興農股份有限公司」被「興農」命中正是想要的；藥劑名不可子字串的原則不適用於此。
    """
    needle = normalize(q)
    rows = _rows(db, needle)
    names = permits.vendor_names(rows)
    if needle:
        # 前綴的排在前面：打「興農」時想找的多半是「興農」本身而不是「大興農業」。
        names.sort(key=lambda item: (not normalize(item['vendor']).startswith(needle), -item['permits'], item['vendor']))
    return dict(items=names[:limit], total=len(names), source=_source(db))


@router.get('/{vendor}/permits')
def vendor_permits(vendor: str, q: str = Query(default='', max_length=120),
                   permit: str = Query(default='', max_length=12),
                   revoked: bool | None = None, page: int = Query(default=1, ge=1),
                   db: Session = Depends(get_db)):
    """某一家廠商的許可證清單。

    **已撤銷的照樣列出**（前端整列淡紅）：植物醫生要能回答農民「手上這罐為什麼不能用」，
    把它從清單拿掉等於讓那個問題無解。
    """
    name = normalize(vendor)
    if not name:
        raise HTTPException(422, '請指定廠商')
    rows = [r for r in _rows(db) if normalize(r['vendor']) == name]
    if not rows:
        raise HTTPException(404, '找不到這家廠商的許可證資料')
    today = taipei_today()
    summary = permits.vendor_summary(rows, today)
    address = _vendor_address(db, vendor)

    needle = normalize(q)
    filtered = [r for r in rows
                if (not needle or needle in normalize(' '.join(
                    (r['chinese_name'], r['en_name'], r['brand_name'], r['permit_number']))))
                and (not permit or r['permit'] == permit)
                and (revoked is None or r['revoked'] == revoked)]
    # 排序照「現在用得到的先看」：還有效的（快到期的排前面，那是要留意的）→ 期限未載明
    # → 已逾期（近的先）→ 已撤銷。照單純的日期排會把 2014 年就過期的原體頂到最上面。
    order = {'expiring': 0, 'active': 1, 'unknown': 2, 'expired': 3, 'revoked': 4}
    def rank(row):
        status = permits.permit_status(row, today)
        expire = row['expire_on'] or ''
        # 有效與快到期：早到期的先；已逾期與撤銷：晚發生的先（比較可能還在農民手上）。
        return (order[status], expire if status in ('expiring', 'active') else '', 
                '' if status in ('expiring', 'active') else _desc(expire), row['permit_number'])
    filtered.sort(key=rank)
    total = len(filtered)
    page_rows = filtered[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    return dict(vendor=rows[0]['vendor'], address=address, summary=summary,
                items=[dict(r, status=permits.permit_status(r, today)) for r in page_rows],
                total=total, page=page, page_size=PAGE_SIZE, source=_source(db))


def _desc(value: str) -> str:
    """把日期字串反轉成可以「由新到舊」升冪排的鍵；沒有日期的排最後。"""
    if not value:
        return 'A'  # 空字串排在所有反轉後的數字之後
    return ''.join(chr(ord('9') - int(ch) + ord('0')) if ch.isdigit() else ch for ch in value)


def _vendor_address(db: Session, vendor: str) -> str:
    """地址在另一個資料集（`PesticideCompanyType`）；查不到就留空，不要猜。"""
    store = DatabaseStore(db)
    state = store.state(dataset_key('PesticideCompanyType', {}))
    snap = db.get(MoaSnapshot, state.active_snapshot_id) if state and state.active_snapshot_id else None
    if not snap:
        return ''
    name = normalize(vendor)
    for record in db.scalars(select(MoaRecord).where(MoaRecord.snapshot_id == snap.id)).all():
        if normalize(record.payload.get('Company_Name')) == name:
            return (record.payload.get('Company_Addr') or '').strip()
    return ''


@router.get('/formulations/summary')
def formulation_summary(ingredient: str = Query(default='', max_length=200),
                        contents: str = Query(default='', max_length=60),
                        formulation: str = Query(default='', max_length=60),
                        db: Session = Depends(get_db)):
    """三源比對的藥劑卡那一行：這個**配方**有幾張許可證、幾家廠商、幾張已撤銷。

    刻意不回「挑中的那一張」：同配方多家廠商是常態，挑一家印在處方卡上就是替植物醫生
    指定品牌。要看是哪幾張，畫面連到農藥廠商查詢。

    三元組（英文成分名＋含量＋劑型）任一項正規化失敗就回 `matched=False`，那一行整個不顯示。
    """
    key = permits.formula_key(ingredient=ingredient, contents=contents, formulation=formulation)
    if not key:
        return dict(matched=False, reason='unmappable', summary=None, source=_source(db))
    summary = permits.formula_summary(_rows(db), key)
    # 一張都對不到也是「不顯示」：可能是上游還沒收錄，不要畫一行「0 張」讓人以為這支藥沒登記。
    if not summary['permits']:
        return dict(matched=False, reason='not_found', summary=None, source=_source(db))
    return dict(matched=True, reason=None, summary=summary, source=_source(db))


@router.get('/permits/lookup')
def lookup_permit(pesticide_code: str = Query(default='', max_length=20),
                  permit_number: str = Query(default='', max_length=20),
                  db: Session = Depends(get_db)):
    """三源比對的藥劑卡用：一支藥對應的那一張許可證。

    只做精確比對（成分代碼／許可證號）；對不到就回 `found=False`，前端不顯示那一行。
    **不要拿藥名比對**——中文沒有詞界，「克枯爛」會命中複方「嘉賜克枯爛」。
    """
    rows = _rows(db)
    hit = permits.match_permit(rows, pesticide_code=pesticide_code, permit_number=permit_number)
    if not hit:
        return dict(found=False, permit=None, source=_source(db))
    return dict(found=True, permit=dict(hit, status=permits.permit_status(hit, taipei_today())),
                source=_source(db))
