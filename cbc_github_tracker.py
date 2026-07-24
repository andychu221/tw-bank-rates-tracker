import os
import json
import time
import base64
import logging
import io
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
import requests
import pandas as pd

# ── 日誌設定 ────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── 帳號與倉庫設定 (請根據您的實際情況修改) ───────────────────────────────────────
GITHUB_OWNER = "andychu221"
GITHUB_REPO = "tw-bank-rates-tracker"
GITHUB_TOKEN = os.environ.get('MY_GITHUB_TOKEN') 

# ── 央行 API URL ───────────────────────────────────────────────────────
URL_SELECT  = "https://cpx.cbc.gov.tw/BIRWEB/Range/RangeSelect"
URL_SET     = "https://cpx.cbc.gov.tw/BIRWEB/Data/SetJsonFromArray"
URL_MAIN    = "https://cpx.cbc.gov.tw/BIRWEB/Data/DataMain"
URL_GET     = "https://cpx.cbc.gov.tw/BIRWEB/Data/GetJsonFromArray"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
    "Accept-Language": "zh-TW,zh-Hant;q=0.9",
    "Origin": "https://cpx.cbc.gov.tw",
    "X-Requested-With": "XMLHttpRequest",
}

RATE_ITEMS = {
    "定期存款":       {"利率項目": "定期存款",       "利率項目代碼": "30"},
    "定期儲蓄存款":   {"利率項目": "定期儲蓄存款",   "利率項目代碼": "31"},
    "活期存款":       {"利率項目": "活期存款",       "利率項目代碼": "10"},
    "活期儲蓄存款":   {"利率項目": "活期儲蓄存款",   "利率項目代碼": "11"},
}

@dataclass
class QueryConfig:
    banks: list[str]        
    rate_item: str          
    nature: list[str]       
    tenors: list[str]       
    quota: list[str]        
    date_from: Optional[str] = None   
    date_to: Optional[str]   = None   

def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(URL_SELECT, timeout=15).raise_for_status()
    return session

def build_payload(config: QueryConfig) -> dict:
    item_info = RATE_ITEMS.get(config.rate_item, RATE_ITEMS["定期存款"])
    values = [
        {"key": "銀行名稱",     "value": config.banks},
        {"key": "利率項目",     "value": [item_info["利率項目"]]},
        {"key": "利率項目代碼", "value": [item_info["利率項目代碼"]]},
        {"key": "性質",         "value": config.nature},
        {"key": "存期",         "value": config.tenors},
        {"key": "額度",         "value": config.quota},
    ]
    return {
        "_range": {"values": values},
        "BeginDate": config.date_from,
        "EndDate":   config.date_to,
        "RateSort":  None,
    }

def scrape_to_dataframe(config: QueryConfig) -> pd.DataFrame:
    """調用央行 API 並取得原始資料表 DataFrame"""
    session = make_session()
    payload = build_payload(config)
    
    log.info(f"🚀 開始從央行網頁爬取 {len(config.banks)} 家銀行資料...")

    try:
        session.post(URL_SET, json=payload, headers={"Referer": URL_SELECT}, timeout=15).raise_for_status()
        session.get(URL_MAIN, headers={"Referer": URL_SELECT}, timeout=15).raise_for_status()
        
        r3 = session.post(
            URL_GET, 
            json=payload, 
            headers={
                "Referer": URL_MAIN,
                "Accept": "application/json, text/javascript, */*; q=0.01"
            }, 
            timeout=15
        )
        r3.raise_for_status()
        
        raw_data = r3.json()
        if isinstance(raw_data, str):
            parsed_data = json.loads(raw_data)
        else:
            parsed_data = raw_data

        if "headerSet" in parsed_data and "data" in parsed_data:
            headers = [item["data"] for item in parsed_data["headerSet"].get("Table1", [])]
            rows = parsed_data["data"]
            
            if not rows:
                log.warning("⚠️ 查詢成功，但該條件下無任何利率資料。")
                return pd.DataFrame()

            df = pd.DataFrame(rows, columns=headers)
            log.info(f"✨ 成功從央行取得 {len(df)} 筆即時利率數據！")
            return df
        else:
            log.error("❌ 央行 API 回傳結構已變更，無法辨識 headerSet 與 data")
            return pd.DataFrame()

    except Exception as e:
        log.error(f"❌ 網路爬取過程中發生錯誤: {e}")
        return pd.DataFrame()

