"""補齊內容管理展示資料；只建立缺少的專用範例，不刪除或覆蓋現有帳號與農場。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.farm_content import FarmContent, FarmContentRelease
from app.models.planning import FarmAccount, FarmEvent, FarmPlan
from app.models.farm_log import FarmWorkLog
from app.models.consult import ConsultCase
from app.models.precision import FarmField
from app.api.v1.planning import password_hash
from app.api.v1.farm_content import Content
from app.services.farm_content import template_base
from app.services.farm_templates import TEMPLATES, generate_events
from app.services.consult import taipei_today
from app.services.farm_log import compliance
from app.precision.demo import generate_demo

PASSWORD = 'demo-walkthrough-2026'
ADMIN = 'demoadmin'
FARMER = 'democontent'
KEY = 'demo-observation'


def seed(db):
    now = datetime.now(timezone.utc)
    today = taipei_today()
    for username, role, name in [(ADMIN, 'admin', '範例管理者'), (FARMER, 'farmer', '內容展示農友')]:
        account = db.scalar(select(FarmAccount).where(FarmAccount.username == username))
        if account and (account.id != username or account.role != role):
            raise RuntimeError(f'{username} 已被其他帳號使用，停止以免覆蓋')
        if not account:
            db.add(FarmAccount(id=username, username=username, name=name, role=role,
                               password_hash=password_hash(PASSWORD), farm_name='內容管理示範農場',
                               location='高雄市大社區', weather_county='高雄市', precision_enabled=True))
    db.flush()
    # 原範本留作可編輯草稿；展示素材放在獨立的示範作物，不能混入真實農藝建議。
    for template in TEMPLATES:
        if not db.get(FarmContent, template['key']):
            db.add(FarmContent(key=template['key'], revision=1, draft=template_base(template)))
    row = db.get(FarmContent, KEY)
    if not row:
        data = template_base(TEMPLATES[0])
        data.update(key=KEY, name='水稻（操作示範）', aliases=['範例水稻'], scope='僅供展示內容編輯與農事紀錄；非真實農場資料')
        for e in data['events']:
            e['media'] = [dict(kind=kind, url=f'/guidance-demo/field-observation.{ext}',
                               caption='操作示意：從全田到部位、細節留存觀察；不是真實病徵或診斷依據。',
                               source_url='https://www.moa.gov.tw/ws.php?id=2415') for kind, ext in [('image', 'svg'), ('video', 'webm')]]
        data = Content.model_validate(data).model_dump()
        row = FarmContent(key=KEY, revision=1, draft=data, published=deepcopy(data), published_version=f'{KEY}.r1')
        db.add(row)
        db.add(FarmContentRelease(crop_key=KEY, revision=1, content=data, published_by=ADMIN, published_at=now.isoformat()))
    db.flush()
    for owner in (ADMIN, FARMER):
        plan_id = f'{owner}-content-plan'
        if db.get(FarmPlan, plan_id):
            continue
        start = today - timedelta(days=20)
        end = start + timedelta(days=120)
        plan = FarmPlan(id=plan_id, farmer_id=owner, crop_key=KEY, crop_name='水稻（操作示範）',
                        field_name='示範田區 A', area='0.6 公頃', planted_on=start, end_on=end,
                        notes='純範例資料，不作為實際作業或用藥證明', sources=row.published['sources'],
                        template_version=row.published_version)
        db.add(plan); db.flush()
        for i, e in enumerate(generate_events(KEY, start, end, row.published)):
            db.add(FarmEvent(id=f'{owner}-content-{i}', plan_id=plan_id, **e))
        db.flush()
        event = db.get(FarmEvent, f'{owner}-content-5')
        event.completed = True
        event.updated_by = '範例管理者'
        values = dict(kind='other', title='示範：確認成活與田水', field_text=plan.field_name,
                      crop_text=plan.crop_name, note='純範例：展示完成事項如何出現在日誌與複合圖。')
        db.add(FarmWorkLog(id=f'{owner}-content-log', farmer_id=owner, plan_id=plan_id, event_id=event.id,
                          created_by=ADMIN, done_on=today - timedelta(days=2), created_at=now, updated_at=now,
                          compliance=compliance('other', values), **values))
        db.add(FarmWorkLog(id=f'{owner}-content-scout', farmer_id=owner, plan_id=plan_id, created_by=ADMIN,
                          done_on=today, kind='scout', title='示範巡田觀察', field_text=plan.field_name,
                          crop_text=plan.crop_name, pest_text='待植物醫生鑑別', note='展示資料：不能據此判斷田間已發病。',
                          compliance={}, created_at=now, updated_at=now))
    if not db.get(ConsultCase, 'demo-content-case'):
        db.add(ConsultCase(id='demo-content-case', doctor_id=ADMIN, farmer_id=FARMER,
                          case_no='DEMO-CONTENT-01', farmer_text='內容展示農友', crop_text='水稻（操作示範）',
                          field_text='示範田區 A', symptoms='操作展示：葉片有異常，尚待現場確認。',
                          diagnosis_text='待鑑別（示範案件）', status='draft'))
    db.flush()
    for account_id in (ADMIN, FARMER):
        if not db.scalar(select(FarmField.id).where(FarmField.farmer_id == account_id)):
            generate_demo(db, db.get(FarmAccount, account_id), now=now, today=today)
    db.commit()


if __name__ == '__main__':
    with SessionLocal() as db:
        seed(db)
    print('範例內容、管理者、農友、日誌、案件與精準模式已備妥；重跑不覆蓋既有資料。')
