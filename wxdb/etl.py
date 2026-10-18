"""แปลงไฟล์ในคลังรายชั่วโมง (branch "data") ของวันหนึ่งเป็นตารางของฐานข้อมูล

ไฟล์ต้นทางใช้เวลาท้องถิ่น (Asia/Bangkok, UTC+7) — ทุกตารางปลายทางแปลงเป็น UTC
ประมวลผลทีละวันตามวันที่ท้องถิ่นของไฟล์ (โดยปกติคือวันที่ผ่านไปแล้ว 2 วัน เพื่อให้ไฟล์ครบ)
"""
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core

from . import catalog

TZ = pd.Timedelta(hours=7)
KMH = 1 / 3.6


def _utc(s):
    return pd.to_datetime(s, errors="coerce") - TZ


def _read_csv(p, **kw):
    p = Path(p)
    if not p.exists():
        return None
    try:
        return pd.read_csv(p, compression="gzip" if p.suffix == ".gz" else None, low_memory=False, **kw)
    except (pd.errors.EmptyDataError, EOFError, gzip.BadGzipFile):
        return None


def _melt(df, site, time, spec, source):
    """spec = [(คอลัมน์ต้นทาง, var_id, period_h, ตัวคูณ)] → observation แบบยาว"""
    out = []
    for col, var, period, mul in spec:
        if col not in df:
            continue
        v = pd.to_numeric(df[col], errors="coerce") * mul
        out.append(pd.DataFrame({"site_id": site, "time": time, "var_id": var, "period_h": period, "value": v, "source_id": source}))
    o = pd.concat(out, ignore_index=True) if out else pd.DataFrame()
    return o.dropna(subset=["value", "time"]) if len(o) else o


