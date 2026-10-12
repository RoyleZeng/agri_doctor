"""`data_sources.config` 裡金鑰／密碼的加密與遮罩。

用 `settings.secret_key` 派生 Fernet 金鑰，不再另外要求使用者管理一把新金鑰——這把金鑰代表的信任邊界
本來就跟資料庫一致（誰能跑這支程式、誰就能解密），真正要擋的是「設定畫面把明文印出來」跟
「有人只拿到 DB dump 卻沒有原始碼」這兩種情況，不是防看得到金鑰設定值的人。
"""
from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

# config 裡符合這些鍵名的值視為機密：寫入前加密，讀出時遮罩，絕不在 API 回應裡出現明文。
SECRET_KEYS = ("api_key", "token", "password", "secret", "topic_password")
MASK = "已設定（不顯示明文）"
# 換過 SECRET_KEY 之後解不回來的舊密文：畫面要說「需重新輸入」，不能顯示成「已設定」——
# 顯示成已設定會讓人以為連線還能用，實際上送出去的密碼取不到，等到連線失敗才發現。
NEEDS_REENTRY = "需重新輸入"


def is_ciphertext(value: str) -> bool:
    """這個值是不是這支程式加密出來的密文（不管解不解得開）。

    用 Fernet token 的結構判斷，不用「解不開就當成明文」——換過金鑰之後舊密文一樣解不開，
    若把它當成明文就會在下一次存檔時再加密一層，把原本只是「解不回來」的欄位變成
    「內容是一串舊密文的密碼」，然後真的送去連線。結構：版本位元組 0x80 ＋ 8 位元組時間戳
    ＋ 16 位元組 IV ＋ 密文 ＋ 32 位元組 HMAC，所以至少 57 位元組。
    """
    try:
        raw = base64.urlsafe_b64decode(value.encode())
    except (ValueError, TypeError):
        return False
    return len(raw) >= 57 and raw[0] == 0x80


@lru_cache
def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.get_secret_value().encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    """解不出來就回 None（金鑰換過、或資料本來就不是密文），呼叫端自行決定要不要當作沒設定。"""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return None


def encrypt_config(config: dict) -> dict:
    """存檔前呼叫：把機密欄位換成密文；已經是密文（例如 PATCH 沒改密碼、或換金鑰後解不回的舊值）
    原樣保留，不重複加密。"""
    out = dict(config)
    for key in SECRET_KEYS:
        value = out.get(key)
        if isinstance(value, str) and value and not is_ciphertext(value):
            out[key] = encrypt(value)
    return out


def mask_config(config: dict) -> dict:
    """回應前呼叫：機密欄位一律回固定字串，只讓畫面看得出「有沒有填」「還能不能用」，
    看不出內容或長度。解不回來的（換過金鑰）回 `NEEDS_REENTRY`，不是回 `MASK` 也不是報錯。"""
    out = {}
    for key, value in config.items():
        if key not in SECRET_KEYS or not value:
            out[key] = value
        elif is_ciphertext(value) and decrypt(value) is None:
            out[key] = NEEDS_REENTRY
        else:
            out[key] = MASK
    return out
