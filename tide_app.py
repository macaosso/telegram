import os
import sys
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "state.json"
DATA_URL = "https://xml.dsmg.gov.mo/c_ftgms.xml"

ORDERED_FLOOD_STATIONS = [
    ("LSI", "內港北"),
    ("LPI", "內港"),
    ("LHK", "康公廟"),
    ("LPH", "司打口"),
    ("LPF", "內港南"),
    ("LPM", "下環街"),
    ("LIV", "青洲河邊馬路"),
    ("LMF", "花地瑪教會街"),
    ("LMM", "紅街市"),
    ("LRR", "光復街"),
    ("LTA", "永樂戲院"),
    ("LHO", "菜園路"),
    ("LCH", "沙維斯街"),
    ("LAO", "柯維納馬路"),
    ("LIL", "益隆"),
    ("LCS", "松樹尾"),
    ("LPN", "黑橋街"),
    ("LSP", "石排灣"),
    ("LCL", "聖方濟各堂"),
]

# ==========================================
# 1. Telegram Dispatch
# ==========================================
def send_telegram(text: str):
    bot_token = os.environ.get("tidefloodtoken")
    chat_id = -1004308045341

    if not bot_token or not chat_id:
        print("⚠️ 未檢測到 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 環境變量，取消發送。")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=payload, timeout=12)
        if res.status_code == 200:
            print("🚀 Telegram 訊息發送成功！")
        else:
            print(f"❌ Telegram 發送失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ Telegram 請求異常: {e}")

# ==========================================
# 2. XML Fetching & Resilient Parsing
# ==========================================
def parse_water_content(content_str):
    pub_dt = None
    tide_val = None
    flood_map = {}

    # 1. 提取時間標籤
    time_match = re.search(r'<(?:SysPubdate|SysPubDate|syspubdate|PubDate|pubdate|time)>([^<]+)</', content_str, re.IGNORECASE)
    if time_match:
        raw_time = time_match.group(1).strip()
        for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
            try:
                pub_dt = datetime.strptime(raw_time, fmt).replace(tzinfo=MACAU_TZ)
                break
            except ValueError:
                pass

    if not pub_dt:
        pub_dt = datetime.now(MACAU_TZ)

    formatted_time = pub_dt.strftime("%Y/%m/%d %H:%M MST")

    # 2. 優先嘗試標準 XML 解析，解析失敗則以正則表達式作為後備
    parsed_via_et = False
    try:
        root = ET.fromstring(content_str)
        # 歷遍所有站點節點
        stations = root.findall('.//station') or root.findall('.//Station') or root.findall('.//Item')
        for st in stations:
            code_el = st.find('code') or st.find('StationCode') or st.find('stationcode')
            name_el = st.find('name') or st.find('stationname') or st.find('StationName')
            val_el = st.find('value') or st.find('Value') or st.find('dValue') or st.find('waterlevel')

            code = code_el.text.strip().upper() if code_el is not None and code_el.text else ""
            name = name_el.text.strip() if name_el is not None and name_el.text else ""
            val = None
            if val_el is not None and val_el.text:
                try:
                    val = float(val_el.text.strip())
                except ValueError:
                    val = None

            if val is None:
                continue

            if "潮" in name or code in ["TIDE", "EXT", "KL", "WL"]:
                tide_val = val
            else:
                if code:
                    flood_map[code] = val
                for c, n in ORDERED_FLOOD_STATIONS:
                    if n in name:
                        flood_map[c] = val
                        break
        parsed_via_et = True
    except ET.ParseError:
        parsed_via_et = False

    # 若 XML 格式不規範噴錯，切換至 Regex 區塊提取
    if not parsed_via_et:
        blocks = re.findall(r'<(?:station|Station|Item)[\s\S]*?</(?:station|Station|Item)>', content_str, re.IGNORECASE)
        for block in blocks:
            code_m = re.search(r'<(?:code|StationCode|stationcode)>([^<]+)</', block, re.IGNORECASE)
            name_m = re.search(r'<(?:stationname|name|StationName)>([^<]+)</', block, re.IGNORECASE)
            val_m = re.search(r'<(?:dValue|Value|value|waterlevel)>([^<]+)</', block, re.IGNORECASE)

            code = code_m.group(1).strip().upper() if code_m else ""
            name = name_m.group(1).strip() if name_m else ""
            val = None
            if val_m:
                try:
                    val = float(val_m.group(1).strip())
                except ValueError:
                    val = None

            if val is None:
                continue

            if "潮" in name or code in ["TIDE", "EXT", "KL", "WL"]:
                tide_val = val
            else:
                if code:
                    flood_map[code] = val
                for c, n in ORDERED_FLOOD_STATIONS:
                    if n in name:
                        flood_map[c] = val
                        break

    return formatted_time, tide_val, flood_map

def fetch_smg_water_data():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'
    }
    try:
        res = requests.get(DATA_URL, headers=headers, timeout=12)
        res.encoding = 'utf-8'
        if res.status_code == 200 and res.text.strip():
            return parse_water_content(res.text)
    except Exception as e:
        print(f"❌ 請求 {DATA_URL} 失敗: {e}")

    return None, None, {}

# ==========================================
# 3. Message Builder
# ==========================================
def get_flood_icon(val):
    if val >= 2.50:
        return " ⬛️"
    elif 1.50 <= val <= 2.49:
        return " 🟥"
    elif 1.00 <= val <= 1.49:
        return " 🟧"
    elif 0.50 <= val <= 0.99:
        return " 🟨"
    elif 0.01 <= val <= 0.49:
        return " 🟦"
    return ""

def build_tide_flood_message(pub_time_str, tide_val, flood_map):
    lines = [pub_time_str]
    has_content = False

    if tide_val is not None and tide_val >= 3.00:
        warning_icon = " ⚠️" if tide_val >= 3.10 else ""
        lines.append(f"潮汐：{tide_val:.2f} m{warning_icon}")
        lines.append("")
        has_content = True

    flood_lines = []
    for code, name in ORDERED_FLOOD_STATIONS:
        val = flood_map.get(code)
        if val is not None and val > 0.00:
            icon = get_flood_icon(val)
            flood_lines.append(f"{name} ({code}) : {val:.2f} m{icon}")

    if flood_lines:
        has_content = True
        lines.extend(flood_lines)

    if not has_content:
        return None

    return "\n".join(lines).strip()

# ==========================================
# 4. State Management & Main Flow
# ==========================================
def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 狀態寫入失敗: {e}")

if __name__ == "__main__":
    pub_time_str, tide_val, flood_map = fetch_smg_water_data()

    if not pub_time_str:
        print("❌ 無法取得水位/潮位數據，結束運行。")
        sys.exit(0)

    state = load_state()
    last_pubdate = state.get("last_pubdate", "")

    if pub_time_str == last_pubdate:
        print(f"ℹ️ 數據尚未更新 (當前時次: {pub_time_str})，略過。")
        sys.exit(0)

    print(f"🔔 檢測到新時次數據: {pub_time_str}")

    message = build_tide_flood_message(pub_time_str, tide_val, flood_map)

    if message:
        print("📤 滿足通報標準，發送 Telegram 訊息:\n" + message)
        send_telegram(message)
    else:
        print("ℹ️ 潮汐 < 3.00 m 且水浸皆為 0.00 m，略過推播。")

    state["last_pubdate"] = pub_time_str
    save_state(state)
