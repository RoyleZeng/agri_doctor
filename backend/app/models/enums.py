import enum


class SourceName(str, enum.Enum):
    PPM = "ppm"          # 植物保護資訊系統（藥毒所 ACRI）
    FDA = "fda"          # 衛福部食藥署 農藥殘留容許量
    KDAIS = "kdais"      # 高雄區農業改良場 作物安全用藥資訊


class SnapshotStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SCHEMA_DRIFT = "schema_drift"   # 欄位結構與預期不符，資料不採用


class PestKind(str, enum.Enum):
    DISEASE = "disease"      # 病害
    INSECT = "insect"        # 蟲害
    WEED = "weed"            # 雜草
    OTHER = "other"          # 生長調節、檢疫處理等


class Confidence(str, enum.Enum):
    HIGH = "high"
    LOW = "low"              # PDF 解析可疑，需人工校對
    VERIFIED = "verified"    # 人工校對過


class IssueType(str, enum.Enum):
    MRL_MISSING = "MRL_MISSING"
    MRL_MISMATCH = "MRL_MISMATCH"
    KDAIS_NOT_RECOMMENDED = "KDAIS_NOT_RECOMMENDED"
    KDAIS_ONLY = "KDAIS_ONLY"
    PPM_COVERAGE_GAP = "PPM_COVERAGE_GAP"
    INGREDIENT_UNMATCHED = "INGREDIENT_UNMATCHED"
    CROP_CATEGORY_UNMAPPED = "CROP_CATEGORY_UNMAPPED"
    BANNED_INGREDIENT = "BANNED_INGREDIENT"          # 食藥署公告禁用，硬性擋下
    BAN_ANNOUNCED = "BAN_ANNOUNCED"
    DILUTION_MISMATCH = "DILUTION_MISMATCH"          # 兩源稀釋倍數不同
    PHI_MISMATCH = "PHI_MISMATCH"                    # 兩源安全採收期不同
    KDAIS_PENDING_REVIEW = "KDAIS_PENDING_REVIEW"    # PDF 解析待人工校對


class Severity(str, enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"
