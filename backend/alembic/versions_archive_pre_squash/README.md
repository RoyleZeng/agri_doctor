# 合併前的舊版 migration（0001-0016）

這裡是 `alembic/versions/0001_initial.py` 合併重建之前的 16 個檔案，純備份，**不會**被 Alembic 掃到
（Alembic 只看 `alembic/versions/`，不會遞迴進子目錄）。

## 為什麼合併

舊的 0001 是用「即時」的 `Base.metadata.create_all()` 建表，不是固定的歷史快照：每次在全新資料庫上
執行，都會直接建出「當下」models.py 的最終結構（含後來 0002-0016 才加的欄位／表）。這代表 0002 之後
每一版想做的 ALTER／RENAME 在全新資料庫上一律撲空——目標狀態早在 0001 就達成了。實際症狀：全新
PostgreSQL 資料庫第一次跑 `alembic upgrade head` 會在 0002 就失敗（`DROP CONSTRAINT uq_registration`
找不到這個名字，因為即時 0001 已經直接建出改名後的 `uq_registration_usage`）。

開發用的資料庫之所以能一路 migrate 到 0009 沒出事，是因為它是照順序一步步升級上來的，從沒有真的
「從零重跑全部」過——這個 bug 一直都在，只是要重建一個全新資料庫才會踩到。

新的 `0001_initial.py` 用 `alembic revision --autogenerate` 對空資料庫產生，內容寫死
`op.create_table()`／`sa.Column()`（跟其餘檔案一直以來的寫法一樣），不再引用會變動的
`Base.metadata`。之後新增的 migration 照一般方式疊加在這版之上即可，不會再犯同樣的錯。

## 這裡面留了什麼

除了純 schema 異動，這批舊檔案裡有兩個真的操作既有資料的回填邏輯，新的 0001 沒有保留（全新資料庫
沒有既有列可回填，用不到）：

- `0008_usage_context_weather.py`：既有 `registrations` 列補 `usage_hash`（SHA-256 雜湊稀釋倍數／
  施藥量／施藥方法／注意事項），才能把唯一鍵從 `uq_registration` 換成 `uq_registration_usage`。
- `0013_precision.py`：既有 `farm_work_logs`（`kind='spray'`）補 `phi_days`／`phi_source` 快照——
  有處方項目的用處方，沒有的退回登記快照，兩者都沒有維持 `none`（不猜、不填 0）。

`tests/test_consult_migration.py`、`tests/test_farm_log_migration.py` 仍然直接載入這個目錄下的檔案
執行升降版，驗證當年這些行為（SQLite 加外鍵整表重建不遺失資料、回填邏輯正確）——當作歷史迴歸測試留著；
之後如果要對已經有真實資料的正式環境做類似的加欄位異動，這裡的寫法可以直接參考。

## 這裡的檔案還會被執行

不只是備份：`backend/app/db/migrate.py` 會在需要時把這個目錄當成 `version_locations`，
自動把「停在合併前中間版本（`0002`-`0016`）」的既有資料庫接著升到 `0016`，再 stamp 成新鏈的
`0001`。所以**不要刪掉這個目錄**，也不要改動裡面的檔案。

⚠️ **只能從中間往前接著跑，不能從零重放**：這裡的 `0001_initial.py` 就是問題本身（即時
`Base.metadata.create_all()`），對空資料庫重放它會直接建出「現在」的 schema，讓 `0002` 之後每一版
的 ALTER 撲空。`0002` 之後每一支都是寫死的 DDL，對既有資料庫接著跑才是安全的。

## 這次合併的代價

專案目前還沒有正式上線的資料庫，這次合併不用保留「從零重放完整歷史」的能力。既有資料庫
（例如本機的 docker-compose 開發資料庫）不需要重建，交給 `python -m app.db.migrate` 自動搭橋即可；
只有版本號落在 `0002`-`0016` 之外的資料庫才需要人工確認。
