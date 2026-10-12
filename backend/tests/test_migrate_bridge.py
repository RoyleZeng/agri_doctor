"""app/db/migrate.py 的 decide() 是純函式：判斷既有資料庫該直接升級、stamp 搭橋、先補跑存檔鏈、
還是中止，離線可測，不需要真的資料庫。實際跑存檔鏈與資料保留是另外用拋棄式 PostgreSQL 驗證的
（見 step 9 報告），那需要真的資料庫，不放進離線測試。
"""
from app.db.migrate import LEGACY_REPLAYABLE_REVISIONS, decide


def test_brand_new_database_is_noop():
    # 沒有 alembic_version 表：全新資料庫，交給正常 upgrade head。
    assert decide(alembic_version_exists=False, stored=None, new_head="0001", at_legacy_head=False,
                  replayable=False) == "noop"


def test_already_on_new_head_with_matching_schema_is_noop():
    assert decide(alembic_version_exists=True, stored="0001", new_head="0001", at_legacy_head=True,
                  replayable=False) == "noop"


def test_stored_matches_new_head_but_schema_mismatched_aborts():
    # 版本號字串剛好撞名（新舊鏈的第一支都叫 '0001'），但 schema 明顯不是新鏈頭該有的樣子——
    # 不能照字面意思當成「已經到頭」，保守起見中止。存檔的 0001 也無法重放，所以不走 replay。
    assert decide(alembic_version_exists=True, stored="0001", new_head="0001", at_legacy_head=False,
                  replayable=False) == "abort"


def test_old_revision_with_legacy_head_schema_stamps():
    # 合併前的舊版本號（例如跑完 0016 的資料庫），schema 已經是最終狀態：直接 stamp，不重跑 DDL。
    for stored in ("0009", "0016"):
        assert decide(alembic_version_exists=True, stored=stored, new_head="0001", at_legacy_head=True,
                      replayable=True) == "stamp"


def test_middle_revision_with_incomplete_schema_replays_archive_chain():
    # 停在合併前的中間版本（例如 0009）：存檔鏈接得上，自己補跑到 0016，不要求人手動處理。
    assert decide(alembic_version_exists=True, stored="0009", new_head="0001", at_legacy_head=False,
                  replayable=True) == "replay"


def test_unknown_revision_with_incomplete_schema_aborts():
    # 版本號不在存檔鏈可接續的範圍（例如別的分支或人工亂填的值）：不猜，中止。
    assert decide(alembic_version_exists=True, stored="beef1234", new_head="0001", at_legacy_head=False,
                  replayable=False) == "abort"


def test_replayable_revisions_exclude_the_unreplayable_first_one():
    # 存檔的 0001 是即時 metadata 版（要修的 bug 本身），重放它會直接建出現在的 schema，不能列入。
    assert "0001" not in LEGACY_REPLAYABLE_REVISIONS
    assert LEGACY_REPLAYABLE_REVISIONS[0] == "0002" and LEGACY_REPLAYABLE_REVISIONS[-1] == "0016"


def test_current_chain_upgrades_without_stamping_past_new_tables():
    assert decide(alembic_version_exists=True,stored='0001',new_head='moa_local_0002',at_legacy_head=True,
        replayable=False,current_chain=('0001','moa_local_0002'))=='noop'
    assert decide(alembic_version_exists=True,stored='moa_local_0002',new_head='future',at_legacy_head=True,
        replayable=False,current_chain=('0001','moa_local_0002','future'))=='noop'


def test_legacy_bridge_stamps_only_the_squash_baseline():
    from app.db.migrate import SQUASH_BASE_REVISION
    assert SQUASH_BASE_REVISION=='0001'
    assert decide(alembic_version_exists=True,stored='0016',new_head='moa_local_0002',at_legacy_head=True,
        replayable=True,current_chain=('0001','moa_local_0002'))=='stamp'
