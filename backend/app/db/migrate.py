"""安全的升級入口：`docker-compose.yml`／部署腳本呼叫這個（`python -m app.db.migrate`），
不要直接呼叫 `alembic upgrade head`。

為什麼要多這一層：`alembic/versions/0001_initial.py` 在 2026-09-16 把合併前的 16 支 migration
（0001-0016，見 `alembic/versions_archive_pre_squash/README.md`）壓成一支固定快照，理由是舊的
0001 用「即時」的 `Base.metadata.create_all()` 建表，導致全新資料庫第一次 `alembic upgrade head`
必定在 0002 就出錯。但這個壓縮對「合併前就已經升級過」的既有資料庫是破壞性的：它們的
`alembic_version` 停在合併前某個版本號（例如 '0009'、'0016'），這個版本號在新鏈裡已經不存在，
直接 `alembic upgrade head` 會報 `Can't locate revision`——包括本機的開發資料庫，也包括之後任何
已經部署過的實例。

`decide()` 是純函式，只負責判斷該怎麼做，不碰資料庫，離線可測：
* 全新資料庫（根本沒有 `alembic_version` 表）：`"noop"`，直接 upgrade head。
* `alembic_version` 已經等於新鏈的頭、schema 也對得上：`"noop"`，直接 upgrade head（本來就是 no-op）。
* `alembic_version` 是別的值，但 schema 已經是合併前的最終狀態（`region_daily_stats` 這幾張表
  存在，只有跑完舊 0016 才會有）：`"stamp"`——**不重跑 DDL**，只把 `alembic_version` stamp 成
  壓縮基準 0001，再 upgrade head（後續新增的 migration 會真正執行）。
* `alembic_version` 停在合併前的中間版本（0002-0016），schema 還沒到 0016：`"replay"`——存檔鏈
  還在 repo 裡，自己用它接著把資料庫升到 0016，再走上面的 `"stamp"`。不要求人手動照 README 做，
  那種步驟一定會在展示前一天忘記。
* 其餘情況（版本號不在存檔鏈可接續的範圍，或版本號對得上新鏈但 schema 對不上）：`"abort"`，
  明確報錯、中止、不動資料庫，交給人確認。

**存檔鏈只能「從中間往前接著跑」，不能從零重放**：存檔的 0001 一樣是即時的
`Base.metadata.create_all()`（就是要修的 bug 本身），重放它會直接建出現在的 schema，讓後面每一版
的 ALTER 撲空。0002 之後每一支都是寫死的 `op.create_table`／`op.add_column`，對既有資料庫接著跑
是安全的，所以可自動處理的範圍是 0002-0016。

判斷「schema 是否為合併前最終狀態」不看 `alembic_version` 的字串本身（新舊兩條鏈的第一支剛好都
叫 '0001'，字串會撞名，不能拿來判斷「新舊」），只看實際 schema 長什麼樣子。
"""
from __future__ import annotations

import sys
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.db.session import engine

# 只有跑完合併前的最後一版（0016）才會有這三張表；用它們的存在與否代表「schema 已達合併前最終狀態」，
# 比對版本號字串更可靠（字串會撞名，schema 形狀不會騙人）。
LEGACY_HEAD_SIGNATURE_TABLES = ("region_daily_stats", "survey_records", "alert_thresholds")

ARCHIVE_VERSION_PATH = "alembic/versions_archive_pre_squash"
LEGACY_FINAL_REVISION = "0016"
# 舊鏈只等價於壓縮後的 0001；之後新增的 migration 必須真正執行，不能 stamp 到新 head。
SQUASH_BASE_REVISION = "0001"
# 存檔鏈裡可以「從這一版往前接著跑」的版本號。**不含 0001**：存檔的 0001 是用即時的
# `Base.metadata.create_all()` 建表（就是要修的那個 bug 本身），重放它只會直接建出現在的 schema，
# 讓後面每一版的 ALTER 撲空。0002 之後每一支都是寫死的 DDL，從既有資料庫往前接著跑是安全的。
# （0001 也剛好跟新鏈頭撞名，會先被 decide() 的撞名分支接走。）
LEGACY_REPLAYABLE_REVISIONS = tuple(f"{n:04d}" for n in range(2, 17))

Decision = Literal["noop", "stamp", "replay", "abort"]

BRIDGE_INSTRUCTIONS = (
    "偵測到資料庫的 alembic_version={stored!r}，但 schema 還沒有到合併前的最終狀態"
    "（缺少 {missing}），而且這個版本號不在存檔鏈可以接著跑的範圍內"
    f"（{LEGACY_REPLAYABLE_REVISIONS[0]}-{LEGACY_REPLAYABLE_REVISIONS[-1]}），沒辦法自動搭橋。\n"
    "請人工確認這個資料庫的實際狀態，需要的話參考 alembic/versions_archive_pre_squash/README.md；"
    "在確認之前這支腳本不會動你的資料庫。"
)