def process_scraped_data(df: pd.DataFrame) -> pd.DataFrame:
    """資料標準化、英文化、清理空白並增加當日時間標記"""
    if df.empty:
        return df
        
    # 1. 新增當日時間
    today_str = datetime.now().strftime("%Y-%m-%d")
    df['Date'] = today_str
    
    # 2. 重新命名欄位為英文，大幅減少 JSON 儲存體積
    df.rename(columns={
        '銀行名稱': 'Bank',
        '利率項目': 'Item',
        '存期': 'Term',
        '額度': 'Quota',
        '固定利率(%)': 'Rate'
    }, inplace=True)
    
    # 3. 清理字串中的多餘空白 (如 "004臺　銀" 變更為 "004臺銀")
    df['Bank'] = df['Bank'].str.replace(r'\s+', '', regex=True)
    df['Term'] = df['Term'].str.replace(r'\s+', '', regex=True)
    df['Quota'] = df['Quota'].str.replace(r'\s+', '', regex=True)
    df['Rate'] = df['Rate'].str.strip()
    
    # 只保留我們需要的核心欄位
    core_columns = ['Date', 'Bank', 'Item', 'Term', 'Quota', 'Rate']
    return df[core_columns]

def sync_with_github(new_df: pd.DataFrame):
    """
    核心功能：透過 GitHub REST API 讀取遠端 JSON 進行合併與去重複，再回推更新
    使用 Git Blobs API 突破 1MB 讀取限制，確保可儲存全年歷史資料。
    """
    if new_df.empty:
        log.warning("⚠️ 新增資料為空，跳過 GitHub 同步流程。")
        return

    current_year = datetime.now().strftime("%Y")
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    file_path = f"data/rates_{current_year}.json"
    dir_path = "data"  # 目錄路徑
    
    # GitHub REST API 節點
    contents_api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{file_path}"
    dir_api_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{dir_path}"
    
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    
    log.info(f"🔄 正在檢查 GitHub 遠端檔案是否存在: {file_path}")
    
    sha = None
    old_df = pd.DataFrame()
    
    # Step 1: 透過目錄列表取得檔案的 SHA (繞過 GET contents 1MB 限制)
    dir_res = requests.get(dir_api_url, headers=headers)
    if dir_res.status_code == 200:
        for item in dir_res.json():
            if item["name"] == f"rates_{current_year}.json":
                sha = item["sha"]
                break
    elif dir_res.status_code == 404:
        log.info("🆕 data 目錄尚未建立。")
    else:
        log.error(f"❌ 讀取目錄失敗 (狀態碼: {dir_res.status_code})，回應訊息: {dir_res.text}")
        return

    # Step 2: 透過 Git Blobs API 讀取檔案內容 (支援最高 100MB)
    if sha:
        log.info("📁 發現遠端已有同年度歷史數據，正在透過 Blobs API 下載進行增量合併...")
        blob_url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/git/blobs/{sha}"
        blob_res = requests.get(blob_url, headers=headers)

        if blob_res.status_code == 200:
            content_b64 = blob_res.json()["content"]
            try:
                # Blob API 會自動分行 Base64 字串，b64decode 可正常處理
                old_json_str = base64.b64decode(content_b64).decode('utf-8')
                old_df = pd.read_json(io.StringIO(old_json_str), orient='records', convert_dates=False)
                if 'Date' in old_df.columns:
                    old_df['Date'] = old_df['Date'].astype(str)
            except Exception as e:
                log.error(f"❌ 解析遠端原有 JSON 發生錯誤，將覆蓋建立新檔。錯誤: {e}")
                old_df = pd.DataFrame()
        else:
            log.error(f"❌ 無法讀取 Blob 內容 (狀態碼: {blob_res.status_code})")
            return
    else:
        log.info("🆕 遠端尚未有此年度的檔案，將動態建立新檔案。")

    # Step 3: 合併新舊資料
    if not old_df.empty:
        old_df['Rate'] = old_df['Rate'].astype(str).str.strip()
        combined_df = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined_df = new_df

    # Step 4: 去重複邏輯 (基準組：日期、銀行、項目、存期、額度 皆相同時)
    combined_df.drop_duplicates(
        subset=['Date', 'Bank', 'Item', 'Term', 'Quota'],
        keep='last',
        inplace=True
    )
    
    combined_df.sort_values(by=['Date', 'Bank', 'Term'], ascending=[False, True, True], inplace=True)

    # Step 5: 轉換與 Base64 編碼
    final_json_str = combined_df.to_json(orient='records', force_ascii=False, indent=2)
    encoded_content = base64.b64encode(final_json_str.encode('utf-8')).decode('utf-8')
    
    # Step 6: 建立 Put 請求的 Payload (上傳支援到 50MB)
    commit_message = f"chore(data): automated daily update {today_str} [skip ci]"
    put_data = {
        "message": commit_message,
        "content": encoded_content
    }
    if sha:
        put_data["sha"] = sha 

    log.info(f"📤 正在將整合後的數據上傳回 GitHub 倉庫 ({len(combined_df)} 筆)...")
    put_res = requests.put(contents_api_url, headers=headers, json=put_data)
    
    if put_res.status_code in [200, 201]:
        log.info(f"🎉 成功！檔案已儲存更新至 GitHub: {file_path}")
        log.info(f"🔗 遠端連結: https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/blob/main/{file_path}")
    else:
        log.error(f"❌ 上傳 GitHub 失敗 (狀態碼: {put_res.status_code})")
        log.error(f"錯誤詳情: {put_res.text}")

