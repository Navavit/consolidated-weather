"""วัดความแม่นของโมเดลอัตโนมัติด้วยค่าวัดจริงจากสถานีในไทย

เฉลย (observation) 3 แหล่ง + การกดปุ่มของผู้ใช้
  tmd3h        สถานีอุตุฯ ใกล้ตำแหน่ง (Weather3Hours)   ฝน 3 ชม. + อุณหภูมิ   ล่วงหน้า 3 ชม. – 8 วัน
  thaiwater24h เครื่องวัดฝนโทรมาตรใกล้ตำแหน่ง (ThaiWater) ฝน 24 ชม. (ถึง 07 น.)  D+1 – D+7
  national     สถานีอุตุฯ ทั้งประเทศ (WeatherToday)      ฝน 24 ชม., Tmax, Tmin  D+1, D+3, D+7 (Previous Runs API)
  user         ปุ่ม 🌧️/☀️ บนหน้าเว็บ                      ตก/ไม่ตก               ล่วงหน้า 3 ชม. – 8 วัน

โครงสร้างใน branch data (archive/)
  obs/tmd3h/YYYY-MM-DD.csv, obs/tmdday/…, obs/thaiwater/…   ค่าวัดที่เก็บทุกชั่วโมง
  verification/points.json                                 จุดตรวจ (สถานีที่ใกล้ตำแหน่งของผู้ใช้) — คงที่เมื่อเลือกแล้ว
  verification/pairs/YYYY-MM.csv.gz                       คู่ (พยากรณ์, ค่าวัด) ทุกโมเดลทุกช่วงเวลาล่วงหน้า
  verification/national_done.json                         วันที่คำนวณ national แล้ว
"""
import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core
import weather_now
import weather_store as store

LOOKBACK_DAYS = 9                     # พยากรณ์ย้อนหลังสูงสุดที่ใช้จับคู่ (8 วัน + เผื่อ)
GAUGE_MAX_KM = 10
NATIONAL_LEADS = [1, 3, 7]
PAIR_KEYS = ["source", "point", "obs_time", "variable", "model", "kind", "lead"]
HOUR_LEADS = [(0, 3, "≤3 ชม."), (3, 6, "3–6 ชม."), (6, 12, "6–12 ชม."), (12, 24, "12–24 ชม."),
              (24, 48, "1–2 วัน"), (48, 96, "2–4 วัน"), (96, 192, "4–8 วัน")]
SECTIONS = {
    "tmd3h_rain": {"title": "ฝนราย 3 ชม. · สถานีอุตุฯ ใกล้ตำแหน่งของคุณ", "variable": "rain", "source": "tmd3h"},
    "thaiwater24h_rain": {"title": "ฝนรายวัน · เครื่องวัดฝนใกล้ตำแหน่งของคุณ (ThaiWater)", "variable": "rain", "source": "thaiwater24h"},
    "national_rain": {"title": "ฝนรายวัน · สถานีอุตุฯ ทั่วประเทศ", "variable": "rain", "source": "national"},
    "user_rain": {"title": "ฝนตก/ไม่ตก · จากปุ่มบนหน้าเว็บ", "variable": "rain", "source": "user"},
    "tmd3h_temp": {"title": "อุณหภูมิ · สถานีอุตุฯ ใกล้ตำแหน่งของคุณ", "variable": "temp", "source": "tmd3h"},
    "national_tmax": {"title": "อุณหภูมิสูงสุดรายวัน · ทั่วประเทศ", "variable": "tmax", "source": "national"},
    "national_tmin": {"title": "อุณหภูมิต่ำสุดรายวัน · ทั่วประเทศ", "variable": "tmin", "source": "national"},
}


def _hour_lead(h):
    for lo, hi, label in HOUR_LEADS:
        if lo <= h < hi:
            return label
    return None


