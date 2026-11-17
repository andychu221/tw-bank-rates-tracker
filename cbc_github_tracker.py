"""
Taiwan bank deposit-rate scraper (CBC BIRWEB) -> GitHub JSON store.

Design goals
- One bad / renamed bank must never fail the whole run.
- Never "succeed silently": real failures end with a non-zero exit code.
- Never overwrite remote history if it cannot be parsed.
"""
import os
import sys
import json
import time
import base64
import logging
import io
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
import pandas as pd

# ── Logging ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── GitHub settings ─────────────────────────────────────────────────────
GITHUB_OWNER = "andychu221"
GITHUB_REPO = "tw-bank-rates-tracker"
GITHUB_TOKEN = os.environ.get("MY_GITHUB_TOKEN")
DATA_DIR = "data"

REPO_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"

# ── CBC endpoints ───────────────────────────────────────────────────────
URL_SELECT = "https://cpx.cbc.gov.tw/BIRWEB/Range/RangeSelect"
URL_SET = "https://cpx.cbc.gov.tw/BIRWEB/Data/SetJsonFromArray"
URL_MAIN = "https://cpx.cbc.gov.tw/BIRWEB/Data/DataMain"
URL_GET = "https://cpx.cbc.gov.tw/BIRWEB/Data/GetJsonFromArray"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
    "Accept-Language": "zh-TW,zh-Hant;q=0.9",
    "Origin": "https://cpx.cbc.gov.tw",
    "X-Requested-With": "XMLHttpRequest",
}

RATE_ITEMS = {
    "定期存款":     {"利率項目": "定期存款",     "利率項目代碼": "30"},
    "定期儲蓄存款": {"利率項目": "定期儲蓄存款", "利率項目代碼": "31"},
    "活期存款":     {"利率項目": "活期存款",     "利率項目代碼": "10"},
    "活期儲蓄存款": {"利率項目": "活期儲蓄存款", "利率項目代碼": "11"},
}

# ── Tuning knobs ────────────────────────────────────────────────────────
TZ = ZoneInfo("Asia/Taipei")   # use Taiwan date, not the runner's UTC date
BATCH_SIZE = 10                # banks per CBC query
REQUEST_RETRIES = 3            # retries for network / HTTP errors
MAX_FAILED_RATIO = 0.10        # more than 10% of banks failing => red run

# Fallback names tried (one-by-one) if the primary name returns no data.
# The CBC silently returns an empty body for a name it does not recognise.
BANK_ALIASES = {
    "臺灣中小企銀": ["台灣中小企銀"],
}

# CBC now returns a different display name than the one stored in our history
# (and shown on the website). Map: value returned by CBC -> value we keep.
BANK_NAME_FIX = {
    "050臺灣企銀": "050中小企銀",
}

BANKS = [
    "臺灣銀行", "臺灣土地銀行", "合作金庫銀行", "第一商業銀行",
    "華南商業銀行", "彰化商業銀行", "上海商業儲蓄銀行", "台北富邦銀行",
    "國泰世華商業銀行", "高雄銀行", "兆豐國際商業銀行", "全國農業金庫",
    "花旗(台灣)商業銀行", "王道商業銀行", "臺灣中小企銀", "台中商業銀行",
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
    "交通銀行台北分行", "中國建設銀行台北分行",
]

RENAME_MAP = {
    "銀行名稱": "Bank",
    "利率項目": "Item",
    "存期": "Term",
    "額度": "Quota",
    "固定利率(%)": "Rate",
}
CORE_COLUMNS = ["Date", "Bank", "Item", "Term", "Quota", "Rate"]
KEY_COLUMNS = ["Date", "Bank", "Item", "Term", "Quota"]


@dataclass
class QueryConfig:
    banks: list[str]
    rate_item: str
    nature: list[str]
    tenors: list[str]
    quota: list[str]
    date_from: Optional[str] = None
    date_to: Optional[str] = None


def build_config() -> QueryConfig:
    return QueryConfig(
        banks=BANKS,
        rate_item="定期存款",
        nature=["固定"],
        tenors=["1個月", "3個月", "6個月", "9個月", "1年"],
        quota=["一般", "大額"],
    )


