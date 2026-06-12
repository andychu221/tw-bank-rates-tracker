# 台灣銀行業定存牌告利率追蹤

[👉 點擊查看即時利率儀表板](https://andychu221.github.io/tw-bank-rates-tracker/)

本專案旨在追蹤與視覺化台灣各大銀行的定期存款利率。透過自動化腳本每日更新數據，並提供一個方便比較的矩陣儀表板。

## 專案架構

* **前端儀表板 (`index.html`)**：以原生 HTML / CSS / JS 開發，直接讀取 Repository 內的 JSON 資料。具備存期與額度篩選、綜合排名計算及歷史走勢圖功能。
* **資料爬蟲 (`cbc_github_tracker.py`)**：基於 Python (Requests + Pandas)，負責向央行 API 請求最新利率，並處理資料清理與去重複邏輯。
* **自動化排程 (`.github/workflows/daily_crawler.yml`)**：使用 GitHub Actions，每天定時觸發爬蟲，並透過 GitHub REST API 直接更新 `data/` 目錄下的 JSON 檔案。

## 資料結構

爬取後的歷史資料按年份儲存於 `data/` 資料夾（如 `rates_2026.json`），採用扁平化 (Flat Array of Objects) 格式設計，方便後續分析或串接前端：

```json
[
  {
    "Date": "2026-06-13",
    "Bank": "臺銀",
    "Item": "定期存款",
    "Term": "1年",
    "Quota": "大額",
    "OriginalQuota": "5佰萬",
    "Rate": 0.77
  }
]
```

## 資料來源與聲明

* **資料來源**：[中華民國中央銀行牌告利率資訊網](https://cpx.cbc.gov.tw/BIRWEB/Range/RangeSelect)
* **免責聲明**：本專案爬取之數據僅供參考與開發交流使用，實際利率與相關規定請以各家銀行最新公告為準。