def _day_lead(h):
    d = int(h // 24) + 1
    return f"D+{d}" if 1 <= d <= 7 else None


# ---------------------------------------------------------------------------
# เก็บค่าวัด
# ---------------------------------------------------------------------------
def _merge_csv(path, df, keys):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        df = pd.concat([pd.read_csv(path, dtype={"id": str}), df], ignore_index=True)
    df = df.drop_duplicates(keys, keep="last").sort_values(keys)
    df.to_csv(path, index=False)


def archive_obs(archive, rows, kind):
    """เก็บค่าวัดแยกไฟล์ตามวันที่ของเวลาที่วัด (ไม่ซ้ำ id+time)"""
    df = pd.DataFrame(rows)
    if df.empty:
        return 0
    df = df[df["time"].notna()].copy()
    df["id"] = df["id"].astype(str)
    for day, g in df.groupby(df["time"].str[:10]):
        _merge_csv(Path(archive) / "obs" / kind / f"{day}.csv", g, ["id", "time"])
    return len(df)


def load_obs(archive, kind, days=LOOKBACK_DAYS + 5):
    files = sorted((Path(archive) / "obs" / kind).glob("*.csv"))[-days:]
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f, dtype={"id": str}) for f in files], ignore_index=True)
    df["time"] = pd.to_datetime(df["time"])
    return df


# ---------------------------------------------------------------------------
# จุดตรวจ: สถานีที่ใกล้ตำแหน่งของผู้ใช้ (บันทึกไว้ให้คงที่ แม้บางชั่วโมงสถานีไม่รายงาน)
# ---------------------------------------------------------------------------
def verification_points(archive, locations, tmd3h, thaiwater, cfg=None):
    cfg = cfg or core.CFG
    path = Path(archive) / "verification" / "points.json"
    current = {name for *_, name in locations}
    # ตำแหน่งที่ถูกลบออกจาก config แล้ว ไม่ต้องเก็บจุดตรวจต่อ (ประวัติคะแนนเดิมยังอยู่ใน pairs)
    pts = {p["key"]: p for p in (json.loads(path.read_text(encoding="utf-8")) if path.exists() else [])
           if p.get("for") in current}
    per_loc = int(cfg.get("gauges_per_location", 1))
    for lat, lon, name in locations:
        s = weather_now.nearest(lat, lon, tmd3h)
        if s and f"tmd:{s['id']}" not in pts:
            pts[f"tmd:{s['id']}"] = {"key": f"tmd:{s['id']}", "kind": "tmd", "id": s["id"], "name": f"สถานี{s['name']}",
                                     "lat": s["lat"], "lon": s["lon"], "for": name, "dist_km": s["dist_km"]}
        near = sorted((g for g in thaiwater if weather_now.distance_km(lat, lon, g["lat"], g["lon"]) <= GAUGE_MAX_KM),
                      key=lambda g: weather_now.distance_km(lat, lon, g["lat"], g["lon"]))[:per_loc]
        for g in near:
            key = f"tw:{g['id']}"
            if key not in pts:
                pts[key] = {"key": key, "kind": "tw", "id": g["id"], "name": f"โทรมาตร{g['name']} ({g['area']})",
                            "lat": g["lat"], "lon": g["lon"], "for": name,
                            "dist_km": round(weather_now.distance_km(lat, lon, g["lat"], g["lon"]), 1)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(pts.values()), ensure_ascii=False, indent=1), encoding="utf-8")
    return list(pts.values())


