"""植物醫生用藥決策平台 API。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api.v1 import api_router
from app.core.config import settings
from app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

if settings.secret_key_is_default:
    # 啟動就印一行，因為這件事在畫面上看不出來：連線設定頁的密碼照樣存得進去、照樣顯示「已設定」，
    # 只是那些密文用的是任何看得到原始碼的人都能重算的金鑰。正式環境請設 SECRET_KEY 環境變數。
    logging.getLogger(__name__).warning(
        "SECRET_KEY 仍是開發預設值：data_sources 儲存的金鑰／密碼等同未加密，正式環境請務必設定"
    )

app = FastAPI(
    title="植物醫生用藥決策平台",
    description=(
        "合併植物保護資訊系統、食藥署農藥殘留容許量、高改場作物安全用藥資訊，"
        "一頁看完三源比對結果並標出不一致處。"
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["ops"])
def health() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}
