import os
import sys
import json
import time
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "state.json"
DATA_URL = "https://xml.dsmg.gov.mo/c_ftgms.xml"

# 19 個水浸站點固定順序
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
# 1. Telegram 發送模組
# ==========================================
def send_telegram(text: str):
    bot_token = os.environ.get("tidefloodtoken")
    chat_id = -1004308045341

    if not bot_token:
        print("⚠️ 未檢測到 tidefloodtoken 環境變量，取消發送。")
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
            print("🚀 Telegram 潮汐/水浸訊息發送成功！")
        else:
            print(f"❌ Telegram 發送失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ Telegram 請求異常: {e}")

# ==========================================
# 2. 獲取海事及水務局 (DSAMA) 即時潮位
# ==========================================
def fetch_dsama_tide():
    """優先透過 DSAMA API 獲取潮位，失敗時讀取本地 flood/tidedata.json"""
    url = f"https://dsama.apigateway.data.gov.mo/currentTideXmlApi?_t={int(time.time())}"
    headers = {
        "Authorization": "APPCODE 09d43a591fba407fb862412970667de4",
        "User-Agent": "Mozilla/5.0"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200 and res.content:
            root = ET.fromstring(res.content)
            raw_tide = root.findtext('.//recordTide')
            if raw_tide and raw_tide.strip():
                tide_m = round(float(raw_tide.strip()) / 100.0, 2)
                return tide_m
    except Exception as e:
        print(f"⚠️ 請求 DSAMA 潮位 API 異常: {e}")

    # 備選方案：從同專案的本地 flood/tidedata.json 讀取
    if os.path.exists("flood/tidedata.json"):
        try:
            with open("flood/tidedata.json", "r", encoding="utf-8") as f:
                d = json.load(f)
                val = d.get("tide_m")
                if val is not None:
                    return float(val)
        except Exception as e:
            print(f"⚠️ 讀取 flood/tidedata.json 失敗: {e}")

    return None

# ==========================================
# 3. 解析 c_ftgms.xml (依據各站 RecordTime)
# ==========================================
def parse_dt(raw_time):
    if not raw_time:
        return None
    for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"]:
        try:
            return datetime.strptime(raw_time.strip(), fmt).replace(tzinfo=MACAU_TZ)
        except ValueError:
            continue
    return None

def parse_ftgms_xml(content_str):
    latest_record_dt = None
    tide_val = None
    flood_map = {}

    try:
        root = ET.fromstring(content_str)
        for st in root.findall('.//station'):
            code = st.get('code') or ''
            if not code:
                c_node = st.find('code')
                if c_node is not None and c_node.text:
                    code = c_node.text.strip()
            code = code.upper()

            name_node = st.find('stationname')
            name = name_node.text.strip() if name_node is not None and name_node.text else ""

            # 抓取各站的 RecordTime
            rec_node = st.find('RecordTime')
            if rec_node is not None and rec_node.text:
                rec_dt = parse_dt(rec_node.text)
                if rec_dt and (latest_record_dt is None or rec_dt > latest_record_dt):
                    latest_record_dt = rec_dt

            # 抓取水浸/潮位數值
            val = None
            val_node = st.find('.//Waterlevel/Value')
            if val_node is None:
                val_node = st.find('.//Value') or st.find('.//value')
            if val_node is not None and val_node.text:
                try:
                    val = float(val_node.text.strip())
                except ValueError:
                    val = None

            if val is None:
                continue

            # 站點區分
            if "潮" in name or code in ["TIDE", "EXT", "KL", "WL"]:
                tide_val = val
            else:
                if code:
                    flood_map[code] = val
                for c, n in ORDERED_FLOOD_STATIONS:
                    if n in name:
                        flood_map[c] = val
                        break

    except ET.ParseError:
        record_times = re.findall(r'<RecordTime>([^<]+)</RecordTime>', content_str, re.IGNORECASE)
        for rt in record_times:
            rec_dt = parse_dt(rt)
            if rec_dt and (latest_record_dt is None or rec_dt > latest_record_dt):
                latest_record_dt = rec_dt

        blocks = re.findall(r'<station[\s\S]*?</station>', content_str, re.IGNORECASE)
        for block in blocks:
            code_m = re.search(r'code=["\']([^"\']+)["\']', block, re.IGNORECASE)
            if not code_m:
                code_m = re.search(r'<code>([^<]+)</code>', block, re.IGNORECASE)
            code = code_m.group(1).strip().upper() if code_m else ""

            name_m = re.search(r'<stationname>([^<]+)</stationname>', block, re.IGNORECASE)
            name = name_m.group(1).strip() if name_m else ""

            val_m = re.search(r'<Value>([^<]+)</Value>', block, re.IGNORECASE)
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

    if not latest_record_dt:
        sys_time_m = re.search(r'<SysPubdate>([^<]+)</SysPubdate>', content_str, re.IGNORECASE)
        if sys_time_m:
            latest_record_dt = parse_dt(sys_time_m.group(1))

    if not latest_record_dt:
        latest_record_dt = datetime.now(MACAU_TZ)

    formatted_time = latest_record_dt.strftime("%Y/%m/%d %H:%M MST")
    return formatted_time, tide_val, flood_map

def fetch_smg_water_data():
    cache_buster = int(time.time() * 1000)
    url = f"{DATA_URL}?_t={cache_buster}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0'
    }
    try:
        res = requests.get(url, headers=headers, timeout=12)
        res.encoding = 'utf-8'
        if res.status_code == 200 and res.text.strip():
            return parse_ftgms_xml(res.text)
    except Exception as e:
        print(f"❌ 請求 {DATA_URL} 失敗: {e}")

    return None, None, {}

