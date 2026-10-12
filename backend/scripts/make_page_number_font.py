"""產生 PDF 頁碼用的子集字型 app/assets/fonts/AgriPageNumber.otf（只含數字與「第頁共／」）。

為什麼要另一個字型：Chromium 產 PDF 時，頁尾範本載入不了 data URL 內嵌字型，只能用系統已安裝的字型。
Dockerfile 把這個檔裝進映像檔的 fontconfig；映像檔是我們自己控制的，和內嵌字型是同一種保證。

來源是隨專案附帶的思源宋體（SIL OFL 1.1）。OFL 規定修改版不得沿用保留字型名稱，所以子集改名為 AgriPageNumber。
需要 fonttools（只在產生字型時用，不列入 requirements）：

    pip install fonttools && python scripts/make_page_number_font.py
"""
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

FONTS = Path(__file__).resolve().parents[1] / "app" / "assets" / "fonts"
TEXT = "第頁共／/0123456789 "
FAMILY = "AgriPageNumber"


def main() -> None:
    font = TTFont(FONTS / "SourceHanSerifTC-Regular.otf")
    options = subset.Options()
    options.name_IDs = ["*"]
    options.name_languages = ["*"]
    subsetter = subset.Subsetter(options)
    subsetter.populate(text=TEXT)
    subsetter.subset(font)
    for record in font["name"].names:
        if record.nameID in (1, 4, 16, 18):
            record.string = FAMILY
        elif record.nameID == 6:
            record.string = f"{FAMILY}-Regular"
        elif record.nameID in (2, 17):
            record.string = "Regular"
        elif record.nameID == 3:
            record.string = f"{FAMILY}-Regular;subset-of-SourceHanSerifTC"
    cff = font["CFF "].cff
    cff.fontNames = [f"{FAMILY}-Regular"]
    top = cff.topDictIndex[0]
    top.FamilyName = FAMILY
    top.FullName = FAMILY
    out = FONTS / f"{FAMILY}.otf"
    font.save(out)
    print(out, out.stat().st_size, "bytes")


if __name__ == "__main__":
    main()
