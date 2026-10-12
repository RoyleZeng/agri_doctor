# 鄉鎮界 GeoJSON

`kaohsiung_districts.geo.json`：高雄市 38 個行政區（2010 年高雄縣市合併後）的邊界，隨程式保留，離線可用。

## 來源與處理

- 原始資料：[ronnywang/twgeojson](https://github.com/ronnywang/twgeojson) 的 `twtown2010.3.json`（全臺鄉鎮市區，簡化版），
  座標系統已是 WGS84（EPSG:4326）經緯度。該專案整理自內政部（MOI）鄉鎮市區界線資料。
- 篩選出 `county == "高雄市"` 的 42 筆，其中前鎮區、小港區、旗津區、鼓山區各多一筆「(海)」（同 `town_id`，
  海域／港區部分另外成圖），依 `town_id` 合併成單一 MultiPolygon，回到 38 個行政區。
- 用 `mapshaper -simplify dp 15% -clean` 再簡化一次，把檔案從合併後的 106 KB 壓到 32 KB；
  簡化程度足夠畫鄉鎮等級的色階地圖，不適合拿來算精確面積或當地籍圖使用。
- 處理腳本沒有留在 repo 裡（一次性資料前處理，之後若要重新產生，照上面三步驟重做即可）。

## 範圍

現在只有高雄市。其他縣市在地圖上顯示「尚未提供」——擴充成本在資料不在程式，之後只要照同樣步驟
篩選、合併、簡化其他縣市的鄉鎮界，加進這個資料夾即可，`services/region_stats.py` 與 `RegionMapView` 不用改。

## 欄位

每筆 Feature 的 `properties`：`county`（固定「高雄市」）、`district`（區名，例如「大社區」）、`town_id`（MOI 鄉鎮代碼）。
