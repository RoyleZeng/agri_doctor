"""排程工作的「跑一次就結束」入口：`python -m app.jobs <工作名稱>`。

為什麼要有這支：本機與 docker compose 是用 `app/scheduler.py` 的 APScheduler 常駐執行這些工作，
但 Lambda 沒有常駐行程（見 `docs/deployment.md`），排程要交給 EventBridge Scheduler，而
EventBridge 需要一個跑完就退出的目標。兩邊呼叫的是同一段程式碼，排程邏輯不會在兩個地方各寫一次。

慣例：
* 成功印一行結果（CloudWatch Logs 看得到做了什麼，不是只有「跑過了」）。
* 失敗讓例外往上拋、行程以非零碼結束——EventBridge 才看得到失敗並依設定重試。
  **不要吞掉例外印成功**，排程默默失敗比沒有排程更糟。
* 例外是單一田區的分析失敗（`try_analyze` 本來就吞掉）：一個田區的資料有問題不該讓整批停擺。
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal


def media_purge(db) -> str:
    """農事日誌照片：軟刪超過保留期的檔案與孤兒檔。照片列一律保留（誰在何時刪了哪張，本身就是紀錄）。"""
    from app.services.farm_media import purge_media_job

    result = purge_media_job(db)  # 只讀資料庫、刪實體檔，沒有寫入要 commit
    return f"照片清理完成：刪除 {result['removed']} 個檔案"


def region_aggregate(db) -> str:
    """區域地圖彙整：等同 `POST /api/v1/region/reaggregate`，整批 upsert，重跑不會把數字灌水。"""
    from app.services.consult import taipei_today
    from app.services.region_stats import reaggregate

    result = reaggregate(db, taipei_today())  # 自己 commit
    return (f"區域彙整完成：寫入 {result['written']} 筆 region_daily_stats，"
            f"清掉 {result['removed']} 筆來源已不在的舊列")


def precision_reanalyze(db) -> str:
    """精準模式重新分析：先撤回過期建議，再對每個田區跑一次規則引擎。

    等同農民在畫面上按「重新分析」，只是對所有田區做。前提已不成立的建議會被標成 expired 並附原因——
    一張叫農民去灌已經濕的田的卡片，比沒有卡片更傷信任，所以這個工作要定期跑，不能只靠人按按鈕。
    """
    from app.models.precision import FarmField
    from app.precision import service
    from app.services.consult import taipei_today

    now = datetime.now(timezone.utc)
    today = taipei_today()
    expired = service.expire_due(db, now)
    fields = db.scalars(select(FarmField)).all()
    for field in fields:
        service.try_analyze(db, field, now=now, today=today)
    db.commit()
    return f"精準模式重新分析完成：{len(fields)} 個田區，另撤回 {expired} 則過期建議"


def moa_sync(db) -> str:
    from app.services.moa_sync import sync
    result = sync(db)
    failed = {name: status for name, status in result.items() if status != 'ok'}
    if failed:
        # 同步狀態已寫入 DB；非零退出讓排程看得到失敗，不能因保住舊資料就回報成功。
        raise RuntimeError(f"農業部同步未完整成功：{failed}")
    return f"農業部同步完成：{len(result)} 個資料範圍；查詢從本地資料庫讀取"


JOBS = {
    "media-purge": media_purge,
    "region-aggregate": region_aggregate,
    "precision-reanalyze": precision_reanalyze,
    "moa-sync": moa_sync,
}


def run(name: str) -> str:
    with SessionLocal() as db:
        return JOBS[name](db)


def lambda_handler(event, context=None) -> dict:
    """EventBridge Scheduler 指向 Lambda 時的進入點；event 給 `{"job": "<工作名稱>"}`。

    名稱不認得就丟例外讓這次執行標記為失敗——排程設定打錯字必須看得出來，不能靜悄悄什麼都沒做。
    """
    name = (event or {}).get("job")
    if name not in JOBS:
        raise ValueError(f"未知的排程工作 {name!r}；可用的有：{', '.join(JOBS)}")
    return {"job": name, "result": run(name)}


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in JOBS:
        print(f"用法：python -m app.jobs <{' | '.join(JOBS)}>", file=sys.stderr)
        return 2
    name = argv[1]
    print(f"[jobs] {name} 開始")
    print(f"[jobs] {run(name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
