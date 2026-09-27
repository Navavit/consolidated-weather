"""ดึงและสรุปพยากรณ์อากาศจากหลายโมเดลผ่าน Open-Meteo

ใช้ร่วมกันระหว่าง notebook, collector.py และแอปในอนาคต (Streamlit/FastAPI)
แหล่งข้อมูลและหลักการของแต่ละโมเดล: docs/MODELS.md
"""
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
AIR_QUALITY_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
TMD_HOURLY_URL = "https://data.tmd.go.th/nwpapi/v1/forecast/location/hourly/at"
GOOGLE_HOURLY_URL = "https://weather.googleapis.com/v1/forecast/hours:lookup"

# ลองใหม่อัตโนมัติเมื่อ timeout / 429 / 5xx (Open-Meteo ensemble ช้าช่วงต้นชั่วโมง) รอ 0, 10, 20 วินาที
SESSION = requests.Session()
SESSION.mount("https://", HTTPAdapter(max_retries=Retry(
    total=3, connect=3, read=2, status=3, backoff_factor=5,
    status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])))

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = {
    "my_location": "auto",                 # "auto" = หาจาก IP หรือใส่ [lat, lon, "ชื่อ"]
    "target_places": ["เชียงใหม่", "ภูเก็ต"],  # ชื่อสถานที่ หรือ [lat, lon, "ชื่อ"]
    "hour_windows": [3, 6, 12, 24],
    "day_leads": [1, 3, 7],
    "rain_threshold_mm": 1.0,              # ใช้กับตารางเปรียบเทียบ/ensemble
    "collect_every_minutes": 60,           # รอบการดึงข้อมูลอัตโนมัติ
    "verify_window_hours": 3,              # ช่วงเวลารอบเวลาที่สังเกตที่ใช้ตัดสินว่าโมเดล "ทายว่าฝนตก"
    "verify_threshold_mm": 0.2,            # ฝนรวมในช่วงนั้น >= ค่านี้ = โมเดลทายว่าฝนตก
    "db_path": "data/weather.db",
    "max_grid_km": None,                   # ตัดโมเดลที่กริดหยาบกว่านี้ออก เช่น 20 (None = ใช้ทุกโมเดล)
    "tmd_token": "",                       # ใส่ใน config.local.json หรือ env TMD_NWP_TOKEN (ห้ามใส่ใน config.json)
    "google_weather_api_key": "",          # ใส่ใน config.local.json หรือ env GOOGLE_WEATHER_API_KEY (ห้ามใส่ใน config.json)
    "google_monthly_cap": 9500,            # เพดานจำนวนครั้ง/เดือน (โควตาฟรี 10,000) — ถึงแล้วหยุดเรียก Google
}


CONFIG_PATH = ROOT / "config.json"              # ขึ้น GitHub (ตำแหน่ง, ค่าตั้งต่าง ๆ)
LOCAL_CONFIG_PATH = ROOT / "config.local.json"  # เฉพาะเครื่องนี้ ไม่ขึ้น GitHub (token)


def load_config(path=CONFIG_PATH, local_path=LOCAL_CONFIG_PATH):
    cfg = dict(DEFAULT_CONFIG)
    for p in (Path(path), Path(local_path)):
        if p.exists():
            cfg.update(json.loads(p.read_text(encoding="utf-8")))
    return cfg


CFG = load_config()

# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------
# โมเดล deterministic ระดับโลก (ครอบคลุมประเทศไทย) — key = ชื่อโมเดลใน Open-Meteo
# grid_km = ระยะห่างกริดที่ Open-Meteo ส่งให้จริงที่ประเทศไทย (วัดเมื่อ 27 ก.ย. 2026, lat × lon)
DETERMINISTIC_MODELS = {
    "ecmwf_ifs":                      {"name": "ECMWF IFS",    "agency": "ECMWF (ยุโรป)",          "type": "Physics", "grid_km": 9,  "grid": "~0.08° (O1280)"},
    "ukmo_global_deterministic_10km": {"name": "UKMO UM",      "agency": "Met Office (อังกฤษ)",     "type": "Physics", "grid_km": 12, "grid": "0.094° × 0.141°"},
    "kma_gdps":                       {"name": "KMA GDPS",     "agency": "KMA (เกาหลีใต้)",         "type": "Physics", "grid_km": 12, "grid": "0.094° × 0.141°"},
    "gfs_global":                     {"name": "NOAA GFS",     "agency": "NOAA/NCEP (สหรัฐฯ)",      "type": "Physics", "grid_km": 13, "grid": "0.117°"},
    "icon_global":                    {"name": "DWD ICON",     "agency": "DWD (เยอรมนี)",          "type": "Physics", "grid_km": 14, "grid": "0.125°"},
    "cma_grapes_global":              {"name": "CMA GRAPES",   "agency": "CMA (จีน)",              "type": "Physics", "grid_km": 14, "grid": "0.125°"},
    "bom_access_global":              {"name": "BOM ACCESS-G", "agency": "BOM (ออสเตรเลีย)",       "type": "Physics", "grid_km": 16, "grid": "0.117° × 0.176°"},
    "gem_global":                     {"name": "ECCC GEM",     "agency": "ECCC (แคนาดา)",          "type": "Physics", "grid_km": 16, "grid": "0.15°"},
    "ecmwf_aifs025_single":           {"name": "ECMWF AIFS",   "agency": "ECMWF (ยุโรป)",          "type": "AI/ML",   "grid_km": 28, "grid": "0.25°"},
    "ncep_aigfs025":                  {"name": "NOAA AIGFS",   "agency": "NOAA (สหรัฐฯ) · สถาปัตยกรรม GraphCast ของ Google DeepMind",
                                       "type": "AI/ML",   "grid_km": 28, "grid": "0.25°"},
    "meteofrance_arpege_world":       {"name": "MF ARPEGE",    "agency": "Météo-France (ฝรั่งเศส)", "type": "Physics", "grid_km": 28, "grid": "0.25°"},
    "jma_gsm":                        {"name": "JMA GSM",      "agency": "JMA (ญี่ปุ่น)",           "type": "Physics", "grid_km": 55, "grid": "0.5°"},
}

