"""ข้อมูลทั่วประเทศสำหรับหน้าเว็บ (แท็บ "ทั่วประเทศ") → site/data/national.json

1) พยากรณ์รายจังหวัด 7 วัน ณ จุดตัวแทนของ 77 จังหวัด (resources/th_provinces.csv)
   จาก Open-Meteo 4 แบบจำลอง (ECMWF IFS, ECMWF AIFS, NOAA GFS, DWD ICON) รายชั่วโมง → สรุปรายวัน (วันปฏิทินเวลาไทย)
   อุณหภูมิสูงสุด/ต่ำสุด, ดัชนีความร้อนสูงสุด (NWS; ไม่ปรับแก้ความเอนเอียง), ฝนรวม, สัดส่วนแบบจำลองที่ฝน ≥ เกณฑ์
   ดึงใหม่ทุก RUN_EVERY_MIN นาที (77 จุด × 12 ตัวแปร ≈ 154 คำขอ/รอบ ≈ 620 คำขอ/วัน ภายใต้โควตาฟรี)
2) ความถูกต้องรายสถานีอุตุนิยมวิทยา (~127 สถานี) รายภาค และทั้งประเทศ ในช่วง VERIFY_DAYS วันล่าสุด
   จากคู่ตรวจสอบ national (ฝนรายวัน, Tmax, Tmin) และ national3h (อุณหภูมิราย 3 ชม.) ของ weather_verify.py

ผลลัพธ์: archive/national/forecast.json (ผลพยากรณ์รอบล่าสุด), archive/national/daily/<วันที่>.csv.gz (ค่ารายวันต่อแบบจำลอง
เก็บวันละครั้งเข้าฐานข้อมูล wxdb ตาราง forecast_district), site/data/national.json
"""
import gzip
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core
import weather_heatsafe as hsafe

RES = core.ROOT / "resources"
RUN_EVERY_MIN = 350
VERIFY_DAYS = 30
NAT_MODELS = ["ecmwf_ifs", "ecmwf_aifs025_single", "gfs_global", "icon_global"]
LEADS = ["H24", "H72", "H168"]


def provinces():
    return pd.read_csv(RES / "th_provinces.csv")


# ---------------------------------------------------------------------------- พยากรณ์รายจังหวัด
def forecast(cfg=None):
    cfg = cfg or core.CFG
    prov = provinces().rename(columns={"pcode": "dcode"})
    active = core.active_models(cfg)
    models = {k: active[k]["name"] for k in NAT_MODELS if k in active}
    df = hsafe.fetch(prov, models)
    today = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None).normalize()
    df["hi"] = hsafe.heat_index_c(df.t, df.rh)
    df["date"] = df.time.dt.normalize()
    d = df.groupby(["dcode", "model", "date"]).agg(n=("t", "size"), tmax=("t", "max"), tmin=("t", "min"), rh_min=("rh", "min"),
                                                   hi_max=("hi", "max"), rain=("rain", "sum")).reset_index()
    d = d[(d.n >= 20)]
    d["lead_day"] = (d.date - today).dt.days
    d = d[d.lead_day.between(0, hsafe.DAYS - 1)]
    thr = cfg.get("rain_threshold_mm", 1.0)
    info = prov.set_index("dcode")
    out = []
    for pc, g in d.groupby("dcode"):
        days = []
        for date, x in g.groupby("date"):
            hi = x.hi_max.dropna()
            med = float(hi.median()) if len(hi) else np.nan
            days.append({"date": str(date.date()), "lead_day": int(x.lead_day.iloc[0]), "n_models": int(len(x)),
                         "tmax": hsafe._r(x.tmax.median()), "tmax_lo": hsafe._r(x.tmax.min()), "tmax_hi": hsafe._r(x.tmax.max()),
                         "tmin": hsafe._r(x.tmin.median()), "hi": hsafe._r(med), "lvl": hsafe.level(med),
                         "p_yellow": hsafe._p(hi >= 33), "p_orange": hsafe._p(hi >= 42),
                         "rain": hsafe._r(x.rain.median()), "rain_hi": hsafe._r(x.rain.max()), "p_rain": hsafe._p(x.rain >= thr)})
        r = info.loc[pc]
        out.append({"pcode": pc, "name": r.province_th, "name_en": r.province_en, "region": r.region,
                    "lat": float(r.lat), "lon": float(r.lon), "days": days})
    return d, {"generated": pd.Timestamp.now(tz="Asia/Bangkok").isoformat(timespec="minutes"),
               "models": sorted(d.model.unique()), "rain_threshold_mm": thr, "provinces": out}


