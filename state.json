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
    
    pub_str = ""
    if pub_node is not None and pub_node.text:
        raw_time = pub_node.text.strip()
        for fmt in ["%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M:%S"]:
            try:
                dt = datetime.strptime(raw_time, fmt).replace(tzinfo=MACAU_TZ)
                pub_str = dt.strftime("%Y-%m-%d %H:%M")
                break
            except ValueError:
                continue

    if not pub_str:
        pub_str = datetime.now(MACAU_TZ).strftime("%Y-%m-%d %H:%M")

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

        stations[target] = item

    return pub_str, stations

# ==========================================
# 3. State Management
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

# ==========================================
# 4. Message Builders (3 Separate Categories)
# ==========================================
def build_temperature_message(pub_time, stations):
    # 閾值設定：酷熱 (>= 33.0°C) 或 寒冷 (<= 12.0°C)
    HOT_LIMIT = 33.0
    COLD_LIMIT = 12.0

    hot_hits = {st: d["temp"] for st, d in stations.items() if d.get("temp") is not None and d["temp"] >= HOT_LIMIT}
    cold_hits = {st: d["temp"] for st, d in stations.items() if d.get("temp") is not None and d["temp"] <= COLD_LIMIT}

    if not hot_hits and not cold_hits:
        return None

    lines = []
    if hot_hits:
        lines.append("☀️ <b>【極端酷熱警報】</b>")
        for st, t in sorted(hot_hits.items(), key=lambda x: x[1], reverse=True):
            lines.append(f"• <b>{st}</b>: <code>{t:.1f}°C</code>")
    if cold_hits:
        if lines: lines.append("")
        lines.append("❄️ <b>【嚴寒低溫警報】</b>")
        for st, t in sorted(cold_hits.items(), key=lambda x: x[1]):
            lines.append(f"• <b>{st}</b>: <code>{t:.1f}°C</code>")

    return f"🌡️ <b>【澳門氣溫即時通報】</b>\n觀測時間: <code>{pub_time}</code>\n\n" + "\n".join(lines)

def build_wind_message(pub_time, stations):
    # 閾值設定：強風 (風速 >= 41.0 km/h) 或 強陣風 (陣風 >= 70.0 km/h)
    WIND_SPEED_LIMIT = 41.0
    GUST_LIMIT = 70.0

    wind_hits = {st: d for st, d in stations.items() if (d.get("wind_speed") and d["wind_speed"] >= WIND_SPEED_LIMIT) or (d.get("gust_speed") and d["gust_speed"] >= GUST_LIMIT)}

    if not wind_hits:
        return None

    lines = ["💨 <b>【強風 / 強陣風即時通報】</b>"]
    for st, d in sorted(wind_hits.items(), key=lambda x: (x[1]["gust_speed"] or 0), reverse=True):
        ws = f"{d['wind_speed']:.1f} km/h" if d.get('wind_speed') is not None else "--"
        g = f"{d['gust_speed']:.1f} km/h" if d.get('gust_speed') is not None else "--"
        direction = f" ({d['wind_dir']})" if d.get('wind_dir') else ""
        lines.append(f"• <b>{st}</b>: 平均風 <code>{ws}</code> | 陣風 <code>{g}</code>{direction}")

    return f"🌬️ <b>【澳門風力警報】</b>\n觀測時間: <code>{pub_time}</code>\n\n" + "\n".join(lines)

def build_rainfall_message(pub_time, stations):
    # 閾值設定：過去 1 小時雨量 >= 10.0 mm (或達到暴雨 50.0 mm)
    RAIN_LIMIT = 10.0

    rain_hits = {st: d["rain_h"] for st, d in stations.items() if d.get("rain_h") is not None and d["rain_h"] >= RAIN_LIMIT}

    if not rain_hits:
        return None

    lines = ["🌧️ <b>【強降雨即時通報】</b>"]
    for st, r in sorted(rain_hits.items(), key=lambda x: x[1], reverse=True):
        tag = " ⚠️ [暴雨級別]" if r >= 50.0 else ""
        lines.append(f"• <b>{st}</b>: 時雨量 <code>{r:.1f} mm</code>{tag}")

    return f"☔ <b>【澳門降雨警報】</b>\n觀測時間: <code>{pub_time}</code>\n\n" + "\n".join(lines)

# ==========================================
# 5. Main Execution
# ==========================================
if __name__ == "__main__":
    pub_time, stations = fetch_smg_weather()
    if not pub_time or not stations:
        print("❌ 無法取得 SMG 數據，結束流程。")
        sys.exit(0)

    state = load_state()
    last_pubdate = state.get("last_pubdate", "")

    # XML 發布時間未變更時跳過，確保不重複推播
    if pub_time == last_pubdate:
        print(f"ℹ️ SMG XML 尚未更新 (當前發布時間: {pub_time})，跳過推播。")
        sys.exit(0)

    print(f"🔔 偵測到 SMG XML 更新: {pub_time}")

    temp_msg = build_temperature_message(pub_time, stations)
    wind_msg = build_wind_message(pub_time, stations)
    rain_msg = build_rainfall_message(pub_time, stations)

    # 依序獨立發送 3 則訊息
    if temp_msg:
        print("📤 發送氣溫警報...")
        send_telegram(temp_msg)

    if wind_msg:
        print("📤 發送風速警報...")
        send_telegram(wind_msg)

    if rain_msg:
        print("📤 發送降雨警報...")
        send_telegram(rain_msg)

    state["last_pubdate"] = pub_time
    save_state(state)
