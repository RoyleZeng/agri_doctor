"""UX 實際走查：使用本機示範帳號，驗證操作並更新截圖與操作步驟 GIF。

先執行 seed_demo_walkthrough.py、精準示範產生器及 region-aggregate。
只在示範帳號建立驗收紀錄；不是正式農戶資料，也不觸發爬蟲。
"""
import json
import re
import sys
from collections import Counter
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 讓這支能 import app.*

from PIL import Image
from playwright.sync_api import sync_playwright, expect, TimeoutError as PlaywrightTimeout

BASE = "http://localhost:5173"
# 走查自己建的列用這兩個固定標記認得出來；清理時連舊的「第三輪…」一起帶走。
WALKTHROUGH_FIELD = "走查田區"
_LEGACY_FIELDS = ("第三輪走查田區",)
_LEGACY_LOG_PREFIX = "第三輪 "


def log_title(role):
    return f"走查 {role} 操作驗收（示範）"


def reset_demo_rows():
    """把上一次走查建的列刪乾淨，讓這支可以重跑。

    跟 `app.precision.demo` 同一個作法：**只刪自己建的示範列**——固定的示範帳號、
    固定的田區名與日誌標題。真實資料一列都不碰，也不為了腳本方便去開一支正式刪除 API：
    這個平台的紀錄是要交給政府的，不能事後從正式介面消失。
    """
    import os
    from sqlalchemy import create_engine, delete, or_, select
    from sqlalchemy.orm import sessionmaker

    from app.models.farm_log import FarmWorkLog
    from app.models.planning import FarmAccount, FarmEvent, FarmPlan

    url = os.environ.get("DATABASE_URL") or "postgresql+psycopg://agri:agri@localhost:5433/agri_doctor"
    with sessionmaker(bind=create_engine(url))() as db:
        owners = db.scalars(select(FarmAccount.id).where(FarmAccount.username.in_(ACCOUNTS.values()))).all()
        if not owners:
            return
        fields = (WALKTHROUGH_FIELD, *_LEGACY_FIELDS)
        plans = db.scalars(select(FarmPlan.id).where(
            FarmPlan.farmer_id.in_(owners), FarmPlan.field_name.in_(fields))).all()
        if plans:
            db.execute(delete(FarmEvent).where(FarmEvent.plan_id.in_(plans)))
            db.execute(delete(FarmPlan).where(FarmPlan.id.in_(plans)))
        titles = [log_title(role) for role in ACCOUNTS]
        db.execute(delete(FarmWorkLog).where(
            FarmWorkLog.farmer_id.in_(owners),
            or_(FarmWorkLog.title.in_(titles),
                FarmWorkLog.title.like(_LEGACY_LOG_PREFIX + "%"),
                FarmWorkLog.field_text.in_(fields))))
        db.commit()
        print(f"  清掉上一次走查的 {len(plans)} 期規劃與相關日誌", flush=True)
OUT = Path(__file__).resolve().parents[2] / "docs" / "screenshots"
ACCOUNTS = {"farmer": "demo", "doctor": "demodoc", "agency": "demoagency"}
VIEWPORT = {"width": 1440, "height": 950}
frames = []
checks = []