def collect_points(points, archive, cfg=None, google_for=None):
    """ดึงพยากรณ์ที่พิกัดจุดตรวจ (ensemble เฉพาะสถานีอุตุฯ เพราะใช้กับฝน 3 ชม.)

    google_for = ชื่อตำแหน่งของฉัน: จุดตรวจของตำแหน่งนี้ดึง Google Weather 48 ชม. ทุก 2 ชม.
    """
    log = []
    for p in points:
        gkey = f"pt:{p['key']}"
        google = 48 if p.get("for") == google_for and core.google_allowed(archive, gkey, 110, cfg) else None
        try:
            data, now, tz = core.fetch_deterministic(p["lat"], p["lon"], cfg, google)
            if google:
                core.google_record(archive, gkey, core.GOOGLE_CALLS["last"])
            ens = core.fetch_all_ensembles(p["lat"], p["lon"], cfg) if p["kind"] == "tmd" else {}
            result = {"name": p["name"], "lat": p["lat"], "lon": p["lon"], "tz": tz, "now": now,
                      "data": data, "ensembles": ens}
            path, n = store.export_snapshot(result, archive, cfg)
            log.append({"location": p["name"], "rows": n, "status": "ok"})
        except Exception as e:
            log.append({"location": p["name"], "rows": 0, "status": f"error: {e}"})
    return pd.DataFrame(log)


# ---------------------------------------------------------------------------
# โหลดพยากรณ์ที่เก็บไว้ (ไฟล์ .csv.gz ใน runs/)
# ---------------------------------------------------------------------------
def load_point_runs(archive, lat, lon, days=LOOKBACK_DAYS):
    """คืน list ของ (fetched_at, DataFrame กว้าง index=valid_time) ของจุดนี้"""
    tag = f"_{lat:.3f}_{lon:.3f}.csv.gz"
    since = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y%m%d")
    out = []
    for f in sorted(Path(archive).glob(f"runs/**/*{tag}")):
        if f.name[:8] < since:
            continue
        df = pd.read_csv(f, parse_dates=["valid_time", "fetched_at"])
        fetched = df["fetched_at"].iloc[0]
        wide = df.drop(columns=["fetched_at", "location", "lat", "lon", "tz"]).set_index("valid_time")
        out.append((fetched, wide))
    return out


def _window(wide, col, hours):
    """ผลรวมของ col ในชั่วโมงที่ระบุ (None ถ้าไม่ครบ)"""
    if col not in wide:
        return None
    v = wide[col].reindex(hours)
    return None if v.isna().any() else float(v.sum())


# ---------------------------------------------------------------------------
# สร้างคู่ (พยากรณ์, ค่าวัด)
# ---------------------------------------------------------------------------
def pairs_tmd3h(archive, points, cfg=None):
    """ฝน 3 ชม. (T-3h, T] และอุณหภูมิ ณ T ที่สถานีอุตุฯ ใกล้ตำแหน่ง"""
    cfg = cfg or core.CFG
    obs = load_obs(archive, "tmd3h")
    if obs.empty:
        return []
    ens_ok = int(cfg["verify_window_hours"]) == 3      # ens_prob ที่เก็บไว้เป็นหน้าต่าง 3 ชม. ที่ชั่วโมงกลาง
    thr = cfg["verify_threshold_mm"]
    recs = []
    for p in (p for p in points if p["kind"] == "tmd"):
        o = obs[obs["id"] == str(p["id"])]
        runs = load_point_runs(archive, p["lat"], p["lon"])
        if o.empty or not runs:
            continue
        for _, ob in o.iterrows():
            T = ob["time"]
            hours = [T - pd.Timedelta(hours=k) for k in (2, 1, 0)]
            start = T - pd.Timedelta(hours=3)
            for fetched, wide in runs:
                for col in wide.columns:
                    source, model = col.split("|", 1)
                    if source == "det_mm" and pd.notna(ob.get("rain_3h")) and fetched <= start:
                        fc = _window(wide, col, hours)
                        lead = _hour_lead((start - fetched).total_seconds() / 3600)
                        if fc is not None and lead:
                            recs.append(dict(source="tmd3h", point=p["name"], obs_time=T, variable="rain", model=model,
                                             kind="det", lead=lead, lead_h=(start - fetched).total_seconds() / 3600,
                                             fc=fc, ob=float(ob["rain_3h"]), ev_fc=fc >= thr, ev_ob=ob["rain_3h"] >= thr))
                    elif source == "ens_prob" and ens_ok and pd.notna(ob.get("rain_3h")) and fetched <= start:
                        c = T - pd.Timedelta(hours=1)
                        if c in wide.index and pd.notna(wide.at[c, col]):
                            lead = _hour_lead((start - fetched).total_seconds() / 3600)
                            if lead:
                                prob = float(wide.at[c, col])
                                recs.append(dict(source="tmd3h", point=p["name"], obs_time=T, variable="rain", model=model,
                                                 kind="ens", lead=lead, lead_h=(start - fetched).total_seconds() / 3600,
                                                 fc=prob, ob=float(ob["rain_3h"]), ev_fc=prob >= 50, ev_ob=ob["rain_3h"] >= thr))
                    elif source == "det_t" and pd.notna(ob.get("temp")) and fetched < T and T in wide.index:
                        v = wide.at[T, col]
                        lead = _hour_lead((T - fetched).total_seconds() / 3600)
                        if pd.notna(v) and lead:
                            recs.append(dict(source="tmd3h", point=p["name"], obs_time=T, variable="temp", model=model,
                                             kind="det", lead=lead, lead_h=(T - fetched).total_seconds() / 3600,
                                             fc=float(v), ob=float(ob["temp"])))
    return recs


