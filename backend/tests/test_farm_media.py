"""照片處理純函式與清理排程的離線測試。照片 fixture 在 tests/fixtures/media（產生方式見 make_fixtures.py）。"""
import os
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.farm_log import FarmLogPhoto, FarmWorkLog
from app.models.planning import FarmAccount
from app.services.farm_media import (
    KEY_PATTERN, MediaError, detect_format, exif_datetime, exif_gps, gps_decimal, photo_keys, process_image,
    purge_media, resolve_key,
)


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kw):
    return "JSON"


MEDIA = Path(__file__).parent / "fixtures" / "media"
NOW = datetime(2030, 3, 10, 2, 0, tzinfo=timezone.utc)
ALL = {"jpeg", "png", "heic"}


def fixture(name: str) -> bytes:
    return (MEDIA / name).read_bytes()


def process(data: bytes, **changes):
    return process_image(data, **(dict(now=NOW, allowed=ALL, max_pixels=50_000_000, thumb_px=320) | changes))


def jpeg_with_date(taken: str) -> bytes:
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = taken
    buffer = BytesIO()
    Image.new("RGB", (8, 8)).save(buffer, format="JPEG", exif=exif.tobytes())
    return buffer.getvalue()


@pytest.mark.parametrize("name,expected", [
    ("exif_gps_rotated.jpg", "jpeg"), ("no_exif.png", "png"), ("phone.heic", "heic"),
    ("not_an_image.jpg", None), ("animated.gif", None),
])
def test_detect_format_uses_file_content_not_extension(name, expected):
    assert detect_format(fixture(name)[:256]) == expected


def test_avif_is_not_mistaken_for_heic():
    # AVIF 也是 HEIF 容器，而且常帶 mif1 相容品牌；主品牌是 avif 就不是我們能轉的 HEIC。
    avif = (24).to_bytes(4, "big") + b"ftypavif" + b"\x00\x00\x00\x00" + b"mif1miaf"
    assert detect_format(avif) is None
    heic_compatible = (24).to_bytes(4, "big") + b"ftypzzzz" + b"\x00\x00\x00\x00" + b"mif1heic"
    assert detect_format(heic_compatible) == "heic"


@pytest.mark.parametrize("value,offset,expected", [
    ("2030:03:09 06:15:30", None, datetime(2030, 3, 8, 22, 15, 30, tzinfo=timezone.utc)),  # 沒有時區當作臺灣時間
    (b"2030:03:09 06:15:30\x00", None, datetime(2030, 3, 8, 22, 15, 30, tzinfo=timezone.utc)),
    ("2030:03:09 06:15:30", "+09:00", datetime(2030, 3, 8, 21, 15, 30, tzinfo=timezone.utc)),
    ("2030:03:09 06:15:30", "-03:00", datetime(2030, 3, 9, 9, 15, 30, tzinfo=timezone.utc)),
    ("2030:03:09 06:15:30", "garbage", datetime(2030, 3, 8, 22, 15, 30, tzinfo=timezone.utc)),
    ("0000:00:00 00:00:00", None, None),
    ("2030:02:30 06:15:30", None, None),
    ("", None, None),
    (None, None, None),
])
def test_exif_datetime(value, offset, expected):
    assert exif_datetime(value, offset) == expected


def test_gps_parsing_signs_refs_and_bad_values():
    assert gps_decimal(((22, 1), (45, 1), (3050, 100)), "N") == pytest.approx(22.7584722)
    assert gps_decimal((23.0, 30.0, 0.0), b"S") == -23.5
    assert gps_decimal((46.0, 15.0, 0.0), "W") == -46.25
    assert gps_decimal(((22, 0), (1, 1), (1, 1)), "N") is None      # 分母為 0
    assert gps_decimal((22.0, 75.0, 0.0), "N") is None               # 分鐘超過 60
    assert gps_decimal(None, "N") is None
    assert exif_gps({1: "N", 2: (0.0, 0.0, 0.0), 3: "E", 4: (0.0, 0.0, 0.0)}) == (None, None)
    assert exif_gps({1: "N", 2: (95.0, 0.0, 0.0), 3: "E", 4: (120.0, 0.0, 0.0)}) == (None, None)
    assert exif_gps({1: "N", 2: (22.0, 0.0, 0.0)}) == (None, None)   # 只有緯度