def record(page, label, name=None):
    # 清掉浮動提示才不會蓋住截圖。用「取第一個、點掉、再看還有沒有」而不是先取一份清單：
    # 清單取好之後又冒出一則（例如封存成功），舊的參照就失效了。
    toasts = page.locator('.toast')
    for _ in range(6):
        if not toasts.count():
            break
        try:
            toasts.first.click(timeout=1000)
        except PlaywrightTimeout:
            break
    if page.locator('.mapbox').count():
        try:
            page.locator('.leaflet-tile-loaded').first.wait_for(timeout=5000)
        except PlaywrightTimeout:
            print('  OSM 圖磚未完成載入；本機鄉鎮界仍可驗收', flush=True)
    page.wait_for_timeout(250)  # 只等 CSS 動畫；資料就緒由呼叫端的 expect 驗證。
    raw = page.locator('.farm-chart').screenshot() if name == '02-plan-chart' else page.screenshot()
    frame = Image.open(BytesIO(raw)).convert("RGB")
    frame.thumbnail((1008, 665))
    canvas = Image.new('RGB', (1008, 665), '#f4f6f3')
    canvas.paste(frame, ((1008 - frame.width) // 2, 0))
    frames.append(canvas)
    if name:
        (OUT / f"{name}.png").write_bytes(raw)
    checks.append(label)
    print(f"✓ {label}", flush=True)


def login(browser, who):
    context = browser.new_context(viewport=VIEWPORT, locale="zh-TW", accept_downloads=True)
    page = context.new_page()
    page.goto(f"{BASE}/farm")
    page.get_by_placeholder("英數帳號或電子信箱").fill(ACCOUNTS[who])
    page.get_by_placeholder("至少10個字元").fill("demo-walkthrough-2026")
    page.get_by_role("button", name="登入工作區 →").click()
    expect(page).to_have_url(re.compile({"farmer": "/farm$", "doctor": "/farmers$", "agency": "/region$"}[who]))
    return context, page


def sidebar(page, label):
    """點側邊欄項目。第四輪的清單是兩層，子項在收合的群組裡點不到，先把群組打開。"""
    link = page.get_by_role("link", name=label, exact=True)
    if not link.first.is_visible():
        # 只打開「裝著這個子項」的那一組；一次展開全部會讓 DOM 變動、後面的定位器失效。
        # 這裡不能用 get_by_role：收合的子項是 display:none，不在無障礙樹裡，role 選取器找不到。
        page.locator(f'.sgroup:has(.kids a:has-text("{label}")) .car').first.click()
        expect(link.first).to_be_visible()
    link.first.click()


def download(page, button, stem, signature):
    with page.expect_download() as pending:
        button.click()
    result = pending.value
    path = OUT / f"{stem}{Path(result.suggested_filename).suffix}"
    result.save_as(path)
    assert signature in path.read_bytes()[:500], path


def mobile_check(page, who):
    page.set_viewport_size({'width': 390, 'height': 844})
    page.evaluate('window.scrollTo(0, 0)')
    page.wait_for_timeout(250)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), who
    page.get_by_role('button', name='開啟導覽選單').click()
    expect(page.locator('aside.side.open')).to_be_visible()
    page.get_by_role('button', name='開啟導覽選單').click()
    record(page, f'{who}：手機抽屜與無整頁橫向溢出', f'round3-mobile-{who}')
    page.set_viewport_size(VIEWPORT)


def log_action(page, role):
    expect(page.locator(".flog-card").first).to_be_visible()
    page.get_by_role("button", name="記一筆", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog).to_be_visible()
    dialog.get_by_role("radio", name="其他", exact=True).click()
    field = dialog.get_by_label("田區名稱", exact=True)
    if field.count():
        field.fill(WALKTHROUGH_FIELD)
    dialog.get_by_label("做了什麼", exact=True).fill(log_title(role))
    dialog.locator(".wlf-save").click()
    expect(dialog).not_to_be_visible()
    expect(page.get_by_text(log_title(role), exact=True).first).to_be_visible()
    record(page, f"{role}：記一筆已實際儲存")


def log_export(page, role, screenshot=False):
    page.get_by_role("button", name="匯出農事紀錄", exact=True).click()
    dialog = page.get_by_role("dialog")
    expect(dialog.get_by_text("正在檢查這段期間的紀錄", exact=False)).to_have_count(0)
    if screenshot:
        record(page, "農民：匯出對話框", "04-export")
    dialog.get_by_role("radio", name=re.compile("Excel")).click()
    download(page, dialog.get_by_role("button", name="產出 Excel", exact=True), f"round3-{role}-log", b"PK")
    expect(dialog).not_to_be_visible()
    record(page, f"{role}：Excel 匯出成功")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    reset_demo_rows()  # 重跑冪等：先清掉上一次走查建的列
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context, page = login(browser, "farmer")
        page.on("pageerror", lambda e: errors.append(str(e)))
        expect(page.locator(".event-bar").first).to_be_visible()
        expect(page.locator(".nearby-alert").first).to_be_visible()
        page.get_by_role('button', name='農場設定', exact=True).click()
        page.get_by_label('縣市與鄉鎮市區', exact=True).fill('高雄市大社區')
        page.get_by_role('button', name='儲存所在地', exact=True).click()
        expect(page.get_by_role('dialog')).not_to_be_visible()
        record(page, '農民：所在地設定實際儲存成功')
        mobile_check(page, 'farmer')
        page.evaluate('window.scrollTo(0, 0)')
        record(page, "農民：複合圖在第一屏、四條色帶與區域警戒", "01-planning")
        page.get_by_role("button", name="新增作物規劃", exact=True).click()
        dialog = page.get_by_role("dialog")
        expect(dialog).to_be_visible()
        dialog.get_by_role("button", name="下一步：種植資料 →").click()
        dialog.get_by_placeholder("例如：溪畔一區、溫室A").fill(WALKTHROUGH_FIELD)
        dialog.locator('button[type="submit"]').click()
        expect(dialog).not_to_be_visible()
        expect(page.locator(".crop-chip").filter(has_text=WALKTHROUGH_FIELD).first).to_be_visible()
        record(page, "農民：新增作物規劃已儲存")
        # 新增驗收期先取消選取，截圖保持兩期示範作物可比較。
        page.locator(".crop-chip").filter(has_text=WALKTHROUGH_FIELD).first.click()
        # 第四輪：點規劃色塊改成就地開抽屜（不再跳日誌頁、也不再是 modal）。
        page.locator(".event-bar.care").first.click()
        expect(page.locator(".record-drawer")).to_be_visible()
        expect(page).to_have_url(re.compile(r"/farm$"))  # 關鍵：沒有離開這一頁
        record(page, "農民：點色塊就地記錄", "09-record-drawer")
        page.locator(".record-drawer .rd-close").click()
        expect(page.locator(".record-drawer")).to_have_count(0)
        # 規劃 vs 實際面板與帶文字的膠囊。
        expect(page.locator(".pva-row").first).to_be_visible()
        expect(page.locator(".log-chip").first).to_be_visible()
        page.locator(".pva").scroll_into_view_if_needed()
        record(page, "農民：規劃 vs 實際", "10-plan-vs-actual")
        checks.append("農民：四個入口都在本頁開抽屜、面板與膠囊就緒")
        print("✓ 農民：四個入口都在本頁開抽屜、面板與膠囊就緒", flush=True)
        page.locator(".nearby-alert").first.scroll_into_view_if_needed()
        record(page, "農民：實際作業與種植前區間", "02-plan-chart")
        download(page, page.get_by_role("button", name="匯出圖表"), "round3-farmer-chart", b"<svg")
        sidebar(page, "農事日誌")
        expect(page.locator(".flog-card").first).to_be_visible()
        record(page, "農民：農事日誌", "03-farm-log")
        log_action(page, "farmer")
        log_export(page, "farmer", True)
        sidebar(page, "精準模式")
        expect(page.get_by_text("補灌 C 區", exact=True)).to_be_visible()
        record(page, "農民：精準示範資料", "05-precision")
        sidebar(page, "附近病蟲害")
        expect(page.locator(".mapbox .ov")).to_have_count(0)
        expect(page.locator(".leaflet-interactive").first).to_be_visible()
        record(page, "農民：自己的鄉鎮地圖", "06-region-farmer")
        context.close()

        context, page = login(browser, "doctor")
        page.on("pageerror", lambda e: errors.append(str(e)))
        calls = Counter()
        page.on("request", lambda r: calls.update([urlparse(r.url).path]) if "/api/" in r.url else None)
        page.locator('a[href="/farmers/demo"]').click()
        expect(page.locator(".event-bar").first).to_be_visible()
        assert page.get_by_role("button", name="新增作物規劃", exact=True).count() == 0
        # 第四輪：點膠囊改成就地開唯讀抽屜。仍要留在該農友下，不能回農民路由被守衛彈走。
        page.locator(".log-chip").first.click()
        expect(page.locator(".record-drawer")).to_be_visible()
        expect(page).to_have_url(re.compile(r"/farmers/demo$"))
        record(page, "醫生：點膠囊就地看紀錄")
        page.locator(".record-drawer .rd-close").click()
        page.get_by_role("tab", name="日誌", exact=True).click()
        expect(page.locator(".flog-card").first).to_be_visible()
        page.locator(".switcher select").select_option("demo2")
        expect(page).to_have_url(re.compile(r"/farmers/demo2/log$"))
        expect(page.get_by_text("鄰田同期出現類似病斑。", exact=True)).to_be_visible()
        expect(page.get_by_text("試驗殺菌劑甲", exact=True)).to_have_count(0)
        page.locator(".switcher select").select_option("demo")
        expect(page.locator(".flog-card").first).to_be_visible()
        page.get_by_role("tab", name="複合圖", exact=True).click()
        page.locator(".event-bar.care").first.click()
        expect(page.locator(".record-drawer")).to_be_visible()
        expect(page).to_have_url(re.compile(r"/farmers/demo$"))
        record(page, "醫生：點色塊就地記錄")
        page.locator(".record-drawer .rd-close").click()
        page.get_by_role("tab", name="日誌", exact=True).click()
        log_action(page, "doctor")
        log_export(page, "doctor")
        # 第四輪：案件層級輪替橫幅的資料（同田區連續三次 FRAC 3）由 seed 產生，這裡只驗頁面接得上。
        sidebar(page, "農藥廠商")
        page.locator(".v-search input").fill("興農")
        expect(page.locator(".v-hits button").first).to_be_visible()
        page.locator(".v-hits button").first.click()
        expect(page.locator(".v-stat").first).to_be_visible()
        expect(page.locator(".tb tbody tr").first).to_be_visible()
        record(page, "醫生：農藥廠商查詢", "11-vendors")
        # 已撤銷的要列得出來——農民問「手上這罐為什麼不能用」靠的就是這一頁。
        page.locator(".v-filters .chip").filter(has_text="已撤銷").click()
        expect(page.locator(".tb tr.rev").first).to_be_visible()
        record(page, "醫生：已撤銷許可證仍列出")

        sidebar(page, "農民管理")
        page.get_by_role("button", name="新增農民", exact=True).click()
        expect(page.locator(".cf-tabs")).to_be_visible()
        expect(page.get_by_text("建立後會自動連結到您，農民", exact=False)).to_be_visible()
        record(page, "醫生：新增農民（兩種模式）", "12-create-farmer")
        page.get_by_role("button", name="取消", exact=True).click()
        page.locator('a[href="/farmers/demo"]').click()
        expect(page.locator(".event-bar").first).to_be_visible()

        page.get_by_role("tab", name="精準", exact=True).click()
        expect(page.get_by_text("補灌 C 區", exact=True)).to_be_visible()
        for label in ("轉成農事待辦", "已執行，記到日誌", "略過"):
            expect(page.get_by_role("button", name=label, exact=True)).to_have_count(0)
        record(page, "醫生：精準模式唯讀")
        mobile_check(page, "doctor")
        page.locator(".switcher select").select_option("demo2")
        expect(page.get_by_text("補灌 C 區", exact=True)).to_have_count(0)
        expect(page.locator(".precision-view, .precision").first).to_be_visible()
        assert calls['/api/v1/planning/catalog'] == calls['/api/v1/planning/me'] == calls['/api/v1/planning/links'] == 0, calls
        record(page, "醫生：切換農友未重抓 session，精準資料不殘留")
        sidebar(page, "區域地圖")
        expect(page.locator(".mapbox .ov")).to_have_count(0)
        record(page, "醫生：區域地圖", "07-region-doctor")
        context.close()

        context, page = login(browser, "agency")
        page.on("pageerror", lambda e: errors.append(str(e)))
        expect(page.locator(".mapbox .ov")).to_have_count(0)
        assert page.get_by_role("button", name="新增作物規劃").count() == 0
        assert page.get_by_role("button", name="記一筆").count() == 0
        page.locator(".leaflet-interactive").first.click(force=True)
        expect(page.get_by_text("已選鄉鎮", exact=True)).to_be_visible()
        download(page, page.get_by_role("button", name="匯出區域報表"), "round3-agency-region", b"PK")
        page.goto(f"{BASE}/farmers/demo/log")
        expect(page).to_have_url(re.compile("/region$"))
        expect(page.locator(".mapbox .ov")).to_have_count(0)
        mobile_check(page, "agency")
        record(page, "機關：點鄉鎮／匯出成功；無農場新增與記錄權限", "08-region-agency")
        context.close()
        browser.close()
    assert not errors, errors
    frames[0].save(OUT / "ux-round3-walkthrough.gif", save_all=True, append_images=frames[1:], duration=1600, loop=0)
    (OUT / "ux-round3-results.json").write_text(json.dumps({"checks": checks, "page_errors": errors}, ensure_ascii=False, indent=2))
    print(f"驗收通過：{len(checks)} 個步驟；{OUT}")


if __name__ == "__main__":
    main()