# ---------------------------------------------------------------------------- ความถูกต้องรายสถานี
def _point_in(poly, x, y):
    """ray casting — poly: [[x, y], ...]"""
    inside, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def _province_of(lat, lon, geo):
    for f in geo["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        if any(_point_in(p[0], lon, lat) for p in polys):
            return f["properties"]["name"]
    return None


def stations(archive):
    """สถานีอุตุนิยมวิทยา: ชื่อ พิกัด จังหวัด ภาค และค่าตรวจวัดล่าสุด (จาก obs/tmd3h)"""
    files = sorted((Path(archive) / "obs" / "tmd3h").glob("*.csv"))[-3:]
    if not files:
        return pd.DataFrame()
    o = pd.concat([pd.read_csv(f, dtype={"id": str}) for f in files], ignore_index=True)
    o["time"] = pd.to_datetime(o.time, errors="coerce")
    o = o.sort_values("time")
    last = o.groupby("id").tail(1).set_index("id")
    prov = provinces()
    region = dict(zip(prov.province_th, prov.region))
    geo = json.loads((core.ROOT / "web" / "th_provinces.geojson").read_text(encoding="utf-8"))
    pv = [p if p in region else (_province_of(la, lo, geo) or p) for p, la, lo in zip(last.province, last.lat, last.lon)]
    return pd.DataFrame({"id": last.index, "name": last["name"].values, "province": pv, "region": [region.get(p) for p in pv],
                         "lat": last.lat.values, "lon": last.lon.values, "time": last.time.dt.strftime("%Y-%m-%dT%H:%M").values,
                         "temp": last.temp.values, "rain_24h": last.rain_24h.values})


def _temp_scores(g):
    e = g.fc - g.ob
    return [round(float(e.abs().mean()), 2), round(float(e.mean()), 2), int(len(g))]


def _rain_scores(g):
    hit = int((g.ev_fc & g.ev_ob).sum())
    miss = int((~g.ev_fc & g.ev_ob).sum())
    fa = int((g.ev_fc & ~g.ev_ob).sum())
    n = len(g)
    acc = round(100 * float((g.ev_fc == g.ev_ob).mean()), 1)
    csi = round(100 * hit / (hit + miss + fa), 1) if hit + miss + fa else None
    pod = round(100 * hit / (hit + miss), 1) if hit + miss else None
    far = round(100 * fa / (hit + fa), 1) if hit + fa else None
    return [csi, acc, pod, far, int(n), int(hit + miss)]


def verification(archive, st):
    import weather_verify as wv
    p = wv.load_pairs(archive, days=VERIFY_DAYS)
    if p is None or not len(p):
        return {}, [], {}
    p = p[p.source.isin(["national", "national3h"]) & p.lead.isin(LEADS) & (p.kind == "det")].copy()
    p["fc"] = pd.to_numeric(p.fc, errors="coerce")
    p["ob"] = pd.to_numeric(p.ob, errors="coerce")
    p = p.dropna(subset=["fc", "ob"])
    for c in ("ev_fc", "ev_ob"):
        p[c] = p[c].astype(str).str.lower().eq("true")
    name2id = dict(zip(st.name, st.id))
    reg = dict(zip(st.id, st.region))
    p["sid"] = p.point.map(name2id)
    p["region"] = p.sid.map(reg)
    sets = {"temp": (p.source == "national3h") & (p.variable == "temp"), "tmax": (p.source == "national") & (p.variable == "tmax"),
            "tmin": (p.source == "national") & (p.variable == "tmin"), "rain": (p.source == "national") & (p.variable == "rain")}

    def agg(df):
        out = {}
        for var, m in sets.items():
            g = df[m.reindex(df.index, fill_value=False)]
            fn = _rain_scores if var == "rain" else _temp_scores
            out[var] = {ld: {mo: fn(x) for mo, x in gl.groupby("model")} for ld, gl in g.groupby("lead")}
        return out

    per_station = {sid: agg(g) for sid, g in p.dropna(subset=["sid"]).groupby("sid")}
    regions = [{"region": r, **agg(g)} for r, g in p.dropna(subset=["region"]).groupby("region")]
    period = {"from": str(pd.to_datetime(p.obs_time).min())[:10], "to": str(pd.to_datetime(p.obs_time).max())[:10], "days": VERIFY_DAYS}
    return per_station, regions, {"all": agg(p), "period": period, "models": sorted(p.model.unique())}


# ---------------------------------------------------------------------------- รวม
def _due(hdir):
    f = hdir / "last_run.txt"
    return not f.exists() or time.time() - float(f.read_text() or 0) > RUN_EVERY_MIN * 60


def update(archive, site_dir=None, cfg=None):
    """เรียกจาก collector.py run ทุกรอบ: พยากรณ์รายจังหวัด (ทุก ~6 ชม.) + ความถูกต้องรายสถานี (ทุกรอบ)"""
    archive = Path(archive)
    hdir = archive / "national"
    hdir.mkdir(parents=True, exist_ok=True)
    if _due(hdir):
        daily, fc = forecast(cfg)
        (hdir / "forecast.json").write_text(json.dumps(fc, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
        now = pd.Timestamp.now(tz="Asia/Bangkok")
        dfile = hdir / "daily" / f"{now:%Y-%m-%d}.csv.gz"
        if now.hour >= 7 and not dfile.exists():
            dfile.parent.mkdir(exist_ok=True)
            with gzip.open(dfile, "wt", encoding="utf-8") as fh:
                daily.assign(issued=now.strftime("%Y-%m-%d %H:%M")).drop(columns=["n"]).round(2).to_csv(fh, index=False)
        (hdir / "last_run.txt").write_text(str(time.time()))
        print(f"ทั่วประเทศ: พยากรณ์ {len(fc['provinces'])} จังหวัด · {len(fc['models'])} แบบจำลอง")
    if not site_dir:
        return
    fc = json.loads((hdir / "forecast.json").read_text(encoding="utf-8")) if (hdir / "forecast.json").exists() else None
    st = stations(archive)
    per_station, regions, overall = verification(archive, st) if len(st) else ({}, [], {})
    st_rows = []
    for r in st.itertuples():
        st_rows.append({"id": r.id, "name": r.name, "province": r.province, "region": r.region, "lat": r.lat, "lon": r.lon,
                        "time": r.time, "temp": None if pd.isna(r.temp) else r.temp, "rain_24h": None if pd.isna(r.rain_24h) else r.rain_24h,
                        "scores": per_station.get(r.id, {})})
    payload = {"generated": pd.Timestamp.now(tz="Asia/Bangkok").isoformat(timespec="minutes"),
               "forecast": fc, "stations": st_rows, "regions": regions,
               "national": {k: v for k, v in overall.items() if k == "all"}.get("all", {}),
               "verify_period": overall.get("period"), "verify_models": overall.get("models", []), "leads": LEADS}
    txt = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=lambda o: None)
    (Path(site_dir) / "data").mkdir(parents=True, exist_ok=True)
    (Path(site_dir) / "data" / "national.json").write_text(txt.replace("NaN", "null"), encoding="utf-8")


if __name__ == "__main__":
    import sys
    update(sys.argv[1] if len(sys.argv) > 1 else core.ROOT / "archive", sys.argv[2] if len(sys.argv) > 2 else None)