# ---------------------------------------------------------------------------- ค่าตรวจวัด
def observations(archive, day):
    a, d = Path(archive), f"{day:%Y-%m-%d}"
    parts, sites = [], []
    x = _read_csv(a / "obs" / "tmd3h" / f"{d}.csv", dtype={"id": str})
    if x is not None and len(x):
        sid = "tmd:" + x["id"]
        parts.append(_melt(x, sid, _utc(x["time"]), [
            ("temp", "t2m", 0, 1), ("rh", "rh2m", 0, 1), ("dew", "td2m", 0, 1), ("rain_3h", "precip", 3, 1),
            ("rain_24h", "precip", 24, 1), ("wind_kmh", "wind_speed", 0, KMH), ("wind_dir", "wind_dir", 0, 1),
            ("pressure", "mslp", 0, 1), ("visibility_km", "visibility", 0, 1)], "tmd"))
        sites.append(pd.DataFrame({"site_id": sid, "network": "tmd", "native_id": x["id"], "name": x.get("name"), "province": x.get("province"),
                                   "agency": "กรมอุตุนิยมวิทยา", "lat": x["lat"], "lon": x["lon"], "source_id": "tmd", "seen": _utc(x["time"])}))
    x = _read_csv(a / "obs" / "tmdday" / f"{d}.csv", dtype={"id": str})
    if x is not None and len(x):
        sid = "tmd:" + x["id"]
        parts.append(_melt(x, sid, _utc(x["time"]), [("rain_24h", "precip", 24, 1), ("tmax", "tmax", 24, 1), ("tmin", "tmin", 24, 1)], "tmd"))
        sites.append(pd.DataFrame({"site_id": sid, "network": "tmd", "native_id": x["id"], "name": x.get("name"), "agency": "กรมอุตุนิยมวิทยา",
                                   "lat": x["lat"], "lon": x["lon"], "source_id": "tmd", "seen": _utc(x["time"])}))
    x = _read_csv(a / "obs" / "thaiwater" / f"{d}.csv", dtype={"id": str})
    if x is not None and len(x):
        sid = "tw:" + x["id"]
        parts.append(_melt(x, sid, _utc(x["time"]), [("rain_24h", "precip", 24, 1)], "thaiwater"))
        sites.append(pd.DataFrame({"site_id": sid, "network": "tw", "native_id": x["id"], "name": x.get("name").astype(str) + (" (" + x["area"].fillna("") + ")" if "area" in x else ""),
                                   "province": x.get("province"), "agency": x.get("agency"), "lat": x["lat"], "lon": x["lon"],
                                   "source_id": "thaiwater", "seen": _utc(x["time"])}))
    x = _read_csv(a / "obs" / "tw1h" / f"{d}.csv", dtype={"id": str})
    if x is not None and len(x):
        parts.append(_melt(x, "tw:" + x["id"], _utc(x["time"]), [("rain_1h", "precip", 1, 1)], "thaiwater"))
    x = _read_csv(a / "obs" / "twday" / f"{d}.csv.gz", dtype={"id": str})
    if x is not None and len(x):
        t = pd.Timestamp(d) + pd.Timedelta(hours=7) - TZ                  # ฝนสะสม 07:00 → 07:00 น. สิ้นสุดวันที่ของไฟล์
        sid = "tw:" + x["id"]
        parts.append(_melt(x, sid, t, [("rain", "precip", 24, 1)], "thaiwater"))
        sites.append(pd.DataFrame({"site_id": sid, "network": "tw", "native_id": x["id"], "name": x.get("name"), "agency": x.get("agency"),
                                   "lat": x["lat"], "lon": x["lon"], "source_id": "thaiwater", "seen": t}))
    x = _read_csv(a / "obs" / "air4thai" / f"{d}.csv", dtype={"id": str})
    if x is not None and len(x):
        sid = "air4thai:" + x["id"]
        parts.append(_melt(x, sid, _utc(x["time"]), [
            ("pm25", "pm25", 24, 1), ("pm10", "pm10", 24, 1), ("o3", "o3", 8, 1), ("co", "co", 8, 1),
            ("no2", "no2", 1, 1), ("so2", "so2", 1, 1), ("aqi", "aqi", 0, 1)], "air4thai"))
        sites.append(pd.DataFrame({"site_id": sid, "network": "air4thai", "native_id": x["id"], "name": x.get("name"),
                                   "province": x["area"].str.split(",").str[-1].str.strip() if "area" in x else None,
                                   "agency": "กรมควบคุมมลพิษ", "lat": x["lat"], "lon": x["lon"], "source_id": "air4thai",
                                   "seen": _utc(x["time"])}))
    for f in sorted((a / "observations").glob("*.json")):
        j = json.loads(f.read_text(encoding="utf-8"))
        t = pd.Timestamp(j["obs_time"])
        if t.normalize() != pd.Timestamp(d):
            continue
        la, lo = round(float(j["lat"]), 2), round(float(j["lon"]), 2)         # ปัดพิกัดเพื่อความเป็นส่วนตัว (~1 กม.)
        sid = f"user:{la:.2f}_{lo:.2f}"
        rows = [{"site_id": sid, "time": t - TZ, "var_id": "rain_flag", "period_h": 0, "value": float(bool(j["rained"])), "source_id": "user"}]
        if j.get("amount_mm") is not None:
            rows.append({**rows[0], "var_id": "precip", "period_h": 1, "value": float(j["amount_mm"])})
        parts.append(pd.DataFrame(rows))
        sites.append(pd.DataFrame([{"site_id": sid, "network": "user", "native_id": f"{la:.2f}_{lo:.2f}", "name": "รายงานผู้ใช้",
                                    "lat": la, "lon": lo, "source_id": "user", "seen": t - TZ}]))
    obs = pd.concat([p for p in parts if len(p)], ignore_index=True) if parts else pd.DataFrame()
    return obs, sites


# ---------------------------------------------------------------------------- พยากรณ์ ณ จุด
_PREFIX = {"det_mm": "precip", "det_t": "t2m", "ens_prob": "precip_prob"}


def _point_site(name, lat, lon, gauges):
    """ตำแหน่งในไฟล์ runs → site_id: โทรมาตร ThaiWater ที่ห่าง < 150 ม. หรือ loc:<พิกัด>"""
    d = (gauges.lat - lat) ** 2 + ((gauges.lon - lon) * np.cos(np.radians(lat))) ** 2
    i = int(np.argmin(d.to_numpy()))
    if np.sqrt(d.iloc[i]) * 111 < 0.15:
        return "tw:" + str(gauges.id.iloc[i])
    return f"loc:{lat:.3f}_{lon:.3f}"