def test_jpeg_original_is_untouched_and_exif_orientation_and_gps_are_read():
    data = fixture("exif_gps_rotated.jpg")
    result = process(data)
    # EXIF 只讀不改：原檔一個位元組都不能變。
    assert result.original == data and result.stored_ext == "jpg" and result.source_format == "jpeg"
    # 方向 6 要轉 90 度：原圖 64x40，轉正後是 40x64。
    assert (result.width, result.height) == (40, 64)
    assert result.taken_at_source == "exif" and result.taken_at == datetime(2030, 3, 8, 22, 15, 30, tzinfo=timezone.utc)
    assert result.lat == pytest.approx(22.7584722) and result.lng == pytest.approx(120.3695)
    with Image.open(BytesIO(result.thumb)) as thumb:
        assert thumb.format == "WEBP" and thumb.size == (40, 64)
        # 原圖左紅右藍；轉正後紅在上、藍在下。縮圖方向錯的話底部會是紅的。
        red, green, blue = thumb.convert("RGB").getpixel((20, 60))
        assert blue > red
        # 縮圖不帶 EXIF，GPS 只留在要過權限檢查的原檔。
        assert not thumb.getexif() and "exif" not in thumb.info


def test_heic_is_converted_to_jpeg_keeping_exif():
    result = process(fixture("phone.heic"))
    assert result.source_format == "heic" and result.stored_ext == "jpg"
    assert detect_format(result.original[:256]) == "jpeg"
    assert result.taken_at == datetime(2030, 3, 8, 9, 40, tzinfo=timezone.utc)
    assert (result.lat, result.lng) == (-23.5, -46.25) and result.taken_at_source == "exif"
    # 轉出來的 JPEG 自己也要帶著原本的 EXIF，匯出時才印得出拍攝時間與地點。
    converted = process(result.original)
    assert (converted.taken_at, converted.lat, converted.lng) == (result.taken_at, result.lat, result.lng)


def test_png_without_exif_falls_back_to_upload_time():
    data = fixture("no_exif.png")
    result = process(data)
    assert result.original == data and result.stored_ext == "png"
    assert result.taken_at == NOW and result.taken_at_source == "upload" and result.lat is None


@pytest.mark.parametrize("data", [fixture("bad_date.jpg"), jpeg_with_date("2099:01:01 00:00:00"), jpeg_with_date("1999:12:31 23:00:00")])
def test_implausible_exif_date_falls_back_to_upload_time(data):
    result = process(data)
    assert result.taken_at == NOW and result.taken_at_source == "upload"


@pytest.mark.parametrize("taken,source", [
    ("2030:03:10 10:05:00", "exif"),    # NOW 是臺灣 10:00；手機時鐘快 5 分鐘，照樣採用
    ("2030:03:11 09:59:00", "exif"),    # 快將近 24 小時仍採用
    ("2030:03:11 10:00:00", "exif"),    # 剛好 24 小時
    ("2030:03:11 10:01:00", "upload"),  # 晚於上傳時間超過 24 小時才退回
])
def test_camera_clock_ahead_of_upload_time_is_tolerated_up_to_24_hours(taken, source):
    assert process(jpeg_with_date(taken)).taken_at_source == source


def test_thumbnail_is_bounded():
    result = process(fixture("large.jpg"), thumb_px=320)
    with Image.open(BytesIO(result.thumb)) as thumb:
        assert thumb.size == (320, 240)
    assert (result.width, result.height) == (640, 480)