# ── CBC scraping ────────────────────────────────────────────────────────
def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    session.get(URL_SELECT, timeout=15).raise_for_status()
    return session


def build_payload(config: QueryConfig) -> dict:
    item_info = RATE_ITEMS.get(config.rate_item, RATE_ITEMS["定期存款"])
    values = [
        {"key": "銀行名稱", "value": config.banks},
        {"key": "利率項目", "value": [item_info["利率項目"]]},
        {"key": "利率項目代碼", "value": [item_info["利率項目代碼"]]},
        {"key": "性質", "value": config.nature},
        {"key": "存期", "value": config.tenors},
        {"key": "額度", "value": config.quota},
    ]
    return {
        "_range": {"values": values},
        "BeginDate": config.date_from,
        "EndDate": config.date_to,
        "RateSort": None,
    }


def fetch_once(config: QueryConfig) -> pd.DataFrame:
    """
    One CBC query.
    - Returns an empty DataFrame when CBC answers with an empty body
      (this is what happens when ANY requested bank name is unknown).
    - Raises on network / HTTP errors and on an unexpected response structure.
    """
    session = make_session()
    payload = build_payload(config)

    session.post(URL_SET, json=payload, headers={"Referer": URL_SELECT}, timeout=30).raise_for_status()
    session.get(URL_MAIN, headers={"Referer": URL_SELECT}, timeout=30).raise_for_status()
    r3 = session.post(
        URL_GET,
        json=payload,
        headers={
            "Referer": URL_MAIN,
            "Accept": "application/json, text/javascript, */*; q=0.01",
        },
        timeout=30,
    )
    r3.raise_for_status()

    if not r3.text.strip():
        log.warning("CBC returned an empty body (unknown bank name in query?): %s", config.banks)
        return pd.DataFrame()

    parsed = r3.json()
    if isinstance(parsed, str):
        parsed = json.loads(parsed)

    if "headerSet" not in parsed or "data" not in parsed:
        raise ValueError(f"Unexpected CBC response structure, keys={list(parsed)[:10]}")

    headers = [item["data"] for item in parsed["headerSet"].get("Table1", [])]
    rows = parsed["data"]
    if not rows:
        log.warning("CBC returned zero rows for: %s", config.banks)
        return pd.DataFrame()
    return pd.DataFrame(rows, columns=headers)


def fetch_with_retry(config: QueryConfig) -> pd.DataFrame:
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            return fetch_once(config)
        except requests.RequestException as e:
            log.warning("Network error (attempt %d/%d): %s", attempt, REQUEST_RETRIES, e)
            if attempt == REQUEST_RETRIES:
                raise
            time.sleep(2 * attempt)


def fetch_single_bank(bank: str, base: QueryConfig) -> pd.DataFrame:
    """Query one bank; try known aliases if the primary name returns nothing."""
    for name in [bank] + BANK_ALIASES.get(bank, []):
        try:
            df = fetch_with_retry(replace(base, banks=[name]))
        except Exception as e:  # isolate every failure to this one bank
            log.error("Bank '%s' (query name '%s') failed: %s", bank, name, e)
            continue
        if not df.empty:
            if name != bank:
                log.warning("Bank '%s' only worked under alias '%s' - update BANKS.", bank, name)
            return df
    return pd.DataFrame()


def scrape_all(config: QueryConfig) -> tuple[pd.DataFrame, list[str]]:
    """
    Scrape in batches. If a batch is empty, fails, or returns fewer distinct
    banks than requested, redo that batch bank-by-bank so a single bad bank
    can only ever lose itself.
    Returns (all_rows, failed_banks).
    """
    frames: list[pd.DataFrame] = []
    failed: list[str] = []
    banks = config.banks
    total_batches = (len(banks) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(banks), BATCH_SIZE):
        batch = banks[i:i + BATCH_SIZE]
        n = i // BATCH_SIZE + 1
        log.info("Batch %d/%d (%d banks)", n, total_batches, len(batch))

        df = pd.DataFrame()
        try:
            df = fetch_with_retry(replace(config, banks=batch))
        except Exception as e:
            log.warning("Batch %d failed: %s", n, e)

        complete = (
            not df.empty
            and "銀行名稱" in df.columns
            and df["銀行名稱"].nunique() >= len(batch)
        )
        if complete:
            frames.append(df)
        else:
            log.warning("Batch %d empty or incomplete; retrying bank by bank", n)
            for bank in batch:
                one = fetch_single_bank(bank, config)
                if one.empty:
                    failed.append(bank)
                else:
                    frames.append(one)
                time.sleep(0.5)
        time.sleep(1)

    result = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    return result, failed


