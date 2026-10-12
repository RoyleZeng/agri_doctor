# 植物醫生用藥決策平台（agri-doctor）

把植物醫師原本要在三個政府網站交叉查詢的用藥資訊合成一處，並延伸到診療開單、診斷書、農場管理、農事日誌、精準農業與區域病蟲害地圖。

| 來源 | 提供什麼 |
| --- | --- |
| 植物保護資訊系統（藥毒所） | 法定核准的「作物 × 病蟲害 → 藥劑」、稀釋倍數、施藥量、FRAC/IRAC |
| 衛福部食藥署 | 殘留容許量（MRL）與公告禁用農藥 |
| 高雄區農業改良場 | 各作物病蟲害防治用藥摘要表（PDF） |

平台的原則是**把三源對不起來的地方標出來，不替植物醫師做決定**。

## 架構

```
frontend/  Vue 3 + Vite + TypeScript（自訂元件、Leaflet 地圖）
backend/   FastAPI + SQLAlchemy 2.0 + Alembic + PostgreSQL 16 (pg_trgm)
  app/api/v1/     REST API（19 個路由模組、125 支端點）
  app/crawlers/   三源爬蟲：純解析器 + 網路驅動，通過檢查才發布
  app/services/   正規化、三源比對、安全採收期、抗藥性輪替、匯出
  app/precision/  精準農業：資料來源 adapter、規則引擎（rules/v1.json）
  app/models/     資料表定義；alembic/ 為資料庫遷移
  tests/          pytest 後端測試
docker-compose.yml  db / api / crawler / web 四個服務
```

## 快速開始

```bash
cp backend/.env.example backend/.env   # 視需要填 CWA_API_KEY、MOA_API_KEY
docker compose up -d
```

- 前端：http://localhost:5173
- API：http://localhost:8000，互動式 API 文件（OpenAPI / Swagger）：http://localhost:8000/docs
- 資料庫：localhost:5433

首次啟動資料庫是空的，先抓三源資料：

```bash
docker compose exec api python -m app.crawlers.kdais
docker compose exec api python -m app.crawlers.ppm
docker compose exec api python -m app.crawlers.fda
```

之後 `crawler` 服務會依排程自動更新（高改場每日、另兩源每週一，皆為離峰時段）。

示範帳號與資料可用 `backend/scripts/seed_demo_walkthrough.py` 建立。

## 測試

```bash
docker compose exec api pytest            # 後端
cd frontend && npm install && npm run build
npm run test:consult                      # 前端純函式測試，其餘見 package.json 的 test:* 指令
```

## 授權說明

`backend/app/assets/fonts/` 內的思源宋體（Source Han Serif）依 SIL Open Font License 1.1 散布，見該目錄 LICENSE.txt。
