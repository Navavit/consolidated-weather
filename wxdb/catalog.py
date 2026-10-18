"""ข้อมูลของตารางมิติคงที่: source, model, variable และฟังก์ชันแปลงชื่อแบบจำลองในระบบเดิมเป็น model_id"""
import re

import pandas as pd

import weather_core as core

SOURCES = [
    # source_id, name, agency, kind, url, license, time_res, notes
    ("tmd", "TMD Weather3Hours / WeatherToday", "กรมอุตุนิยมวิทยา", "station_network",
     "https://data.tmd.go.th/api/", "TMD Open Data", "ราย 3 ชม. และรายวัน (07:00 น.)",
     "API ให้เฉพาะค่าล่าสุด ระบบสะสมเองทุกชั่วโมงตั้งแต่ 27 ก.ย. 2569"),
    ("thaiwater", "ThaiWater (คลังข้อมูลน้ำแห่งชาติ)", "สถาบันสารสนเทศทรัพยากรน้ำ (สสน.)", "station_network",
     "https://www.thaiwater.net/", "เปิดเผยตามเงื่อนไขของ สสน.", "รายชั่วโมงและสะสม 24 ชม.",
     "โทรมาตรจากหลายหน่วยงาน (~4,300 เครื่อง)"),
    ("air4thai", "Air4Thai", "กรมควบคุมมลพิษ", "station_network", "http://air4thai.pcd.go.th/", "เปิดเผย", "รายชั่วโมง", ""),
    ("noaa_isd", "NOAA Integrated Surface Database / GSOD", "NOAA NCEI", "station_network",
     "https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database", "สาธารณสมบัติ (US Gov)",
     "ราย 1–3 ชม. และรายวัน", "ข้อมูลย้อนหลังของสถานี synoptic ในประเทศไทย"),
    ("user", "รายงานฝนจากผู้ใช้", "ผู้ใช้ระบบ (GitHub Issues)", "crowd", "", "CC BY 4.0", "ตามเหตุการณ์",
     "พิกัดปัดเศษเป็น 0.01° เพื่อคุ้มครองความเป็นส่วนตัว"),
    ("openmeteo", "Open-Meteo Forecast / Ensemble API", "Open-Meteo (รวบรวมจากศูนย์พยากรณ์)", "model_api",
     "https://open-meteo.com/", "CC BY 4.0 (ใช้งานไม่เชิงพาณิชย์ ≤ 10,000 คำขอ/วัน)", "รายชั่วโมง", ""),
    ("tmd_nwp", "TMD NWP API (WRF)", "กรมอุตุนิยมวิทยา", "model_api", "https://data.tmd.go.th/nwpapi/", "TMD Open Data", "รายชั่วโมง",
     "ต้องลงทะเบียนรับ token"),
    ("google_weather", "Google Weather API", "Google Maps Platform", "model_api",
     "https://developers.google.com/maps/documentation/weather", "ตามเงื่อนไข Google (โควตาฟรี)", "รายชั่วโมง", "เฉพาะจุดตรวจสอบหลัก"),
    ("hii_image", "ThaiWater ภาพพยากรณ์ฝน WRF-ROMS", "สสน.", "model_image", "https://www.thaiwater.net/weather", "เปิดเผย", "รายวัน",
     "ค่าเป็นช่วงตามแถบสีของภาพ"),
    ("ecmwf_opendata", "ECMWF Open Data", "ECMWF", "model_grib", "https://data.ecmwf.int/forecasts/", "CC BY 4.0",
     "ราย 3 ชม. ถึง 144 ชม. แล้วราย 6 ชม.", "IFS และ AIFS ความละเอียด 0.25°"),
    ("noaa_nomads", "NOAA NOMADS GRIB filter", "NOAA NCEP", "model_grib", "https://nomads.ncep.noaa.gov/", "สาธารณสมบัติ (US Gov)",
     "ราย 3 ชม.", "GFS 0.25° ตัดกรอบประเทศไทยที่ฝั่งผู้ให้บริการ"),
    ("era5", "ERA5 / ERA5-Land (ARCO, Google Cloud Public Datasets)", "ECMWF / Copernicus C3S", "reanalysis",
     "https://cloud.google.com/storage/docs/public-datasets/era5", "Copernicus licence", "รายชั่วโมง", "ช้ากว่าเวลาจริง ~5 วัน"),
    ("chirps", "CHIRPS v2.0", "UCSB Climate Hazards Center", "satellite", "https://www.chc.ucsb.edu/data/chirps", "CC BY 4.0", "รายวัน",
     "ฝนจากดาวเทียมร่วมกับสถานี 0.05°"),
    ("heatsafe", "พยากรณ์ภัยความร้อนรายเขต (weather_heatsafe.py)", "Consolidated Weather", "model_api", "", "CC BY 4.0", "รายวัน",
     "ปรับแก้ความเอนเอียงจากผลทดสอบย้อนหลัง"),
]