def process_scraped_data(df: pd.DataFrame, today_str: str) -> pd.DataFrame:
    """Standardise columns, strip whitespace, stamp the (Taiwan) date."""
    if df.empty:
        return df

    missing = [c for c in RENAME_MAP if c not in df.columns]
    if missing:
        raise ValueError(f"CBC columns changed; missing {missing}, got {list(df.columns)}")

    df = df.rename(columns=RENAME_MAP).copy()
    df["Date"] = today_str
    for col in ("Bank", "Term", "Quota"):
        df[col] = df[col].astype(str).str.replace(r"\s+", "", regex=True)
    df["Rate"] = df["Rate"].astype(str).str.strip()
    df["Bank"] = df["Bank"].replace(BANK_NAME_FIX)
    return df[CORE_COLUMNS].drop_duplicates(subset=KEY_COLUMNS, keep="last")


# ── GitHub sync ─────────────────────────────────────────────────────────
def _gh_headers() -> dict:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
    }


def _load_remote(file_name: str) -> tuple[Optional[str], pd.DataFrame]:
    """Return (sha, existing DataFrame). Raises on any problem - never guesses."""
    dir_res = requests.get(f"{REPO_API}/contents/{DATA_DIR}", headers=_gh_headers(), timeout=30)
    if dir_res.status_code == 404:
        log.info("'%s' directory does not exist yet.", DATA_DIR)
        return None, pd.DataFrame()
    if dir_res.status_code != 200:
        hint = " (token invalid or expired?)" if dir_res.status_code in (401, 403) else ""
        raise RuntimeError(f"Listing '{DATA_DIR}' failed: HTTP {dir_res.status_code}{hint} {dir_res.text[:200]}")

    sha = next((it["sha"] for it in dir_res.json() if it["name"] == file_name), None)
    if sha is None:
        log.info("%s does not exist yet; it will be created.", file_name)
        return None, pd.DataFrame()

    # Blobs API works for files above the 1 MB contents-API limit
    blob_res = requests.get(f"{REPO_API}/git/blobs/{sha}", headers=_gh_headers(), timeout=60)
    if blob_res.status_code != 200:
        raise RuntimeError(f"Reading blob failed: HTTP {blob_res.status_code} {blob_res.text[:200]}")

    try:
        text = base64.b64decode(blob_res.json()["content"]).decode("utf-8")
        old_df = pd.read_json(io.StringIO(text), orient="records", convert_dates=False, dtype=False)
    except Exception as e:
        # Never fall back to "start a new file": that would wipe the history.
        raise RuntimeError(f"Remote JSON could not be parsed; refusing to overwrite history: {e}") from e

    if not old_df.empty:
        missing = [c for c in CORE_COLUMNS if c not in old_df.columns]
        if missing:
            raise RuntimeError(f"Remote JSON is missing columns {missing}; refusing to overwrite.")
        old_df["Date"] = old_df["Date"].astype(str)
        old_df["Rate"] = old_df["Rate"].astype(str).str.strip()
        old_df["Bank"] = old_df["Bank"].replace(BANK_NAME_FIX)   # self-heal if variants were stored earlier
    return sha, old_df


