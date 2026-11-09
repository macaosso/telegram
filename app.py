import os
import sys
import json
import math
import sqlite3
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "state.json"
DB_FILE = "wind.db"

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
    "路環": {"code": "DC", "disp": "路環"},
    "友誼大橋北": {"code": "PN", "disp": "友誼橋北"},
    "友誼大橋南": {"code": "PS", "disp": "友誼橋南"},
    "澳門大橋北": {"code": "MN", "disp": "澳門橋北"},
    "澳門大橋南": {"code": "MS", "disp": "澳門橋南"},
    "嘉樂庇總督大橋": {"code": "PG", "disp": "嘉樂庇橋"},
    "西灣大橋": {"code": "PV", "disp": "西灣大橋"},
    "蓮花大橋": {"code": "PL", "disp": "蓮花大橋"}
}

BRIDGE_STATIONS = [
    "友誼大橋北", "友誼大橋南", "澳門大橋北", "澳門大橋南",
    "嘉樂庇總督大橋", "西灣大橋", "蓮花大橋"
]

# ==========================================
# 0. 字體轉換（轉換為圖片中的全形/等寬數字與符號）
# ==========================================
MONO_MAP = {
    '0': '０', '1': '１', '2': '２', '3': '３', '4': '４',
    '5': '５', '6': '６', '7': '７', '8': '８', '9': '９',
    ':': '：', '.': '．', '(': '（', ')': '）', '|': '｜',
    '-': '－', ' ': '　'
}

def to_mono(text: str) -> str:
    """將常規數字、冒號、括號等字元轉換為等寬字型樣式"""
    return "".join(MONO_MAP.get(c, c) for c in str(text))

