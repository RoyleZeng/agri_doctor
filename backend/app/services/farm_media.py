"""農事日誌照片：格式辨識、EXIF 時間與 GPS、HEIC 轉 JPEG、縮圖、存檔路徑與清理。

上半部是純函式（只吃 bytes、回傳結果），用 tests/fixtures/media 的照片離線測。
下半部碰檔案系統與資料庫（存檔、清理排程），跟純函式分開，方便各自測。

幾條刻意的規則：
* 格式看檔案內容的魔術位元組，不信瀏覽器送來的 Content-Type 或副檔名。
* EXIF 只讀不改：JPEG／PNG 原檔一個位元組都不動；HEIC 瀏覽器多半顯示不了，轉成 JPEG，
  並把原本的 EXIF 帶過去，拍攝時間與 GPS 不會在轉檔時遺失。
* 縮圖不帶 EXIF。縮圖給列表顯示，GPS 只留在原檔，看原檔一樣要過權限檢查。
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select

pillow_heif.register_heif_opener()

TAIPEI = ZoneInfo("Asia/Taipei")
EXIF_IFD, GPS_IFD = 0x8769, 0x8825
DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL = 0x9003, 0x9011
GPS_LAT_REF, GPS_LAT, GPS_LNG_REF, GPS_LNG = 1, 2, 3, 4

# 相機時鐘常快幾分鐘；EXIF 時間晚於上傳時間超過這麼多才算不合理。
# 快 5 分鐘就整張退回上傳時間，會讓大量正常照片失去拍攝時間，反而削弱證據。
CLOCK_SKEW = timedelta(hours=24)

# HEIF 的 ftyp 品牌。AVIF 也是 HEIF 容器且常帶 mif1，所以主品牌是 avif/avis 時先排除。
HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"hevm", b"hevs", b"mif1", b"msf1"}
AVIF_BRANDS = {b"avif", b"avis"}

STORED = {"jpeg": ("jpg", "image/jpeg"), "png": ("png", "image/png"), "heic": ("jpg", "image/jpeg")}
MEDIA_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
FORMAT_LABELS = {"jpeg": "JPEG", "png": "PNG", "heic": "HEIC"}


class MediaError(ValueError):
    """訊息直接給使用者看；status 對應 HTTP 狀態碼。"""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class ProcessedPhoto:
    source_format: str        # 上傳的格式：jpeg / png / heic
    stored_ext: str           # 實際存檔的副檔名：jpg / png
    original: bytes           # 要存的原檔；JPEG／PNG 與上傳完全相同
    thumb: bytes              # webp 縮圖，不含 EXIF
    width: int                # 依 EXIF 方向轉正後的寬高，前端排版用
    height: int
    taken_at: datetime        # UTC
    taken_at_source: str      # exif：相機記錄的拍攝時間；upload：EXIF 沒有可信時間，退回上傳時間
    lat: float | None
    lng: float | None


def detect_format(head: bytes) -> str | None:
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        major = head[8:12]
        if major in AVIF_BRANDS:
            return None
        box_end = min(int.from_bytes(head[0:4], "big"), len(head))
        compatible = {head[i:i + 4] for i in range(16, box_end - 3, 4)}
        if ({major} | compatible) & HEIF_BRANDS:
            return "heic"
    return None


_EXIF_DATETIME = re.compile(r"^(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_EXIF_OFFSET = re.compile(r"^([+-])(\d{2}):(\d{2})$")


def _text(value) -> str:
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    return str(value or "").strip().strip("\x00")


def exif_datetime(value, offset=None) -> datetime | None:
    """EXIF 的「2030:03:09 06:15:30」轉成 UTC。

    沒有 OffsetTimeOriginal 時當作臺灣時間：EXIF 時間是相機當地時間，而使用者在臺灣的田裡拍照。
    格式錯或像「0000:00:00」這種相機預設值，回 None，不猜。
    """
    match = _EXIF_DATETIME.match(_text(value))
    if not match:
        return None
    try:
        naive = datetime(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    tz = TAIPEI
    offset_match = _EXIF_OFFSET.match(_text(offset))
    if offset_match:
        sign = 1 if offset_match.group(1) == "+" else -1
        delta = timedelta(hours=int(offset_match.group(2)), minutes=int(offset_match.group(3)))
        if delta <= timedelta(hours=14):
            tz = timezone(sign * delta)
    return naive.replace(tzinfo=tz).astimezone(timezone.utc)


def _number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        try:
            numerator, denominator = value
            number = numerator / denominator
        except (TypeError, ValueError, ZeroDivisionError):
            return None
    return number if math.isfinite(number) else None


def gps_decimal(dms, ref) -> float | None:
    try:
        degrees, minutes, seconds = (_number(part) for part in dms)
    except (TypeError, ValueError):
        return None
    if None in (degrees, minutes, seconds) or not (0 <= minutes < 60 and 0 <= seconds < 60):
        return None
    value = degrees + minutes / 60 + seconds / 3600
    return -value if _text(ref).upper() in ("S", "W") else value


def exif_gps(gps: dict) -> tuple[float | None, float | None]:
    lat = gps_decimal(gps.get(GPS_LAT), gps.get(GPS_LAT_REF))
    lng = gps_decimal(gps.get(GPS_LNG), gps.get(GPS_LNG_REF))
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None, None
    if lat == 0 and lng == 0:
        # 很多手機沒定到位時寫 0,0（非洲外海）；拿去當田區位置比沒有更糟。
        return None, None
    return round(lat, 7), round(lng, 7)


def read_exif(image: Image.Image) -> tuple[datetime | None, float | None, float | None]:
    exif = image.getexif()
    ifd = exif.get_ifd(EXIF_IFD)
    taken = exif_datetime(ifd.get(DATETIME_ORIGINAL), ifd.get(OFFSET_TIME_ORIGINAL))
    lat, lng = exif_gps(exif.get_ifd(GPS_IFD))
    return taken, lat, lng


def process_image(data: bytes, *, now: datetime, allowed: set[str], max_pixels: int, thumb_px: int) -> ProcessedPhoto:
    source_format = detect_format(data[:256])
    if source_format is None or source_format not in allowed:
        labels = "、".join(FORMAT_LABELS[f] for f in ("jpeg", "png", "heic") if f in allowed)
        raise MediaError(f"只接受 {labels} 照片", status=415)
    try:
        with Image.open(BytesIO(data)) as image:
            # 開檔只讀檔頭，還沒解碼；先擋超大像素，避免解壓縮炸彈把記憶體吃光。
            if image.width * image.height > max_pixels:
                raise MediaError("照片像素過大，請先縮小後再上傳", status=413)
            image.load()
            taken, lat, lng = read_exif(image)
            original = data if source_format != "heic" else _heic_to_jpeg(image)
            display = ImageOps.exif_transpose(image)
            width, height = display.size
            thumb = _thumbnail(display, thumb_px)
    except MediaError:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise MediaError("照片檔案損壞或無法讀取，請重新拍攝或換一張") from error

    # 只有明顯不合理才退回上傳時間：早於 2000 年，或晚於上傳時間超過 CLOCK_SKEW。
    earliest = datetime(2000, 1, 1, tzinfo=timezone.utc)
    trusted = taken is not None and earliest <= taken <= now + CLOCK_SKEW
    return ProcessedPhoto(source_format=source_format, stored_ext=STORED[source_format][0], original=original,
                          thumb=thumb, width=width, height=height, taken_at=taken if trusted else now,
                          taken_at_source="exif" if trusted else "upload", lat=lat, lng=lng)


def _heic_to_jpeg(image: Image.Image) -> bytes:
    options = {"quality": 92}
    if image.info.get("exif"):
        options["exif"] = image.info["exif"]
    if image.info.get("icc_profile"):
        options["icc_profile"] = image.info["icc_profile"]
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", **options)
    return buffer.getvalue()


def _thumbnail(image: Image.Image, size: int) -> bytes:
    thumb = image.copy()
    if thumb.mode not in ("RGB", "RGBA"):
        has_alpha = "A" in thumb.getbands() or "transparency" in thumb.info
        thumb = thumb.convert("RGBA" if has_alpha else "RGB")
    thumb.thumbnail((size, size))
    buffer = BytesIO()
    thumb.save(buffer, format="WEBP", quality=80)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# 檔案系統：存檔路徑、原子寫入、清理排程
# --------------------------------------------------------------------------

# 只有這個樣式的檔案是我們寫的；清理排程碰到其他任何檔案都不動。
KEY_PATTERN = re.compile(r"^[0-9A-Za-z_-]{1,64}/\d{4}/\d{2}/[0-9a-f]{32}(\.jpg|\.png|\.thumb\.webp)(\.part)?$")


def photo_keys(farmer_id: str, now: datetime, ext: str) -> tuple[str, str]:
    # 存相對路徑，根目錄放設定檔；搬移儲存位置時不用改資料庫。年月依上傳時間，不依 EXIF（EXIF 可能是錯的）。
    stem = f"{farmer_id}/{now:%Y}/{now:%m}/{uuid4().hex}"
    storage_key, thumb_key = f"{stem}.{ext}", f"{stem}.thumb.webp"
    if not KEY_PATTERN.match(storage_key):
        raise MediaError("無法為此農場建立照片路徑", status=500)
    return storage_key, thumb_key


def resolve_key(root: Path, key: str) -> Path:
    base = Path(root).resolve()
    path = (base / key).resolve()
    if base not in path.parents:
        raise MediaError("照片路徑無效", status=404)
    return path


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 先寫 .part 再改名：程式中途掛掉只會留下 .part，清理排程會當孤兒回收，不會出現半張照片。
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(data)
    os.replace(temp, path)


def purge_media(db, *, root: Path, now: datetime, trash_period: timedelta, orphan_grace: timedelta) -> dict:
    """刪掉不再需要的實體檔。照片列一律保留（誰在何時刪了哪張照片，本身就是紀錄）。

    * 軟刪超過 trash_period 的照片：刪檔，列留著。
    * 沒有任何照片列指到的檔案（24 小時內撤回的日誌、上傳到一半失敗）：
      檔案超過 orphan_grace 才刪，避免刪到正在上傳、還沒寫進資料庫的照片。
    """
    from app.models.farm_log import FarmLogPhoto

    base = Path(root)
    if not base.is_dir():
        return {"removed": 0}
    referenced = {}
    for storage_key, thumb_key, deleted_at in db.execute(
            select(FarmLogPhoto.storage_key, FarmLogPhoto.thumb_key, FarmLogPhoto.deleted_at)):
        referenced[storage_key] = referenced[thumb_key] = deleted_at

    removed = 0
    for path in base.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        key = path.relative_to(base).as_posix()
        if not KEY_PATTERN.match(key):
            continue
        if key in referenced:
            deleted_at = referenced[key]
            if deleted_at is None:
                continue
            if deleted_at.tzinfo is None:
                deleted_at = deleted_at.replace(tzinfo=timezone.utc)
            if now - deleted_at < trash_period:
                continue
        else:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if now - modified < orphan_grace:
                continue
        path.unlink(missing_ok=True)
        removed += 1
    return {"removed": removed}


def purge_media_job(db) -> dict:
    from app.core.config import settings

    return purge_media(db, root=settings.media_root, now=datetime.now(timezone.utc),
                       trash_period=timedelta(days=settings.media_trash_days),
                       orphan_grace=timedelta(hours=24))
