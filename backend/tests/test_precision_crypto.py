"""data_sources.config 加密／遮罩。"""
import pytest

from app.precision import crypto
from app.precision.crypto import (MASK, NEEDS_REENTRY, decrypt, encrypt, encrypt_config, is_ciphertext,
                                  mask_config)


@pytest.fixture
def rotated_key(monkeypatch):
    """換一把 SECRET_KEY，模擬金鑰輪替後既有密文解不回來的狀態。"""
    def rotate():
        crypto._fernet.cache_clear()
        monkeypatch.setattr(crypto.settings, "secret_key",
                            type(crypto.settings.secret_key)("a-completely-different-key"))
        crypto._fernet.cache_clear()
    yield rotate
    crypto._fernet.cache_clear()


def test_round_trip_and_masking():
    token = encrypt("shh-秘密金鑰")
    assert token != "shh-秘密金鑰" and decrypt(token) == "shh-秘密金鑰"
    assert decrypt("not-a-token-at-all") is None  # 解不出來就說不知道，不拋例外炸掉呼叫端


def test_encrypt_config_only_touches_known_secret_fields_and_is_idempotent():
    config = {"endpoint": "https://example.invalid", "api_key": "raw-key-123", "topic": "farm/soil"}
    encrypted = encrypt_config(config)
    assert encrypted["endpoint"] == config["endpoint"] and encrypted["topic"] == config["topic"]
    assert encrypted["api_key"] != "raw-key-123" and decrypt(encrypted["api_key"]) == "raw-key-123"

    # 再存一次（例如 PATCH 沒有改密碼、原樣送回已加密的值）：不會加密兩層變得解不回來。
    again = encrypt_config(encrypted)
    assert again["api_key"] == encrypted["api_key"] and decrypt(again["api_key"]) == "raw-key-123"


def test_mask_config_hides_values_but_shows_whether_something_is_set():
    encrypted = encrypt_config({"api_key": "raw-key-123", "endpoint": "https://example.invalid", "password": ""})
    masked = mask_config(encrypted)
    assert masked["api_key"] == MASK and masked["endpoint"] == "https://example.invalid"
    assert masked["password"] == ""  # 沒填的密碼不假裝有值
    assert "raw-key-123" not in str(masked)


def test_ciphertext_is_recognised_by_structure_not_by_whether_it_decrypts(rotated_key):
    token = encrypt("raw-key-123")
    assert is_ciphertext(token)
    assert not is_ciphertext("raw-key-123")
    assert not is_ciphertext("")
    rotated_key()
    # 換了金鑰之後解不開，但它仍然是密文——這是不把它當明文重新加密的關鍵。
    assert decrypt(token) is None
    assert is_ciphertext(token)


def test_rotated_key_shows_needs_reentry_instead_of_pretending_it_is_set(rotated_key):
    encrypted = encrypt_config({"api_key": "raw-key-123", "endpoint": "https://example.invalid"})
    assert mask_config(encrypted)["api_key"] == MASK
    rotated_key()
    masked = mask_config(encrypted)
    assert masked["api_key"] == NEEDS_REENTRY  # 不是 MASK：顯示「已設定」會讓人以為連線還能用
    assert masked["endpoint"] == "https://example.invalid"


def test_rotated_key_secret_is_not_re_encrypted_into_a_password_made_of_old_ciphertext(rotated_key):
    encrypted = encrypt_config({"api_key": "raw-key-123"})
    old_token = encrypted["api_key"]
    rotated_key()
    # 再存一次（例如使用者只改了 label）：舊密文原樣保留。
    # 若把它當成明文再加密一層，解出來會變成「一串舊密文」並被真的當密碼送出去。
    again = encrypt_config(encrypted)
    assert again["api_key"] == old_token
    assert decrypt(again["api_key"]) is None