# ==========================================
# 1. Telegram Dispatch
# ==========================================
def send_telegram(text: str):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("⚠️ 未檢測到 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID。")
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
# 2. Database Helpers (wind.db)
# ==========================================
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS wind_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                station_name TEXT,
                station_code TEXT,
                wind_speed REAL,
                gust_speed REAL,
                wind_index REAL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_time ON wind_records(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_station ON wind_records(station_name)")
        conn.commit()

def save_wind_data(pub_dt: datetime, stations_data: dict, wind_idx: float):
    init_db()
    ts_str = pub_dt.isoformat()
    cutoff_str = (pub_dt - timedelta(hours=24)).isoformat()

    rows = []
    for st_name, d in stations_data.items():
        rows.append((
            ts_str,
            st_name,
            d.get("code", ""),
            d.get("wind_speed"),
            d.get("gust_speed"),
            wind_idx
        ))

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.executemany("""
            INSERT INTO wind_records (timestamp, station_name, station_code, wind_speed, gust_speed, wind_index)
            VALUES (?, ?, ?, ?, ?, ?)
        """, rows)
        cursor.execute("DELETE FROM wind_records WHERE timestamp < ?", (cutoff_str,))
        conn.commit()

def query_24h_bridge_summary(pub_dt: datetime):
    cutoff_str = (pub_dt - timedelta(hours=24)).isoformat()
    summary = {
        "max_index": None,
        "max_index_time": "",
        "tier_counts": {"颶風": 0, "暴風": 0, "烈風": 0, "強風": 0},
        "stations": {}
    }

    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()

        # 24小時最高風力指數
        cursor.execute("""
            SELECT wind_index, timestamp
            FROM wind_records
            WHERE timestamp >= ? AND wind_index IS NOT NULL
            ORDER BY wind_index DESC, timestamp DESC
            LIMIT 1
        """, (cutoff_str,))
        row = cursor.fetchone()
        if row and row[0] is not None:
            summary["max_index"] = row[0]
            try:
                dt_obj = datetime.fromisoformat(row[1]).astimezone(MACAU_TZ)
                summary["max_index_time"] = dt_obj.strftime("%H:%M")
            except Exception:
                summary["max_index_time"] = "--:--"

        # 7 座大橋 24 小時內各自最高風速與最高陣風
        for b_name in BRIDGE_STATIONS:
            cursor.execute("""
                SELECT wind_speed, timestamp
                FROM wind_records
                WHERE timestamp >= ? AND station_name = ? AND wind_speed IS NOT NULL
                ORDER BY wind_speed DESC, timestamp DESC
                LIMIT 1
            """, (cutoff_str, b_name))
            ws_row = cursor.fetchone()

            cursor.execute("""
                SELECT gust_speed, timestamp
                FROM wind_records
                WHERE timestamp >= ? AND station_name = ? AND gust_speed IS NOT NULL
                ORDER BY gust_speed DESC, timestamp DESC
                LIMIT 1
            """, (cutoff_str, b_name))
            g_row = cursor.fetchone()

            max_ws = ws_row[0] if ws_row else None
            max_ws_time = datetime.fromisoformat(ws_row[1]).astimezone(MACAU_TZ).strftime("%H:%M") if ws_row else ""

            max_g = g_row[0] if g_row else None
            max_g_time = datetime.fromisoformat(g_row[1]).astimezone(MACAU_TZ).strftime("%H:%M") if g_row else ""

            summary["stations"][b_name] = {
                "max_ws": max_ws,
                "max_ws_time": max_ws_time,
                "max_g": max_g,
                "max_g_time": max_g_time
            }

            if max_ws is not None:
                if max_ws >= 117.5:
                    summary["tier_counts"]["颶風"] += 1
                elif max_ws >= 87.5:
                    summary["tier_counts"]["暴風"] += 1
                elif max_ws >= 62.5:
                    summary["tier_counts"]["烈風"] += 1
                elif max_ws >= 40.5:
                    summary["tier_counts"]["強風"] += 1

    return summary

# ==========================================
# 3. Parsing Helpers & Weather Formulas
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

def estimate_wbgt(ta, rh, w, uvi=0.0):
    if ta is None or rh is None or w is None:
        return None
    try:
        uvi_val = 0.0 if uvi is None else float(uvi)
        rh_sqrt = math.sqrt(rh + 8.313659)
        twb = (ta * math.atan(0.151977 * rh_sqrt)
               + math.atan(ta + rh)
               - math.atan(rh - 1.676331)
               + 0.00391838 * (rh ** 1.5) * math.atan(0.023101 * rh)
               - 4.686035)
        tg = ta + (1.404 * uvi_val) - (0.111 * w)
        wbgt = 0.7 * twb + 0.2 * tg + 0.1 * ta
        return round(float(wbgt), 1)
    except:
        return None

def calculate_apparent_temperature(ta, rh, w):
    if ta is None or rh is None or w is None:
        return None
    try:
        wind_speed_ms = w / 3.6
        e = (rh / 100.0) * 6.105 * math.exp((17.27 * ta) / (237.7 + ta))
        apparent_temp = (1.04 * ta) + (0.2 * e) - (0.65 * wind_speed_ms) - 2.7
        return round(float(apparent_temp), 1)
    except:
        return None

def fetch_smg_uvi():
    url = "https://xml.smg.gov.mo/c_actualUVI.xml"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        root = ET.fromstring(res.content)
        val_node = root.find('.//UVI') or root.find('.//Value')
        if val_node is not None and val_node.text:
            return float(val_node.text.strip())
    except:
        pass
    return 0.0

def fetch_smg_weather(current_uvi=0.0):
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

        item = {
            "code": STATION_COORDS[target]["code"],
            "disp": STATION_COORDS[target]["disp"],
            "temp": parse_float(st.find('Temperature')),
            "humidity": parse_float(st.find('Humidity')),
            "rain_h": 0.0,
            "wind_speed": None,
            "gust_speed": None,
            "wind_dir": ""
        }

        for rf in st.findall('Rainfall'):
            type_node = rf.find('Type')
            if type_node is not None and type_node.text and type_node.text.strip() == '4':
                val = parse_float(rf)
                if val is not None:
                    item['rain_h'] = val

        for el in st.iter():
            tag = el.tag.split('}')[-1].lower()
            if tag in ['windspeed', 'wind']:
                v = parse_float(el)
                if v is not None: item['wind_speed'] = v
            elif tag in ['windgust', 'gust']:
                v = parse_float(el)
                if v is not None: item['gust_speed'] = v
            elif 'direction' in tag or tag == 'dir':
                if el.text and el.text.strip():
                    item['wind_dir'] = el.text.strip().upper()
                else:
                    v_node = el.find('.//Value')
                    if v_node is not None and v_node.text:
                        item['wind_dir'] = v_node.text.strip().upper()

        ta = item['temp']
        rh = item['humidity']
        w = item['wind_speed'] or 0.0
        item['apparent'] = calculate_apparent_temperature(ta, rh, w)
        item['wbgt'] = estimate_wbgt(ta, rh, w, current_uvi)

        stations[target] = item

    return formatted_time_str, pub_dt, stations

def get_display_width(s):
    width = 0
    for char in s:
        width += 2 if '\u4e00' <= char <= '\u9fff' or '\u3000' <= char <= '\u303f' or '\uff01' <= char <= '\uffee' else 1
    return width

# ==========================================
# 4. 蒲福氏風級與風力指數計算
# ==========================================
def get_beaufort_scale(v):
    if v is None: return "--"
    v = float(v)
    if v <= 1.4: return "F0"
    elif v <= 6.4: return "F1"
    elif v <= 12.4: return "F2"
    elif v <= 19.4: return "F3"
    elif v <= 30.4: return "F4"
    elif v <= 40.4: return "F5"
    elif v <= 51.4: return "F6"
    elif v <= 62.4: return "F7"
    elif v <= 75.4: return "F8"
    elif v <= 87.4: return "F9"
    elif v <= 103.4: return "F10"
    elif v <= 117.4: return "F11"
    else: return "F12"

def compute_wind_index(stations_data):
    land_ws, land_gust = [], []
    bridge_ws, bridge_gust = [], []

    for name, d in stations_data.items():
        ws = d.get('wind_speed')
        g = d.get('gust_speed')
        is_bridge = name in BRIDGE_STATIONS

        if ws is not None:
            (bridge_ws if is_bridge else land_ws).append(float(ws))
        if g is not None:
            (bridge_gust if is_bridge else land_gust).append(float(g))

    U = (sum(land_ws) / len(land_ws)) if land_ws else 0.0
    Ug = (sum(land_gust) / len(land_gust)) if land_gust else 0.0
    V = (sum(bridge_ws) / len(bridge_ws)) if bridge_ws else 0.0
    Vg = (sum(bridge_gust) / len(bridge_gust)) if bridge_gust else 0.0

    def get_r(a):
        r = -0.00005 * (a ** 2) + 0.0081 * a - 0.0005
        return max(r, 0.0)

    Ur = get_r(U)
    Vr = get_r(V)
    wind_idx = 0.56 * ((1.0 - Ur) * U + Ur * Ug) + 0.44 * ((1.0 - Vr) * V + Vr * Vg)
    idx_rounded = round(wind_idx, 2)

    if idx_rounded <= 13.0: level, grade = "L1", "輕微"
    elif idx_rounded <= 24.0: level, grade = "L2", "清勁"
    elif idx_rounded <= 44.0: level, grade = "L3", "強勁"
    elif idx_rounded <= 74.0: level, grade = "L4", "強烈"
    else: level, grade = "L5", "極強"

    return {
        "index": idx_rounded,
        "level": level,
        "grade": grade,
        "bridge_avg_ws": round(V, 1),
        "bridge_avg_gust": round(Vg, 1)
    }

# ==========================================
# 5. Message Builders
# ==========================================

def format_bridge_col(val, time_str):
    """格式化風速/陣風欄位，確保長度一致以對齊 | 符號"""
    if val is not None:
        v_int = round(val)
        val_str = f"{v_int:>2}"
        t_str = f"({time_str})"
    else:
        val_str = " --"
        t_str = "(--:--)"
    # 組合並轉換為全形等寬字元
    return to_mono(f"{val_str} {t_str}")

# --- 5.1 風力訊息 ---
def build_wind_message(pub_time_str, pub_dt, stations):
    wind_info = compute_wind_index(stations)

    tier_defs = [
        ("颶風", lambda ws, g: ws is not None and ws >= 117.5),
        ("暴風", lambda ws, g: ws is not None and 87.5 <= ws < 117.5),
        ("烈風", lambda ws, g: ws is not None and 62.5 <= ws < 87.5),
        ("強風", lambda ws, g: ws is not None and 40.5 <= ws < 62.5),
        ("颶陣", lambda ws, g: g is not None and g >= 117.5),
        ("暴陣", lambda ws, g: g is not None and 87.5 <= g < 117.5),
        ("烈陣", lambda ws, g: g is not None and 62.5 <= g < 87.5),
        ("強陣", lambda ws, g: g is not None and 40.5 <= g < 62.5),
    ]

    tier_stations = {name: [] for name, _ in tier_defs}
    assigned_stations = set()

    for tier_name, condition in tier_defs:
        for st_name, d in stations.items():
            if st_name in assigned_stations:
                continue

            ws = d.get('wind_speed')
            g = d.get('gust_speed')

            if condition(ws, g):
                assigned_stations.add(st_name)
                code = d.get('code', STATION_COORDS.get(st_name, {}).get('code', ''))
                tier_stations[tier_name].append({
                    "name": st_name,
                    "code": code,
                    "ws": ws,
                    "gust": g,
                    "ws_sort": float(ws) if ws is not None else -1.0,
                    "gust_sort": float(g) if g is not None else -1.0
                })

    if not assigned_stations:
        return None

    w_idx_str = f"{wind_info['index']:.2f}"
    msg_lines = [
        f"#風力 {pub_time_str}",
        f"風力指數：{to_mono(w_idx_str)} ({wind_info['level']}, {wind_info['grade']})",
        ""
    ]

    TARGET_LABEL_WIDTH = 20

    # 即時風力各分級列表
    for tier_name, _ in tier_defs:
        st_list = tier_stations[tier_name]
        if not st_list:
            continue

        st_list.sort(key=lambda x: (x["ws_sort"], x["gust_sort"]), reverse=True)

        group_lines = [f"<b>#{tier_name}({to_mono(len(st_list))})</b>"]
        for item in st_list:
            ws_val = item['ws']
            g_val = item['gust']

            ws_f = get_beaufort_scale(ws_val)
            g_f = get_beaufort_scale(g_val)

            ws_num_str = to_mono(f"{ws_val:>4.1f}") if ws_val is not None else "　－－"
            g_num_str = to_mono(f"{g_val:>4.1f}") if g_val is not None else "　－－"

            ws_str = f"{ws_num_str} ({ws_f})"
            g_str = f"{g_num_str} ({g_f})"

            label = f"{item['name']} ({item['code']})"
            pad = " " * max(1, TARGET_LABEL_WIDTH - get_display_width(label))

            group_lines.append(f"<code>{label}{pad}{ws_str} ｜ {g_str}</code>")

        msg_lines.append("\n".join(group_lines))

    avg_ws_str = f"{wind_info['bridge_avg_ws']:.1f}"
    avg_gust_str = f"{wind_info['bridge_avg_gust']:.1f}"

    msg_lines.extend([
        "",
        f"大橋平均風速：{to_mono(avg_ws_str)} km/h",
        f"大橋平均陣風：{to_mono(avg_gust_str)} km/h"
    ])

    # 移除 strong_wind_count 限制：只要風力訊息觸發發送，必定附加 24 小時大橋紀錄
    b_summary = query_24h_bridge_summary(pub_dt)
    max_idx_val = b_summary["max_index"]
    max_idx_str = to_mono(f"{max_idx_val:.2f}") if max_idx_val is not None else "－－"
    max_idx_time_str = to_mono(f"({b_summary['max_index_time']})") if b_summary['max_index_time'] else "（－－:－－）"

    tc = b_summary["tier_counts"]
    # 7 中 0 則不顯示，只列出 > 0 的等級
    tier_sub_items = []
    for t_name in ["颶風", "暴風", "烈風", "強風"]:
        count = tc[t_name]
        if count > 0:
            tier_sub_items.append(f"{t_name}７中{to_mono(count)}")

    bridge_lines = [
        "",
        f"２４小時橋上最高風力指數：{max_idx_str} {max_idx_time_str}"
    ]

    if tier_sub_items:
        bridge_lines.append("、".join(tier_sub_items))

    # 大橋名稱包含代號時，最長為「嘉樂庇總督大橋 (PG)」（寬度 19）
    TARGET_BRIDGE_WIDTH = 20

    for b_name in BRIDGE_STATIONS:
        code = STATION_COORDS.get(b_name, {}).get("code", "")
        label = f"{b_name} ({code})"
        name_width = get_display_width(label)
        pad = " " * max(1, TARGET_BRIDGE_WIDTH - name_width)

        st_info = b_summary["stations"].get(b_name, {})
        max_ws = st_info.get("max_ws")
        max_ws_t = st_info.get("max_ws_time")
        max_g = st_info.get("max_g")
        max_g_t = st_info.get("max_g_time")

        ws_part = format_bridge_col(max_ws, max_ws_t)
        g_part = format_bridge_col(max_g, max_g_t)

        # 輸出 <code>，確保縱向對齊在 | 符號
        bridge_lines.append(f"<code>{label}{pad}{ws_part} ｜ {g_part}</code>")

    msg_lines.extend(bridge_lines)

    return "\n".join(msg_lines)


# --- 5.2 氣溫訊息 ---
def build_temperature_message(pub_time_str, stations):
    TARGET_WIDTH = 18

    temp_list = []
    for st_name, d in stations.items():
        t = d.get('temp')
        if t is None: continue
        code = d.get('code', '')
        label = f"{st_name} ({code})"

        rank = None
        icon = ""
        if t >= 37.5: rank, icon = 1, "🔴"
        elif 35.5 <= t < 37.5: rank, icon = 2, "🟠"
        elif 32.5 <= t < 35.5: rank, icon = 3, "🟡"
        elif t <= 3.4: rank, icon = 4, "🟣"
        elif 3.5 <= t < 7.5: rank, icon = 5, "🔵"
        elif 7.5 <= t <= 12.4: rank, icon = 6, "🟢"

        if rank is not None:
            temp_list.append({"label": label, "val": t, "rank": rank, "icon": icon})

    app_list = []
    for st_name, d in stations.items():
        at = d.get('apparent')
        if at is None: continue
        code = d.get('code', '')
        label = f"{st_name} ({code})"

        rank = None
        icon = ""
        if at >= 37.5: rank, icon = 1, "🔴"
        elif 35.5 <= at < 37.5: rank, icon = 2, "🟠"
        elif at <= 3.4: rank, icon = 3, "🟣"
        elif 3.5 <= at < 7.5: rank, icon = 4, "🔵"

        if rank is not None:
            app_list.append({"label": label, "val": at, "rank": rank, "icon": icon})

    wbgt_list = []
    for st_name, d in stations.items():
        wb = d.get('wbgt')
        if wb is None: continue
        code = d.get('code', '')
        label = f"{st_name} ({code})"

        rank = None
        icon = ""
        if wb >= 32.5: rank, icon = 1, "⚫"
        elif 30.5 <= wb < 32.5: rank, icon = 2, "🔴"
        elif 29.5 <= wb < 30.5: rank, icon = 3, "🟠"

        if rank is not None:
            wbgt_list.append({"label": label, "val": wb, "rank": rank, "icon": icon})

    if not temp_list and not app_list and not wbgt_list:
        return None

    msg_sections = [f"#氣溫 {pub_time_str}"]

    if temp_list:
        temp_list.sort(key=lambda x: (x["rank"], -x["val"] if x["rank"] <= 3 else x["val"]))
        lines = ["<b>氣溫</b>", "<pre>"]
        for item in temp_list:
            pad = " " * max(1, TARGET_WIDTH - get_display_width(item['label']))
            lines.append(f"{item['label']}{pad}{item['val']:>4.1f} °C {item['icon']}")
        lines.append("</pre>")
        msg_sections.append("\n".join(lines))

    if app_list:
        app_list.sort(key=lambda x: (x["rank"], -x["val"] if x["rank"] <= 2 else x["val"]))
        lines = ["<b>體感</b>", "<pre>"]
        for item in app_list:
            pad = " " * max(1, TARGET_WIDTH - get_display_width(item['label']))
            lines.append(f"{item['label']}{pad}{item['val']:>4.1f} °C {item['icon']}")
        lines.append("</pre>")
        msg_sections.append("\n".join(lines))

    if wbgt_list:
        wbgt_list.sort(key=lambda x: (x["rank"], -x["val"]))
        lines = ["<b>暑熱</b>", "<pre>"]
        for item in wbgt_list:
            pad = " " * max(1, TARGET_WIDTH - get_display_width(item['label']))
            lines.append(f"{item['label']}{pad}{item['val']:>4.1f} {item['icon']}")
        lines.append("</pre>")
        msg_sections.append("\n".join(lines))

    return "\n\n".join(msg_sections)

# --- 5.3 降雨訊息 ---
def build_rainfall_message(pub_time_str, stations):
    TARGET_WIDTH = 18
    rain_list = []

    for st_name, d in stations.items():
        r = d.get('rain_h')
        if r is not None and r > 0.0:
            code = d.get('code', '')
            label = f"{st_name} ({code})"

            icon = ""
            if r >= 100.0: icon = " 🟣"
            elif 80.0 <= r < 100.0: icon = " ⚫"
            elif 50.0 <= r < 80.0: icon = " 🔴"
            elif 20.0 <= r < 50.0: icon = " 🟡"

            rain_list.append({"label": label, "val": r, "icon": icon})

    if not rain_list:
        return None

    rain_list.sort(key=lambda x: x["val"], reverse=True)

    lines = [f"#降雨 {pub_time_str}", "<pre>"]
    for item in rain_list:
        pad = " " * max(1, TARGET_WIDTH - get_display_width(item['label']))
        lines.append(f"{item['label']}{pad}{item['val']:>4.1f} mm{item['icon']}")
    lines.append("</pre>")

    return "\n".join(lines)

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
    current_uvi = fetch_smg_uvi()
    pub_time_str, pub_dt, stations = fetch_smg_weather(current_uvi=current_uvi)
    if not pub_time_str or not stations:
        print("❌ 無法取得 SMG 數據，結束流程。")
        sys.exit(0)

    state = load_state()
    last_pubdate = state.get("last_pubdate", "")

    if pub_time_str == last_pubdate:
        print(f"ℹ️ SMG XML 尚未更新 (當前發布時間: {pub_time_str})，跳過。")
        sys.exit(0)

    print(f"🔔 檢測到新發布數據: {pub_time_str}")

    wind_calc = compute_wind_index(stations)
    save_wind_data(pub_dt, stations, wind_calc["index"])

    wind_msg = build_wind_message(pub_time_str, pub_dt, stations)
    temp_msg = build_temperature_message(pub_time_str, stations)
    rain_msg = build_rainfall_message(pub_time_str, stations)

    if wind_msg:
        print("📤 滿足風力警報標準，發送 Telegram 訊息...")
        send_telegram(wind_msg)
    else:
        print("ℹ️ 未達風力推播標準。")

    if temp_msg:
        print("📤 滿足氣溫/體感/暑熱警報標準，發送 Telegram 訊息...")
        send_telegram(temp_msg)
    else:
        print("ℹ️ 未達氣溫推播標準。")

    if rain_msg:
        print("📤 過去1小時雨量大於0，發送降雨 Telegram 訊息...")
        send_telegram(rain_msg)
    else:
        print("ℹ️ 各站過去1小時雨量均為0。")

    state["last_pubdate"] = pub_time_str
    save_state(state)