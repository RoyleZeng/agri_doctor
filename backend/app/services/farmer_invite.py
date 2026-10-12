"""代建農民帳號：帳號建議、一次性密碼與有效期限。純函式，離線可測。

為什麼這幾條要獨立測：一次性密碼是「植物醫生知道、農民也知道」的共用秘密，
只要有一段時間沒被換掉，那個帳號就等於兩個人的。所以
**過期判斷寧可嚴格**（看不懂的期限一律當成已過期），
而且首次登入一定要換掉（`must_change_password`）。
"""
from datetime import datetime, timedelta, timezone
import re
import secrets
import unicodedata

#: 一次性密碼有效時數。超過就重發，不自動延長。
OTP_HOURS = 72
#: 後端 `Login` 要求密碼至少 10 碼，OTP 不能比這短。
OTP_LENGTH = 12
#: 去掉看起來像的字元（0/O、1/l/I）：這組密碼會被印在紙卡上唸給農民聽。
OTP_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

_ASCII = re.compile(r"[^a-z0-9]+")


def _ascii_stem(name: str) -> str:
    """姓名裡取得出來的英數字；中文名取不到就回空字串。

    刻意不做中文轉拼音：專案沒有拼音套件，而台灣的羅馬拼音有漢語／通用／威妥瑪三套並行，
    自己挑一套等於替使用者決定他的名字怎麼拼。取不到就改用日期序號，帳號反正可以改。
    """
    folded = unicodedata.normalize("NFKD", name or "")
    stem = _ASCII.sub("", folded.encode("ascii", "ignore").decode().lower())
    return stem[:16]


def suggest_username(name: str, taken: set[str], today: datetime) -> str:
    """建議一個沒被用過的帳號。

    有英文名就用英文名，沒有就用 `f` ＋年月（民國無關，用西元後兩碼）＋流水號——
    農民要打得出來、唸得出來，比「拼得像不像他的名字」重要。
    """
    stem = _ascii_stem(name) or f"f{today.strftime('%y%m')}"
    if stem not in taken and len(stem) >= 3:
        return stem
    for serial in range(1, 1000):
        candidate = f"{stem}{serial:02d}"
        if candidate not in taken:
            return candidate
    # 同一個字根用掉一千個的機率極低；真的發生就加隨機碼，不要回一個已經被用掉的帳號。
    return f"{stem}{secrets.token_hex(3)}"


def one_time_password() -> str:
    """一次性密碼。用不易看錯的字集，因為它會被印在紙卡上、或用電話唸。"""
    return "".join(secrets.choice(OTP_ALPHABET) for _ in range(OTP_LENGTH))


def otp_expiry(now: datetime) -> str:
    return (now + timedelta(hours=OTP_HOURS)).astimezone(timezone.utc).isoformat()


def otp_expired(expires_at: str | None, now: datetime) -> bool:
    """期限已過（或看不懂）就是過期。

    看不懂一律當成過期——寧可讓植物醫生多按一次「重新產生」，
    也不要因為一個壞掉的字串讓共用密碼永遠有效。
    """
    if not expires_at:
        return True
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc) >= deadline


def invite_state(account, now: datetime) -> dict:
    """農民管理卡片要顯示的邀請狀態。

    `pending` 還沒改密碼且沒過期、`expired` 還沒改密碼但過期了、
    `active` 已經改過密碼（帳號從此只屬於農民）。
    """
    if not account.must_change_password:
        return dict(state="active", expires_at=None)
    expired = otp_expired(account.otp_expires_at, now)
    return dict(state="expired" if expired else "pending", expires_at=account.otp_expires_at)
