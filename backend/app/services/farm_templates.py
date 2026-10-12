"""Curated summaries, not machine-inferred prescriptions.

Day offsets are EDITORIAL PLANNING ESTIMATES. Sources support agronomic actions and
observations, not exact dates, local disease probability, fertilizer doses or efficacy.
Persist generated events and source metadata so future source refreshes cannot rewrite plans.
"""
import json
from datetime import timedelta
from pathlib import Path

VERSION = "2026-09-08.v1"
DISCLAIMER = "日期依種植日與規劃長度估算，請依品種、季節及田間生育調整；巡查區間不代表已發病或發生機率。"


def event(kind, title, start, end, detail, *sources):
    return dict(kind=kind, title=title, start=start, end=end, detail=detail, source_ids=list(sources))


TEMPLATES = [
    dict(key="rice", name="水稻", aliases=["稻米", "稻作"], emoji="🌾", days=120, anchor="插秧日", scope="移植稻作；期作與品種會改變日數", events=[
        event("stage", "成活期", 0, 10, "觀察秧苗成活與缺株，階段邊界為規劃估計。", "rice-care"),
        event("stage", "分蘗期", 11, 55, "觀察稻叢分蘗與葉色；生育進度因期作而異。", "rice-care"),
        event("stage", "幼穗形成・孕穗", 56, 75, "以實際幼穗形成判斷進入生殖生長。", "rice-care"),
        event("stage", "抽穗開花", 76, 90, "記錄田間抽穗情形，供調整後續管理。", "rice-care"),
        event("stage", "充實・成熟", 91, 120, "觀察穀粒成熟，實際採收日需田間確認。", "rice-care"),
        event("care", "確認成活與田水", 0, 10, "查看缺株及田水。插秧後的水分管理需配合秧苗狀況。", "rice-care"),
        event("care", "評估曬田時機", 30, 50, "分蘗盛期評估曬田，先確認實際分蘗與田間水分。", "rice-care"),
        event("care", "檢查孕穗期灌排水", 56, 75, "幼穗形成與抽穗開花階段留意供水；依現場條件調整。", "rice-care"),
        event("nutrition", "檢視葉色與肥培紀錄", 15, 30, "先確認生長與施肥紀錄，再與植物醫生討論肥培；不依日期直接施肥。", "rice-care"),
        event("care", "確認成熟與採收安排", 110, 120, "查看成熟度，安排採收，並核對已使用藥劑的安全採收期。", "rice-care"),
        event("pest", "稻熱病・紋枯病", 25, 120, "分蘗盛期起，溫濕度合適時可能發生並延續至完熟；巡田記錄葉片及葉鞘異常，交由植物醫生判讀。", "rice-pests"),
        event("pest", "二化螟（枯心）", 10, 55, "分蘗階段留意枯心與蟲害跡象，記錄受害位置及株數。", "rice-pests"),
        event("pest", "二化螟（白穗）", 65, 105, "孕穗至乳熟階段留意白穗；異常穗需進一步鑑別原因。", "rice-pests"),
    ]),
    dict(key="tomato", name="番茄", aliases=["蕃茄", "小番茄", "小果番茄"], emoji="🍅", days=150, anchor="定植日", scope="以小果番茄管理資料為主；採收可持續多批", events=[
        event("stage", "定植・緩苗", 0, 14, "確認移植後的生長狀況。", "tomato-care"),
        event("stage", "營養生長", 15, 35, "觀察枝葉生長並安排支架。", "tomato-care"),
        event("stage", "開花著果", 36, 60, "記錄開花與著果；日期僅作初始排程。", "tomato-care"),
        event("stage", "果實發育", 61, 85, "觀察果實發育與植株負載。", "tomato-care"),
        event("stage", "分批採收", 86, 150, "採收與開花著果可能同時持續；可依田況調整區間。", "tomato-care"),
        event("care", "檢查苗株與排水", 0, 14, "記錄苗株成活，檢查田區排水與水分供應。", "tomato-care"),
        event("care", "架設支架・確認整枝", 15, 35, "配合栽培方式立支架及整枝，後續持續檢查固定情形。", "tomato-care"),
        event("nutrition", "評估第一次追肥", 20, 30, "參考資料在定植後20～30天安排首次追肥；實際種類、用量依土壤與生長評估。", "tomato-care"),
        event("care", "確認分批採收安排", 86, 100, "依成熟程度安排採收，並核對用藥紀錄與安全採收期。", "tomato-care"),
        event("pest", "粉蝨・捲葉病毒徵兆", 0, 150, "翻看葉背有無粉蝨，記錄頂葉黃化或捲曲；徵兆需進一步診斷。", "tomato-care"),
        event("pest", "番茄夜蛾", 36, 150, "開花結果後檢查花器、果蒂及果實有無取食或蛀孔。", "tomato-care"),
    ]),
    dict(key="cabbage", name="甘藍", aliases=["高麗菜", "包心菜", "結球甘藍"], emoji="🥬", days=75, anchor="定植日", scope="結球甘藍；品種與季節影響採收天數", events=[
        event("stage", "定植・成活", 0, 10, "確認定植苗成活，日數為規劃估計。", "cabbage-care"),
        event("stage", "蓮座葉生長", 11, 30, "觀察本葉數與植株生長。", "cabbage-care"),
        event("stage", "結球期", 31, 59, "以實際開始包心作為階段判斷。", "cabbage-care"),
        event("stage", "採收期", 60, 75, "官方資料概述約栽種50～80天可採收，依品種與結球程度確認。", "cabbage-care"),
        event("care", "檢查定植與成活", 0, 7, "確認苗株與水分，記錄缺株情形。", "cabbage-care"),
        event("care", "中耕除草檢查", 12, 25, "在本葉15～20葉以前安排中耕、除草與培土。", "cabbage-care"),
        event("nutrition", "檢視追肥需求", 12, 25, "配合葉片生長及土壤條件評估追肥，先檢查既有施肥紀錄。", "cabbage-care"),
        event("care", "確認結球期供水", 31, 45, "結球期避免乾旱；雨後檢查積水並維持排水。", "cabbage-care"),
        event("care", "確認結球與採收", 60, 75, "依結球狀況安排採收，避免將估計日期當成成熟判定。", "cabbage-care"),
        event("pest", "小菜蛾", 0, 75, "查看葉背與葉片重疊處，留意透明薄膜狀食痕、孔洞及幼蟲。", "cabbage-pests"),
        event("pest", "黑腐病", 0, 75, "溫暖潮濕時加強查看葉緣V形黃褐色病斑及葉脈變黑；記錄並請植物醫生鑑別。", "cabbage-disease"),
    ]),
]


def source_index():
    path = Path(__file__).resolve().parents[1] / "data" / "planning_sources.json"
    return json.loads(path.read_text()) if path.exists() else []


def catalog():
    return dict(version=VERSION, notice=DISCLAIMER, sources=source_index(), crops=[{k: v for k, v in t.items() if k != "events"} for t in TEMPLATES])


def generate_events(key, planted_on, end_on, template=None):
    template = template or next((t for t in TEMPLATES if t["key"] == key), None)
    if not template:
        return []
    days = (end_on - planted_on).days
    # Inclusive intervals; scaling contiguous stage boundaries avoids overlapping stages.
    result = []
    for e in template["events"]:
        start = round(e["start"] * (days + 1) / (template["days"] + 1))
        end = max(start, round((e["end"] + 1) * (days + 1) / (template["days"] + 1)) - 1)
        result.append({k: v for k, v in e.items() if k not in ("start", "end")} | dict(start_on=planted_on + timedelta(days=start), end_on=planted_on + timedelta(days=min(end, days))))
    return result
