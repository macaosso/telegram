import os
import sys
import json
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "rain_state.json"
DB_FILE = "rain.db"

# Station regions for max 24H calculation
STATION_COORDS = {
    "紀念孫中山公園": {"code": "DP", "disp": "孫中山公園", "region": "澳門"},
    "黑沙環": {"code": "EM", "disp": "黑沙環", "region": "澳門"},
    "大炮台": {"code": "FM", "disp": "大炮台", "region": "澳門"},
    "外港碼頭": {"code": "PE", "disp": "外港碼頭", "region": "澳門"},
    "媽閣": {"code": "MM", "disp": "媽閣", "region": "澳門"},
    "大潭山": {"code": "TG", "disp": "大潭山", "region": "氹仔"},
    "東亞運大馬路": {"code": "JA", "disp": "東亞運", "region": "氹仔"},
    "澳門大學": {"code": "UM", "disp": "澳門大學", "region": "氹仔"},
    "九澳": {"code": "KV", "disp": "九澳", "region": "路環"},
    "路環": {"code": "DC", "disp": "路環", "region": "路環"},
}

# ==========================================
# 1. Telegram Dispatch
# ==========================================
def send_telegram(text: str):
    bot_token = os.environ.get("RAIN_API_TOKEN")
    chat_id = "@moraindata"

    if not bot_token:
        print("⚠️ 未檢測到 RAIN_API_TOKEN。")
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
# 2. Database Helpers (rain.db)
# ==========================================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS rain_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                station_name TEXT,
                station_code TEXT,
                region TEXT,
                rain_1m REAL,
                rain_1h REAL,
                rain_daily REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_time ON rain_records(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_station ON rain_records(station_name)")
        conn.commit()

def save_rain_data(pub_dt: datetime, stations_data: dict):
    init_db()
    ts_str = pub_dt.isoformat()
    # Keep 72 hours of data to safely calculate 48H history of 24H rolling sums
    cutoff_str = (pub_dt - timedelta(hours=72)).isoformat()

    rows = []
    for st_name, d in stations_data.items():
        rows.append((
            ts_str,
            st_name,
            d.get("code", ""),
            d.get("region", ""),
            d.get("rain_1m"),
            d.get("rain_1h"),
            d.get("rain_daily")
        ))

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO rain_records (timestamp, station_name, station_code, region, rain_1m, rain_1h, rain_daily)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, rows)
        cursor.execute("DELETE FROM rain_records WHERE timestamp < ?", (cutoff_str,))
        conn.commit()

def calculate_24h_rain(station_name: str, current_dt: datetime, conn: sqlite3.Connection):
    """
    Calculate 24H rainfall by summing the past 24 hourly '1-hour rainfall' (Type 4) values.
    """
    cursor = conn.cursor()
    total_rain = 0.0
    
    for i in range(24):
        target_dt = current_dt - timedelta(hours=i)
        
        # If target is exactly 00:00, use 23:55 from previous day
        if target_dt.hour == 0 and target_dt.minute == 0:
            target_dt = target_dt - timedelta(minutes=5)
            
        target_str = target_dt.isoformat()
        
        cursor.execute("""
            SELECT rain_1h 
            FROM rain_records 
            WHERE station_name = ? AND timestamp <= ?
            ORDER BY timestamp DESC LIMIT 1
        """, (station_name, target_str))
        
        row = cursor.fetchone()
        if row and row[0] is not None:
            total_rain += row[0]
            
    return total_rain

def query_max_24h_past_48h(current_dt: datetime):
    """
    Calculate the maximum 24H accumulated rain over the past 48 hours for each region.
    """
    regions = {"澳門": {"val": 0.0, "code": ""}, "氹仔": {"val": 0.0, "code": ""}, "路環": {"val": 0.0, "code": ""}}
    
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        
        # Get all timestamps in the past 48 hours
        cutoff_dt = current_dt - timedelta(hours=48)
        cursor.execute("""
            SELECT DISTINCT timestamp FROM rain_records 
            WHERE timestamp >= ? ORDER BY timestamp
        """, (cutoff_dt.isoformat(),))
        
        timestamps = [datetime.fromisoformat(row[0]) for row in cursor.fetchall()]
        
        for st_name, meta in STATION_COORDS.items():
            reg = meta["region"]
            code = meta["code"]
            
            for ts in timestamps:
                rain_24h = calculate_24h_rain(st_name, ts, conn)
                if rain_24h > regions[reg]["val"]:
                    regions[reg]["val"] = rain_24h
                    regions[reg]["code"] = code
                    
    return regions

