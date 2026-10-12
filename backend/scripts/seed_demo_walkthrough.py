"""建立端到端走查用的示範資料（第 9 步第四項）。可重複執行：先砍掉舊的示範帳號再重建。

資料都是「合理但假的」試驗值：帳號叫示範農場、地區用高雄市大社區（區域地圖目前只有高雄），
作物用既有的三個參考範本之一（水稻、番茄），病蟲害用高雄常見的稻熱病與東方果實蠅。
數字不抄設計稿，也不假裝是真實農戶的資料。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 讓這支能 import app.*

import os
from datetime import date, timedelta

from sqlalchemy import create_engine, delete, or_, select
from sqlalchemy.orm import sessionmaker

from app.api.v1.farm_logs import sync_application_record
from app.api.v1.planning import digest, password_hash
from app.models.consult import ApplicationRecord, ConsultCase, PrescriptionItem
from app.models.farm_log import FarmWorkLog
from app.models.planning import DoctorFarmer, FarmAccount, FarmEvent, FarmPlan, FarmSession
from app.models.precision import DataSource, FarmField
from app.models.region import SurveyRecord
from app.services.farm_templates import VERSION, generate_events, source_index
from app.services.farm_log import compliance, default_title

# 容器裡跑（`docker compose exec api`）與從 host 跑連的位址不一樣；有 DATABASE_URL 就用它，
# 沒有才退回 host 的對外 port，兩種跑法都通。
DB_URL = os.environ.get("DATABASE_URL") or "postgresql+psycopg://agri:agri@localhost:5433/agri_doctor"
PASSWORD = "demo-walkthrough-2026"
TODAY = date.today()

ACCOUNTS = [
    # (username, 名稱, 角色, 農場名, 地區, 同意匿名彙整)
    ("demo", "陳示範", "farmer", "示範農場", "高雄市大社區", True),
    ("demo2", "林示範", "farmer", "示範農場乙", "高雄市大社區", True),
    ("demo3", "黃示範", "farmer", "示範農場丙", "高雄市大社區", True),
    ("demodoc", "王植醫", "doctor", "", "", False),
    ("demoagency", "區域示範機關", "agency", "", "", False),
]


def spray_snapshot(product, moa_codes, phi_days):
    """施藥日誌的登記快照（示範用）。

    作用機制就是從這裡進到 `application_records` 的——`moa_codes_for()` 讀的是這個欄位。
    藥名是虛構的試驗名稱，機制代碼用真的格式（含分號後的分類字母），
    好讓輪替比對跑在跟正式資料一樣的字串上。
    """
    return {"registration_id": None, "registration_snapshot_id": None,
            "crop": None, "pest": None, "product_text": product,
            "dilution": "1000", "dose": "0.5 公升/公頃", "phi_days": phi_days,
            "moa_codes": moa_codes, "banned": False, "crop_matches": None,
            "captured_at": None}


def work_log(log_id, *, farmer, kind, days_ago, field, crop, **extra):
    values = dict(field_text=field, crop_text=crop, **extra)
    return FarmWorkLog(
        id=log_id, farmer_id=farmer, created_by=farmer, done_on=TODAY - timedelta(days=days_ago),
        kind=kind, title=extra.pop("title", None) or default_title(kind, material_name=extra.get("material_name", ""),
                                                                   dilution=extra.get("dilution", ""), fallback=crop),
        compliance=compliance(kind, values), **values)


def main() -> None:
    engine = create_engine(DB_URL)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        # 重跑時先清掉舊的示範帳號與它們的所有資料。
        old = db.scalars(select(FarmAccount.id).where(FarmAccount.username.in_([a[0] for a in ACCOUNTS]))).all()
        if old:
            db.execute(delete(ApplicationRecord).where(ApplicationRecord.farmer_id.in_(old)))
            db.execute(delete(FarmWorkLog).where(FarmWorkLog.farmer_id.in_(old)))
            # 走查一定會開單，開完之後這支就得刪得掉自己造成的案件，否則「可重複執行」只成立一次。
            cases = db.scalars(select(ConsultCase.id).where(
                or_(ConsultCase.doctor_id.in_(old), ConsultCase.farmer_id.in_(old)))).all()
            if cases:
                db.execute(delete(PrescriptionItem).where(PrescriptionItem.case_id.in_(cases)))
                db.execute(delete(ConsultCase).where(ConsultCase.id.in_(cases)))
            plans = db.scalars(select(FarmPlan.id).where(FarmPlan.farmer_id.in_(old))).all()
            if plans:
                db.execute(delete(FarmEvent).where(FarmEvent.plan_id.in_(plans)))
                db.execute(delete(FarmPlan).where(FarmPlan.id.in_(plans)))
            # 精準模式的示範資料：田區一刪，分區／測站／讀數／圖層／建議都跟著 CASCADE。
            db.execute(delete(FarmField).where(FarmField.farmer_id.in_(old)))
            db.execute(delete(DataSource).where(DataSource.farmer_id.in_(old)))
            db.execute(delete(SurveyRecord).where(SurveyRecord.recorded_by.in_(old)))
            db.execute(delete(DoctorFarmer).where(DoctorFarmer.farmer_id.in_(old)))
            db.execute(delete(DoctorFarmer).where(DoctorFarmer.doctor_id.in_(old)))
            db.execute(delete(FarmSession).where(FarmSession.account_id.in_(old)))
            db.execute(delete(FarmAccount).where(FarmAccount.id.in_(old)))
            db.commit()

        for username, name, role, farm_name, location, share in ACCOUNTS:
            db.add(FarmAccount(id=username, username=username, name=name, role=role,
                               password_hash=password_hash(PASSWORD), farm_name=farm_name or "我的農場",
                               location=location, weather_county="高雄市" if location else None,
                               precision_enabled=(username == "demo"), share_region=share))
        db.flush()
        db.add(DoctorFarmer(doctor_id="demodoc", farmer_id="demo"))  # 植物醫生看得到示範農場
        # 第二位農友供切換驗收：空白工作區能抓出錯把上一位日誌／感測值留下的問題。
        db.add(DoctorFarmer(doctor_id="demodoc", farmer_id="demo2"))

        # ---- 規劃：兩期作物，用既有參考範本產生事項（複合圖才有生育期與巡查色帶） ----
        sources = [s for s in source_index() if s["status"] == "ok"]
        for plan_id, crop_key, crop_name, field_name, area, planted_days_ago, days in [
            ("demo-rice", "rice", "水稻", "第一田區（北）", "0.8 公頃", 86, 120),
            ("demo-tomato", "tomato", "番茄", "第二田區（南）", "0.3 公頃", 38, 150),
        ]:
            planted = TODAY - timedelta(days=planted_days_ago)
            end = planted + timedelta(days=days)
            db.add(FarmPlan(id=plan_id, farmer_id="demo", crop_key=crop_key, crop_name=crop_name,
                            field_name=field_name, variety="", area=area, planted_on=planted, end_on=end,
                            notes="", template_version=VERSION, sources=sources))
            db.flush()
            for event in generate_events(crop_key, planted, end):
                db.add(FarmEvent(plan_id=plan_id, **event))

        # ---- 一條對得上 alert_thresholds 的巡查事項 ----
        # 下面的主動調查讓「東方果實蠅」在大社區達警戒，但參考範本的巡查項目裡沒有它，
        # 複合圖的「附近已達警戒」紅標就永遠出不來、截圖與展示也看不到。
        # 番茄是東方果實蠅的寄主，農民自己加一條巡查窗口是合理的；標成 user_added，
        # 不要冒充官方範本。窗口涵蓋今天，才會同時出現在「田間多留意」卡片上。
        db.add(FarmEvent(id="demo-tomato-fruitfly", plan_id="demo-tomato", kind="pest",
                         title="東方果實蠅", start_on=TODAY - timedelta(days=20),
                         end_on=TODAY + timedelta(days=40), source_ids=[],
                         detail="果實近成熟期留意叮咬孔與落果；本項為農民自行新增，非參考範本。",
                         updated_by="陳示範", basis="user_added"))

        # ---- 日誌：整地→施肥→施藥→巡田，含一次採收 ----
        db.add_all([
            work_log("demo-log-1", farmer="demo", kind="tillage", days_ago=88, field="第一田區（北）", crop="水稻",
                     note="整地與基肥翻耕。"),
            work_log("demo-log-2", farmer="demo", kind="fertilize", days_ago=72, field="第一田區（北）", crop="水稻",
                     material_name="試驗複合肥料", material_supplier="試驗資材行", material_lot="T-2026-01",
                     amount_value="40", amount_unit="公斤", area_text="0.8 公頃"),
            work_log("demo-log-3", farmer="demo", kind="irrigate", days_ago=60, field="第一田區（北）", crop="水稻",
                     note="分蘗盛期後曬田，之後回水。"),
            # ---- 同田區連續三次同機制（FRAC 3）：抗藥性輪替橫幅要有東西可算 ----
            # 三筆都在第一田區（北）。植物醫生替這塊田開單時再選一支 FRAC 3（水稻有 20 筆登記，
            # 例如待克利、依普座），就會是「連續第 4 次」，案件層級的 crit 橫幅才看得到。
            #
            # 為什麼挑 FRAC 3 而不是別的機制：水稻的 FRAC 3 登記多、最短安全採收期 7 天，
            # 在這期規劃剩下的天數內開得出來。換成 FRAC 11 的話水稻只有一支藥、採收期 35 天，
            # 會先被安全採收期擋下，橫幅根本來不及出現——示範資料要能走到想展示的那一步。
            work_log("demo-log-8", farmer="demo", kind="spray", days_ago=52, field="第一田區（北）", crop="水稻",
                     material_name="試驗殺菌劑丙", material_supplier="試驗資材行", material_lot="T-2026-03",
                     dilution="1500", amount_value="0.4", amount_unit="公升", area_text="0.8 公頃",
                     pest_text="稻熱病", phi_days=6, phi_source="registration",
                     registration_snapshot=spray_snapshot("試驗殺菌劑丙 水懸劑", ["FRAC 3;G1"], 6)),
            work_log("demo-log-9", farmer="demo", kind="spray", days_ago=36, field="第一田區（北）", crop="水稻",
                     material_name="試驗殺菌劑乙", material_supplier="試驗資材行", material_lot="T-2026-05",
                     dilution="2000", amount_value="0.5", amount_unit="公升", area_text="0.8 公頃",
                     pest_text="稻熱病", phi_days=6, phi_source="registration",
                     registration_snapshot=spray_snapshot("試驗殺菌劑乙 水分散性粒劑", ["FRAC 3;G1"], 6)),
            work_log("demo-log-4", farmer="demo", kind="spray", days_ago=21, field="第一田區（北）", crop="水稻",
                     material_name="試驗殺菌劑甲", material_supplier="試驗資材行", material_lot="T-2026-07",
                     dilution="1000", amount_value="0.5", amount_unit="公升", area_text="0.8 公頃",
                     pest_text="稻熱病", phi_days=6, phi_source="registration",
                     registration_snapshot=spray_snapshot("試驗殺菌劑甲 水懸劑", ["FRAC 3;G1"], 6)),
            work_log("demo-log-5", farmer="demo", kind="scout", days_ago=9, field="第一田區（北）", crop="水稻",
                     pest_text="稻熱病", note="下位葉出現紡錘形病斑，範圍侷限在北側田角。"),
            # 這筆跟另外兩戶同一天：同一鄉鎮、同一天、同一作物病蟲害滿 3 戶，地圖那格才顯示數字。
            work_log("demo-log-6", farmer="demo", kind="scout", days_ago=5, field="第一田區（北）", crop="水稻",
                     pest_text="稻熱病", note="病斑略增，已與植物醫生聯繫。"),
            work_log("demo-log-7", farmer="demo", kind="harvest", days_ago=5, field="第二田區（南）", crop="番茄",
                     area_text="0.3 公頃", yield_value="180", yield_unit="公斤", yield_destination="果菜市場"),
        ])

        # ---- 另外兩戶同一天也通報同一個病害：滿 3 戶，地圖才顯示數字（< 3 戶顯示「樣本不足」） ----
        for farmer in ("demo2", "demo3"):
            db.add(work_log(f"{farmer}-scout", farmer=farmer, kind="scout", days_ago=5,
                            field="本場田區", crop="水稻", pest_text="稻熱病",
                            note="鄰田同期出現類似病斑。"))

        # ---- 施藥紀錄由日誌投影出來 ----
        # 用正式流程那支 `sync_application_record()`，不要自己塞 application_records：
        # 兩邊各寫一份遲早會對不起來，而輪替檢查看的就是這張表。
        db.flush()
        for log in db.scalars(select(FarmWorkLog).where(FarmWorkLog.kind == "spray")).all():
            sync_application_record(db, log)

        # ---- 主動調查：黏板每板 22 隻，超過門檻 15 → 大社區在地圖上畫紅框 ----
        db.add(SurveyRecord(id="demo-survey", district="大社區", surveyed_on=TODAY - timedelta(days=2),
                            crop_text="番石榴", pest_text="東方果實蠅", trap_count=22.0, traps=3,
                            recorded_by="demodoc", note="三點平均；果園東側最高。"))
        db.commit()

    print("示範資料完成：")
    print(f"  農民　　 demo / demo2 / demo3　密碼 {PASSWORD}")
    print(f"  植物醫生 demodoc　　　　　　　密碼 {PASSWORD}")
    print(f"  主管機關 demoagency　　　　　 密碼 {PASSWORD}")
    print("  抗藥性輪替：第一田區（北）已有連續三次 FRAC 3 的施藥紀錄；")
    print("             從農民的「水稻／第一田區（北）」規劃開單，加入一支 FRAC 3（例如待克利 → 紋枯病），")
    print("             就會是連續第 4 次，開單頁最上方出現案件層級的紅色橫幅。")
    print("  接著跑：python -m app.precision.demo --username demo（精準模式）")
    print("  以及：  python -m app.jobs region-aggregate（區域地圖彙整）")


if __name__ == "__main__":
    main()