# โมเดลความละเอียดสูงของไทย (ต้องมี token) — เพิ่มเป็นคอลัมน์เดียวกับโมเดลอื่นเมื่อมี token
TMD_MODEL = {"name": "TMD WRF", "agency": "กรมอุตุนิยมวิทยา (ไทย)", "type": "Physics (regional)",
             "grid_km": 2, "grid": "2 กม. (hourly, 48 ชม.)"}

# Google Weather API (Maps Platform) — เฉพาะตำแหน่งของฉัน เพื่ออยู่ในโควตาฟรี (ต้องมี API key)
GOOGLE_MODEL = {"name": "Google Weather", "agency": "Google (Maps Platform · WeatherNext)", "type": "AI/ML",
                "grid_km": None, "grid": "ไม่เปิดเผย (รายชั่วโมง 10 วัน)"}

MODEL_NAMES = [m["name"] for m in DETERMINISTIC_MODELS.values()] + [TMD_MODEL["name"], GOOGLE_MODEL["name"]]

# ระบบ ensemble (หลายสมาชิก ใช้คำนวณความน่าจะเป็น)
ENSEMBLE_MODELS = {
    "ecmwf_ifs025":              {"name": "ECMWF ENS",      "members": 51, "grid_km": 28},
    "ecmwf_aifs025":             {"name": "ECMWF AIFS ENS", "members": 51, "grid_km": 28},
    "gfs025":                    {"name": "NOAA GEFS",      "members": 31, "grid_km": 28},
    "ncep_aigefs025":            {"name": "NOAA AIGEFS",    "members": 31, "grid_km": 28},
    "icon_seamless":             {"name": "DWD ICON-EPS",   "members": 40, "grid_km": 28},
    "ukmo_global_ensemble_20km": {"name": "UKMO MOGREPS-G", "members": 18, "grid_km": 25},
    "gem_global":                {"name": "ECCC GEPS",      "members": 21, "grid_km": 55},
}

# ตัวแปรที่ใช้ในชีวิตประจำวัน
#   hour: วิธีสรุปช่วง +N ชม. — sum=รวม, at=ค่า ณ เวลานั้น, max=สูงสุดในช่วง, None=ไม่แสดง
#   day:  วิธีสรุปทั้งวัน D+N — sum / max / min / mean
VARIABLES = {
    "rain":      {"api": "precipitation",             "label": "ฝน",                    "unit": "มม.",     "hour": "sum", "day": "sum",  "cmap": "Blues"},
    "rain_prob": {"api": "precipitation_probability", "label": "โอกาสฝน (ของโมเดล)",     "unit": "%",       "hour": "max", "day": "max",  "cmap": "Greens"},
    "tmax":      {"api": "temperature_2m",            "label": "อุณหภูมิสูงสุด",          "unit": "°C",      "hour": "at",  "day": "max",  "cmap": "OrRd"},
    "tmin":      {"api": "temperature_2m",            "label": "อุณหภูมิต่ำสุด",           "unit": "°C",      "hour": None,  "day": "min",  "cmap": "PuBu"},
    "feels":     {"api": "apparent_temperature",      "label": "อุณหภูมิที่รู้สึก (สูงสุด)", "unit": "°C",      "hour": "at",  "day": "max",  "cmap": "Reds"},
    "rh":        {"api": "relative_humidity_2m",      "label": "ความชื้นสัมพัทธ์ (เฉลี่ย)", "unit": "%",       "hour": "at",  "day": "mean", "cmap": "GnBu"},
    "wind":      {"api": "wind_speed_10m",            "label": "ความเร็วลม (สูงสุด)",      "unit": "กม./ชม.", "hour": "at",  "day": "max",  "cmap": "Purples"},
    "gust":      {"api": "wind_gusts_10m",            "label": "ลมกระโชก (สูงสุด)",       "unit": "กม./ชม.", "hour": "max", "day": "max",  "cmap": "Purples"},
    "cloud":     {"api": "cloud_cover",               "label": "เมฆปกคลุม (เฉลี่ย)",      "unit": "%",       "hour": "at",  "day": "mean", "cmap": "Greys"},
    "uv":        {"api": "uv_index",                  "label": "ดัชนี UV (สูงสุด)",        "unit": "",        "hour": "at",  "day": "max",  "cmap": "YlOrBr"},
}
API_VARIABLES = sorted({v["api"] for v in VARIABLES.values()})


