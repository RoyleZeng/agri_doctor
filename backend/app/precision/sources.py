"""感測來源介面：現在只有 `CsvUploadSource` 真的有人呼叫；`DemoSensorSource` 只是把介面補齊，讓
之後接 `HttpPollSource`／`MqttSource` 時，儀表板、規則引擎、建議卡都不用因為多一種協定而改——
新協定只要多寫一個實作這個介面的 adapter，`sensor_groups.source_id → data_sources.kind` 決定用哪一個。

CSV 是刻意優先做的：幾乎零成本，而且讓沒有開放 API 的舊型感測設備也能用。
"""
from __future__ import annotations

import csv
import io
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")
READING_COLUMNS = ("station_code", "metric", "ts", "value")


@dataclass(frozen=True)
class ReadingRow:
    station_code: str
    metric: str
    ts: datetime
    value: float
    unit: str = ""


class SensorSource(ABC):
    """`pull` 回傳這段時間內的讀數；`since=None` 代表全部（CSV 上傳沒有「上次同步到哪」的概念，一次全拿）。"""

    @abstractmethod
    def pull(self, since: datetime | None) -> list[ReadingRow]: ...


class DemoSensorSource(SensorSource):
    """示範資料由 `app/precision/demo.py` 直接寫進資料庫，不經過這個介面；這裡只是符合介面形狀。"""

    def pull(self, since: datetime | None = None) -> list[ReadingRow]:
        return []


def _parse_ts(text: str) -> datetime:
    # 裝置常常沒有時區資訊；跟 EXIF 拍攝時間同一個假設——沒寫時區就當作農場所在地的 Asia/Taipei。
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return value if value.tzinfo else value.replace(tzinfo=TAIPEI)


def parse_readings_csv(text: str) -> list[ReadingRow]:
    """欄位：station_code, metric, ts（ISO 8601）, value, unit（選填）。哪一行有問題就直接說第幾行，不整批默默跳過。"""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not set(READING_COLUMNS) <= set(reader.fieldnames):
        raise ValueError(f"CSV 缺少必要欄位：{'、'.join(READING_COLUMNS)}")
    rows = []
    for line_number, row in enumerate(reader, start=2):  # 第 1 行是表頭
        station = (row.get("station_code") or "").strip()
        metric = (row.get("metric") or "").strip()
        if not station or not metric:
            raise ValueError(f"第 {line_number} 行缺少 station_code 或 metric")
        try:
            ts = _parse_ts(row["ts"])
        except (TypeError, ValueError):
            raise ValueError(f"第 {line_number} 行的 ts「{row.get('ts')}」不是可辨識的時間格式，建議用 ISO 8601")
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            raise ValueError(f"第 {line_number} 行的 value「{row.get('value')}」不是數字")
        rows.append(ReadingRow(station, metric, ts, value, (row.get("unit") or "").strip()))
    if not rows:
        raise ValueError("CSV 沒有任何資料列")
    return rows


class CsvUploadSource(SensorSource):
    def __init__(self, text: str):
        self.text = text

    def pull(self, since: datetime | None = None) -> list[ReadingRow]:
        rows = parse_readings_csv(self.text)
        return [row for row in rows if since is None or row.ts > since]
