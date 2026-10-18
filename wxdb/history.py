"""ค่าตรวจวัดย้อนหลังรายชั่วโมงของสถานีในประเทศไทยจาก NOAA GHCNh (Global Historical Climatology Network – hourly)

GHCNh เป็นชุดข้อมูลที่ NOAA ใช้แทน ISD/global-hourly (ISD หยุดปรับปรุงเมื่อ ส.ค. 2568) ครอบคลุมสถานี synoptic/METAR
ของประเทศไทย ~160 สถานี หน่วยเมตริก ล่าช้าจากเวลาจริงประมาณ 3 วัน · ไฟล์ Parquet รายสถานีรายปี (ไม่ต้องลงทะเบียน)
  https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly/access/by-year/<ปี>/parquet/GHCNh_<ID>_<ปี>.parquet

การควบคุมคุณภาพ: ใช้เฉพาะค่าที่ Quality_Code ว่าง หรือเป็นรหัสผ่านการตรวจของ ISD เดิม (0, 1, 4, 5, 9)
ค่าที่ถูกตั้งธงโดยการตรวจของ GHCNh (ตัวอักษร) หรือรหัส 2, 3, 6, 7 ถูกตัดออก
จุด: สถานีที่มีรหัส WMO ใช้ site_id = tmd:<WMO> (จุดเดียวกับค่าตรวจวัดของกรมอุตุนิยมวิทยา) มิฉะนั้น ghcnh:<ID>
ผลลัพธ์: release "db-history" ไฟล์ observation__ghcnh_<ปี>.parquet (source_id = noaa_ghcnh)
"""
import re
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core

BASE = "https://www.ncei.noaa.gov/oa/global-historical-climatology-network/hourly"
LIST_URL = f"{BASE}/doc/ghcnh-station-list.txt"
RELEASE = "db-history"
GOOD_QC = {"", "0", "1", "4", "5", "9"}
# คอลัมน์ GHCNh → (var_id, period_h)
VARS = {"temperature": ("t2m", 0), "dew_point_temperature": ("td2m", 0), "relative_humidity": ("rh2m", 0),
        "sea_level_pressure": ("mslp", 0), "wind_speed": ("wind_speed", 0), "wind_direction": ("wind_dir", 0),
        "wind_gust": ("wind_gust", 0), "visibility": ("visibility", 0), "precipitation": ("precip", 1),
        "precipitation_3_hour": ("precip", 3), "precipitation_6_hour": ("precip", 6), "precipitation_12_hour": ("precip", 12),
        "precipitation_24_hour": ("precip", 24)}


def stations(country="TH"):
    """รายชื่อสถานี GHCNh ของประเทศ → DataFrame(ghcnh_id, lat, lon, elev, name, wmo, site_id)"""
    txt = core.SESSION.get(LIST_URL, timeout=120).text
    rows = []
    for line in txt.splitlines():
        if not line.startswith(country):
            continue
        p = line.split()
        wmo = re.findall(r"\b(\d{5})\b", line[72:])
        rows.append({"ghcnh_id": p[0], "lat": float(p[1]), "lon": float(p[2]), "elev": float(p[3]),
                     "name": line[41:72].strip().title(), "wmo": wmo[0] if wmo else None})
    df = pd.DataFrame(rows)
    df["site_id"] = np.where(df.wmo.notna(), "tmd:" + df.wmo.fillna(""), "ghcnh:" + df.ghcnh_id)
    # สถานีซ้ำรหัส WMO (เช่น THM/THU) — ใช้ THM (สถานีหลักของกรมอุตุนิยมวิทยา) ก่อน
    df = df.sort_values("ghcnh_id").drop_duplicates("site_id", keep="first")
    return df.reset_index(drop=True)


def _get(url, tries=4):
    for i in range(tries):
        try:
            r = core.SESSION.get(url, timeout=180)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.content
        except Exception:                               # noqa: BLE001 — ลองใหม่
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def station_year(row, year):
    """ไฟล์รายปีของสถานีเดียว → observation แบบยาว"""
    raw = _get(f"{BASE}/access/by-year/{year}/parquet/GHCNh_{row.ghcnh_id}_{year}.parquet")
    if raw is None:
        return None
    with tempfile.NamedTemporaryFile(suffix=".parquet") as f:
        f.write(raw)
        f.flush()
        cols = ["DATE"] + [c for k in VARS for c in (k, f"{k}_Quality_Code")]
        import pyarrow.parquet as pq
        have = set(pq.read_schema(f.name).names)
        d = pd.read_parquet(f.name, columns=[c for c in cols if c in have])
    t = pd.to_datetime(d.DATE, errors="coerce").dt.floor("min")
    out = []
    for col, (var, period) in VARS.items():
        if col not in d:
            continue
        v = pd.to_numeric(d[col], errors="coerce")
        qc = d.get(f"{col}_Quality_Code", pd.Series("", index=d.index)).fillna("").astype(str).str.strip()
        ok = v.notna() & qc.isin(GOOD_QC) & t.notna()
        if ok.any():
            out.append(pd.DataFrame({"site_id": row.site_id, "time": t[ok], "var_id": var, "period_h": period,
                                     "value": v[ok].astype("float32"), "source_id": "noaa_ghcnh"}))
    if not out:
        return None
    o = pd.concat(out, ignore_index=True)
    return o.drop_duplicates(["site_id", "time", "var_id", "period_h"], keep="first")


def year(st, yr, workers=8):
    """ทุกสถานีของปีหนึ่ง → DataFrame"""
    with ThreadPoolExecutor(workers) as ex:
        parts = list(ex.map(lambda r: station_year(r, yr), st.itertuples()))
    parts = [p for p in parts if p is not None and len(p)]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def site_rows(st):
    """ตาราง site สำหรับสถานี GHCNh (จุดที่ซ้ำกับสถานีของกรมอุตุนิยมวิทยาจะถูกรวมใน merge_sites)"""
    return pd.DataFrame({"site_id": st.site_id, "network": np.where(st.site_id.str.startswith("tmd:"), "tmd", "ghcnh"),
                         "native_id": np.where(st.wmo.notna(), st.wmo, st.ghcnh_id), "name": st.name, "lat": st.lat, "lon": st.lon,
                         "elev_m": st.elev, "source_id": "noaa_ghcnh"})
