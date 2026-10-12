"""邊界匯入用的地理座標轉換與點在多邊形內判斷，純函式、不依賴額外的地理套件。

台灣地籍圖資常見 EPSG:3826（TWD97 TM2，全臺一區）；GeoJSON 一律存 WGS84（EPSG:4326），
所以只需要 TM2 → 經緯度這個方向。公式是標準的 Snyder 橫麥卡托反算（USGS Professional Paper 1395），
GRS80 橢球——跟 UTM 反算用的是同一組式子，只是投影參數（中央經線、縮放係數、假東距）換成 TWD97 的。
"""
from __future__ import annotations

import math

# GRS80 橢球參數（TWD97 採用）。
_A = 6_378_137.0
_F = 1 / 298.257222101
_E2 = _F * (2 - _F)
_EP2 = _E2 / (1 - _E2)

# EPSG:3826：全臺一區 TM2，中央經線 121°E，縮放係數 0.9999，假東距 250000 m，假北距 0（原點在赤道）。
LON0 = math.radians(121.0)
K0 = 0.9999
FALSE_EASTING = 250_000.0
FALSE_NORTHING = 0.0

_M_COEFFS = (1 - _E2 / 4 - 3 * _E2**2 / 64 - 5 * _E2**3 / 256,
            3 * _E2 / 8 + 3 * _E2**2 / 32 + 45 * _E2**3 / 1024,
            15 * _E2**2 / 256 + 45 * _E2**3 / 1024,
            35 * _E2**3 / 3072)


def _meridian_arc(lat: float) -> float:
    """緯度（弧度）到赤道的子午線弧長；只用來獨立驗證反算公式（見 tests/test_precision_geo.py）。"""
    c0, c1, c2, c3 = _M_COEFFS
    return _A * (c0 * lat - c1 * math.sin(2 * lat) + c2 * math.sin(4 * lat) - c3 * math.sin(6 * lat))


def wgs84_to_twd97(lon: float, lat: float) -> tuple[float, float]:
    """經緯度（WGS84，度）→ TWD97 TM2 (x, y) 公尺。邊界匯入不需要這個方向，留給之後畫面要顯示 TWD97 座標時用。"""
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    n = _A / math.sqrt(1 - _E2 * math.sin(lat_r) ** 2)
    t = math.tan(lat_r) ** 2
    c = _EP2 * math.cos(lat_r) ** 2
    a = (lon_r - LON0) * math.cos(lat_r)
    m = _meridian_arc(lat_r)
    x = K0 * n * (a + (1 - t + c) * a**3 / 6 + (5 - 18 * t + t**2 + 72 * c - 58 * _EP2) * a**5 / 120) + FALSE_EASTING
    y = K0 * (m + n * math.tan(lat_r) * (a**2 / 2 + (5 - t + 9 * c + 4 * c**2) * a**4 / 24
              + (61 - 58 * t + t**2 + 600 * c - 330 * _EP2) * a**6 / 720)) + FALSE_NORTHING
    return x, y


def twd97_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """TWD97 TM2 (x, y) 公尺 → 經緯度（WGS84，度）。邊界匯入時把使用者上傳的地籍座標換成 GeoJSON 要求的 4326。"""
    m = (y - FALSE_NORTHING) / K0
    mu = m / (_A * _M_COEFFS[0])
    e1 = (1 - math.sqrt(1 - _E2)) / (1 + math.sqrt(1 - _E2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1**3 / 32) * math.sin(2 * mu) + (21 * e1**2 / 16 - 55 * e1**4 / 32) * math.sin(4 * mu)
           + (151 * e1**3 / 96) * math.sin(6 * mu) + (1097 * e1**4 / 512) * math.sin(8 * mu))
    c1 = _EP2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = _A / math.sqrt(1 - _E2 * math.sin(phi1) ** 2)
    r1 = _A * (1 - _E2) / (1 - _E2 * math.sin(phi1) ** 2) ** 1.5
    d = (x - FALSE_EASTING) / (n1 * K0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (d**2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1**2 - 9 * _EP2) * d**4 / 24
                + (61 + 90 * t1 + 298 * c1 + 45 * t1**2 - 252 * _EP2 - 3 * c1**2) * d**6 / 720)
    lon = LON0 + (d - (1 + 2 * t1 + c1) * d**3 / 6
                 + (5 - 2 * c1 + 28 * t1 - 3 * c1**2 + 8 * _EP2 + 24 * t1**2) * d**5 / 120) / math.cos(phi1)
    return math.degrees(lon), math.degrees(lat)


def convert_ring(ring: list[list[float]], crs: str) -> list[list[float]]:
    """轉換一圈座標；只認 EPSG:4326（原樣）與 EPSG:3826（換算）。其他 CRS 交給使用者自行先轉，不猜。"""
    if crs in ("EPSG:4326", "urn:ogc:def:crs:OGC:1.3:CRS84", ""):
        return [[float(x), float(y)] for x, y in ring]
    if crs == "EPSG:3826":
        return [list(twd97_to_wgs84(float(x), float(y))) for x, y in ring]
    raise ValueError(f"不支援的座標系統 {crs}；請先轉成 EPSG:4326 或 EPSG:3826 再上傳")


def polygon_rings(geojson: dict) -> list[list[list[float]]]:
    """從 Polygon／MultiPolygon／包著其中之一的 Feature 取出外圈座標（不處理內圈挖洞，示範階段夠用）。"""
    geometry = geojson.get("geometry", geojson) if geojson.get("type") == "Feature" else geojson
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        return [coords[0]]
    if kind == "MultiPolygon":
        return [polygon[0] for polygon in coords]
    raise ValueError("邊界只接受 Polygon 或 MultiPolygon（或包著其中之一的 Feature）")


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """標準射線法：從點往右畫射線，數穿過幾條邊；奇數在內，偶數在外。邊界本身算在內，避免格心剛好落在邊上時被誤判在外。"""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        if (x1, y1) == (lon, lat) or (x2, y2) == (lon, lat):
            return True
        crosses = (y1 > lat) != (y2 > lat)
        if crosses:
            x_at_lat = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if x_at_lat == lon:
                return True
            if x_at_lat > lon:
                inside = not inside
    return inside


def point_in_polygon(lon: float, lat: float, rings: list[list[list[float]]]) -> bool:
    return any(point_in_ring(lon, lat, ring) for ring in rings)


def bounds(rings: list[list[list[float]]]) -> tuple[float, float, float, float]:
    lons = [point[0] for ring in rings for point in ring]
    lats = [point[1] for ring in rings for point in ring]
    return min(lons), min(lats), max(lons), max(lats)


def outside_cells(rings: list[list[list[float]]], grid_cols: int, grid_rows: int) -> list[list[int]]:
    """依邊界外接矩形切出 grid_cols×grid_rows 格，回傳中心點落在邊界外的格子座標；分區圖據此不著色。"""
    min_lon, min_lat, max_lon, max_lat = bounds(rings)
    cell_w, cell_h = (max_lon - min_lon) / grid_cols, (max_lat - min_lat) / grid_rows
    outside = []
    for row in range(grid_rows):
        for col in range(grid_cols):
            center_lon = min_lon + (col + 0.5) * cell_w
            center_lat = min_lat + (row + 0.5) * cell_h
            if not point_in_polygon(center_lon, center_lat, rings):
                outside.append([col, row])
    return outside