def active_models(cfg=None):
    """โมเดล deterministic ที่ใช้ตาม max_grid_km"""
    limit = (cfg or CFG).get("max_grid_km")
    return {k: m for k, m in DETERMINISTIC_MODELS.items() if not limit or m["grid_km"] <= limit}


def active_ensembles(cfg=None):
    """ensemble ทุกระบบหยาบ (≥25 กม.) แต่ใช้เพื่อความน่าจะเป็น ไม่ใช่รายละเอียดพื้นที่ จึงไม่กรองด้วย max_grid_km"""
    return dict(ENSEMBLE_MODELS)


def tmd_token(cfg=None):
    return os.environ.get("TMD_NWP_TOKEN") or (cfg or CFG).get("tmd_token") or ""


def google_key(cfg=None):
    return os.environ.get("GOOGLE_WEATHER_API_KEY") or (cfg or CFG).get("google_weather_api_key") or ""


def model_table(cfg=None):
    """ตารางโมเดลทั้งหมด เรียงตามความละเอียด พร้อมสถานะว่าใช้อยู่หรือไม่"""
    act = active_models(cfg)
    rows = [{"open_meteo_key": k, **m, "ใช้งาน": k in act} for k, m in DETERMINISTIC_MODELS.items()]
    rows.append({"open_meteo_key": "(TMD NWP API)", **TMD_MODEL, "ใช้งาน": bool(tmd_token(cfg))})
    rows.append({"open_meteo_key": "(Google Weather API)", **GOOGLE_MODEL, "ใช้งาน": bool(google_key(cfg))})
    return pd.DataFrame(rows).set_index("name").sort_values("grid_km", na_position="last")


def forecast_days(cfg=None):
    cfg = cfg or CFG
    return max(cfg["day_leads"]) + 2       # ต้องครอบคลุม D+7 ทั้งวัน


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------
def get_my_location():
    """หาตำแหน่งจาก IP (ความแม่นยำระดับเมือง)"""
    for url, lat_k, lon_k, city_k in [
        ("http://ip-api.com/json/", "lat", "lon", "city"),
        ("https://ipapi.co/json/", "latitude", "longitude", "city"),
    ]:
        try:
            j = requests.get(url, timeout=10).json()
            if j.get(lat_k) is not None:
                return float(j[lat_k]), float(j[lon_k]), f"ตำแหน่งของฉัน ({j.get(city_k) or 'IP'})"
        except (requests.RequestException, ValueError):
            continue
    raise RuntimeError("หาตำแหน่งจาก IP ไม่ได้ กรุณาใส่ my_location เป็น [lat, lon, 'ชื่อ']")


def geocode(name):
    """ค้นหาพิกัดจากชื่อสถานที่ (Open-Meteo Geocoding API)"""
    r = SESSION.get(GEOCODING_URL, params={"name": name, "count": 1, "language": "th"}, timeout=15)
    r.raise_for_status()
    res = r.json().get("results")
    if not res:
        raise ValueError(f"ไม่พบสถานที่: {name}")
    x = res[0]
    label = x["name"] + (f", {x['admin1']}" if x.get("admin1") and x["name"] not in x["admin1"] else "")
    return x["latitude"], x["longitude"], label


def parse_coords(text):
    """แปลงข้อความเป็น (lat, lon)

    รองรับ "13.7563, 100.5018", "13.7563 100.5018" และลิงก์ Google Maps
    (…/@13.75,100.50,15z, …!3d13.75!4d100.50, ?q=13.75,100.50, ?ll=…)
    คืน None ถ้าไม่พบพิกัด
    """
    text = (text or "").strip()
    num = r"(-?\d{1,3}(?:\.\d+)?)"
    for pat in (r"!3d" + num + r"!4d" + num,              # ตำแหน่งของหมุดใน Google Maps
                r"@" + num + "," + num,                   # จุดกึ่งกลางของแผนที่
                r"[?&](?:q|ll|query|destination)=" + num + r"(?:,|%2C)\s*" + num,
                r"^" + num + r"\s*[,\s]\s*" + num + r"$"):
        m = re.search(pat, text)
        if m:
            lat, lon = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
    return None