def forecast_points(archive, day, gauges):
    base = Path(archive) / "runs" / f"{day:%Y/%m/%d}"
    frames, sites, runs = [], {}, []
    for f in sorted(base.glob("*.csv.gz")):
        x = _read_csv(f)
        meta_f = f.with_name(f.name.replace(".csv.gz", ".runs.json"))
        if x is None or not len(x):
            continue
        # ไฟล์ก่อน ~29 ก.ย. 2569 ไม่มี .runs.json — ใช้เวลาที่ดึงแทนเวลาเริ่มรอบ (init_time ไม่แม่นยำ)
        meta = json.loads(meta_f.read_text(encoding="utf-8")) if meta_f.exists() else {}
        fetched = pd.Timestamp(meta["fetched_at_utc"]) if "fetched_at_utc" in meta else _utc(x.fetched_at.iloc[0])
        lat, lon, name = float(x.lat.iloc[0]), float(x.lon.iloc[0]), str(x.location.iloc[0])
        key = (round(lat, 4), round(lon, 4))
        if key not in sites:
            sid = _point_site(name, lat, lon, gauges)
            sites[key] = {"site_id": sid, "network": sid.split(":")[0], "native_id": sid.split(":", 1)[1], "name": name,
                          "lat": lat, "lon": lon, "source_id": "thaiwater" if sid.startswith("tw:") else "openmeteo", "seen": fetched}
        sid = sites[key]["site_id"]
        valid = _utc(x.valid_time)
        cols = [c for c in x.columns if "|" in c and c.split("|")[0] in _PREFIX]
        long = x[cols].assign(valid_time=valid).melt("valid_time", var_name="col", value_name="value").dropna(subset=["value"])
        if not len(long):
            continue
        pre_model = long.col.str.split("|", n=1, expand=True)
        long["var_id"] = pre_model[0].map(_PREFIX)
        long["model_name"] = pre_model[1]
        long["model_id"] = pre_model[1].map(catalog.model_id)
        inits = {n: pd.Timestamp(r["init"]) for n, r in (meta.get("runs") or {}).items() if r.get("init")}
        long["init_time"] = long.model_name.map(inits)
        long["init_time"] = pd.to_datetime(long.init_time).fillna(fetched.floor("h"))   # แหล่งที่ไม่มี metadata รอบ: ใช้เวลาที่ดึง
        long["fetched_at"] = fetched
        long["site_id"] = sid
        frames.append(long.drop(columns=["col", "model_name"]))
        for n, t in inits.items():
            runs.append({"model_id": catalog.model_id(n), "init_time": t, "first_fetched": fetched, "source_id": "openmeteo"})
    if not frames:
        return pd.DataFrame(), list(sites.values()), pd.DataFrame(runs)
    fp = pd.concat(frames, ignore_index=True).sort_values("fetched_at")
    # เก็บค่าที่ได้รับครั้งแรกของแต่ละรอบ (ค่าที่ดึงซ้ำในรอบเดียวกันไม่เพิ่มสารสนเทศ)
    fp = fp.drop_duplicates(["model_id", "init_time", "site_id", "valid_time", "var_id"], keep="first")
    fp["lead_h"] = ((fp.valid_time - fp.init_time).dt.total_seconds() / 3600).round().astype("int16")
    fp = fp[fp.lead_h >= 0]
    return fp, list(sites.values()), pd.DataFrame(runs)


# ---------------------------------------------------------------------------- HII / ภัยความร้อน / คู่ตรวจสอบ
def hii(archive, day, name2site):
    x = _read_csv(Path(archive) / "hii" / f"{day:%Y%m%d}T1900.csv")
    if x is None or not len(x):
        return pd.DataFrame()
    return pd.DataFrame({"init_time": _utc(x["init"]), "site_id": [name2site.get(p, f"loc:{la:.3f}_{lo:.3f}") for p, la, lo in zip(x.point, x.lat, x.lon)],
                         "day": x["day"], "domain_km": x["domain_km"], "valid_start": _utc(x["start"]), "valid_end": _utc(x["end"]),
                         "low": x["low"], "high": x["high"], "mid": x["mid"]})


