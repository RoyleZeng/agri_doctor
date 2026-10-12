from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CropOut(ORMModel):
    id: int
    name: str
    scientific_name: str | None = None
    fda_category: str | None = None
    # 實際用來查容許量的作物類別候選（由具體到概括）
    fda_categories: list[str] = []
    aliases: list[str] = []
    description: str | None = None


class PestOut(ORMModel):
    id: int
    name: str
    kind: str
    scientific_name: str | None = None
    english_name: str | None = None


class SuggestOut(ORMModel):
    id: int
    name: str
    matched_on: str | None = None      # 命中的是本名還是別名
    alias: str | None = None


class MrlOut(ORMModel):
    ingredient_id: int | None = None
    ingredient_name: str
    ingredient_english: str | None = None
    limit_ppm: Decimal | None = None
    limit_text: str | None = None
    matched_category: str | None = None
    match_basis: str | None = None
    group_name: str | None = None
    note: str | None = None
    found: bool


class IssueOut(ORMModel):
    issue_type: str
    severity: str
    summary: str
    detail: dict = {}


class RowSourceInfoOut(ORMModel):
    """這一列在某來源的涵蓋狀況與版本。

    `version` 只有高改場會有值（PDF 修訂日期）；植保系統與食藥署的網頁
    沒有公告版本號，前端要顯示成「以抓取時間為準」而不是假裝有版本。
    """

    present: bool = False
    version: str | None = None
    version_label: str | None = None
    document: str | None = None
    fetched_at: str | None = None
    snapshot_id: int | None = None
    detail: str | None = None
    url: str | None = None


class ComparisonRowOut(ORMModel):
    registration_id: int
    product_id: int
    product_name: str
    concentration: str | None = None
    formulation: str | None = None
    english_composition: str | None = None

    dilution_text: str | None = None
    dilution_value: int | None = None
    dose_per_hectare: str | None = None
    application_method: str | None = None
    precautions: list[str] = []
    phi_days: int | None = None
    mix_warnings: list[str] = []

    moa_codes: list[str] = []
    ppm_mrl_text: str | None = None
    source_url: str | None = None

    mrls: list[MrlOut] = []
    kdais_recommended: bool = False
    kdais_entries: list[dict] = []
    banned: bool = False
    issues: list[IssueOut] = []
    sources: dict[str, RowSourceInfoOut] = {}


class RotationGroupOut(ORMModel):
    code: str
    label: str
    product_names: list[str] = []


class RotationOut(ORMModel):
    groups: list[RotationGroupOut] = []
    unknown_products: list[str] = []
    note: str = ""


class SourceDocumentOut(ORMModel):
    title: str
    crop_text: str | None = None
    revision_date: str | None = None
    pdf_url: str | None = None


class SourceInfoOut(ORMModel):
    label: str
    fetched_at: str | None = None
    row_count: int = 0
    status: str = "never"
    snapshot_id: int | None = None
    # 來源文件本身是否帶版本號；只有高改場的 PDF 有修訂日期。
    has_document_version: bool = False
    documents: list[SourceDocumentOut] = []
    complete: bool = False
    coverage: dict | None = None


class ComparisonOut(ORMModel):
    crop: CropOut
    pest: PestOut
    profile: dict | None = None
    rows: list[ComparisonRowOut] = []
    rotation: RotationOut
    issues: list[IssueOut] = []
    sources: dict[str, SourceInfoOut] = {}
    coverage: dict = {}
