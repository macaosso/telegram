import os
import sys
import json
import xml.etree.ElementTree as ET
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

MACAU_TZ = ZoneInfo('Asia/Macau')
STATE_FILE = "state.json"

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
            print("🚀 Telegram 風力訊息發送成功！")
        else:
            print(f"❌ Telegram 發送失敗: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"❌ Telegram 請求異常: {e}")

# ==========================================
# 2. Parsing Helpers
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

def fetch_smg_weather():
    url = "https://xml.smg.gov.mo/c_actualweather.xml"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        res.encoding = 'utf-8'
        root = ET.fromstring(res.content)
    except Exception as e:
        print(f"❌ SMG XML 獲取失敗: {e}")
        return None, None

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

    # 轉為 2026/11/04 20:00 MST 格式
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

        stations[target] = item

    return formatted_time_str, stations

# ==========================================
# 3. Wind Index & Bridge Metrics
# ==========================================
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

    if idx_rounded <= 13.0:
        level, grade = "L1", "輕微"
    elif idx_rounded <= 24.0:
        level, grade = "L2", "清勁"
    elif idx_rounded <= 44.0:
        level, grade = "L3", "強勁"
    elif idx_rounded <= 74.0:
        level, grade = "L4", "強烈"
    else:
        level, grade = "L5", "極強"

    return {
        "index": idx_rounded,
        "level": level,
        "grade": grade,
        "bridge_avg_ws": round(V, 1),
        "bridge_avg_gust": round(Vg, 1)
    }

# ==========================================
# 4. Wind Message Builder
# ==========================================
def build_wind_message(pub_time_str, stations):
    wind_info = compute_wind_index(stations)

    # 站點單一歸類容器 (保持唯一出現與最高優先級)
    tiers_wind = {"颶風": [], "暴風": [], "烈風": [], "強風": []}
    tiers_gust = {"颶陣": [], "暴陣": [], "烈陣": [], "強陣": []}

    # 1. 平均風速優先級分配
    for st_name, d in stations.items():
        ws = d.get('wind_speed')
        code = d.get('code', st_name)
        if ws is None:
            continue
        if ws >= 117.5:
            tiers_wind["颶風"].append(code)
        elif ws >= 87.5:
            tiers_wind["暴風"].append(code)
        elif ws >= 62.5:
            tiers_wind["烈風"].append(code)
        elif ws >= 40.5:
            tiers_wind["強風"].append(code)

    # 2. 最高陣風優先級分配
    for st_name, d in stations.items():
        g = d.get('gust_speed')
        code = d.get('code', st_name)
        if g is None:
            continue
        if g >= 117.5:
            tiers_gust["颶陣"].append(code)
        elif g >= 87.5:
            tiers_gust["暴陣"].append(code)
        elif g >= 62.5:
            tiers_gust["烈陣"].append(code)
        elif g >= 0.5:
            tiers_gust["強陣"].append(code)

    # 檢查是否有任何站點達標
    total_hits = sum(len(v) for v in tiers_wind.values()) + sum(len(v) for v in tiers_gust.values())
    if total_hits == 0:
        return None

    def fmt_line(label, codes):
        code_str = f" {','.join(codes)}" if codes else ""
        return f"{label}({len(codes)})：{code_str}"

    msg_lines = [
        "#風力",
        f"{pub_time_str}",
        f"風力指數：{wind_info['index']:.2f} ({wind_info['level']}, {wind_info['grade']})",
        "",
        fmt_line("颶風", tiers_wind["颶風"]),
        fmt_line("颶陣", tiers_gust["颶陣"]),
        fmt_line("暴風", tiers_wind["暴風"]),
        fmt_line("暴陣", tiers_gust["暴陣"]),
        fmt_line("烈風", tiers_wind["烈風"]),
        fmt_line("烈陣", tiers_gust["烈陣"]),
        fmt_line("強風", tiers_wind["強風"]),
        fmt_line("強陣", tiers_gust["強陣"]),
        "",
        f"大橋平均風速：{wind_info['bridge_avg_ws']:.1f} km/h",
        f"大橋平均陣風：{wind_info['bridge_avg_gust']:.1f} km/h"
    ]

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
    pub_time_str, stations = fetch_smg_weather()
    if not pub_time_str or not stations:
        print("❌ 無法取得 SMG 數據，結束流程。")
        sys.exit(0)

    state = load_state()
    last_pubdate = state.get("last_pubdate", "")

    # XML 時間未更新則跳過
    if pub_time_str == last_pubdate:
        print(f"ℹ️ SMG XML 尚未更新 (當前發布時間: {pub_time_str})，跳過。")
        sys.exit(0)

    print(f"🔔 檢測到新發布數據: {pub_time_str}")

    wind_message = build_wind_message(pub_time_str, stations)

    if wind_message:
        print("📤 滿足風力警報標準，發送 Telegram 訊息...")
        send_telegram(wind_message)
    else:
        print("ℹ️ 所有站點平均風與陣風均低於 40.5 km/h，無需發布風力通報。")

    state["last_pubdate"] = pub_time_str
    save_state(state)
