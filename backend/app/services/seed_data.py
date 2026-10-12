"""跨來源命名對照的種子資料。

三個來源對同一個作物不一定用同一個名字，最典型的是芒果：

    植保系統  檬果
    高改場    芒果   （但它自己的病蟲害欄又寫「檬果細蛾」「檬果壯鋏普癭蚋」）
    食藥署    芒果

沒有這張對照表，高改場的芒果推薦永遠接不上植保系統的檬果核准登記，
比對表上就會整片顯示「高改場未列入推薦」—— 錯得很安靜，也很難察覺。

收錄原則
--------
只放**已經實際比對過兩邊資料確認同一種作物**的對照。
拿不準的不要放進來，讓它進 match_issues 由人工判斷，
因為錯誤的別名會讓兩種不同作物的容許量互相污染，比漏掉更危險。
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActiveIngredient, Crop, CropAlias, SourceName

log = logging.getLogger(__name__)

# canonical 名稱 -> 其他來源用的寫法
CROP_ALIASES: dict[str, tuple[str, ...]] = {
    # 植保系統寫「檬果」，高改場與食藥署寫「芒果」。
    # 已比對兩邊病蟲害清單確認為同一作物（炭疽病、疫病、白粉病、蒂腐病、東方果實蠅…）。
    "檬果": ("芒果",),
    # 植保系統的作物分群名稱較長，食藥署與高改場常用簡稱。
    "葫蘆科瓜果類": ("瓜果類",),
    "葫蘆科瓜菜類": ("瓜菜類",),
    "十字花科包葉菜類": ("包葉菜類",),
    "十字花科小葉菜類": ("小葉菜類",),
    "菊科小葉菜類": ("小葉菜類",),
    "柑桔類": ("柑橘類", "柑桔"),
    "紅龍果": ("火龍果",),
    "番石榴": ("芭樂",),
    "蓮霧": ("洋蒲桃",),
}


def seed_crop_aliases(db: Session) -> int:
    """把對照表寫進 crop_aliases。已存在的略過，不覆蓋人工調整過的資料。"""
    added = 0
    for canonical, aliases in CROP_ALIASES.items():
        crop = db.scalars(select(Crop).where(Crop.name == canonical)).first()
        if crop is None:
            # 這個作物還沒被爬到，等下次正規化再補。
            continue
        for alias in aliases:
            if alias == crop.name:
                continue
            # 別名不能同時是另一個作物的本名，否則兩邊資料會互相污染。
            clash = db.scalars(select(Crop).where(Crop.name == alias)).first()
            if clash is not None and clash.id != crop.id:
                log.warning(
                    "略過別名 %s -> %s：%s 已經是另一個作物的本名（id=%s）",
                    alias, canonical, alias, clash.id,
                )
                continue
            exists = db.scalars(
                select(CropAlias).where(
                    CropAlias.crop_id == crop.id, CropAlias.alias == alias
                )
            ).first()
            if exists is None:
                db.add(
                    CropAlias(crop_id=crop.id, alias=alias, source=SourceName.PPM)
                )
                added += 1

    db.flush()
    if added:
        log.info("seeded %d crop aliases", added)
    return added


# PPM 用的個別成分英文名 -> 食藥署以群組訂容許量的那個名稱。
#
# 起因：鋅錳乃浦(mancozeb)、免得爛(maneb) 這些常用殺菌劑在食藥署容許量表裡
# 查不到，因為表上只列「Dithiocarbamates 二硫代胺基甲酸鹽類」。沒有這層對照，
# 這些藥會全被標成「查無容許量」，變成大量假警報。
#
# 這裡收錄的是化學分類上明確的群組成員，不做臆測。
INGREDIENT_MRL_GROUPS: dict[str, tuple[str, ...]] = {
    "Dithiocarbamates": (
        "mancozeb", "maneb", "zineb", "ziram", "thiram",
        "propineb", "metiram", "ferbam", "nabam",
    ),
}


def seed_ingredient_groups(db: Session) -> int:
    """把個別成分掛到食藥署的群組成分底下。"""
    linked = 0
    for group_name, members in INGREDIENT_MRL_GROUPS.items():
        group = db.scalars(
            select(ActiveIngredient).where(
                ActiveIngredient.english_name.ilike(group_name)
            )
        ).first()
        if group is None:
            log.warning("食藥署資料裡找不到群組成分 %s，略過", group_name)
            continue

        for member_name in members:
            member = db.scalars(
                select(ActiveIngredient).where(
                    ActiveIngredient.english_name.ilike(member_name)
                )
            ).first()
            if member is None or member.id == group.id:
                continue
            if member.mrl_group_id != group.id:
                member.mrl_group_id = group.id
                linked += 1

    db.flush()
    if linked:
        log.info("linked %d ingredients to MRL groups", linked)
    return linked