REPLAY_FAILED = (
    "已經用存檔鏈跑到 {target}，但 schema 仍然缺少 {missing}。這不該發生，"
    "為了不把版本號 stamp 成與實際 schema 不符的值，這裡停下來，請人工確認資料庫狀態。"
)


def decide(*, alembic_version_exists: bool, stored: str | None, new_head: str, at_legacy_head: bool,
           replayable: bool, current_chain: tuple[str, ...] = ()) -> Decision:
    """純函式：只判斷該直接升級、stamp、先補跑存檔鏈、還是中止，不碰資料庫。"""
    if not alembic_version_exists:
        return "noop"
    if stored == new_head:
        # 新舊鏈的第一支都叫 '0001'，字串撞名：schema 對得上才算真的在新鏈頭，否則不能當成已經到頭。
        return "noop" if at_legacy_head else "abort"
    if stored in current_chain or stored == SQUASH_BASE_REVISION:
        return "noop" if at_legacy_head else "abort"
    if at_legacy_head:
        return "stamp"
    return "replay" if replayable else "abort"


def alembic_config() -> Config:
    return Config("alembic.ini")


def archive_config() -> Config:
    """指向存檔鏈的設定；用來把停在中間版本的既有資料庫接著升到 0016。"""
    cfg = Config("alembic.ini")
    cfg.set_main_option("version_locations", ARCHIVE_VERSION_PATH)
    return cfg


def legacy_head_reached(conn) -> bool:
    """schema 是否已經是合併前最後一版（0016）跑完的樣子。"""
    insp = inspect(conn)
    return all(insp.has_table(name) for name in LEGACY_HEAD_SIGNATURE_TABLES)


def bridge_if_needed() -> None:
    cfg = alembic_config()
    script = ScriptDirectory.from_config(cfg)
    new_head = script.get_current_head()
    current_chain = tuple(r.revision for r in script.walk_revisions())

    with engine.connect() as conn:
        insp = inspect(conn)
        version_table_exists = insp.has_table("alembic_version")
        stored = conn.execute(text("SELECT version_num FROM alembic_version")).scalar() if version_table_exists else None
        at_legacy_head = legacy_head_reached(conn)
        missing = [t for t in LEGACY_HEAD_SIGNATURE_TABLES if not insp.has_table(t)]

    action = decide(alembic_version_exists=version_table_exists, stored=stored, new_head=new_head,
                    at_legacy_head=at_legacy_head, replayable=stored in LEGACY_REPLAYABLE_REVISIONS,
                    current_chain=current_chain)

    if action == "noop":
        return
    if action == "abort":
        sys.exit(BRIDGE_INSTRUCTIONS.format(stored=stored, missing="、".join(missing)))

    if action == "replay":
        # 停在合併前的中間版本：存檔鏈還在 repo 裡，自己接著跑完，不要叫人手動照 README 做——
        # 那種步驟一定會在展示前一天忘記。只跑 stored 之後的那幾支（都是寫死的 DDL），不碰存檔的 0001。
        print(f"[migrate] 偵測到合併前的舊版本號 {stored!r}，schema 還沒到 {LEGACY_FINAL_REVISION}"
              f"（缺少 {'、'.join(missing)}）")
        print(f"[migrate] 先用存檔的舊 migration 鏈 {ARCHIVE_VERSION_PATH} 接著升到 {LEGACY_FINAL_REVISION}…")
        command.upgrade(archive_config(), LEGACY_FINAL_REVISION)
        with engine.connect() as conn:
            still_missing = [t for t in LEGACY_HEAD_SIGNATURE_TABLES if not inspect(conn).has_table(t)]
        if still_missing:
            sys.exit(REPLAY_FAILED.format(target=LEGACY_FINAL_REVISION, missing="、".join(still_missing)))
        print(f"[migrate] 存檔鏈跑完，schema 已達 {LEGACY_FINAL_REVISION}")

    # schema 已達合併前最終狀態，只把版本號追上壓縮基準；後續新增表仍由 upgrade 執行。
    # purge=True：舊版本號（例如 '0016'）在新鏈裡已經不存在，stamp 不能先嘗試從它「算路徑」到目標
    # （會報 Can't locate revision），要直接覆蓋 alembic_version，不管它原本是什麼。
    print(f"[migrate] stamp 到壓縮基準 {SQUASH_BASE_REVISION!r}，再執行後續 migration")
    command.stamp(cfg, SQUASH_BASE_REVISION, purge=True)


def main() -> None:
    bridge_if_needed()
    command.upgrade(alembic_config(), "head")


if __name__ == "__main__":
    main()
