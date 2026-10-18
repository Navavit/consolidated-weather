"""ข้อมูลย้อนหลังเชิงกริดรายวัน ณ จุดศูนย์กลางเซลล์ th025 → ตาราง reanalysis_daily (release "db-history")

ชุดข้อมูล
  era5_land  ECMWF ERA5-Land (0.1°) รายวันตามวัน UTC: อุณหภูมิเฉลี่ย/สูงสุด/ต่ำสุด, จุดน้ำค้างเฉลี่ย, ฝนรวม, รังสีคลื่นสั้นขาลงรวม
  chirps     CHIRPS v2.0 (0.05°) ฝนรายวัน (ดาวเทียมร่วมกับสถานี)

ช่องทางการดึง (ให้ค่าเดียวกัน คือค่าของเซลล์ต้นทางที่ครอบจุดศูนย์กลางเซลล์ th025)
  gee        Google Earth Engine — ใช้ดึงย้อนหลังครั้งแรก (ต้องมีบัญชี GEE และตัวแปรแวดล้อม GEE_PROJECT ในเครื่อง)
  openmeteo  Open-Meteo Historical Weather API (timezone=GMT) — ปรับปรุงรายเดือนบน GitHub Actions: อุณหภูมิจาก era5_land
             ฝนและรังสีจาก era5 (ERA5-Land ใช้ฝนและรังสีของ ERA5 เป็นข้อมูลขับเคลื่อน)
  chc        ไฟล์ GeoTIFF รายวันของ UCSB Climate Hazards Center — ปรับปรุงรายเดือนบน GitHub Actions
ไฟล์: reanalysis__<dataset>_<ปี>.parquet (แทนที่ทั้งปีเมื่อปรับปรุง)
"""
import gzip
import io
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core

from . import catalog

RELEASE = "db-history"
COLS = ["t2m_mean", "t2m_max", "t2m_min", "td2m_mean", "precip", "ssrd"]
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CHC_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05/{y}/chirps-v2.0.{y}.{m:02d}.{d:02d}.tif.gz"
ERA5_BANDS = {"temperature_2m": "t2m_mean", "temperature_2m_max": "t2m_max", "temperature_2m_min": "t2m_min",
              "dewpoint_temperature_2m": "td2m_mean", "total_precipitation_sum": "precip",
              "surface_solar_radiation_downwards_sum": "ssrd"}


def _finish(df, dataset, channel):
    df["dataset_id"] = dataset
    df["channel"] = channel
    for c in COLS:
        if c not in df:
            df[c] = np.nan
    df[COLS] = df[COLS].astype("float32").round(2)
    return df[["dataset_id", "cell_id", "date", *COLS, "channel"]]


# ---------------------------------------------------------------------------- Google Earth Engine (ย้อนหลัง)
def _ee():
    import ee
    ee.Initialize(project=os.environ["GEE_PROJECT"])
    return ee


def gee_month(dataset, y, m, cells):
    ee = _ee()
    pts = ee.FeatureCollection([ee.Feature(ee.Geometry.Point(float(lo), float(la)), {"cell_id": int(c)})
                                for c, la, lo in zip(cells.cell_id, cells.lat, cells.lon)])
    start = ee.Date.fromYMD(y, m, 1)
    if dataset == "era5_land":
        col = ee.ImageCollection("ECMWF/ERA5_LAND/DAILY_AGGR").filterDate(start, start.advance(1, "month")).select(list(ERA5_BANDS))
        scale = 11132
    else:
        col = ee.ImageCollection("UCSB-CHG/CHIRPS/DAILY").filterDate(start, start.advance(1, "month")).select(["precipitation"])
        scale = 5566
    if col.size().getInfo() == 0:
        return pd.DataFrame()
    for i in range(4):
        try:
            feats = col.toBands().reduceRegions(pts, ee.Reducer.first(), scale).getInfo()["features"]
            break
        except Exception:                               # noqa: BLE001 — GEE ขัดข้องชั่วคราว
            if i == 3:
                raise
            time.sleep(20 * (i + 1))
    rows = []
    for f in feats:
        p = f["properties"]
        cid = p.pop("cell_id")
        for k, v in p.items():
            day, band = k.split("_", 1)
            rows.append((cid, day, band, v))
    d = pd.DataFrame(rows, columns=["cell_id", "day", "band", "value"])
    d = d.pivot_table(index=["cell_id", "day"], columns="band", values="value").reset_index()
    d["date"] = pd.to_datetime(d.day, format="%Y%m%d")
    if dataset == "era5_land":
        d = d.rename(columns=ERA5_BANDS)
        for c in ("t2m_mean", "t2m_max", "t2m_min", "td2m_mean"):
            d[c] = d[c] - 273.15
        d["precip"] = d.precip * 1000
        d["ssrd"] = d.ssrd / 1e6
    else:
        d = d.rename(columns={"precipitation": "precip"})
    return _finish(d, dataset, "gee")