@pytest.mark.parametrize("data,changes,status", [
    (fixture("not_an_image.jpg"), {}, 415),
    (fixture("animated.gif"), {}, 415),
    (fixture("phone.heic"), {"allowed": {"jpeg", "png"}}, 415),
    (fixture("large.jpg")[:300], {}, 422),              # 截斷的 JPEG
    (fixture("large.jpg"), {"max_pixels": 1000}, 413),  # 解壓縮炸彈防線
])
def test_rejected_images(data, changes, status):
    with pytest.raises(MediaError) as error:
        process(data, **changes)
    assert error.value.status == status


def test_storage_keys_and_path_traversal(tmp_path):
    storage_key, thumb_key = photo_keys("farmer-1", NOW, "jpg")
    assert storage_key.startswith("farmer-1/2030/03/") and storage_key.endswith(".jpg")
    assert thumb_key == storage_key[:-4] + ".thumb.webp"
    assert KEY_PATTERN.match(storage_key) and KEY_PATTERN.match(thumb_key)
    assert resolve_key(tmp_path, storage_key) == (tmp_path / storage_key).resolve()
    for bad in ("../escape.jpg", "/etc/passwd", "a/../../escape.jpg"):
        with pytest.raises(MediaError):
            resolve_key(tmp_path, bad)
    with pytest.raises(MediaError):
        photo_keys("../other", NOW, "jpg")


def test_purge_removes_only_expired_deleted_and_stale_orphans_and_keeps_rows(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    stem = "farmer/2030/03/"
    names = {
        "active": stem + "a" * 32, "recent_deleted": stem + "b" * 32, "old_deleted": stem + "c" * 32,
        "new_orphan": stem + "d" * 32, "old_orphan": stem + "e" * 32,
    }
    files = {}
    for label, key in names.items():
        for suffix in (".jpg", ".thumb.webp"):
            path = tmp_path / (key + suffix)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            files[label + suffix] = path
    stale_part = tmp_path / (stem + "f" * 32 + ".jpg.part")
    stale_part.write_bytes(b"x")
    foreign = tmp_path / "farmer" / "keep-me.txt"
    foreign.write_bytes(b"not ours")
    old = (NOW - timedelta(days=2)).timestamp()
    for path in [*files.values(), stale_part, foreign]:
        os.utime(path, (old, old))
    fresh = (NOW - timedelta(hours=1)).timestamp()
    for suffix in (".jpg", ".thumb.webp"):
        os.utime(files["new_orphan" + suffix], (fresh, fresh))

    with factory() as db:
        db.add(FarmAccount(id="farmer", username="farmer", name="farmer", role="farmer", password_hash="unused"))
        db.flush()
        db.add(FarmWorkLog(id="log", farmer_id="farmer", created_by="farmer", done_on=NOW.date(), kind="irrigate",
                           field_text="試驗田", title="灌溉", compliance={}))
        db.flush()
        for label, deleted_at in [("active", None), ("recent_deleted", NOW - timedelta(days=1)),
                                  ("old_deleted", NOW - timedelta(days=31))]:
            key = names[label]
            db.add(FarmLogPhoto(id=label, log_id="log", storage_key=key + ".jpg", thumb_key=key + ".thumb.webp", bytes=1,
                                taken_at=NOW, deleted_at=deleted_at, created_at=NOW))
        db.commit()

        result = purge_media(db, root=tmp_path, now=NOW, trash_period=timedelta(days=30), orphan_grace=timedelta(hours=24))
        assert db.get(FarmLogPhoto, "old_deleted") is not None  # 照片列本身保留

    removed = {label for label, path in files.items() if not path.exists()}
    assert removed == {"old_deleted.jpg", "old_deleted.thumb.webp", "old_orphan.jpg", "old_orphan.thumb.webp"}
    assert not stale_part.exists() and foreign.exists()
    assert result == {"removed": 5}
    assert purge_media(None, root=tmp_path / "missing", now=NOW, trash_period=timedelta(days=30),
                       orphan_grace=timedelta(hours=24)) == {"removed": 0}
    engine.dispose()
