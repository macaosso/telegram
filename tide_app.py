import os
import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "state.json"

# 預設憑證（亦可由 GitHub Secrets / 環境變數覆寫）
DEFAULT_BOT_TOKEN = "9228555664:AAFcTGN-rlyR7bdE6mrc8Ic14UmISX3LWSc"
DEFAULT_CHAT_ID = "-1004308045341"

# 依指定順序排列的水浸站點代碼與中文名稱
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
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", DEFAULT_BOT_TOKEN)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", DEFAULT_CHAT_ID)

    if not bot_token or not chat_id:
        print("⚠️ 未檢測到有效的 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID。")
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
            print("🚀 Telegram 水浸/潮汐警報發送成功！")
        else:
            print(f"❌ Telegram 發送失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ Telegram 請求異常: {e}")

# ==========================================
# 2. SMG XML Fetching & Parsing
# ==========================================
def parse_val(text):
    if not text:
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None

def fetch_smg_water_data():
    """
    抓取 SMG 實時水位及潮位 XML (c_actualwaterlevel.xml)
    """
    url = "https://xml.smg.gov.mo/c_actualwaterlevel.xml"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        root = ET.fromstring(res.content)
    except Exception as e:
        print(f"❌ SMG 水位 XML 抓取失敗: {e}")
        return None, None, {}

    # 解析發布時間
    pub_node = root.find('.//SysPubdate') or root.find('.//SysPubDate') or root.find('.//System/SysPubdate')
    pub_dt = None
    if pub_node is not None and pub_node.text:
        raw_time = pub_node.text.strip()
        for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"]:
            try:
                pub_dt = datetime.strptime(raw_time, fmt).replace(tzinfo=MACAU_TZ)
                break
            except ValueError:
                continue

    if not pub_dt:
        pub_dt = datetime.now(MACAU_TZ)

    formatted_time = pub_dt.strftime("%Y/%m/%d %H:%M MST")

    tide_val = None
    flood_map = {}

    for st in root.findall('.//station'):
        code_node = st.find('code') or st.find('StationCode') or st.find('stationcode')
        name_node = st.find('stationname') or st.find('name')
        val_node = st.find('Value') or st.find('dValue') or st.find('waterlevel')

        code = code_node.text.strip().upper() if (code_node is not None and code_node.text) else ""
        name = name_node.text.strip() if (name_node is not None and name_node.text) else ""
        val = parse_val(val_node.text) if (val_node is not None and val_node.text) else None

        # 潮汐站辨識 (外港碼頭潮位站或以 Tide 標記)
        if "潮" in name or code in ["TIDE", "EXT"]:
            if val is not None:
                tide_val = val
        else:
            if code:
                flood_map[code] = val
            if name:
                # 依站名備用匹配
                for c, n in ORDERED_FLOOD_STATIONS:
                    if n in name:
                        flood_map[c] = val
                        break

    return formatted_time, tide_val, flood_map

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

    # 1. 潮汐邏輯 (< 3.00 m 不顯示；>= 3.10 m 顯示 ⚠️)
    if tide_val is not None and tide_val >= 2.60:
        warning_icon = " ⚠️" if tide_val >= 3.10 else ""
        lines.append(f"潮汐：{tide_val:.2f} m{warning_icon}")
        lines.append("")
        has_content = True

    # 2. 水浸邏輯 (0.00 m 不顯示)
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
        print("❌ 無法取得 SMG 水位數據，結束運行。")
        sys.exit(0)

    state = load_state()
    last_pubdate = state.get("last_pubdate", "")

    # 若 XML 時間未更新則跳過
    if pub_time_str == last_pubdate:
        print(f"ℹ️ SMG 水位 XML 尚未更新 (當前時次: {pub_time_str})，跳過。")
        sys.exit(0)

    print(f"🔔 檢測到新水位發布數據: {pub_time_str}")

    message = build_tide_flood_message(pub_time_str, tide_val, flood_map)

    if message:
        print("📤 滿足潮汐/水浸通報標準，發送 Telegram 訊息:\n" + message)
        send_telegram(message)
    else:
        print("ℹ️ 潮汐 < 3.00 m 且所有站點水浸均為 0.00 m，略過推播。")

    state["last_pubdate"] = pub_time_str
    save_state(state)
