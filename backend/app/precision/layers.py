"""圖層資料的共用處理：無人機上傳（示範階段只吃 CSV）用的格子解析，與格子→分區平均。"""
from __future__ import annotations

import csv
import io

CELL_COLUMNS = ("col", "row", "value")


def parse_cell_csv(text: str, grid_cols: int, grid_rows: int) -> dict[str, float]:
    """欄位：col, row, value。格子超出田區範圍直接拒絕整份檔案——座標對不上就不該悄悄丟掉幾格了事。"""
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None or not set(CELL_COLUMNS) <= set(reader.fieldnames):
        raise ValueError(f"CSV 缺少必要欄位：{'、'.join(CELL_COLUMNS)}")
    cell_values: dict[str, float] = {}
    for line_number, row in enumerate(reader, start=2):
        try:
            col, grid_row = int(row["col"]), int(row["row"])
        except (TypeError, ValueError):
            raise ValueError(f"第 {line_number} 行的 col/row 不是整數")
        if not (0 <= col < grid_cols and 0 <= grid_row < grid_rows):
            raise ValueError(f"第 {line_number} 行的格子 ({col},{grid_row}) 超出田區範圍（{grid_cols}×{grid_rows}）")
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            raise ValueError(f"第 {line_number} 行的 value「{row.get('value')}」不是數字")
        cell_values[f"{col},{grid_row}"] = value
    if not cell_values:
        raise ValueError("CSV 沒有任何資料列")
    return cell_values


def zone_averages(cell_values: dict[str, float], zones) -> dict[str, float]:
    """zones 是有 `.code` 與 `.cells`（[[col,row],...]）的物件列表，ORM 的 FarmZone 或任何 dataclass 都相容。

    分區裡沒有任何一格落在這次上傳範圍內就不給那個分區的值——不用其他分區的數字去湊，那不是這區的實測。
    """
    out: dict[str, float] = {}
    for zone in zones:
        values = [cell_values[f"{col},{row}"] for col, row in zone.cells if f"{col},{row}" in cell_values]
        if values:
            out[zone.code] = round(sum(values) / len(values), 3)
    return out
