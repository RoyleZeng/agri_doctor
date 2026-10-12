from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

# 開發預設值。單獨命名是為了讓「有沒有換掉」可以被程式檢查（見 Settings.secret_key_is_default
# 與 app/main.py 的啟動警告），而不是只寫在文件裡靠人記得。
DEV_SECRET_KEY = "dev-only-change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://agri:agri@localhost:5432/agri_doctor"
    raw_data_dir: Path = Path("./data/raw")
    planning_cookie_secure: bool = False  # Set true behind HTTPS in production.
    # data_sources.config 裡的金鑰／密碼加密存放的衍生金鑰來源；開發用預設值，正式環境務必在 .env 覆寫，
    # 否則任何看得到程式碼的人都能還原加密內容——加密防的是「畫面印出明文」與「有人只看到 DB dump」。
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    cwa_api_key: SecretStr = SecretStr("")
    moa_api_key: SecretStr = SecretStr("")
    update_email_enabled: bool = False
    update_email_to: str = ""
    update_email_from: str = ""
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")

    # 農事日誌照片。只能經由受權限保護的 /planning/media 端點讀取，不可掛成 static。
    media_root: Path = Path("./data/media")
    media_max_bytes: int = 10 * 1024 * 1024
    media_allowed_formats: list[str] = ["jpeg", "png", "heic"]
    # 約 5000 萬像素；手機原圖在這之內，擋的是刻意做出來的解壓縮炸彈。
    media_max_pixels: int = 50_000_000
    media_thumb_px: int = 320
    media_max_photos_per_log: int = 20
    # 軟刪的照片保留多久才清實體檔；期間內日誌卡可以「還原」。紀錄可能要交補助查核，留一段手滑可以救回的時間。
    media_trash_days: int = 30
    # 匯出農事紀錄表：照片全部內嵌進 PDF，張數與期間都要有上限，避免一次產檔吃光記憶體（Lambda 尤其）。
    media_export_max_photos: int = 200
    farm_log_export_max_days: int = 400
    # PDF 頁碼字型：Chromium 頁尾載入不了內嵌字型，映像檔另裝這個子集字型（見 docs/deployment.md）。
    # 找不到時頁尾退回只印「1 / 3」，不會整個省略頁碼。
    pdf_page_font_file: Path = Path("/usr/local/share/fonts/agri/AgriPageNumber.otf")

    # 藥毒所已改制為 ACRI，舊網域 otserv2.tactri.gov.tw 會 302 到這裡。
    ppm_base_url: str = "https://otserv2.acri.gov.tw/PPM"
    fda_list_url: str = "https://consumer.fda.gov.tw/Law/PesticideList.aspx?nodeID=520"
    kdais_list_url: str = "https://www.kdais.gov.tw/ws.php?id=7665"

    # 對政府站台友善：每秒最多一次請求上下。
    crawl_rate_limit: float = 1.5
    crawl_timeout: float = 60.0
    user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 "
        "AgriDoctorBot/0.1 (plant-clinic internal tool)"
    )

    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @property
    def secret_key_is_default(self) -> bool:
        """還在用開發預設金鑰。正式環境等於沒加密——`data_sources` 存的密碼任何拿得到程式碼的人都能解。"""
        return self.secret_key.get_secret_value() == DEV_SECRET_KEY


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
