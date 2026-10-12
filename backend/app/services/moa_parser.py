"""農業開放資料的純解析器：市場報價、休市與成本文件不能混成同一種數值。"""
from __future__ import annotations

from datetime import date
import math
import re
from urllib.parse import urlparse


class MoaDataError(ValueError):
    def __init__(self, status='unavailable'):
        self.status = status
        super().__init__(status)  # 不把可能含金鑰的上游原文塞進日誌。


def unwrap(body):
    if isinstance(body, list):
        if not all(isinstance(row, dict) for row in body): raise MoaDataError('schema_drift')
        return body, False
    if not isinstance(body, dict): raise MoaDataError('schema_drift')
    if body.get('RS') != 'OK':
        message = str(body).lower()
        if any(word in message for word in ('limit', 'quota', '上限', '超過')): raise MoaDataError('rate_limited')
        if any(word in message for word in ('api_key', 'key', '授權', '非會員', '只限回傳第一頁')): raise MoaDataError('key_required')
        raise MoaDataError()
    rows, next_page = body.get('Data'), body.get('Next')
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows): raise MoaDataError('schema_drift')
    if next_page not in (True, False, 'true', 'false', 'True', 'False'): raise MoaDataError('schema_drift')
    return rows, next_page in (True, 'true', 'True')


def number(raw):
    if raw is None or isinstance(raw, bool): return None
    try:
        n = float(str(raw).replace(',', '').strip())
        return n if math.isfinite(n) and n >= 0 else None
    except (TypeError, ValueError): return None


def trade_date(raw):
    match = re.fullmatch(r'(\d{2,4})[./-](\d{1,2})[./-](\d{1,2})', str(raw or '').strip())
    if not match: return None
    year, month, day = map(int, match.groups())
    if year < 1911: year += 1911
    try: return date(year, month, day).isoformat()
    except ValueError: return None


def parse_prices(raw, start, end):
    rows, closed, ignored = {}, {}, 0
    for row in raw:
        required = {'TransDate', 'CropCode', 'CropName', 'MarketCode', 'MarketName', 'Avg_Price', 'Trans_Quantity'}
        if not required.issubset(row): raise MoaDataError('schema_drift')
        day = trade_date(row['TransDate'])
        if day is None:
            ignored += 1; continue
        if not start <= day <= end: continue
        market_code = str(row['MarketCode'])
        market = str(row['MarketName'])
        category = str(row.get('TcType') or '')
        if row['CropName'] == '休市':
            closed[(day, market_code, category)] = dict(date=day, market_code=market_code, market=market, category=category)
            continue  # 休市的零元不是交易價格，也不放進走勢。
        if not row['CropCode'] or row['CropCode'] == '-':
            ignored += 1; continue
        quantity = number(row['Trans_Quantity'])
        item = dict(date=day, crop_code=str(row['CropCode']), crop=str(row['CropName']), market_code=market_code,
                    market=market, category=category, volume_kg=quantity,
                    average=number(row['Avg_Price']) if quantity and quantity > 0 else None,
                    upper=number(row.get('Upper_Price')) if quantity and quantity > 0 else None,
                    middle=number(row.get('Middle_Price')) if quantity and quantity > 0 else None,
                    lower=number(row.get('Lower_Price')) if quantity and quantity > 0 else None)
        key = (day, item['crop_code'], market_code, category)
        if key in rows and rows[key] != item: raise MoaDataError('schema_drift')
        rows[key] = item
    return dict(rows=sorted(rows.values(), key=lambda r: (r['date'], r['crop_code'], r['market_code']), reverse=True),
                closed=sorted(closed.values(), key=lambda r: (r['date'], r['market_code']), reverse=True), ignored_rows=ignored)


def document_url(value):
    text = str(value or '').strip()
    parsed = urlparse(text)
    if parsed.scheme not in ('http', 'https') or parsed.hostname != 'agrcost.afa.gov.tw' or parsed.username or parsed.password:
        return None
    if not re.search(r'\.(pdf|docx?|xlsx?)$', parsed.path, re.I): return None
    return text


def parse_costs(raw):
    out = []
    for row in raw:
        if not {'作物名稱', '年度', '成本檔URL', '群組'}.issubset(row): raise MoaDataError('schema_drift')
        year = number(row['年度'])
        year = int(year) if year is not None and year.is_integer() and 1900 <= year <= 2200 else None
        title, group = str(row['作物名稱']), str(row['群組'])
        # 名稱比群組優先：實測有「範例」放在雜糧群組，不能因此當成調查結果。
        if '範例' in title or '範例' in group: kind = '填表範例'
        elif '調查表' in title or group == '調查表': kind = '調查表'
        elif any(k in title + group for k in ('需知', '須知', '注意', '手冊')): kind = '填表說明'
        else: kind = '成本相關文件'
        out.append(dict(title=title, year=year, group=group, kind=kind, url=document_url(row['成本檔URL'])))
    return sorted(out, key=lambda r: (r['year'] or 0, r['title']), reverse=True)


# 只用來找公開市場／成本文件候選，不用來推斷品種相同或變更用藥。
QUERY_ALIASES = {'高麗菜': '甘藍', '蕃茄': '番茄', '西紅柿': '番茄'}
def crop_query(value):
    value = value.strip()
    return QUERY_ALIASES.get(value, value)