def pairs_thaiwater24h(archive, points, cfg=None):
    """ฝน 24 ชม. สิ้นสุดราว 07 น. ที่เครื่องวัดโทรมาตรใกล้ตำแหน่ง"""
    cfg = cfg or core.CFG
    obs = load_obs(archive, "thaiwater")
    if obs.empty:
        return []
    thr = cfg["rain_threshold_mm"]
    recs = []
    for p in (p for p in points if p["kind"] == "tw"):
        o = obs[obs["id"] == str(p["id"])].copy()
        runs = load_point_runs(archive, p["lat"], p["lon"])
        if o.empty or not runs:
            continue
        # วันละหนึ่งค่า: ค่าที่เวลาใกล้ 07:00 ที่สุด (±2 ชม.)
        o["off"] = (o["time"] - o["time"].dt.normalize() - pd.Timedelta(hours=7)).abs()
        o = o[o["off"] <= pd.Timedelta(hours=2)]
        if o.empty:
            continue
        o = o.loc[o.groupby(o["time"].dt.date)["off"].idxmin()]
        for _, ob in o.iterrows():
            T = ob["time"]
            hours = [T - pd.Timedelta(hours=k) for k in range(23, -1, -1)]
            start = T - pd.Timedelta(hours=24)
            for fetched, wide in runs:
                if fetched > start:
                    continue
                lead = _day_lead((start - fetched).total_seconds() / 3600)
                for col in (c for c in wide.columns if c.startswith("det_mm|")):
                    fc = _window(wide, col, hours)
                    if fc is not None and lead:
                        recs.append(dict(source="thaiwater24h", point=p["name"], obs_time=T, variable="rain",
                                         model=col.split("|", 1)[1], kind="det", lead=lead,
                                         lead_h=(start - fetched).total_seconds() / 3600, fc=fc, ob=float(ob["rain_24h"]),
                                         ev_fc=fc >= thr, ev_ob=ob["rain_24h"] >= thr))
    return recs