def warn_on_renamed_banks(old_df: pd.DataFrame, new_df: pd.DataFrame) -> None:
    """Warn when a new Bank value shares its 3-digit code with a different stored name."""
    if old_df.empty:
        return
    old_names = set(old_df["Bank"].unique())
    by_code: dict[str, set] = {}
    for n in old_names:
        m = re.match(r"\d{3}", n)
        if m:
            by_code.setdefault(m.group(), set()).add(n)
    for n in new_df["Bank"].unique():
        m = re.match(r"\d{3}", n)
        if n not in old_names and m and m.group() in by_code:
            log.warning("Bank value '%s' is new but code %s is stored as %s - add it to BANK_NAME_FIX.",
                        n, m.group(), sorted(by_code[m.group()]))


def sync_with_github(new_df: pd.DataFrame, year: str, today_str: str) -> None:
    """Merge new rows into data/rates_<year>.json. Raises on any failure."""
    if new_df.empty:
        raise RuntimeError("Nothing to sync (empty DataFrame).")
    if not GITHUB_TOKEN:
        raise RuntimeError("MY_GITHUB_TOKEN is not set.")

    file_name = f"rates_{year}.json"
    file_path = f"{DATA_DIR}/{file_name}"

    for attempt in range(1, 4):
        sha, old_df = _load_remote(file_name)
        warn_on_renamed_banks(old_df, new_df)

        combined = pd.concat([old_df, new_df], ignore_index=True) if not old_df.empty else new_df.copy()
        combined = combined.drop_duplicates(subset=KEY_COLUMNS, keep="last")
        combined = combined.sort_values(by=["Date", "Bank", "Term"], ascending=[False, True, True])

        if not old_df.empty and len(combined) < len(old_df):
            raise RuntimeError(f"Merged data ({len(combined)}) is smaller than remote ({len(old_df)}); aborting.")

        final_json = combined.to_json(orient="records", force_ascii=False, indent=2)
        put_data = {
            "message": f"chore(data): automated daily update {today_str} [skip ci]",
            "content": base64.b64encode(final_json.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            put_data["sha"] = sha

        log.info("Uploading %d rows to %s (attempt %d)", len(combined), file_path, attempt)
        put_res = requests.put(f"{REPO_API}/contents/{file_path}", headers=_gh_headers(), json=put_data, timeout=120)

        if put_res.status_code in (200, 201):
            log.info("Saved: https://github.com/%s/%s/blob/main/%s", GITHUB_OWNER, GITHUB_REPO, file_path)
            return
        if put_res.status_code == 409 and attempt < 3:
            log.warning("SHA conflict (file changed meanwhile); reloading and retrying.")
            time.sleep(2)
            continue
        raise RuntimeError(f"Upload failed: HTTP {put_res.status_code} {put_res.text[:300]}")


# ── Main ────────────────────────────────────────────────────────────────
def main() -> int:
    """Returns the process exit code (0 = ok, 1 = needs attention)."""
    now = datetime.now(TZ)
    today_str, year = now.strftime("%Y-%m-%d"), now.strftime("%Y")
    config = build_config()

    raw_df, failed = scrape_all(config)
    if raw_df.empty:
        log.error("No data scraped for any bank.")
        return 1

    processed = process_scraped_data(raw_df, today_str)
    log.info("Scraped %d rows from %d/%d banks.", len(processed), len(config.banks) - len(failed), len(config.banks))

    # Good data is saved even if a few banks failed.
    sync_with_github(processed, year, today_str)

    if failed:
        for b in failed:
            # GitHub Actions turns this into a yellow annotation on the run page
            print(f"::warning title=Bank scrape failed::{b}", flush=True)
        log.error("Failed banks (%d): %s", len(failed), failed)
        if len(failed) / len(config.banks) > MAX_FAILED_RATIO:
            log.error("More than %.0f%% of banks failed; marking the run as failed.", MAX_FAILED_RATIO * 100)
            return 1
    return 0


def run_cli() -> int:
    """Entry point: exits with a real exit code in GitHub Actions, just returns it in Colab/Jupyter."""
    try:
        code = main()
    except Exception:
        log.exception("Run failed")
        code = 1
    if "ipykernel" in sys.modules:   # Colab / Jupyter: avoid the noisy SystemExit
        print(f"Finished with exit code {code} (0 = ok, 1 = needs attention)")
        return code
    sys.exit(code)


if __name__ == "__main__":
    run_cli()