def heat_district(archive, day):
    """พยากรณ์รายวันต่อแบบจำลอง: รายเขต กทม. (heatsafe/, ปรับแก้ความเอนเอียง) และรายจังหวัด (national/, ไม่ปรับแก้)"""
    out = []
    for sub, prefix in (("heatsafe", "bkkdist:"), ("national", "prov:")):
        x = _read_csv(Path(archive) / sub / "daily" / f"{day:%Y-%m-%d}.csv.gz", dtype={"dcode": str})
        if x is None or not len(x):
            continue
        raw = x["hi_max_raw"] if "hi_max_raw" in x else x["hi_max"]
        out.append(pd.DataFrame({"issued": _utc(x["issued"]), "site_id": prefix + x["dcode"], "model_id": x.model.map(catalog.model_id),
                                 "date": pd.to_datetime(x["date"]), "lead_day": x["lead_day"], "tmax": x["tmax"], "tmin": x["tmin"],
                                 "rh_min": x["rh_min"], "hi_max_raw": raw, "bias": x.get("bias"), "hi_max": x["hi_max"], "precip": x["rain"]}))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def pairs(archive, day, name2site):
    x = _read_csv(Path(archive) / "verification" / "pairs" / f"{day:%Y-%m}.csv.gz")
    if x is None or not len(x):
        return pd.DataFrame()
    x = x[pd.to_datetime(x.obs_time).dt.normalize() == pd.Timestamp(day)]
    if not len(x):
        return pd.DataFrame()
    var = x.variable.map({"rain": "precip", "temp": "t2m"}).fillna(x.variable)
    return pd.DataFrame({"obs_source": x.source, "site_id": [name2site.get(p, "loc:" + str(p)) for p in x.point],
                         "valid_time": _utc(x.obs_time), "var_id": var, "model_id": x.model.map(catalog.model_id),
                         "kind": x.kind, "lead": x.lead, "lead_h": x.lead_h, "init_time": pd.to_datetime(x["init"], errors="coerce"),
                         "scheme": x.scheme, "fc": x.fc, "ob": x.ob,
                         "ev_fc": x.ev_fc.astype(str).str.lower().eq("true"), "ev_ob": x.ev_ob.astype(str).str.lower().eq("true")})


# ---------------------------------------------------------------------------- ตาราง site
def static_sites(archive):
    """จุดที่ทราบล่วงหน้า: โทรมาตรทั้งหมด (resources/gauges.csv), จุดตัวแทนเขต กทม., จุดตรวจสอบใน verification/points.json"""
    g = pd.read_csv(core.ROOT / "resources" / "gauges.csv", dtype={"id": str})
    out = [pd.DataFrame({"site_id": "tw:" + g.id, "network": "tw", "native_id": g.id, "name": g.name, "agency": g.agency,
                         "lat": g.lat, "lon": g.lon, "source_id": "thaiwater"})]
    b = pd.read_csv(core.ROOT / "resources" / "bkk_districts.csv", dtype={"dcode": str})
    out.append(pd.DataFrame({"site_id": "bkkdist:" + b.dcode, "network": "bkkdist", "native_id": b.dcode,
                             "name": "เขต" + b.dname, "province": "กรุงเทพมหานคร", "agency": "กรุงเทพมหานคร",
                             "lat": b.lat, "lon": b.lon, "source_id": "heatsafe"}))
    locs = [core.CFG.get("my_location")] + list(core.CFG.get("target_places") or [])
    locs = [x for x in locs if isinstance(x, (list, tuple)) and len(x) == 3]
    out.append(pd.DataFrame([{"site_id": f"loc:{la:.3f}_{lo:.3f}", "network": "loc", "native_id": f"{la:.3f}_{lo:.3f}", "name": n,
                              "lat": la, "lon": lo, "source_id": "openmeteo"} for la, lo, n in locs]
                            + [{"site_id": "user:gps", "network": "user", "native_id": "gps", "name": "รายงานผู้ใช้ตามพิกัด GPS (ไม่ระบุพิกัด)",
                                "source_id": "user"}]))
    pv = pd.read_csv(core.ROOT / "resources" / "th_provinces.csv")
    out.append(pd.DataFrame({"site_id": "prov:" + pv.pcode, "network": "prov", "native_id": pv.pcode, "name": "จุดตัวแทนจังหวัด" + pv.province_th,
                             "province": pv.province_th, "lat": pv.lat, "lon": pv.lon, "source_id": "openmeteo"}))
    pj = Path(archive) / "verification" / "points.json"
    if pj.exists():
        p = pd.DataFrame(json.loads(pj.read_text(encoding="utf-8")))
        out.append(pd.DataFrame({"site_id": p.key, "network": p.kind, "native_id": p.id, "name": p.name, "lat": p.lat, "lon": p.lon,
                                 "source_id": p.kind.map({"tmd": "tmd", "tw": "thaiwater"}).fillna("openmeteo")}))
    return out