def in_tmd_domain(lat, lon):
    """พื้นที่คร่าว ๆ ที่ TMD WRF ครอบคลุม (ประเทศไทยและรอบข้าง)"""
    return 4 <= lat <= 22 and 96 <= lon <= 107


def save_locations(my_location, target_places, path=CONFIG_PATH):
    """บันทึกตำแหน่งลง config.json (คงค่าอื่นไว้) และอัปเดต CFG ที่โหลดอยู่"""
    p = Path(path)
    cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    cfg["my_location"] = my_location
    cfg["target_places"] = target_places
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    CFG["my_location"], CFG["target_places"] = my_location, target_places
    return cfg


def resolve(loc):
    if loc == "auto":
        if os.environ.get("GITHUB_ACTIONS"):
            raise RuntimeError("บน GitHub Actions ใช้ my_location = \"auto\" ไม่ได้ (จะได้ตำแหน่งของเครื่อง GitHub) "
                               "กรุณาตั้งพิกัดใน config.json หรือใช้ workflow 'ตั้งค่าตำแหน่ง'")
        return get_my_location()
    if isinstance(loc, str):
        return geocode(loc)
    lat, lon, name = loc
    return float(lat), float(lon), name


def resolve_locations(cfg=None):
    cfg = cfg or CFG
    return [resolve(cfg["my_location"])] + [resolve(p) for p in cfg["target_places"]]


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def _local_now(utc_offset_seconds):
    return (pd.Timestamp.utcnow().tz_localize(None) + pd.Timedelta(seconds=utc_offset_seconds)).floor("h")


def fetch_deterministic(lat, lon, cfg=None, google_hours=None):
    """คืน (dict[api_var -> DataFrame(index=เวลาท้องถิ่น, columns=ชื่อโมเดล)], now_local, timezone)"""
    r = SESSION.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(API_VARIABLES),
        "models": ",".join(active_models(cfg)),
        "forecast_days": forecast_days(cfg),
        "timezone": "auto",
    }, timeout=90)
    r.raise_for_status()
    j = r.json()
    h = j["hourly"]
    idx = pd.to_datetime(h["time"])
    data = {}
    for var in API_VARIABLES:
        df = pd.DataFrame({m["name"]: h.get(f"{var}_{k}") for k, m in active_models(cfg).items()},
                          index=idx, dtype=float)
        data[var] = df.dropna(axis=1, how="all")       # ตัดโมเดลที่ไม่มีข้อมูลตัวแปรนี้
    now = _local_now(j["utc_offset_seconds"])

    if tmd_token(cfg):
        try:
            tmd = fetch_tmd_hourly(lat, lon, now, cfg)
            for var, series in tmd.items():
                if var in data:
                    data[var].insert(0, TMD_MODEL["name"], series.reindex(data[var].index))   # ละเอียดสุด ไว้คอลัมน์แรก
        except Exception as e:                         # TMD ล่มหรืออยู่นอกประเทศไทย ก็ยังใช้โมเดลอื่นต่อได้
            print(f"⚠️ TMD WRF: {e}")

    GOOGLE_CALLS["last"] = 0
    if google_hours and google_key(cfg):
        try:
            g, calls = fetch_google_hourly(lat, lon, google_hours, j["timezone"], cfg)
            GOOGLE_CALLS["last"] = calls
            pos = 1 if TMD_MODEL["name"] in data["precipitation"] else 0
            for var, series in g.items():
                if var in data:
                    data[var].insert(min(pos, data[var].shape[1]), GOOGLE_MODEL["name"], series.reindex(data[var].index))
        except Exception as e:
            print(f"⚠️ Google Weather: {e}")
    return data, now, j["timezone"]


GOOGLE_CALLS = {"last": 0, "page_hint": None, "page_used": None}   # สถานะการเรียก Google ล่าสุด (ใช้บันทึกงบ)