# ==========================================
# 4. 訊息生成與圖示邏輯
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

def build_tide_flood_message(record_time_str, tide_val, flood_map):
    lines = [record_time_str]
    has_content = False

    # 潮汐邏輯：>= 3.10 m 顯示 ⚠️；3.00 <= tide_val < 3.10 正常顯示；< 3.00 m 不顯示
    if tide_val is not None:
        if tide_val >= 3.10:
            lines.append(f"潮汐：{tide_val:.2f} m ⚠️")
            lines.append("")
            has_content = True
        elif tide_val >= 3.00:
            lines.append(f"潮汐：{tide_val:.2f} m")
            lines.append("")
            has_content = True

    # 水浸邏輯 (0.00 m 不顯示)
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
# 5. 狀態儲存與主流程
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
    record_time_str, smg_tide, flood_map = fetch_smg_water_data()

    if not record_time_str:
        print("❌ 無法取得水位數據，結束運行。")
        sys.exit(0)

    # 若 c_ftgms.xml 無潮位數據，自動向 DSAMA 潮位 API 或 flood/tidedata.json 獲取
    tide_val = smg_tide if smg_tide is not None else fetch_dsama_tide()

    print(f"📍 觀測時次 (RecordTime): {record_time_str}")
    print(f"🌊 當前潮高 (Tide): {f'{tide_val:.2f} m' if tide_val is not None else '未獲取'}")
    active_floods = {k: v for k, v in flood_map.items() if v > 0}
    print(f"🚨 水浸站狀態: {f'檢測到 {len(active_floods)} 站水浸' if active_floods else '全線 0.00 m'}")

    state = load_state()
    last_record_time = state.get("last_record_time", "")

    if record_time_str == last_record_time:
        print(f"ℹ️ RecordTime 尚未更新 (當前數據時次: {record_time_str})，略過。")
        sys.exit(0)

    print(f"🔔 檢測到新時次數據，正在評估推播標準...")

    message = build_tide_flood_message(record_time_str, tide_val, flood_map)

    if message:
        print("📤 滿足通報標準，發送 Telegram 訊息:\n" + message)
        send_telegram(message)
    else:
        print("ℹ️ 潮汐 < 3.00 m 且水浸皆為 0.00 m，略過推播。")

    state["last_record_time"] = record_time_str
    save_state(state)
