import os
import sys
import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "state.json"
DB_FILE = "temp.db"
DEFAULT_CHANNEL = "@motempinfo"

# 僅保留陸上氣象站點
STATION_COORDS = {
    "紀念孫中山公園": {"code": "DP", "disp": "孫中山公園"},
    "黑沙環": {"code": "EM", "disp": "黑沙環"},
    "大炮台": {"code": "FM", "disp": "大炮台"},
    "外港碼頭": {"code": "PE", "disp": "外港碼頭"},
    "媽閣": {"code": "MM", "disp": "媽閣"},
    "大潭山": {"code": "TG", "disp": "大潭山"},
    "東亞運大馬路": {"code": "JA", "disp": "東亞運"},
    "澳門大學": {"code": "UM", "disp": "澳門大學"},
    "九澳": {"code": "KV", "disp": "九澳"},
    "路環": {"code": "DC", "disp": "路環"}
}

# ==========================================
# 1. Telegram Dispatch (@motempbot -> @motempinfo)
# ==========================================
def send_telegram(text: str):
    bot_token = os.environ.get("MO_TEMP_API")
    # 優先讀取 Secrets，預設發送至 @motempinfo 頻道
    chat_id = os.environ.get("MO_TEMP_CHAT_ID") or DEFAULT_CHANNEL

    if not bot_token:
        print("⚠️ 未檢測到 MO_TEMP_API（Bot Token）。")
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
            print(f"🚀 Telegram 訊息成功推播至 {chat_id}！")
        else:
            print(f"❌ Telegram 發送失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ Telegram 請求異常: {e}")

# ==========================================
# 2. Database Helpers (temp.db)
# ==========================================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS temp_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                station_name TEXT,
                station_code TEXT,
                temp REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_time ON temp_records(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_station ON temp_records(station_name)")
        conn.commit()

def save_temp_data(pub_dt: datetime, stations_data: dict):
    init_db()
    ts_str = pub_dt.isoformat()
    cutoff_str = (pub_dt - timedelta(hours=24)).isoformat()

    rows = []
    for st_name, d in stations_data.items():
        if d.get("temp") is not None:
            rows.append((
                ts_str,
                st_name,
                d.get("code", ""),
                d.get("temp")
            ))

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO temp_records (timestamp, station_name, station_code, temp)
            VALUES (?, ?, ?, ?)
        """, rows)
        cursor.execute("DELETE FROM temp_records WHERE timestamp < ?", (cutoff_str,))
        conn.commit()

def query_24h_min_temp(pub_dt: datetime, station_name: str):
    cutoff_str = (pub_dt - timedelta(hours=24)).isoformat()
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT temp, timestamp
            FROM temp_records
            WHERE timestamp >= ? AND station_name = ? AND temp IS NOT NULL
            ORDER BY temp ASC, timestamp DESC
            LIMIT 1
        """, (cutoff_str, station_name))
        row = cursor.fetchone()
        if row and row[0] is not None:
            min_t = row[0]
            try:
                min_t_str = datetime.fromisoformat(row[1]).astimezone(MACAU_TZ).strftime("%H:%M")
            except Exception:
                min_t_str = "--:--"
            return min_t, min_t_str
    return None, "--:--"

# ==========================================
# 3. Parsing & Formatting Helpers
# ==========================================
def clean_name(n):
    return (n.replace("市政", "").replace("氣象站", "").replace("站", "")
             .replace('纪','紀').replace('孙','孫').replace('园','園')
             .replace('臺','台').replace('妈','媽').replace('阁','閣')
             .replace('东','東').replace('运','運').replace('学','學')
             .replace('莲','蓮').replace('桥','橋').strip())

def parse_float(el):
    if el is None: return None
    for tag in ['dValue', 'Value']:
        v = el.find(f'.//{tag}')
        if v is not None and v.text:
            try: return float(v.text.strip())
            except: pass
    if el.text and el.text.strip():
        try: return float(el.text.strip())
        except: pass
    return None