def get_current_24h_rain_all(current_dt: datetime):
    results = []
    with sqlite3.connect(DB_FILE) as conn:
        for st_name, meta in STATION_COORDS.items():
            rain_24h = calculate_24h_rain(st_name, current_dt, conn)
            results.append({
                "name": st_name,
                "code": meta["code"],
                "val": rain_24h
            })
    return results

# ==========================================
# 3. Parsing Helpers
# ==========================================
def clean_name(n):
    return (n.replace("市政", "").replace("氣象站", "").replace("站", "")
             .replace('纪','紀').replace('孙','孫').replace('园','園')
             .replace('臺','台').replace('妈','媽').replace('阁','閣')
             .replace('东','東').replace('运','運').replace('学','學')
             .strip())

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

    formatted_time_str = pub_dt.strftime("%Y-%m-%d  %H:%M MST")

    stations = {}
    for st in root.findall('.//WeatherReport/station'):
        name_node = st.find('stationname')
        if name_node is None or not name_node.text:
            continue
        st_clean = clean_name(name_node.text)
        target = next((k for k in STATION_COORDS if clean_name(k) in st_clean or st_clean in clean_name(k)), None)
        if not target:
            continue

        item = {
            "code": STATION_COORDS[target]["code"],
            "region": STATION_COORDS[target]["region"],
            "rain_1m": 0.0,
            "rain_1h": 0.0,
            "rain_daily": 0.0
        }

        for rf in st.findall('.//Rainfall'):
            t_node = rf.find('Type')
            if t_node is not None and t_node.text:
                r_type = t_node.text.strip()
                v = parse_float(rf)
                if v is not None:
                    if r_type == "3":
                        item["rain_1m"] = v
                    elif r_type == "4":
                        item["rain_1h"] = v
                    elif r_type == "5":
                        item["rain_daily"] = v

        stations[target] = item

    return formatted_time_str, pub_dt, stations

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

def get_1h_indicator(val):
    if val >= 100: return "🟣"
    if val >= 80: return "⚫"
    if val >= 50: return "🔴"
    if val >= 20: return "🟡"
    if val >= 10: return "🟢"
    return ""

def get_24h_indicator(val):
    if val >= 350: return "⚫"
    if val >= 200: return "🔴"
    if val >= 100: return "🟠"
    return ""

# ==========================================
# 5. Message Builder
# ==========================================
def build_rain_message(pub_time_str, pub_dt, stations):
    # Sort for Block 1 (1H descending)
    sorted_1h = sorted(stations.items(), key=lambda x: x[1]['rain_1h'], reverse=True)
    
    msg_lines = [
        f"<code>{pub_time_str}</code>\n"
    ]
    
    # Block 1: 1-min | 1-hour
    FULL_NAME_WIDTH = 18
    for st_name, data in sorted_1h:
        label = f"{st_name} ({data['code']})"
        label_padded = pad_cjk(label, FULL_NAME_WIDTH)
        ind = get_1h_indicator(data['rain_1h'])
        msg_lines.append(f"<code>{label_padded} {data['rain_1m']:>4.1f} | {data['rain_1h']:>4.1f} {ind}</code>")

    # Block 2: Current 24H Rain
    msg_lines.extend(["", "<code>二十四小時雨量:</code>"])
    current_24h = get_current_24h_rain_all(pub_dt)
    current_24h.sort(key=lambda x: x['val'], reverse=True)
    
    for item in current_24h:
        label = f"{item['name']} ({item['code']})"
        label_padded = pad_cjk(label, FULL_NAME_WIDTH)
        ind = get_24h_indicator(item['val'])
        msg_lines.append(f"<code>{label_padded} {item['val']:>4.1f} {ind}</code>")

    # Block 3: Max 24H in Past 48H
    msg_lines.extend(["", "<code>過去48小時內最高24小時累積雨量:</code>"])
    max_48h = query_max_24h_past_48h(pub_dt)
    for region in ["澳門", "氹仔", "路環"]:
        data = max_48h[region]
        code_str = f" ({data['code']})" if data['code'] else ""
        ind = get_24h_indicator(data['val'])
        msg_lines.append(f"<code>{region}：{data['val']:.1f} mm{code_str} {ind}</code>")
        
    return "\n".join(msg_lines)

# ==========================================
# 6. Main Execution
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

    save_rain_data(pub_dt, stations)
    rain_msg = build_rain_message(pub_time_str, pub_dt, stations)
    
    # Send message based on conditions if desired, or send all updates:
    send_telegram(rain_msg)

    state["last_pubdate"] = pub_time_str
    save_state(state)
