"""สภาพอากาศปัจจุบันที่ "วัดได้จริง" จากสถานีใกล้ที่สุด

- กรมอุตุนิยมวิทยา (TMD) Weather3Hours: สถานีอุตุฯ ~125 แห่ง ทุก 3 ชม. (01, 04, 07, 10, 13, 16, 19, 22 น.)
  อุณหภูมิ ความชื้น ฝน 3 ชม./24 ชม. ลม ความกดอากาศ ทัศนวิสัย
- Air4Thai (กรมควบคุมมลพิษ): PM2.5 รายชั่วโมงจากสถานีตรวจวัดคุณภาพอากาศ

ค่าจากโมเดลปัจจุบัน (Open-Meteo current) และเรดาร์ (RainViewer) หน้าเว็บดึงเองในเบราว์เซอร์เพื่อให้สดที่สุด
"""
import math
import xml.etree.ElementTree as ET

import pandas as pd
import requests
import urllib3

import weather_core as core

TMD_OBS_URL = "https://data.tmd.go.th/api/Weather3Hours/V2/"
AIR4THAI_URL = "http://air4thai.pcd.go.th/services/getNewAQI_JSON.php"
MAX_STATION_KM = 60                    # ไกลกว่านี้ถือว่าไม่เป็นตัวแทนของตำแหน่ง
AQI_LEVELS = {"1": "🔵 ดีมาก", "2": "🟢 ดี", "3": "🟡 ปานกลาง", "4": "🟠 เริ่มมีผลต่อสุขภาพ", "5": "🔴 มีผลต่อสุขภาพ"}


def distance_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _f(text):
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return None if v <= -99 else v


def fetch_tmd_stations(cfg=None):
    """ค่าตรวจวัดล่าสุดของสถานีอุตุฯ (uid/ukey เริ่มต้นเป็นคีย์สาธิตที่ TMD เผยแพร่ ตั้งเองได้ใน config)"""
    cfg = cfg or core.CFG
    r = core.SESSION.get(TMD_OBS_URL, params={"uid": cfg.get("tmd_obs_uid", "api"),
                                              "ukey": cfg.get("tmd_obs_ukey", "api12345")}, timeout=60)
    r.raise_for_status()
    out = []
    for s in ET.fromstring(r.content).iter("Station"):
        g = lambda tag: (s.findtext(f".//{tag}") or "").strip()     # ค่าวัดอยู่ใต้ element ย่อย
        lat, lon = _f(g("Latitude")), _f(g("Longitude"))
        if lat is None or lon is None:
            continue
        try:
            t = pd.to_datetime(g("DateTime"), format="%m/%d/%Y %H:%M:%S").isoformat()
        except ValueError:
            t = None
        out.append({
            "name": g("StationNameThai"), "province": g("Province"), "lat": lat, "lon": lon, "time": t,
            "temp": _f(g("AirTemperature")), "rh": _f(g("RelativeHumidity")), "dew": _f(g("DewPoint")),
            "rain_3h": _f(g("Rainfall")), "rain_24h": _f(g("Rainfall24Hr")),
            "wind_kmh": _f(g("WindSpeed")), "wind_dir": _f(g("WindDirection")),
            "pressure": _f(g("MeanSeaLevelPressure")), "visibility_km": _f(g("LandVisibility")),
        })
    return out


def fetch_air4thai():
    """PM2.5 ล่าสุดของสถานีตรวจวัดคุณภาพอากาศ (กรมควบคุมมลพิษ)"""
    try:
        r = core.SESSION.get(AIR4THAI_URL, timeout=60)
    except requests.exceptions.SSLError:
        # เว็บ Air4Thai ส่ง certificate chain ไม่ครบ (ขาด intermediate) บาง OS จึงตรวจไม่ผ่าน
        # ข้อมูลเป็นค่าสาธารณะแบบอ่านอย่างเดียว ไม่มีการส่งข้อมูลลับ จึงยอมดึงแบบไม่ตรวจ cert เฉพาะแหล่งนี้
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        r = core.SESSION.get(AIR4THAI_URL, timeout=60, verify=False)
    r.raise_for_status()
    out = []
    for s in r.json().get("stations", []):
        last = s.get("AQILast") or {}
        pm = last.get("PM25") or {}
        v = _f(pm.get("value"))
        if v is None:
            continue
        out.append({
            "name": s.get("nameTH"), "area": s.get("areaTH"), "lat": _f(s.get("lat")), "lon": _f(s.get("long")),
            "time": f"{last.get('date')}T{last.get('time')}", "pm25": v,
            "level": AQI_LEVELS.get(str(pm.get("color_id")), "–"),
        })
    return [s for s in out if s["lat"] is not None and s["lon"] is not None]


def nearest(lat, lon, stations, max_km=MAX_STATION_KM):
    best = min(stations, key=lambda s: distance_km(lat, lon, s["lat"], s["lon"]), default=None)
    if best is None:
        return None
    d = distance_km(lat, lon, best["lat"], best["lon"])
    return {**best, "dist_km": round(d, 1)} if d <= max_km else None


def fetch_all(cfg=None):
    """ดึงทั้งสองแหล่งครั้งเดียว ใช้กับทุกตำแหน่ง (ล้มเหลวแหล่งไหนก็ข้ามแหล่งนั้น)"""
    out = {"tmd": [], "air": []}
    for key, fn in (("tmd", lambda: fetch_tmd_stations(cfg)), ("air", fetch_air4thai)):
        try:
            out[key] = fn()
        except Exception as e:
            print(f"⚠️ ข้อมูลปัจจุบัน {key}: {e}")
    return out


def for_location(lat, lon, obs):
    return {"station": nearest(lat, lon, obs["tmd"]), "air": nearest(lat, lon, obs["air"])}
