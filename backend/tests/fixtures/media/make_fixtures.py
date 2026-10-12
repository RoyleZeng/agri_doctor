"""產生照片測試用的 fixture。檔案已提交進 repo，只有要改 fixture 時才需要重跑：

    cd backend && ./.venv/bin/python tests/fixtures/media/make_fixtures.py

座標與時間都是虛構的試驗值，不是任何真實農場。
"""
from io import BytesIO
from pathlib import Path

from PIL import Image
from PIL.TiffImagePlugin import IFDRational
import pillow_heif

HERE = Path(__file__).parent

# EXIF 標籤編號
ORIENTATION, DATETIME_ORIGINAL, OFFSET_TIME_ORIGINAL, GPS_IFD = 0x0112, 0x9003, 0x9011, 0x8825
EXIF_IFD = 0x8769


def dms(value: float) -> tuple:
    degrees = int(value)
    minutes = int((value - degrees) * 60)
    seconds = round(((value - degrees) * 60 - minutes) * 60 * 100)
    return (IFDRational(degrees, 1), IFDRational(minutes, 1), IFDRational(seconds, 100))


def exif(*, taken: str | None = None, offset: str | None = None, lat=None, lng=None, orientation: int | None = None) -> bytes:
    data = Image.Exif()
    if orientation:
        data[ORIENTATION] = orientation
    ifd = data.get_ifd(EXIF_IFD)
    if taken:
        ifd[DATETIME_ORIGINAL] = taken
    if offset:
        ifd[OFFSET_TIME_ORIGINAL] = offset
    if lat is not None:
        gps = data.get_ifd(GPS_IFD)
        gps[1] = "N" if lat >= 0 else "S"
        gps[2] = dms(abs(lat))
        gps[3] = "E" if lng >= 0 else "W"
        gps[4] = dms(abs(lng))
    return data.tobytes()


def canvas(width: int, height: int) -> Image.Image:
    # 左半紅、右半藍：旋轉之後從顏色看得出方向有沒有轉對。
    image = Image.new("RGB", (width, height), (200, 30, 30))
    image.paste((30, 30, 200), (width // 2, 0, width, height))
    return image


def main() -> None:
    canvas(64, 40).save(HERE / "exif_gps_rotated.jpg", quality=90,
                        exif=exif(taken="2030:03:09 06:15:30", lat=22.7584722, lng=120.3695, orientation=6))
    canvas(48, 48).save(HERE / "offset_time.jpg", quality=90,
                        exif=exif(taken="2030:03:09 06:15:30", offset="+09:00"))
    canvas(48, 48).save(HERE / "bad_date.jpg", quality=90, exif=exif(taken="0000:00:00 00:00:00"))
    canvas(40, 30).save(HERE / "no_exif.png")
    canvas(640, 480).save(HERE / "large.jpg", quality=90)

    pillow_heif.register_heif_opener()
    canvas(64, 40).save(HERE / "phone.heic", format="HEIF", quality=80,
                        exif=exif(taken="2030:03:08 17:40:00", lat=-23.5, lng=-46.25))
    (HERE / "not_an_image.jpg").write_bytes(b"this is plain text pretending to be a jpeg\n")
    buffer = BytesIO()
    canvas(8, 8).save(buffer, format="GIF")
    (HERE / "animated.gif").write_bytes(buffer.getvalue())


if __name__ == "__main__":
    main()
