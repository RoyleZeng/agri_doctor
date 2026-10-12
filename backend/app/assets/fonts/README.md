# 診斷書內嵌字型

本目錄隨服務部署 `SourceHanSerifTC-Regular.otf`（思源宋體繁體中文）。
來源：https://github.com/adobe-fonts/source-han-serif/tree/release/OTF/TraditionalChinese
原始下載：https://raw.githubusercontent.com/adobe-fonts/source-han-serif/release/OTF/TraditionalChinese/SourceHanSerifTC-Regular.otf
授權：同目錄 LICENSE.txt（SIL Open Font License 1.1）。

HTML 的 `@font-face` 使用檔案內容組成 data URL，離線載入；Chromium 輸出嵌入字型子集。
字型缺失時產出失敗，不回退至系統字型。更新字型或版型後應重新進行 PDF 排版與中文擷取測試。

## 頁碼子集字型 `AgriPageNumber.otf`

由 `SourceHanSerifTC-Regular.otf` 子集化，只含數字與「第頁共／」，供 PDF 頁尾使用（Chromium 頁尾載入不了內嵌字型，
只能用系統已安裝的字型，由 Dockerfile 安裝到 fontconfig）。依 SIL OFL 1.1，修改版不得沿用保留字型名稱，所以家族名稱改為
`AgriPageNumber`。重新產生：`backend/scripts/make_page_number_font.py`。