def get_cjk_width(s):
    width = 0
    for ch in s:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f' or '\uff01' <= ch <= '\uffee':
            width += 2
        else:
            width += 1
    return width

def pad_cjk(text, target_width):
    w = get_cjk_width(text)
    return text + (" " * max(1, target_width - w))

def fetch_smg_weather():
    url = "https://xml.smg.gov.mo/c_actualweather.xml"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        root = ET.fromstring(res.content)
    except Exception as e:
        print(f"❌ SMG XML 獲取失敗: {e}")
        return None, None, None

    pub_node = root.find('.//System/SysPubdate')
    if pub_node is None:
        pub_node = root.find('.//SysPubdate') or root.find('.//SysPubDate')
    
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

    formatted_time_str = pub_dt.strftime("%Y/%m/%d %H:%M MST")

    stations = {}
    for st in root.findall('.//WeatherReport/station'):
        name_node = st.find('stationname')
        if name_node is None or not name_node.text:
            continue
        st_clean = clean_name(name_node.text)
        target = next((k for k in STATION_COORDS if clean_name(k) in st_clean or st_clean in clean_name(k)), None)
        if not target:
            continue

        temp_val = parse_float(st.find('Temperature'))
        stations[target] = {
            "code": STATION_COORDS[target]["code"],
            "temp": temp_val
        }

    return formatted_time_str, pub_dt, stations

# ==========================================
# 4. Message Builder
# ==========================================
def build_temperature_message(pub_time_str, pub_dt, stations):
    FULL_NAME_WIDTH = 19
    st_records = []

    for st_name, d in stations.items():
        curr_t = d.get("temp")
        min_t, min_t_time = query_24h_min_temp(pub_dt, st_name)

        if min_t is None and curr_t is not None:
            min_t, min_t_time = curr_t, pub_dt.strftime("%H:%M")

        st_records.append({
            "name": st_name,
            "code": d.get("code", ""),
            "curr_temp": curr_t,
            "min_temp": min_t,
            "min_temp_time": min_t_time
        })

    # 依當前氣溫升序排列（低溫優先）
    st_records.sort(
        key=lambda x: (
            x["curr_temp"] if x["curr_temp"] is not None else 999.0,
            x["min_temp"] if x["min_temp"] is not None else 999.0
        )
    )

    msg_lines = [
        f"<b>#氣溫 {pub_time_str}</b>",
        ""
    ]

    for item in st_records:
        label = f"{item['name']} ({item['code']})"
        label_padded = pad_cjk(label, FULL_NAME_WIDTH)

        curr_str = f"{item['curr_temp']:>4.1f}" if item['curr_temp'] is not None else "  --"
        min_str = f"{item['min_temp']:>4.1f}" if item['min_temp'] is not None else "  --"
        time_str = f"({item['min_temp_time']})"

        msg_lines.append(f"<code>{label_padded} {curr_str} | {min_str} {time_str}</code>")

    return "\n".join(msg_lines)

# ==========================================
# 5. Main Execution
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
    pub_time_str, pub_dt, stations = fetch_smg_weather()
    if not pub_time_str or not stations:
        print("❌ 無法取得 SMG 數據，結束流程。")
        sys.exit(0)

    state = load_state()
    last_pubdate = state.get("last_pubdate", "")

    if pub_time_str == last_pubdate:
        print(f"ℹ️ SMG XML 尚未更新 (當前發布時間: {pub_time_str})，跳過。")
        sys.exit(0)

    print(f"🔔 檢測到新發布數據: {pub_time_str}")

    # 1. 寫入 temp.db 並維護 24 小時數據
    save_temp_data(pub_dt, stations)

    # 2. 構建並發送 Telegram 訊息至 @motempinfo
    temp_msg = build_temperature_message(pub_time_str, pub_dt, stations)
    if temp_msg:
        print(f"📤 推播氣溫數據至 {DEFAULT_CHANNEL}...")
        send_telegram(temp_msg)

    # 3. 更新狀態
    state["last_pubdate"] = pub_time_str
    save_state(state)