def pairs_national(archive, cfg=None, max_days=14):
    """ทั่วประเทศ: WeatherToday (07 น.) เทียบ Previous Runs API ล่วงหน้า 1/3/7 วัน — ทำวันละครั้งต่อวันที่"""
    cfg = cfg or core.CFG
    done_path = Path(archive) / "verification" / "national_done.json"
    done = set(json.loads(done_path.read_text())) if done_path.exists() else set()
    obs = load_obs(archive, "tmdday", days=max_days)
    if obs.empty:
        return []
    thr = cfg["rain_threshold_mm"]
    recs = []
    for day, o in obs.groupby(obs["time"].dt.date):
        key = str(day)
        if key in done:
            continue
        T = pd.Timestamp(day) + pd.Timedelta(hours=7)
        hours = pd.date_range(T - pd.Timedelta(hours=23), T, freq="h")
        o = o.dropna(subset=["lat", "lon"]).reset_index(drop=True)
        models = [k for k in core.active_models(cfg) if k not in ("kma_gdps", "bom_access_global")]
        hv = [f"{v}_previous_day{d}" for v in ("precipitation", "temperature_2m") for d in NATIONAL_LEADS]
        ok = True
        for i in range(0, len(o), 40):
            part = o.iloc[i:i + 40]
            try:
                r = core.SESSION.get(core.PREVIOUS_RUNS_URL, params={
                    "latitude": ",".join(f"{x:.4f}" for x in part["lat"]),
                    "longitude": ",".join(f"{x:.4f}" for x in part["lon"]),
                    "hourly": ",".join(hv), "models": ",".join(models),
                    "start_date": str((T - pd.Timedelta(days=1)).date()), "end_date": str(T.date()),
                    "timezone": "Asia/Bangkok"}, timeout=120)
                r.raise_for_status()
            except Exception as e:
                print(f"⚠️ national {key}: {e}")
                ok = False
                break
            js = r.json()
            js = js if isinstance(js, list) else [js]
            for (_, ob), j in zip(part.iterrows(), js):
                h = j["hourly"]
                idx = pd.to_datetime(h["time"])
                for k in models:
                    name = core.DETERMINISTIC_MODELS[k]["name"]
                    for d in NATIONAL_LEADS:
                        pr = pd.Series(h.get(f"precipitation_previous_day{d}_{k}"), index=idx, dtype=float).reindex(hours)
                        tt = pd.Series(h.get(f"temperature_2m_previous_day{d}_{k}"), index=idx, dtype=float).reindex(hours)
                        base = dict(source="national", point=ob["name"], obs_time=T, model=name, kind="det",
                                    lead=f"D+{d}", lead_h=24.0 * d)
                        if pr.notna().all() and pd.notna(ob["rain_24h"]):
                            fc = float(pr.sum())
                            recs.append({**base, "variable": "rain", "fc": fc, "ob": float(ob["rain_24h"]),
                                         "ev_fc": fc >= thr, "ev_ob": ob["rain_24h"] >= thr})
                        if tt.notna().all():
                            if pd.notna(ob["tmax"]):
                                recs.append({**base, "variable": "tmax", "fc": float(tt.max()), "ob": float(ob["tmax"])})
                            if pd.notna(ob["tmin"]):
                                recs.append({**base, "variable": "tmin", "fc": float(tt.min()), "ob": float(ob["tmin"])})
        if ok:
            done.add(key)
    done_path.parent.mkdir(parents=True, exist_ok=True)
    done_path.write_text(json.dumps(sorted(done)))
    return recs


