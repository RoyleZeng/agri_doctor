"""邊界匯入用的座標轉換與點在多邊形內判斷。純函式，離線測試。

TWD97→WGS84 沒有隨手可信的參考點可以拿來核對，所以測試分三層：
1. 投影原點是定義本身保證成立的恆等式（中央經線、假東距處必為 121°E、緯度 0）。
2. 用另一條完全獨立算出來的子午線弧長公式，核對「沿中央經線」這條路徑的反算對不對。
3. 用同一組公式的正算／反算互相對得回去，核對「離開中央經線」的修正項沒有寫反或漏乘。
三層合起來，一般的符號、係數手誤都會被抓到。
"""
import math

from app.precision.geo import (K0, LON0, _meridian_arc, bounds, convert_ring, outside_cells, point_in_polygon,
                               point_in_ring, polygon_rings, twd97_to_wgs84, wgs84_to_twd97)

EPS = 1e-6  # 度；約 0.1 mm 等級，遠比地籍圖精度嚴格


def test_projection_origin_is_the_defining_identity():
    lon, lat = twd97_to_wgs84(250_000.0, 0.0)
    assert abs(lon - 121.0) < EPS and abs(lat - 0.0) < EPS


def test_inverse_matches_an_independently_derived_meridian_arc():
    for lat_deg in (0, 5, 15, 21.5, 23.5, 25.5, -10):
        y = K0 * _meridian_arc(math.radians(lat_deg))
        lon, lat = twd97_to_wgs84(250_000.0, y)
        assert abs(lon - 121.0) < EPS, f"lat={lat_deg}: lon drifted off the central meridian"
        assert abs(lat - lat_deg) < EPS, f"lat={lat_deg}: recovered {lat}"


def test_forward_and_inverse_round_trip_across_taiwan():
    # 台灣本島大約落在這個範圍；TM2 在中央經線 ±2° 內都準確。
    for lon_deg in (119.6, 120.2, 121.0, 121.6, 122.4):
        for lat_deg in (21.9, 23.0, 24.5, 25.3):
            x, y = wgs84_to_twd97(lon_deg, lat_deg)
            lon, lat = twd97_to_wgs84(x, y)
            assert abs(lon - lon_deg) < EPS and abs(lat - lat_deg) < EPS, (lon_deg, lat_deg, lon, lat)


def test_convert_ring_passes_4326_through_and_converts_3826():
    ring_4326 = [[121.0, 24.0], [121.1, 24.0], [121.1, 24.1]]
    assert convert_ring(ring_4326, "EPSG:4326") == ring_4326
    assert convert_ring(ring_4326, "") == ring_4326  # 沒填當作已經是 4326

    x, y = wgs84_to_twd97(121.5, 24.8)
    converted = convert_ring([[x, y]], "EPSG:3826")
    assert abs(converted[0][0] - 121.5) < EPS and abs(converted[0][1] - 24.8) < EPS

    try:
        convert_ring(ring_4326, "EPSG:3857")
        assert False, "應該拒絕不支援的座標系統"
    except ValueError as error:
        assert "EPSG:3857" in str(error)


def test_polygon_rings_accepts_polygon_multipolygon_and_feature_wrapper():
    square = [[0, 0], [1, 0], [1, 1], [0, 1]]
    assert polygon_rings({"type": "Polygon", "coordinates": [square]}) == [square]
    assert polygon_rings({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [square]}}) == [square]
    multi = polygon_rings({"type": "MultiPolygon", "coordinates": [[square], [square]]})
    assert multi == [square, square]
    try:
        polygon_rings({"type": "Point", "coordinates": [0, 0]})
        assert False, "應該拒絕 Polygon/MultiPolygon 以外的幾何"
    except ValueError:
        pass


def test_point_in_polygon_ray_casting():
    square = [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]]
    assert point_in_ring(1.0, 1.0, square) is True
    assert point_in_ring(3.0, 1.0, square) is False
    assert point_in_ring(0.0, 0.0, square) is True  # 頂點算在內，不因浮點誤差被判在外
    assert point_in_ring(1.0, 0.0, square) is True  # 邊上
    assert point_in_polygon(1.0, 1.0, [square]) is True
    assert bounds([square]) == (0.0, 0.0, 2.0, 2.0)


def test_outside_cells_marks_corners_of_a_triangle_grid():
    # 直角三角形頂點在左下、右下、左上；右上角那格的中心點理論上在斜邊外。
    triangle = [[0.0, 0.0], [4.0, 0.0], [0.0, 4.0]]
    outside = outside_cells([triangle], grid_cols=4, grid_rows=4)
    assert [3, 3] in outside  # 最遠離原點的格子，鐵定在外
    assert [0, 0] not in outside  # 貼著直角頂點的格子，鐵定在內
    assert len(outside) < 16  # 不是整個格子都判成外面