# ---------------------------------------------------------------------------- Open-Meteo / CHC (รายเดือน)
def openmeteo_month(y, m, cells, batch=100):
    start = pd.Timestamp(y, m, 1)
    end = min(start + pd.offsets.MonthEnd(0), pd.Timestamp.now(tz="UTC").tz_localize(None).normalize() - pd.Timedelta(days=6))
    if end < start:
        return pd.DataFrame()
    # ERA5-Land ใน Open-Meteo มีเฉพาะตัวแปรพื้นผิวบางส่วน ฝนและรังสีจึงใช้ ERA5 (ERA5-Land เองก็ใช้ฝนและรังสีของ ERA5
    # ที่ประมาณค่าในช่วงลงกริด 0.1° เป็นข้อมูลขับเคลื่อน จึงสอดคล้องกัน)
    sets = {"era5_land": "temperature_2m_mean,temperature_2m_max,temperature_2m_min,dew_point_2m_mean",
            "era5": "precipitation_sum,shortwave_radiation_sum"}
    names = {"temperature_2m_mean": "t2m_mean", "temperature_2m_max": "t2m_max", "temperature_2m_min": "t2m_min",
             "dew_point_2m_mean": "td2m_mean", "precipitation_sum": "precip", "shortwave_radiation_sum": "ssrd"}
    frames = []
    for model, daily in sets.items():
        rows = []
        for i in range(0, len(cells), batch):
            part = cells.iloc[i:i + batch]
            r = core.SESSION.get(ARCHIVE_URL, timeout=300, params={
                "latitude": ",".join(f"{x:.3f}" for x in part.lat), "longitude": ",".join(f"{x:.3f}" for x in part.lon),
                "start_date": f"{start:%Y-%m-%d}", "end_date": f"{end:%Y-%m-%d}", "models": model, "timezone": "GMT", "daily": daily})
            r.raise_for_status()
            js = r.json()
            for cid, j in zip(part.cell_id, js if isinstance(js, list) else [js]):
                dd = j["daily"]
                rows.append(pd.DataFrame({"cell_id": cid, "date": pd.to_datetime(dd["time"]),
                                          **{names[k]: dd[k] for k in daily.split(",")}}))
            time.sleep(1)
        frames.append(pd.concat(rows, ignore_index=True).set_index(["cell_id", "date"]))
    d = pd.concat(frames, axis=1).reset_index()
    d[COLS] = d[COLS].apply(pd.to_numeric, errors="coerce")
    return _finish(d, "era5_land", "openmeteo")


def chc_month(y, m, cells):
    import tifffile
    r = np.floor((50.0 - cells.lat.to_numpy()) / 0.05).astype(int)
    c = np.floor((cells.lon.to_numpy() + 180.0) / 0.05).astype(int)
    out = []
    for day in pd.date_range(pd.Timestamp(y, m, 1), pd.Timestamp(y, m, 1) + pd.offsets.MonthEnd(0)):
        resp = core.SESSION.get(CHC_URL.format(y=day.year, m=day.month, d=day.day), timeout=300)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        a = tifffile.imread(io.BytesIO(gzip.decompress(resp.content)))
        v = a[r, c].astype("float32")
        v[v < 0] = np.nan
        out.append(pd.DataFrame({"cell_id": cells.cell_id.to_numpy(), "date": day, "precip": v}))
    return _finish(pd.concat(out, ignore_index=True), "chirps", "chc") if out else pd.DataFrame()


def cells():
    return catalog.grid_cells()[["cell_id", "lat", "lon"]]
