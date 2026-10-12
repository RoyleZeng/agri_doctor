"""代建帳號的帳號建議、一次性密碼與過期判斷；不連資料庫或外網。"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.farmer_invite import (
    OTP_ALPHABET, OTP_HOURS, OTP_LENGTH,
    invite_state, one_time_password, otp_expired, otp_expiry, suggest_username,
)

NOW = datetime(2026, 9, 23, 10, 0, tzinfo=timezone.utc)


def test_username_uses_an_english_name_when_there_is_one():
    assert suggest_username("Chen Ming", set(), NOW) == "chenming"
    assert suggest_username("O'Brien-Wu", set(), NOW) == "obrienwu"


def test_username_falls_back_to_a_typeable_code_for_chinese_names():
    """不自己挑一套羅馬拼音：台灣漢語／通用／威妥瑪三套並行，挑哪一套都是替人決定他的名字怎麼拼。"""
    name = suggest_username("陳示範", set(), NOW)
    assert name.isascii() and name.isalnum()
    assert name.startswith("f2609")


def test_username_never_collides():
    taken = {"chenming", "chenming01", "chenming02"}
    assert suggest_username("Chen Ming", taken, NOW) == "chenming03"
    # 兩個中文名字會撞到同一個字根，一樣要讓開。
    first = suggest_username("陳示範", set(), NOW)
    second = suggest_username("林示範", {first}, NOW)
    assert second != first


def test_username_avoids_stems_too_short_to_type():
    # 一個字母的英文名不成帳號，補流水號。
    assert suggest_username("A", set(), NOW) == "a01"


def test_one_time_password_is_long_enough_and_unambiguous():
    password = one_time_password()
    # 後端 Login 要求至少 10 碼。
    assert len(password) == OTP_LENGTH >= 10
    assert set(password) <= set(OTP_ALPHABET)
    # 這組密碼會被印在紙卡上、或用電話唸：0/O、1/l/I 不可以出現。
    assert not (set(password) & set("01OIl"))
    # 不可以每次都一樣。
    assert len({one_time_password() for _ in range(20)}) > 1


def test_expiry_is_72_hours_and_boundaries_are_strict():
    expires = otp_expiry(NOW)
    assert datetime.fromisoformat(expires) == NOW + timedelta(hours=OTP_HOURS)
    assert otp_expired(expires, NOW) is False
    assert otp_expired(expires, NOW + timedelta(hours=OTP_HOURS - 1)) is False
    # 剛好到期就算過期，不多給。
    assert otp_expired(expires, NOW + timedelta(hours=OTP_HOURS)) is True
    assert otp_expired(expires, NOW + timedelta(hours=OTP_HOURS + 1)) is True


@pytest.mark.parametrize("value", [None, "", "not-a-date", "2026-13-45T00:00:00"])
def test_unreadable_expiry_counts_as_expired(value):
    """看不懂一律當成過期：寧可多按一次重發，也不要讓共用密碼永遠有效。"""
    assert otp_expired(value, NOW) is True


def test_naive_expiry_is_treated_as_utc_not_local():
    """沒有時區的字串當成 UTC；當成本地時間會依伺服器所在地差 8 小時。"""
    naive = (NOW + timedelta(hours=1)).replace(tzinfo=None).isoformat()
    assert otp_expired(naive, NOW) is False
    assert otp_expired(naive, NOW + timedelta(hours=2)) is True


def account(**changes):
    base = dict(must_change_password=True, otp_expires_at=otp_expiry(NOW))
    return SimpleNamespace(**(base | changes))


def test_invite_state_tracks_the_three_situations():
    assert invite_state(account(), NOW)["state"] == "pending"
    assert invite_state(account(), NOW + timedelta(hours=OTP_HOURS))["state"] == "expired"
    # 改過密碼之後這個帳號只屬於農民，不該再顯示任何邀請狀態。
    done = invite_state(account(must_change_password=False), NOW)
    assert done["state"] == "active" and done["expires_at"] is None
