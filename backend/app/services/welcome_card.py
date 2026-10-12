"""代建帳號的歡迎小卡 PDF：沿用診斷書那條 HTML → PDF 的路（同字型、同併發上限、同樣不連外）。"""
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.services.consult_report import TEMPLATE_DIR, font_data, html_to_pdf

LOGIN_URL = "https://agri-doctor.example/login"


def expires_text(expires_at: str | None) -> str:
    """給農民看的到期時間，用台北時間。看不懂就照實說，不要印一個假的期限。"""
    if not expires_at:
        return "（期限未知，請向建立帳號的植物醫生確認）"
    try:
        moment = datetime.fromisoformat(expires_at)
    except ValueError:
        return "（期限未知，請向建立帳號的植物醫生確認）"
    return moment.astimezone(ZoneInfo("Asia/Taipei")).strftime("%Y/%m/%d %H:%M")


def render_welcome_card(*, farmer, username: str, password: str, doctor_name: str,
                        expires_at: str | None, login_url: str = LOGIN_URL) -> bytes:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(['html']),
                      undefined=StrictUndefined)
    html = env.get_template('welcome_card.html').render(
        farmer=farmer, username=username, password=password, doctor_name=doctor_name,
        login_url=login_url, expires_text=expires_text(expires_at), font_url=font_data())
    return html_to_pdf(html, label='帳號小卡')
