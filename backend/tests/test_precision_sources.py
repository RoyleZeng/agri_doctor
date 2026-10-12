"""CSV 上傳解析與格子→分區平均的離線測試。都是純函式，不碰資料庫。"""
from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from app.precision.layers import parse_cell_csv, zone_averages
from app.precision.sources import CsvUploadSource, TAIPEI, parse_readings_csv


def test_reading_csv_parses_rows_and_defaults_naive_timestamps_to_taipei():
    text = "station_code,metric,ts,value,unit\nS-1,soil_moisture,2030-03-09T06:00:00+00:00,32.5,%\nS-1,soil_moisture,2030-03-09 07:00,31.0,\n"
    rows = parse_readings_csv(text)
    assert len(rows) == 2
    assert rows[0].ts == datetime(2030, 3, 9, 6, 0, tzinfo=timezone.utc) and rows[0].unit == "%"
    assert rows[1].ts == datetime(2030, 3, 9, 7, 0, tzinfo=TAIPEI) and rows[1].unit == ""


def test_reading_csv_rejects_missing_columns_and_reports_the_bad_line():
    with pytest.raises(ValueError, match="缺少必要欄位"):
        parse_readings_csv("station_code,metric,value\nS-1,soil_moisture,30\n")
    with pytest.raises(ValueError, match="第 3 行"):
        parse_readings_csv("station_code,metric,ts,value\nS-1,soil_moisture,2030-03-09T06:00,30\nS-1,soil_moisture,not-a-time,30\n")
    with pytest.raises(ValueError, match="不是數字"):
        parse_readings_csv("station_code,metric,ts,value\nS-1,soil_moisture,2030-03-09T06:00,abc\n")
    with pytest.raises(ValueError, match="沒有任何資料列"):
        parse_readings_csv("station_code,metric,ts,value\n")


def test_csv_upload_source_filters_by_since():
    text = ("station_code,metric,ts,value\n"
            "S-1,soil_moisture,2030-03-09T06:00:00+00:00,30\n"
            "S-1,soil_moisture,2030-03-09T08:00:00+00:00,32\n")
    all_rows = CsvUploadSource(text).pull(None)
    assert len(all_rows) == 2
    recent = CsvUploadSource(text).pull(datetime(2030, 3, 9, 7, 0, tzinfo=timezone.utc))
    assert len(recent) == 1 and recent[0].value == 32


def test_cell_csv_rejects_out_of_range_cells_with_line_number():
    with pytest.raises(ValueError, match="第 3 行"):
        parse_cell_csv("col,row,value\n0,0,0.5\n6,0,0.4\n", grid_cols=6, grid_rows=4)
    with pytest.raises(ValueError, match="不是整數"):
        parse_cell_csv("col,row,value\nx,0,0.5\n", grid_cols=6, grid_rows=4)
    with pytest.raises(ValueError, match="不是數字"):
        parse_cell_csv("col,row,value\n0,0,abc\n", grid_cols=6, grid_rows=4)


def test_cell_csv_and_zone_averages_round_trip():
    text = "col,row,value\n0,0,0.70\n0,1,0.80\n1,0,0.50\n"
    cells = parse_cell_csv(text, grid_cols=2, grid_rows=2)
    assert cells == {"0,0": 0.70, "0,1": 0.80, "1,0": 0.50}

    @dataclass
    class Zone:
        code: str
        cells: list

    zones = [Zone("A", [[0, 0], [0, 1]]), Zone("B", [[1, 0]]), Zone("C", [[1, 1]])]
    averages = zone_averages(cells, zones)
    assert averages == {"A": 0.75, "B": 0.5}  # C 沒有任何一格在這次上傳範圍內，不出現，不是 0
