"""農業部資料用途與解析契約；補充查核資料，不提升成處方合法性依據。"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unicodedata
from app.services.moa_parser import MoaDataError

@dataclass(frozen=True)
class Dataset:
    name: str
    group: str
    keys: tuple[str, ...]
    titles: tuple[str, ...]
    hours: int = 24
    exact: tuple[str, ...] = ()

CATALOG = {
 'PesticideType': Dataset('農藥型別','施肥用藥',('PesticideID','PesticideName'),('PesticideName','PesticideProductName','PesticideCoaID'),exact=('PesticideName','PesticideEnName','PesticideID','PesticideProductName','PesticideCoaID')),
 'PesticideCompanyType': Dataset('農藥廠商','施肥用藥',('Company_Name','Company_Addr'),('Company_Name',)),
 'PesticideDataQueryType': Dataset('農藥許可證與撤銷資料','施肥用藥',('Permit','PermitNumber','ChineseName','ExpireDate','RevocationType'),('ChineseName','contents','formCode','BrandName','Permit','PermitNumber'),exact=('ChineseName','EnName','PesticideCode','BrandName','PermitNumber','_permit')),
 'PesticideManualType': Dataset('農藥名稱手冊','施肥用藥',('Name','CompositPercentage','PesticidesForm'),('Name','CompositPercentage','PesticidesForm','ProductName'),exact=('Name','EnName','ProductName')),
 'FertilizerIntegrationInfoType': Dataset('肥料登記與成分','施肥用藥',('FertilizerID','BrandName','Element','ExpiryDate'),('BrandName','ItemName','FertilizerID')),
 'ImportantAgriculturalPestDiagnosticsType': Dataset('重要農業害蟲診斷圖鑑','病蟲害',('ID','PestName_Ch','Crop_Name'),('PestName_Ch','Crop_Name')),
 'TreePestInfoType': Dataset('樹木病蟲害案例','病蟲害',('CaseName','TreeType','symptom'),('CaseName','TreeType')),
 'PlantEpidemicType': Dataset('植物疫情公告','病蟲害',('Subject','PubDate','Body'),('Subject',),6),
 'PestDiseaseDiagnosisServiceType': Dataset('病蟲害診斷問答','病蟲害',('ProductName','Question','Answer'),('ProductName','Type')),
 'FruitVegetalePestControlType': Dataset('蔬果重要害蟲防疫旬報','病蟲害',('City','Town','PestName','Year','TenDays'),('PestName','City','Town','Year','Month','TenDays')),
 'TaiwanMeteorologicalStationInformationType': Dataset('氣象測站名錄','農業氣象',('Station_ID','Station_name','CITY'),('Station_name','CITY','TOWN'),168),
 'TaiwanRainfallStationInformationType': Dataset('雨量測站名錄','農業氣象',('Station_ID','Station_name','CITY'),('Station_name','CITY','TOWN'),168),
 'AutoWeatherStationType': Dataset('自動氣象站觀測','農業氣象',('Station_ID','TIME','TEMP','CITY'),('Station_name','CITY','TOWN'),1),
 'AutoRainfallStationType': Dataset('自動雨量站觀測','農業氣象',('Station_ID','TIME','RAIN','CITY'),('Station_name','CITY','TOWN'),1),
}
FIELDS = json.loads((Path(__file__).resolve().parents[1]/'data/moa_fields.json').read_text())
NOTICES = {
 '施肥用藥':'許可證、手冊與肥料登記是補充查核資料。許可證仍在有效期不代表可用於所有作物；請核對撤銷狀態，開藥仍須三源比對與 PHI 檢查。肥料成分不等於田區施肥量。',
 '病蟲害':'圖鑑、歷史案例與區域公告是輔助判讀，可能同時有多種病蟲害。歷史防治文字可能已過時，不直接帶入處方；附近公告也不代表本田已發病。',
 '農業氣象':'這是測站觀測，不是田區感測或未來預報。請核對測站位置及觀測時間；過期、缺測資料不產生當下施藥或灌溉建議。',
}
NUMERIC = {'TEMP','HUMD','WDSD','WDIR','PRES','SUN','H_24R','RAIN','MIN_10','HOUR_3','HOUR_6','HOUR_12','HOUR_24','NOW','ELEV','D_TX','D_TN'}
SENTINELS = {-99, -999, -9999, -99999}

def normalize(text):
    return ''.join(unicodedata.normalize('NFKC',str(text or '')).split()).casefold()

def weather_number(raw, field):
    import math
    try: value=float(raw)
    except (TypeError,ValueError): return None
    if not math.isfinite(value) or value in SENTINELS:return None
    if field not in ('TEMP','ELEV','D_TX','D_TN') and value<0:return None
    return value

def parse_catalog(endpoint, rows):
    spec=CATALOG[endpoint]
    parsed=[]
    for raw in rows:
        if not set(spec.keys).issubset(raw):raise MoaDataError('schema_drift')
        row={k:(v.strip() if isinstance(v,str) else v) for k,v in raw.items()}
        if endpoint.startswith('Auto'):
            for field in NUMERIC.intersection(row): row[field]=weather_number(row[field],field)
        parsed.append(row)
    if not rows:raise MoaDataError('empty_dataset')  # 全量參考目錄忽然清空不能清掉既有資料。
    return parsed

def title_for(endpoint, row):
    return ' · '.join(str(row.get(k) or '') for k in CATALOG[endpoint].titles if row.get(k)) or '名稱未載明'

def observed_at(value):
    text=str(value or '').strip()
    for fmt in ('%Y/%m/%d %H:%M','%Y/%m/%d %H:%M:%S','%Y-%m-%d %H:%M:%S','%Y-%m-%dT%H:%M:%S'):
        try:return datetime.strptime(text,fmt).replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:pass
    return None

def observed_state(row, now):
    stamp=observed_at(row.get('TIME'))
    if stamp is None:return 'unknown'
    age=(now-stamp).total_seconds()
    return 'recent' if 0<=age<=3*3600 else 'outdated'
