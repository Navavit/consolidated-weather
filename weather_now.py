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
TMD_TODAY_URL = "https://data.tmd.go.th/api/WeatherToday/V2/"
THAIWATER_RAIN24_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h"
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


def _tmd_xml(url, cfg=None):
    """XML ของ TMD Data API (uid/ukey เริ่มต้นเป็นคีย์สาธิตที่ TMD เผยแพร่ ตั้งเองได้ใน config)"""
    cfg = cfg or core.CFG
    r = core.SESSION.get(url, params={"uid": cfg.get("tmd_obs_uid", "api"),
                                      "ukey": cfg.get("tmd_obs_ukey", "api12345")}, timeout=60)
    r.raise_for_status()
    return ET.fromstring(r.content)


def fetch_tmd_stations(cfg=None):
    """ค่าตรวจวัดราย 3 ชม. ล่าสุดของสถานีอุตุฯ"""
    root = _tmd_xml(TMD_OBS_URL, cfg)
    out = []
    for s in root.iter("Station"):
        g = lambda tag: (s.findtext(f".//{tag}") or "").strip()     # ค่าวัดอยู่ใต้ element ย่อย
        lat, lon = _f(g("Latitude")), _f(g("Longitude"))
        if lat is None or lon is None:
            continue
        try:
            t = pd.to_datetime(g("DateTime"), format="%m/%d/%Y %H:%M:%S").isoformat()
        except ValueError:
            t = None
        out.append({
            "id": g("WmoStationNumber"),
            "name": g("StationNameThai"), "province": g("Province"), "lat": lat, "lon": lon, "time": t,
            "temp": _f(g("AirTemperature")), "rh": _f(g("RelativeHumidity")), "dew": _f(g("DewPoint")),
            "rain_3h": _f(g("Rainfall")), "rain_24h": _f(g("Rainfall24Hr")),
            "wind_kmh": _f(g("WindSpeed")), "wind_dir": _f(g("WindDirection")),
            "pressure": _f(g("MeanSeaLevelPressure")), "visibility_km": _f(g("LandVisibility")),
        })
    return out


def fetch_tmd_today(cfg=None):
    """สรุปรายวันเวลา 07.00 น.: ฝน 24 ชม., อุณหภูมิสูงสุด/ต่ำสุด ของ 24 ชม. ที่ผ่านมา"""
    out = []
    for s in _tmd_xml(TMD_TODAY_URL, cfg).iter("Station"):
        g = lambda tag: (s.findtext(f".//{tag}") or "").strip()
        lat, lon = _f(g("Latitude")), _f(g("Longitude"))
        if lat is None or lon is None:
            continue
        t = pd.to_datetime(g("DateTime")[:19], errors="coerce")
        out.append({"id": g("WmoStationNumber"), "name": g("StationNameThai"), "lat": lat, "lon": lon,
                    "time": None if pd.isna(t) else t.isoformat(),
                    "rain_24h": _f(g("Rainfall")), "tmax": _f(g("MaxTemperature")), "tmin": _f(g("MinTemperature"))})
    return out


def fetch_thaiwater_rain24():
    """ฝนสะสม 24 ชม. ล่าสุดจากสถานีโทรมาตรทั่วประเทศ (สสน., กรมอุตุฯ, กรมชลประทาน ฯลฯ ~4,000 จุด)"""
    r = core.SESSION.get(THAIWATER_RAIN24_URL, timeout=90, headers={"User-Agent": "consolidated-weather"})
    r.raise_for_status()
    j = r.json()
    rows = j.get("data", j)
    rows = rows.get("data", rows) if isinstance(rows, dict) else rows
    out = []
    for x in rows:
        st, geo = x.get("station") or {}, x.get("geocode") or {}
        lat, lon = _f(st.get("tele_station_lat")), _f(st.get("tele_station_long"))
        v = _f(x.get("rain_24h"))
        if lat is None or lon is None or v is None:
            continue
        name = (st.get("tele_station_name") or {}).get("th") or ""
        amphoe = (geo.get("amphoe_name") or {}).get("th") or ""
        tumbon = (geo.get("tumbon_name") or {}).get("th") or ""
        out.append({"id": str(st.get("id")), "name": name.strip(), "area": f"ต.{tumbon} อ.{amphoe}".strip(),
                    "agency": ((x.get("agency") or {}).get("agency_shortname") or {}).get("th", "").strip(),
                    "lat": lat, "lon": lon, "time": pd.Timestamp(x.get("rainfall_datetime")).isoformat(),
                    "rain_24h": v})
    return out


THAIWATER_HOURLY_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_24h_graph"


def fetch_thaiwater_hourly(station_id):
    """ฝนรายชั่วโมงของเครื่องวัดโทรมาตร 1 สถานี (ย้อนหลัง ~36 ชม. · ThaiWater ไม่มีประวัติรายชั่วโมงเก่ากว่านี้)
    rain_1h ที่เวลา t = ฝนใน (t-1h, t] (เวลาไทย)"""
    r = core.SESSION.get(THAIWATER_HOURLY_URL, params={"station_type": "tele_station", "station_id": station_id},
                         timeout=60, headers={"User-Agent": "consolidated-weather"})
    r.raise_for_status()
    return [{"id": str(station_id), "time": pd.Timestamp(x["rainfall_datetime"]).isoformat(), "rain_1h": _f(x["rainfall_value"])}
            for x in r.json().get("data", []) if _f(x.get("rainfall_value")) is not None]


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
    """ดึงทุกแหล่งครั้งเดียว ใช้กับทุกตำแหน่งและแผนที่สถานี (ล้มเหลวแหล่งไหนก็ข้ามแหล่งนั้น)"""
    out = {"tmd": [], "air": [], "tw": []}
    for key, fn in (("tmd", lambda: fetch_tmd_stations(cfg)), ("air", fetch_air4thai), ("tw", fetch_thaiwater_rain24)):
        try:
            out[key] = fn()
        except Exception as e:
            print(f"⚠️ ข้อมูลปัจจุบัน {key}: {e}")
    return out


def for_location(lat, lon, obs):
    return {"station": nearest(lat, lon, obs["tmd"]), "air": nearest(lat, lon, obs["air"])}
