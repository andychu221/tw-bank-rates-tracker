🏦 台灣銀行業定存牌告利率追蹤 (Taiwan Bank Rates Tracker)

這是一個全自動化的開源專案，每日定時從「台灣中央銀行」抓取各大銀行業的定期存款利率，並提供一個現代化的互動式網頁儀表板供大眾查詢與比較。

🌐 點我查看即時利率儀表板 (Live Demo)

✨ 專案亮點 (Features)

📊 互動式矩陣儀表板 (Web Dashboard)

智慧排名：自動計算各銀行在各存期的平均排名，抓出整體最優銀行。

動態篩選：支援銀行、存期、額度（大額/一般）多條件交叉過濾。

歷史走勢：點選任一利率，即可彈出該銀行特定存期的歷史變動折線圖（基於 Chart.js）。

🤖 全自動化資料工程 (Data Pipeline)

透過 GitHub Actions 每日早上 09:30 (TST) 自動執行爬蟲，完全無須人工介入。

輕量化 API 更新：使用 GitHub REST API 直接更新 JSON 檔案，避免頻繁的 git push 操作。

智慧去重複：自動比對歷史資料，精準保留最新與最高的利率紀錄。

📁 資料結構 (Data Structure)

每日抓取的歷史資料會存放於 data/ 目錄下（例如：data/rates_2026.json）。採用扁平化 (Flat Array) 設計，極度適合使用 Pandas、React 或 Vue 等框架進行二次開發。

[
  {
    "Date": "2026-06-13",
    "Bank": "臺銀",
    "Item": "定期存款",
    "Term": "1年",
    "Quota": "大額",
    "OriginalQuota": "5佰萬",
    "Rate": 0.770
  }
]


🛠️ 開發與本地測試 (Local Setup)

本專案分為「前端網頁」與「後端爬蟲」兩部分：

1. 前端儀表板：
只需打開根目錄下的 index.html 即可在本地瀏覽器預覽，無需編譯。

2. Python 爬蟲腳本：
如果你想在本地測試爬蟲抓取資料：

# 1. 安裝必要套件
pip install requests pandas

# 2. 設定你的 GitHub Token (必須擁有 repo 讀寫權限)
export GITHUB_TOKEN="你的_GITHUB_TOKEN"

# 3. 執行爬蟲更新資料
python cbc_github_tracker.py


📜 授權與聲明 (License & Disclaimer)

本專案採 MIT License，歡迎自由 Fork、修改與建立 Pull Request！

原始資料來源歸屬為 中華民國中央銀行牌告利率資訊網。

本專案僅供程式學習與交流使用，實際利率請以各銀行最新公告為準。