def fetch_google_hourly(lat, lon, hours, tz, cfg=None):
    """Google Weather API รายชั่วโมง → (dict[api_var แบบ Open-Meteo -> Series(เวลาท้องถิ่น)], จำนวนครั้งที่เรียก)

    ฝน (qpf) เป็นปริมาณในช่วง [startTime, endTime) จึงวางไว้ที่ endTime ให้ตรงกับ Open-Meteo (ฝนของชั่วโมงก่อนหน้า)
    ค่าอื่นเป็นค่า ณ startTime
    """
    # ขนาดหน้าที่เคยใช้ได้ (จำไว้ใน google_usage.json) — ไม่ต้องเสียคำขอลองหน้าใหญ่ทุกรอบ
    rows, token, calls = [], None, 0
    page = min(int(hours), int(GOOGLE_CALLS.get("page_hint") or hours))
    while True:
        params = {"key": google_key(cfg), "location.latitude": lat, "location.longitude": lon,
                  "hours": int(hours), "pageSize": page}
        if token:
            params["pageToken"] = token
        r = SESSION.get(GOOGLE_HOURLY_URL, params=params, timeout=60)
        calls += 1
        if r.status_code == 400 and page > 24 and not rows:
            page = 24                                   # ถ้าขอหน้าใหญ่ไม่ได้ ใช้ขนาดมาตรฐาน 24 ชม.
            continue
        r.raise_for_status()
        j = r.json()
        rows += j.get("forecastHours", [])
        token = j.get("nextPageToken")
        if not token or len(rows) >= hours:
            break
    GOOGLE_CALLS["page_used"] = page
    to_local = lambda t: pd.Timestamp(t).tz_convert(tz).tz_localize(None).floor("h")
    get = lambda h, *path: _dig(h, path)
    start = [to_local(h["interval"]["startTime"]) for h in rows]
    end = [to_local(h["interval"]["endTime"]) for h in rows]
    S = lambda idx, vals: pd.Series(vals, index=idx, dtype=float).groupby(level=0).last()
    out = {
        "precipitation": S(end, [get(h, "precipitation", "qpf", "quantity") for h in rows]),
        "precipitation_probability": S(start, [get(h, "precipitation", "probability", "percent") for h in rows]),
        "temperature_2m": S(start, [get(h, "temperature", "degrees") for h in rows]),
        "apparent_temperature": S(start, [get(h, "feelsLikeTemperature", "degrees") for h in rows]),
        "relative_humidity_2m": S(start, [get(h, "relativeHumidity") for h in rows]),
        "wind_speed_10m": S(start, [get(h, "wind", "speed", "value") for h in rows]),
        "wind_gusts_10m": S(start, [get(h, "wind", "gust", "value") for h in rows]),
        "cloud_cover": S(start, [get(h, "cloudCover") for h in rows]),
        "uv_index": S(start, [get(h, "uvIndex") for h in rows]),
    }
    return {k: v for k, v in out.items() if v.notna().any()}, calls