def name_to_site(archive):
    """ชื่อจุดที่ปรากฏในไฟล์ pairs/hii → site_id"""
    m = {}
    for f in sorted((Path(archive) / "obs" / "tmd3h").glob("*.csv"))[-3:]:          # ชื่อสถานีอุตุฯ (ชุด national)
        x = _read_csv(f, dtype={"id": str})
        if x is not None:
            m.update(dict(zip(x["name"], "tmd:" + x["id"])))
    for f in sorted((Path(archive) / "obs" / "thaiwater").glob("*.csv")):            # "โทรมาตร<ชื่อ> (ต.… อ.…)"
        x = _read_csv(f, dtype={"id": str})
        if x is not None:
            m.update(dict(zip("โทรมาตร" + x["name"].str.strip() + " (" + (x["area"] if "area" in x else pd.Series("", index=x.index)).fillna("").str.strip() + ")", "tw:" + x.id)))
    m.update({"ตรงที่ฉันอยู่ (GPS)": "user:gps", "ตำแหน่งของผู้ใช้ (GPS)": "user:gps"})   # รายงานจาก GPS (พิกัดเปลี่ยนตามผู้ใช้)
    pj = Path(archive) / "verification" / "points.json"
    if pj.exists():
        for p in json.loads(pj.read_text(encoding="utf-8")):
            m[p["name"]] = p["key"]
            if p.get("for"):
                m.setdefault(p["for"], "loc:" + p["for"])
    for loc in [core.CFG.get("my_location")] + list(core.CFG.get("target_places") or []):
        if isinstance(loc, (list, tuple)) and len(loc) == 3:
            m[loc[2]] = f"loc:{loc[0]:.3f}_{loc[1]:.3f}"
    return m


def merge_sites(old, new_parts):
    """รวมตาราง site เดิมกับจุดที่พบใหม่ ปรับ first_seen/last_seen และเติม cell_id"""
    new = pd.concat([p for p in new_parts if p is not None and len(p)], ignore_index=True)
    if "seen" not in new:
        new["seen"] = pd.NaT
    new["seen"] = pd.to_datetime(new["seen"])
    seen = new.groupby("site_id").seen.agg(["min", "max"])
    keep = [c for c in new.columns if c != "seen"]
    cur = new[keep].groupby("site_id", as_index=False).first()
    if old is not None and len(old):
        cur = pd.concat([old.drop(columns=["first_seen", "last_seen", "cell_id"], errors="ignore"), cur]).groupby("site_id", as_index=False).first()
        prev = old.set_index("site_id")[["first_seen", "last_seen"]]
    else:
        prev = pd.DataFrame(columns=["first_seen", "last_seen"])
    cur = cur.set_index("site_id")
    cur["first_seen"] = pd.concat([pd.to_datetime(prev.first_seen), seen["min"]], axis=1).min(axis=1).reindex(cur.index)
    cur["last_seen"] = pd.concat([pd.to_datetime(prev.last_seen), seen["max"]], axis=1).max(axis=1).reindex(cur.index)
    cur = cur.reset_index()
    cur["cell_id"] = catalog.nearest_cell(cur.lat, cur.lon)
    return cur


def run_day(archive, day):
    """แปลงไฟล์ทั้งหมดของวันที่ day (ท้องถิ่น) → {table: DataFrame}, [site parts], runs"""
    day = pd.Timestamp(day).normalize()
    gauges = pd.read_csv(core.ROOT / "resources" / "gauges.csv", dtype={"id": str})
    n2s = name_to_site(archive)
    obs, site_parts = observations(archive, day)
    fp, fsites, runs = forecast_points(archive, day, gauges)
    facts = {"observation": obs, "forecast_point": fp, "forecast_hii": hii(archive, day, n2s),
             "forecast_district": heat_district(archive, day), "verification_pair": pairs(archive, day, n2s)}
    return facts, site_parts + [pd.DataFrame(fsites)], runs