VARIABLES = [
    # var_id, name_th, name_en, unit, cell_method, cf_name
    ("t2m", "อุณหภูมิอากาศ 2 ม.", "2 m air temperature", "°C", "point", "air_temperature"),
    ("td2m", "อุณหภูมิจุดน้ำค้าง 2 ม.", "2 m dew point temperature", "°C", "point", "dew_point_temperature"),
    ("rh2m", "ความชื้นสัมพัทธ์ 2 ม.", "2 m relative humidity", "%", "point", "relative_humidity"),
    ("tmax", "อุณหภูมิสูงสุด", "maximum temperature", "°C", "max", "air_temperature"),
    ("tmin", "อุณหภูมิต่ำสุด", "minimum temperature", "°C", "min", "air_temperature"),
    ("precip", "ปริมาณฝน", "precipitation amount", "มม.", "sum", "precipitation_amount"),
    ("precip_prob", "ความน่าจะเป็นฝน (สัดส่วนสมาชิก ensemble ที่ฝน ≥ เกณฑ์)", "probability of precipitation", "%", "point", ""),
    ("rain_flag", "มีฝน (รายงานผู้ใช้)", "rain occurrence", "0/1", "point", ""),
    ("wind_speed", "ความเร็วลม 10 ม.", "10 m wind speed", "ม./วินาที", "point", "wind_speed"),
    ("wind_dir", "ทิศทางลม 10 ม.", "10 m wind direction", "องศา", "point", "wind_from_direction"),
    ("u10", "ลมองค์ประกอบตะวันออก 10 ม.", "10 m eastward wind", "ม./วินาที", "point", "eastward_wind"),
    ("v10", "ลมองค์ประกอบเหนือ 10 ม.", "10 m northward wind", "ม./วินาที", "point", "northward_wind"),
    ("mslp", "ความกดอากาศระดับน้ำทะเล", "mean sea level pressure", "hPa", "point", "air_pressure_at_mean_sea_level"),
    ("visibility", "ทัศนวิสัย", "visibility", "กม.", "point", "visibility_in_air"),
    ("tcc", "เมฆปกคลุมทั้งหมด", "total cloud cover", "%", "point", "cloud_area_fraction"),
    ("ssrd", "รังสีคลื่นสั้นขาลงที่ผิวโลก", "surface downwelling shortwave flux", "W/m²", "mean",
     "surface_downwelling_shortwave_flux_in_air"),
    ("pm25", "ฝุ่นละอองขนาดไม่เกิน 2.5 ไมครอน", "PM2.5 mass concentration", "µg/m³", "mean", "mass_concentration_of_pm2p5_ambient_aerosol_particles_in_air"),
]

# แบบจำลองที่ดึงไฟล์ GRIB เอง (ใช้ model_id เดียวกับ Open-Meteo เพื่อเทียบข้ามช่องทางได้)
GRIB_MODELS = {"ecmwf_ifs": "ecmwf_opendata", "ecmwf_aifs025_single": "ecmwf_opendata", "gfs_global": "noaa_nomads"}


def slug(name):
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def models():
    rows = []
    for k, m in core.DETERMINISTIC_MODELS.items():
        rows.append({"model_id": k, "name": m["name"], "agency": m["agency"], "kind": "det", "physics": m["type"],
                     "grid_km": m["grid_km"], "members": 1, "source_id": GRIB_MODELS.get(k, "openmeteo")})
    for k, m in core.ENSEMBLE_MODELS.items():
        rows.append({"model_id": f"ens_{k}", "name": m["name"], "agency": "", "kind": "ens", "physics": "AI/ML" if "AI" in m["name"] else "Physics",
                     "grid_km": m["grid_km"], "members": m["members"], "source_id": "openmeteo"})
    rows += [
        {"model_id": "tmd_wrf", "name": core.TMD_MODEL["name"], "agency": core.TMD_MODEL["agency"], "kind": "regional",
         "physics": "Physics", "grid_km": core.TMD_MODEL["grid_km"], "members": 1, "source_id": "tmd_nwp"},
        {"model_id": "google_weather", "name": core.GOOGLE_MODEL["name"], "agency": core.GOOGLE_MODEL["agency"], "kind": "det",
         "physics": "AI/ML", "grid_km": core.GOOGLE_MODEL["grid_km"], "members": 1, "source_id": "google_weather"},
        {"model_id": "hii_wrfroms", "name": "HII WRF-ROMS", "agency": "สสน.", "kind": "regional", "physics": "Physics",
         "grid_km": 3, "members": 1, "source_id": "hii_image"},
        {"model_id": "median", "name": "ค่ามัธยฐานหลายแบบจำลอง", "agency": "Consolidated Weather", "kind": "det",
         "physics": "Physics", "grid_km": None, "members": None, "source_id": "openmeteo"},
    ]
    return pd.DataFrame(rows)


_NAME2ID = None


def model_id(name):
    """ชื่อแบบจำลองในไฟล์เดิม (เช่น 'ECMWF IFS', 'ECMWF ENS') → model_id; ชื่อที่ไม่รู้จัก → slug"""
    global _NAME2ID
    if _NAME2ID is None:
        m = models()
        _NAME2ID = dict(zip(m.name, m.model_id))
        _NAME2ID.update({"ค่ากลาง": "median", "ค่ากลางทุกโมเดล": "median", "median": "median"})
    return _NAME2ID.get(name, slug(name))


def sources():
    return pd.DataFrame(SOURCES, columns=["source_id", "name", "agency", "kind", "url", "license", "time_res", "notes"])


def variables():
    return pd.DataFrame(VARIABLES, columns=["var_id", "name_th", "name_en", "unit", "cell_method", "cf_name"])


def grid_cells():
    g = pd.read_csv(core.ROOT / "resources" / "grid_th025.csv")
    return g[["cell_id", "row", "col", "lat", "lon", "in_thailand", "province_th", "province_en"]]


def nearest_cell(lat, lon):
    """เซลล์ th025 ที่จุดศูนย์กลางใกล้ที่สุด (None ถ้านอกกริดที่เก็บ)"""
    import numpy as np
    g = grid_cells().set_index(["row", "col"]).cell_id
    la, lo = np.atleast_1d(np.asarray(lat, float)), np.atleast_1d(np.asarray(lon, float))
    r = np.rint((21.0 - np.nan_to_num(la, nan=-99)) / 0.25).astype(int)
    c = np.rint((np.nan_to_num(lo, nan=-99) - 97.0) / 0.25).astype(int)
    return [None if np.isnan(x) else g.get((a, b)) for x, a, b in zip(la, r, c)]