def _dig(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


# ---------------------------------------------------------------------------
# งบ Google Weather API: บันทึกเวลาเรียกล่าสุดและจำนวนครั้งต่อเดือนไว้ใน branch data
# ---------------------------------------------------------------------------
def google_allowed(archive, key, min_minutes, cfg=None):
    """เรียก Google ได้ไหม: ห่างจากครั้งก่อนพอ และยังไม่ถึงเพดานรายเดือน"""
    if not google_key(cfg) or not archive:
        return False
    st = _google_state(archive)
    GOOGLE_CALLS["page_hint"] = st.get("page_size")
    now = pd.Timestamp.now(tz="Asia/Bangkok")
    last = st["last"].get(key)
    if last and now - pd.Timestamp(last) < pd.Timedelta(minutes=min_minutes):
        return False
    if st["month"].get(now.strftime("%Y-%m"), 0) >= int((cfg or CFG).get("google_monthly_cap", 9500)):
        print("⚠️ Google Weather: ถึงเพดานรายเดือนแล้ว หยุดเรียกจนถึงเดือนหน้า")
        return False
    return True


def google_record(archive, key, calls):
    if not calls:
        return
    st = _google_state(archive)
    now = pd.Timestamp.now(tz="Asia/Bangkok")
    st["last"][key] = now.isoformat(timespec="seconds")
    m = now.strftime("%Y-%m")
    st["month"][m] = st["month"].get(m, 0) + int(calls)
    if GOOGLE_CALLS.get("page_used"):
        st["page_size"] = GOOGLE_CALLS["page_used"]
    (Path(archive) / "google_usage.json").write_text(json.dumps(st, indent=1), encoding="utf-8")


def _google_state(archive):
    p = Path(archive) / "google_usage.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"last": {}, "month": {}}


def fetch_tmd_hourly(lat, lon, now, cfg=None, hours=48):
    """TMD WRF 2 กม. รายชั่วโมง 48 ชม. (https://data.tmd.go.th/nwpapi/doc/)

    คืน dict[api_var แบบ Open-Meteo -> Series(index=เวลาไทย)]
    สมมติว่า rain ที่เวลา t คือฝนในชั่วโมงก่อนหน้า เหมือน Open-Meteo
    """
    r = SESSION.get(TMD_HOURLY_URL, headers={
        "accept": "application/json", "authorization": f"Bearer {tmd_token(cfg)}",
    }, params={
        "lat": lat, "lon": lon, "date": f"{now:%Y-%m-%d}", "hour": now.hour, "duration": hours,
        "fields": "tc,rh,rain,ws10m,cloudlow,cloudmed,cloudhigh",
    }, timeout=60)
    r.raise_for_status()
    j = r.json()
    key = next(k for k in j if k.startswith("WeatherFor"))   # API สะกดว่า "WeatherForcasts"
    fc = j[key][0]["forecasts"]
    df = pd.DataFrame([f["data"] for f in fc],
                      index=pd.to_datetime([f["time"] for f in fc]).tz_localize(None), dtype=float)
    out = {
        "temperature_2m": df.get("tc"),
        "relative_humidity_2m": df.get("rh"),
        "precipitation": df.get("rain"),
        "wind_speed_10m": df.get("ws10m") * 3.6 if "ws10m" in df else None,     # m/s → กม./ชม.
        # เมฆรวมประมาณจากชั้นที่มากที่สุด (TMD แยกเมฆ 3 ระดับ)
        "cloud_cover": df[[c for c in ("cloudlow", "cloudmed", "cloudhigh") if c in df]].max(axis=1)
        if any(c in df for c in ("cloudlow", "cloudmed", "cloudhigh")) else None,
    }
    return {k: v for k, v in out.items() if v is not None}


def fetch_ensemble(lat, lon, key, cfg=None):
    """คืน DataFrame ฝนรายชั่วโมง: index=เวลา, columns=สมาชิกแต่ละตัว"""
    r = SESSION.get(ENSEMBLE_URL, params={
        "latitude": lat, "longitude": lon,
        "hourly": "precipitation", "models": key,
        "forecast_days": forecast_days(cfg), "timezone": "auto",
    }, timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]
    cols = {k: v for k, v in h.items() if k.startswith("precipitation")}
    return pd.DataFrame(cols, index=pd.to_datetime(h["time"]), dtype=float).dropna(axis=1, how="all")


def fetch_all_ensembles(lat, lon, cfg=None):
    out = {}
    for key, meta in active_ensembles(cfg).items():
        try:
            df = fetch_ensemble(lat, lon, key, cfg)
        except requests.RequestException as e:
            print(f"⚠️ {meta['name']}: {e}")
            continue
        if not df.empty:
            out[meta["name"]] = df
    return out


def fetch_air_quality(lat, lon, cfg=None):
    """PM2.5, US AQI, UV จาก CAMS (โมเดลเดียว ใช้เป็นข้อมูลประกอบ)"""
    r = SESSION.get(AIR_QUALITY_URL, params={
        "latitude": lat, "longitude": lon,
        "hourly": "pm2_5,us_aqi,uv_index",
        "forecast_days": min(forecast_days(cfg), 7), "timezone": "auto",
    }, timeout=60)
    r.raise_for_status()
    h = r.json()["hourly"]
    return pd.DataFrame({k: v for k, v in h.items() if k != "time"}, index=pd.to_datetime(h["time"]), dtype=float)


def fetch_previous_runs(lat, lon, start_date, end_date, days=range(1, 8)):
    """ฝนรายชั่วโมงที่แต่ละโมเดลพยากรณ์ไว้ล่วงหน้า N วัน (Previous Runs API)

    คืน DataFrame แบบ long: time, model, lead_days, precipitation
    """
    hv = ["precipitation_previous_day%d" % d for d in days]
    r = SESSION.get(PREVIOUS_RUNS_URL, params={
        "latitude": lat, "longitude": lon,
        "hourly": ",".join(hv), "models": ",".join(active_models()),
        "start_date": str(start_date), "end_date": str(end_date), "timezone": "auto",
    }, timeout=90)
    r.raise_for_status()
    h = r.json()["hourly"]
    idx = pd.to_datetime(h["time"])
    rows = []
    for d in days:
        for k, m in active_models().items():
            vals = h.get(f"precipitation_previous_day{d}_{k}")
            if vals and any(v is not None for v in vals):
                rows.append(pd.DataFrame({"time": idx, "model": m["name"], "lead_days": d,
                                          "precipitation": pd.Series(vals, dtype=float).values}))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(
        columns=["time", "model", "lead_days", "precipitation"])


# ---------------------------------------------------------------------------
# Summaries
# ---------------------------------------------------------------------------
def window_masks(index, now, cfg=None, kinds=("hour", "day")):
    """คืน dict {ชื่อช่วง: (ชนิด, mask, เวลาปลายช่วง)}

    ค่า precipitation ที่เวลา t คือฝนใน (t-1h, t] ดังนั้น
      +N ชม. = เวลา (now, now+N]
      D+N    = เวลา (00:00, 24:00] ของวันนั้น (รวม 01:00..24:00)
    """
    cfg = cfg or CFG
    masks = {}
    if "hour" in kinds:
        for n in cfg["hour_windows"]:
            end = now + pd.Timedelta(hours=n)
            masks[f"+{n} ชม."] = ("hour", (index > now) & (index <= end), end)
    if "day" in kinds:
        today = now.normalize()
        for d in cfg["day_leads"]:
            start = today + pd.Timedelta(days=d)
            masks[f"D+{d} ({start:%d/%m})"] = ("day", (index > start) & (index <= start + pd.Timedelta(days=1)), None)
    return masks


def summarize(hourly, now, hour_how="sum", day_how="sum", cfg=None):
    """ตาราง: แถว=โมเดล/สมาชิก, คอลัมน์=ช่วงเวลา (NaN ถ้าข้อมูลไม่ครบช่วง)"""
    out = {}
    for label, (kind, m, t_at) in window_masks(hourly.index, now, cfg).items():
        how = hour_how if kind == "hour" else day_how
        if how is None:
            continue
        if how == "at":
            out[label] = hourly.loc[t_at] if t_at in hourly.index else pd.Series(np.nan, index=hourly.columns)
            continue
        block = hourly[m]
        complete = block.notna().all() & (len(block) > 0)
        out[label] = getattr(block, how)().where(complete)
    return pd.DataFrame(out)


def summarize_var(data, key, now, cfg=None):
    v = VARIABLES[key]
    df = data.get(v["api"])
    if df is None or df.empty:
        return pd.DataFrame()
    return summarize(df, now, v["hour"], v["day"], cfg).dropna(how="all")


def consensus(table, cfg=None):
    thr = (cfg or CFG)["rain_threshold_mm"]
    rain = table.ge(thr).astype(float).where(table.notna())
    return pd.DataFrame({
        "median": table.median(),
        "min": table.min(),
        "max": table.max(),
        "จำนวนโมเดล": table.notna().sum(),
        f"โมเดลที่ว่าฝนตก (≥{thr} มม.)": rain.sum().astype(int),
        "% เห็นตรงกันว่าฝนตก": (rain.mean() * 100).round(0),
    }).T


def ensemble_probability(ensembles, now, cfg=None):
    """ความน่าจะเป็น (%) ที่ฝน >= threshold = สัดส่วนสมาชิกที่เกิน threshold"""
    thr = (cfg or CFG)["rain_threshold_mm"]
    rows = {}
    for name, members in ensembles.items():
        t = summarize(members, now, cfg=cfg)
        valid = t.notna().sum() >= 0.8 * len(t)          # ต้องมีสมาชิกเกือบครบ
        rows[f"{name} ({members.shape[1]})"] = (t.ge(thr).mean() * 100).where(valid)
    return pd.DataFrame(rows).T


def heat_level(t):
    """ระดับดัชนีความร้อนตามเกณฑ์กรมควบคุมโรค"""
    if pd.isna(t):  return "–"
    if t < 27:      return "🟢 ปกติ"
    if t < 33:      return "🟡 เฝ้าระวัง"
    if t < 42:      return "🟠 เตือนภัย"
    if t < 52:      return "🔴 อันตราย"
    return "🟣 อันตรายมาก"


def uv_level(u):
    if pd.isna(u):  return "–"
    if u < 3:       return "🟢 ต่ำ"
    if u < 6:       return "🟡 ปานกลาง"
    if u < 8:       return "🟠 สูง"
    if u < 11:      return "🔴 สูงมาก"
    return "🟣 อันตราย"


def pm25_level(p):
    """ระดับ PM2.5 เฉลี่ย 24 ชม. (มคก./ลบ.ม.) ตามช่วงดัชนีคุณภาพอากาศของไทย"""
    if pd.isna(p):  return "–"
    if p <= 15:     return "🔵 ดีมาก"
    if p <= 25:     return "🟢 ดี"
    if p <= 37.5:   return "🟡 ปานกลาง"
    if p <= 75:     return "🟠 เริ่มมีผลต่อสุขภาพ"
    return "🔴 มีผลต่อสุขภาพ"


def daily_brief_records(tables, aq, now, cfg=None):
    """ค่าสรุปรายวัน (median ของทุกโมเดล) เป็น list ของ dict — ใช้ทั้งใน notebook และหน้าเว็บ"""
    thr = (cfg or CFG)["rain_threshold_mm"]
    idx = aq.index if aq is not None else pd.DatetimeIndex([])
    out = []
    for label, (_, m, _) in window_masks(idx, now, cfg, kinds=("day",)).items():
        med = {k: (t[label].median() if label in t else np.nan) for k, t in tables.items()}
        rain_t = tables.get("rain")
        has = rain_t is not None and label in rain_t
        pm, uv = np.nan, med.get("uv", np.nan)
        if aq is not None and m.any():
            pm = aq.loc[m, "pm2_5"].mean()
            uv = np.nanmax([uv, aq.loc[m, "uv_index"].max()])
        out.append({
            "label": label,
            "rain": med.get("rain"),
            "n_rain": int(rain_t[label].ge(thr).sum()) if has else 0,
            "n_models": int(rain_t[label].notna().sum()) if has else 0,
            "tmin": med.get("tmin"), "tmax": med.get("tmax"),
            "feels": med.get("feels"), "heat": heat_level(med.get("feels")),
            "rh": med.get("rh"), "gust": med.get("gust"),
            "uv": uv, "uv_level": uv_level(uv),
            "pm25": pm, "pm25_level": pm25_level(pm),
        })
    return out


def daily_brief(tables, aq, now, cfg=None):
    """การ์ดสรุปรายวันสำหรับคนทั่วไป (median ของทุกโมเดล) เป็นตารางสำหรับ notebook"""
    thr = (cfg or CFG)["rain_threshold_mm"]
    r1 = lambda v: round(v, 1) if isinstance(v, float) and not np.isnan(v) else v
    rows = {}
    for b in daily_brief_records(tables, aq, now, cfg):
        rows[b["label"]] = {
            "🌧️ ฝน (มม.)": r1(b["rain"]),
            f"โมเดลที่ว่าฝนตก ≥{thr} มม.": f"{b['n_rain']}/{b['n_models']}",
            "🌡️ ต่ำสุด–สูงสุด (°C)": f"{b['tmin']:.0f}–{b['tmax']:.0f}",
            "🥵 รู้สึกเหมือน (°C)": r1(b["feels"]),
            "ระดับความร้อน": b["heat"],
            "💧 ความชื้น (%)": r1(b["rh"]),
            "💨 ลมกระโชก (กม./ชม.)": r1(b["gust"]),
            "☀️ UV": r1(b["uv"]),
            "ระดับ UV": b["uv_level"],
            "😷 PM2.5": r1(b["pm25"]),
            "ระดับ PM2.5": b["pm25_level"],
        }
    return pd.DataFrame(rows)


def analyze_location(lat, lon, name, cfg=None, google_hours=None):
    """ดึงข้อมูลทั้งหมดของตำแหน่งเดียวแล้วสรุปผล"""
    data, now, tz = fetch_deterministic(lat, lon, cfg, google_hours)
    google_calls = GOOGLE_CALLS["last"]
    try:
        aq = fetch_air_quality(lat, lon, cfg)
    except requests.RequestException as e:
        print(f"⚠️ air quality: {e}")
        aq = None
    ensembles = fetch_all_ensembles(lat, lon, cfg)
    tables = {k: summarize_var(data, k, now, cfg) for k in VARIABLES}
    tables = {k: t for k, t in tables.items() if not t.empty}
    return {
        "name": name, "lat": lat, "lon": lon, "tz": tz, "now": now,
        "data": data, "aq": aq, "ensembles": ensembles, "tables": tables,
        "consensus": consensus(tables["rain"], cfg),
        "ensemble": ensemble_probability(ensembles, now, cfg),
        "brief": daily_brief(tables, aq, now, cfg),
        "google_calls": google_calls,
    }


# ---------------------------------------------------------------------------
# Grid (สำหรับแผนที่)
# ---------------------------------------------------------------------------
def fetch_grid(variable="precipitation_sum", day=1, models=None, step=1.0,
               bbox=(5.5, 20.5, 97.5, 105.5), chunk=100):
    """ค่ารายวันบนกริด: คืน DataFrame lat, lon, <ชื่อโมเดล>..., Median"""
    models = models or [k for k in active_models() if k not in ("kma_gdps", "bom_access_global")]
    lats = np.arange(bbox[0], bbox[1] + 1e-9, step)
    lons = np.arange(bbox[2], bbox[3] + 1e-9, step)
    pts = [(round(a, 3), round(o, 3)) for a in lats for o in lons]
    rows = []
    for i in range(0, len(pts), chunk):
        part = pts[i:i + chunk]
        r = SESSION.get(FORECAST_URL, params={
            "latitude": ",".join(str(p[0]) for p in part),
            "longitude": ",".join(str(p[1]) for p in part),
            "daily": variable, "models": ",".join(models),
            "forecast_days": day + 1, "timezone": "Asia/Bangkok",
        }, timeout=120)
        r.raise_for_status()
        js = r.json()
        js = js if isinstance(js, list) else [js]
        for (la, lo), j in zip(part, js):
            row = {"lat": la, "lon": lo}
            for k in models:
                vals = j["daily"].get(f"{variable}_{k}") or [None] * (day + 1)
                row[DETERMINISTIC_MODELS[k]["name"]] = vals[day]
            rows.append(row)
    grid = pd.DataFrame(rows)
    names = [DETERMINISTIC_MODELS[k]["name"] for k in models]
    grid[names] = grid[names].astype(float)
    grid = grid.dropna(axis=1, how="all")
    grid["Median"] = grid[[n for n in names if n in grid]].median(axis=1)
    return grid