# ── 主程式 ────────────────────────────────────────────────────────────
def main():
    
    config = QueryConfig(
        banks=[
            "臺灣銀行", "臺灣土地銀行", "合作金庫銀行", "第一商業銀行",
            "華南商業銀行", "彰化商業銀行", "上海商業儲蓄銀行", "台北富邦銀行",
            "國泰世華商業銀行", "高雄銀行", "兆豐國際商業銀行", "全國農業金庫",
            "花旗(台灣)商業銀行", "王道商業銀行", "台灣中小企銀", "台中商業銀行",
            "京城商業銀行", "渣打國際商業銀行", "匯豐(台灣)商業銀行",
            "新加坡星展銀行", "瑞興商業銀行", "華泰商業銀行", "新光銀行",
            "陽信商銀", "板信商業銀行", "三信商業銀行", "中華郵政股份有限公司",
            "聯邦銀行", "遠東銀行", "元大商業銀行", "永豐商業銀行", "玉山銀行",
            "凱基銀行", "星展(台灣)商業銀行", "台新銀行", "安泰銀行",
            "中國信託銀行", "將來商業銀行", "連線商業銀行", "樂天國際商業銀行",
            "日商瑞穗銀行台北分行", "美國商業銀行", "泰國盤谷銀行", "菲律賓首都銀行", 
            "美國紐約銀行", "新加坡大華銀行", "美商道富銀行", "法商興業銀行",
            "澳商澳盛銀行", "德商德意志銀行", "香港商東亞銀行", "美商摩根大通銀行", 
            "法國巴黎銀行", "英商渣打銀行台北分行", "新加坡華僑銀行", "法國東方匯理銀行",
            "瑞士商瑞士銀行", "荷商安智銀行", "美商富國銀行", "日商三菱日聯銀行", 
            "日商三井住友銀行", "西班牙對外銀行台北分行", "法商法國外貿銀行",
            "印尼商印尼人民銀行台北分行", "韓商韓亞銀行台北分行", "中國銀行台北分行", 
            "交通銀行台北分行", "中國建設銀行台北分行"
           # "日商瑞穗銀行","美國銀","盤谷銀","首都銀","美國紐約","大華銀","道富銀","興業銀","澳盛台北",
           # "德意志","香港東亞","摩根大通","法國巴黎","英商渣打","華僑銀","東方匯理","瑞銀","安智銀",
           # "富國銀","三菱日聯","三井住友","西班牙對外","法國外貿","印尼人民","韓商韓亞",
           # "中國台北","交銀台北","中國建設"
        ],
        rate_item="定期存款",
        nature=["固定"],
        tenors=["1個月", "3個月", "6個月", "9個月", "1年"],
        quota=["一般", "大額"],  # 同時抓取一般金額與大額定存
        date_from=None,
        date_to=None,
    )

    # 1. 執行爬蟲
    raw_df = scrape_to_dataframe(config)
    
    # 2. 資料清洗與轉換
    processed_df = process_scraped_data(raw_df)
    
    # 3. 雲端同步與增量去重
    sync_with_github(processed_df)

if __name__ == "__main__":
    main()
