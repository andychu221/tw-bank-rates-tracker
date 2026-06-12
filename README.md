# 🏦 台灣央行牌告利率自動追蹤器 (Taiwan Bank Rates Tracker)

這是一個全自動化的開源專案，每日定時從「[台灣中央銀行牌告利率資訊網](https://cpx.cbc.gov.tw/BIRWEB/Range/RangeSelect)」抓取各大銀行的定期存款利率，並將結構化的歷史數據保存在 GitHub 儲存庫中。

## ✨ 專案亮點

* **🤖 全自動化排程**：透過 GitHub Actions 每日早上 09:30 (TST) 自動執行爬蟲，無須人工介入。
* **⚡ 輕量化 API 更新**：跳過繁瑣的 `git clone` 與 `git push`，使用 GitHub REST API 直接將資料做 Base64 編碼後更新檔案。
* **🧹 智慧去重複**：自動比對歷史資料，若同一天、同一家銀行、同額度條件下已有紀錄，則僅保留最新的一筆。
* **📊 友善的資料結構**：採用標準的「扁平化 Array of Objects」JSON 格式，極度適合使用 Pandas 讀取或餵給前端框架 (Vue/React) 渲染儀表板。

## 📁 資料結構 (Data Structure)

爬取下來的歷史資料會依照「年份」分檔存放於 `data/` 目錄下（例如：`data/rates_2026.json`）。

**JSON 範例：**
```json
[
  {
    "Date": "2026-06-12",
    "Bank": "004臺銀",
    "Item": "定期存款",
    "Term": "1年",
    "Quota": "大額",
    "Rate": "0.770"
  }
]
