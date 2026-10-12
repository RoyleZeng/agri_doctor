"""從 PPM「注意事項」自由文字抽出結構化欄位。

安全採收期（PHI）與混用禁忌是植物醫生最需要一眼看到的兩件事，但 PPM 把
它們混在注意事項的流水句裡，例如：

    1.採收前14天停止施藥。
    2.不可與強酸及強鹼性藥劑混合。
    3.本藥劑混合25%賓克隆可濕性粉劑、75%三賽唑可濕性粉劑等農藥可能發生藥害。

抽不出來就回 None —— 寧可讓前端顯示「未載明，請看原文」，
也不要猜一個數字出來害人。
"""

from __future__ import annotations

import re
import unicodedata

# 「採收前14天停止施藥」「收穫前 7 天停止用藥」
_PHI_PATTERNS = [
    re.compile(r"(?:採收|收穫|採收期)前\s*(\d+)\s*天(?:以上)?\s*停(?:止|用)?(?:施藥|用藥|藥)"),
    re.compile(r"(?:採收|收穫)前\s*(\d+)\s*天"),
    re.compile(r"安全採收期\s*(?:為)?\s*(\d+)\s*天"),
]

# 混用／藥害相關的句子
_MIX_HINTS = ("混合", "混用", "藥害", "不可與", "勿與", "不宜與", "相混")

# 句子切割：中文句號、分號、換行，以及「1.」「2.」這種條列編號
_SENTENCE_SPLIT = re.compile(r"[。；;\n]+|(?<=[。])\s*(?=\d+\.)")
_LEADING_NUM = re.compile(r"^\s*\d+\s*[.、)]\s*")


def _normalize(text: str) -> str:
    """全形數字轉半形，否則「１４天」抓不到。"""
    return unicodedata.normalize("NFKC", text)


def extract_phi_days(precautions: str | None) -> int | None:
    """回傳安全採收期天數；文字裡沒寫就回 None。

    同一段可能出現多個數字（例如不同作物別），取最保守（最大）的那個。
    """
    if not precautions:
        return None
    text = _normalize(precautions)

    found: list[int] = []
    for pattern in _PHI_PATTERNS:
        found.extend(int(m) for m in pattern.findall(text))
        if found:
            break        # 只用第一個命中的樣式，避免寬鬆樣式撿到不相干的數字

    if not found:
        return None
    return max(found)


def extract_mix_warnings(precautions: str | None) -> list[str]:
    """挑出談混用禁忌／藥害的句子，原文保留。"""
    if not precautions:
        return []

    warnings: list[str] = []
    for raw in _SENTENCE_SPLIT.split(_normalize(precautions)):
        sentence = _LEADING_NUM.sub("", raw).strip()
        if not sentence:
            continue
        if any(hint in sentence for hint in _MIX_HINTS):
            warnings.append(sentence)
    return warnings


def split_precautions(precautions: str | None) -> list[str]:
    """把注意事項拆成條列，供前端逐條顯示。"""
    if not precautions:
        return []
    out: list[str] = []
    for raw in _SENTENCE_SPLIT.split(_normalize(precautions)):
        item = _LEADING_NUM.sub("", raw).strip()
        if item:
            out.append(item)
    return out