def pairs_user(archive, cfg=None):
    """การกดปุ่มบนหน้าเว็บ: ใช้ตรรกะเดิมของ weather_store บนฐานข้อมูลชั่วคราว (เฉพาะรอบช่วง 9 วันล่าสุด)"""
    cfg = dict(cfg or core.CFG)
    obs_files = list((Path(archive) / "observations").glob("*.json"))
    if not obs_files:
        return []
    tmp = Path(archive).parent / ".verify_tmp.db"
    tmp.unlink(missing_ok=True)
    cfg["db_path"] = str(tmp)
    since = (pd.Timestamp.now() - pd.Timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")
    try:
        with sqlite3.connect(tmp) as con:
            con.executescript(store.SCHEMA)
        store.import_archive(archive, cfg, since=since)
        rec = store.verification_records(cfg, use_previous_runs=True)
    finally:
        tmp.unlink(missing_ok=True)
    thr = cfg["verify_threshold_mm"]
    out = []
    for r in rec.itertuples():
        out.append(dict(source="user", point=r.location, obs_time=r.obs_time, variable="rain", model=r.model,
                        kind=r.kind, lead=str(r.lead_bin), lead_h=r.lead_h, fc=r.value,
                        ob=float(r.amount_mm) if pd.notna(r.amount_mm) else np.nan,
                        ev_fc=bool(r.predicted), ev_ob=bool(r.observed)))
    return out


def save_pairs(archive, recs):
    if not recs:
        return 0
    df = pd.DataFrame(recs)
    df["obs_time"] = pd.to_datetime(df["obs_time"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    for month, g in df.groupby(df["obs_time"].str[:7]):
        path = Path(archive) / "verification" / "pairs" / f"{month}.csv.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            g = pd.concat([pd.read_csv(path), g], ignore_index=True)
        g = g.drop_duplicates(PAIR_KEYS, keep="last").sort_values(PAIR_KEYS)
        g.to_csv(path, index=False, compression="gzip")
    return len(df)


def load_pairs(archive, days=None):
    files = sorted((Path(archive) / "verification" / "pairs").glob("*.csv.gz"))
    if not files:
        return pd.DataFrame()
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df["obs_time"] = pd.to_datetime(df["obs_time"])
    if days:
        df = df[df["obs_time"] >= pd.Timestamp.now() - pd.Timedelta(days=days)]
    return df


# ---------------------------------------------------------------------------
# คะแนน
# ---------------------------------------------------------------------------
def _rain_scores(g):
    fc, ob = g["ev_fc"].astype(bool), g["ev_ob"].astype(bool)
    hits, misses = int((fc & ob).sum()), int((~fc & ob).sum())
    fa, cn = int((fc & ~ob).sum()), int((~fc & ~ob).sum())
    n = hits + misses + fa + cn
    div = lambda a, b: round(100 * a / b, 1) if b else None
    out = {"n": n, "acc": div(hits + cn, n), "pod": div(hits, hits + misses), "far": div(fa, hits + fa),
           "csi": div(hits, hits + misses + fa), "bias": round((hits + fa) / (hits + misses), 2) if hits + misses else None,
           "n_rain": hits + misses}
    if (g["kind"] == "ens").all():
        out["brier"] = round(float(((g["fc"] / 100 - ob.astype(float)) ** 2).mean()), 3)
    else:
        amt = g.dropna(subset=["ob"])
        out["mae"] = round(float((amt["fc"] - amt["ob"]).abs().mean()), 2) if len(amt) else None
    return out


def _temp_scores(g):
    err = g["fc"] - g["ob"]
    return {"n": len(g), "mae": round(float(err.abs().mean()), 2), "bias": round(float(err.mean()), 2),
            "rmse": round(float(np.sqrt((err ** 2).mean())), 2)}


def leaderboard(archive, days=30, cfg=None):
    """ตารางอันดับทุกส่วน (สำหรับหน้าเว็บและ notebook)"""
    pairs = load_pairs(archive, days)
    meta = {m["name"]: m.get("grid_km") for m in list(core.DETERMINISTIC_MODELS.values()) + [core.TMD_MODEL, core.GOOGLE_MODEL]}
    points_path = Path(archive) / "verification" / "points.json"
    points = json.loads(points_path.read_text(encoding="utf-8")) if points_path.exists() else []
    out = {"updated": pd.Timestamp.now(tz="Asia/Bangkok").isoformat(timespec="minutes"), "days": days,
           "since": None if pairs.empty else str(pairs["obs_time"].min())[:10],
           "points": points, "sections": []}
    current_points = {p["name"] for p in points}
    for sid, spec in SECTIONS.items():
        g = pairs[(pairs["source"] == spec["source"]) & (pairs["variable"] == spec["variable"])] if not pairs.empty else pairs
        if spec["source"] in ("tmd3h", "thaiwater24h") and not g.empty:
            g = g[g["point"].isin(current_points)]          # เฉพาะจุดตรวจของตำแหน่งที่ยังอยู่ใน config
        # จัดอันดับแยกตามช่วงเวลาล่วงหน้า: แต่ละโมเดลพยากรณ์ไปได้ไกลไม่เท่ากัน ถ้ารวมทุกช่วง
        # โมเดลที่มีแค่ช่วงสั้น (ซึ่งง่ายกว่า) จะได้คะแนนสูงเกินจริง
        sec = {"id": sid, **spec, "n_obs": 0, "n_points": 0, "leads": [], "tables": {}, "ens_tables": {}}
        if not g.empty:
            score = _rain_scores if spec["variable"] == "rain" else _temp_scores
            sec["n_obs"] = int(g[["point", "obs_time"]].drop_duplicates().shape[0])
            sec["n_points"] = int(g["point"].nunique())
            order = [lab for *_, lab in HOUR_LEADS] + [f"D+{d}" for d in range(1, 8)]
            sec["leads"] = [l for l in order if l in set(g["lead"])]
            for lead in sec["leads"]:
                for kind, key in (("det", "tables"), ("ens", "ens_tables")):
                    k = g[(g["kind"] == kind) & (g["lead"] == lead)]
                    rows = [{"model": m, "grid_km": meta.get(m), **score(mg)} for m, mg in k.groupby("model")]
                    if spec["variable"] == "rain":
                        rows.sort(key=lambda r: r.get("brier", 9) if kind == "ens" else (-(r["csi"] or 0), -(r["acc"] or 0)))
                    else:
                        rows.sort(key=lambda r: r["mae"])
                    if rows:
                        sec[key][lead] = rows
        out["sections"].append(sec)
    return out


def _clean(x):
    """NaN/numpy → ค่าที่ JSON รับได้"""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if np.isnan(x) or np.isinf(x) else float(x)
    return x


# ---------------------------------------------------------------------------
# ทั้งหมดในรอบเดียว (เรียกจาก collector.py run บน GitHub Actions)
# ---------------------------------------------------------------------------
def update(archive, locations, cfg=None, site_dir=None):
    cfg = cfg or core.CFG
    archive = Path(archive)
    obs = {}
    for kind, fn in (("tmd3h", weather_now.fetch_tmd_stations), ("tmdday", weather_now.fetch_tmd_today),
                     ("thaiwater", lambda c=None: weather_now.fetch_thaiwater_rain24())):
        try:
            obs[kind] = fn(cfg)
        except Exception as e:
            print(f"⚠️ ค่าวัด {kind}: {e}")
            obs[kind] = []
    points = verification_points(archive, locations, obs["tmd3h"], obs["thaiwater"], cfg)
    # เก็บค่าวัด: สถานีอุตุฯ ทั้งหมด, โทรมาตรเฉพาะจุดตรวจ (ทั้งประเทศใหญ่เกินไป)
    archive_obs(archive, obs["tmd3h"], "tmd3h")
    archive_obs(archive, obs["tmdday"], "tmdday")
    tw_ids = {p["id"] for p in points if p["kind"] == "tw"}
    archive_obs(archive, [g for g in obs["thaiwater"] if g["id"] in tw_ids], "thaiwater")
    log = collect_points(points, archive, cfg, google_for=locations[0][2] if locations else None)
    print(log.to_string(index=False))
    n = 0
    for fn in (pairs_tmd3h, pairs_thaiwater24h, pairs_national, pairs_user):
        try:
            n += save_pairs(archive, fn(archive, cfg=cfg) if fn in (pairs_national, pairs_user) else fn(archive, points, cfg))
        except Exception as e:
            print(f"⚠️ {fn.__name__}: {e}")
    print(f"คู่ (พยากรณ์, ค่าวัด) ใหม่/อัปเดต: {n}")
    if site_dir:
        lb = leaderboard(archive, days=int(cfg.get("leaderboard_days", 30)), cfg=cfg)
        (Path(site_dir) / "data").mkdir(parents=True, exist_ok=True)
        (Path(site_dir) / "data" / "verification.json").write_text(
            json.dumps(_clean(lb), ensure_ascii=False, allow_nan=False), encoding="utf-8")
    return points
