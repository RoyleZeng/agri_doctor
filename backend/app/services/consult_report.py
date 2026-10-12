"""診斷書只接收快照字典，刻意不持有 DB session，避免重印時誤查現行來源。"""
from base64 import b64encode
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
TEMPLATE_DIR=ROOT/'templates'
FONT_PATH=ROOT/'assets/fonts/SourceHanSerifTC-Regular.otf'
_render_slots=BoundedSemaphore(2)
PAGE_FONT_FAMILY='AgriPageNumber'


def page_font_installed():
    from app.core.config import settings
    return Path(settings.pdf_page_font_file).is_file()


def page_footer(font_installed):
    """頁尾只能用系統已安裝的字型。有頁碼字型印「第 1 頁／共 3 頁」；沒有就退回數字「1 / 3」，絕不省略頁碼——
    交給政府的紀錄少一頁沒人看得出來，頁碼是防這件事的。"""
    style='width:100%;margin:0 13mm;text-align:right;font-size:8pt;color:#555;'
    if font_installed:
        return (f'<div style="{style}font-family:{PAGE_FONT_FAMILY};">第 <span class="pageNumber"></span> 頁／共 '
                '<span class="totalPages"></span> 頁</div>')
    return f'<div style="{style}"><span class="pageNumber"></span> / <span class="totalPages"></span></div>'


def roc_date(value):
    if not value: return '未填'
    if isinstance(value,str): value=datetime.fromisoformat(value) if 'T' in value or ' ' in value else date.fromisoformat(value)
    if isinstance(value,datetime):
        value=(value if value.tzinfo else value.replace(tzinfo=timezone.utc)).astimezone(ZoneInfo('Asia/Taipei'))
    return f'{value.year-1911} 年 {value.month} 月 {value.day} 日'


@lru_cache(maxsize=1)
def font_data():
    # 缺少隨程式附帶的字型就明確失敗，不靜默改用主機字型。
    return 'data:font/otf;base64,'+b64encode(FONT_PATH.read_bytes()).decode('ascii')


def render_report_html(case):
    env=Environment(loader=FileSystemLoader(TEMPLATE_DIR),autoescape=select_autoescape(['html']),undefined=StrictUndefined)
    env.filters['roc']=roc_date
    return env.get_template('consult_report.html').render(case=case,font_url=font_data(),
        examinations={'on_site':'現場勘查','sample':'樣本送診','photo':'照片研判','other':'其他'},
        bases={'visual':'目視病徵','microscopy':'鏡檢','culture':'分離培養','lab':'專業機構鑑定','other':'其他'},
        source_labels={'ppm':'植物保護資訊系統','fda':'衛福部食藥署','kdais':'高雄區農業改良場'},
        levels={'block':'不可開立','warning':'需確認','ok':'通過'},
        fields={'dilution':'稀釋倍數','dose':'每公頃量','frequency':'施用頻率','advice':'醫師囑咐'})


def html_to_pdf(html, *, font_family='ReportSerif', label='診斷書', page_numbers=False):
    """HTML 轉 A4 PDF。診斷書與農事紀錄表共用：同一個併發上限，同樣不允許連外、字型沒載入就失敗。"""
    with _render_slots, sync_playwright() as playwright:
        browser=playwright.chromium.launch()
        try:
            context=browser.new_context(java_script_enabled=False)
            # 報表無外部依賴；即使未來版型不小心引入 URL，也不允許 PDF 引擎連外。
            context.route('**/*',lambda route:route.abort())
            page=context.new_page()
            page.set_content(html,wait_until='load',timeout=60000)
            page.evaluate('document.fonts.ready')
            if not page.evaluate(f'document.fonts.check(\'12px "{font_family}"\')'):
                raise RuntimeError(f'{label}內嵌字型未載入')
            options=dict(format='A4',print_background=True,prefer_css_page_size=True)
            if page_numbers:
                options.update(display_header_footer=True,header_template='<span></span>',
                               footer_template=page_footer(page_font_installed()))
            return page.pdf(**options)
        finally:
            browser.close()


def render_report_pdf(case):
    # 診斷書是法定文件（植物診療師法 §7），比農事紀錄表更需要「共 N 頁」；同一套字型與退回規則。
    return html_to_pdf(render_report_html(case), page_numbers=True)
