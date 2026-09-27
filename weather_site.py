"""สร้างข้อมูลสำหรับหน้าเว็บ (GitHub Pages)

site/
  index.html, app.js, style.css   คัดลอกจาก web/
  data/index.json                 รายชื่อตำแหน่ง + เวลาอัปเดต
  data/loc-<n>.json               ข้อมูลของแต่ละตำแหน่ง (ผลจาก weather_core.analyze_location)
"""
import json
import math
import os
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core
import weather_now

WEB_DIR = core.ROOT / "web"
HOURLY_DAYS = 7
HOURLY_VARS = ["precipitation", "temperature_2m", "apparent_temperature", "relative_humidity_2m", "wind_gusts_10m"]
MODEL_META = {m["name"]: m for m in list(core.DETERMINISTIC_MODELS.values()) + [core.TMD_MODEL, core.GOOGLE_MODEL]}


def _num(v, nd=1):
    """ตัวเลข → float ปัดเศษ หรือ None (JSON ไม่รองรับ NaN)"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else round(f, nd)


def _rows(table, nd=1):
    return {str(idx): [_num(v, nd) for v in row] for idx, row in zip(table.index, table.values)}


def location_payload(result, cfg=None, obs=None):
    cfg = cfg or core.CFG
    now = result["now"]
    tables = result["tables"]
    rain = tables["rain"]
    windows = list(rain.columns)

    models = [{"name": n, **{k: MODEL_META.get(n, {}).get(k) for k in ("grid_km", "agency", "type")}}
              for n in result["data"]["precipitation"].columns]

    cons = result["consensus"]
    ens = result["ensemble"].reindex(columns=windows)

    end = now + pd.Timedelta(days=HOURLY_DAYS)
    hourly = {}
    times = None
    for var in HOURLY_VARS:
        df = result["data"].get(var)
        if df is None or df.empty:
            continue
        df = df[(df.index > now) & (df.index <= end)]
        times = df.index if times is None else times
        hourly[var] = {m: [_num(v) for v in df[m].values] for m in df.columns}
    pm25 = None
    if result.get("aq") is not None and times is not None:
        pm25 = [_num(v) for v in result["aq"]["pm2_5"].reindex(times).values]

    return {
        "name": result["name"], "lat": result["lat"], "lon": result["lon"], "tz": result["tz"],
        "now": now.isoformat(),
        "threshold_mm": cfg["rain_threshold_mm"],
        "windows": windows,
        "models": models,
        "tables": {k: {"label": core.VARIABLES[k]["label"], "unit": core.VARIABLES[k]["unit"],
                       "columns": list(t.columns), "rows": _rows(t)}
                   for k, t in tables.items()},
        "consensus": {
            "median": [_num(v) for v in cons.loc["median"]],
            "min": [_num(v) for v in cons.loc["min"]],
            "max": [_num(v) for v in cons.loc["max"]],
            "n_models": [_num(v, 0) for v in cons.loc["จำนวนโมเดล"]],
            "agree_pct": [_num(v, 0) for v in cons.loc["% เห็นตรงกันว่าฝนตก"]],
        },
        "ensemble": _rows(ens, 0),
        "brief": [{k: (_num(v) if isinstance(v, (float, np.floating)) else v) for k, v in b.items()}
                  for b in core.daily_brief_records(tables, result.get("aq"), now, cfg)],
        "hourly": {"time": [t.isoformat() for t in times] if times is not None else [],
                   "series": hourly, "pm25": pm25},
        "now": weather_now.for_location(result["lat"], result["lon"], obs) if obs else None,
    }


def write_site(results, out_dir, cfg=None):
    """เขียนหน้าเว็บทั้งหมดลง out_dir (คัดลอก web/ + สร้าง data/*.json)"""
    out = Path(out_dir)
    if WEB_DIR.exists():
        shutil.copytree(WEB_DIR, out, dirs_exist_ok=True)
    (out / "data").mkdir(parents=True, exist_ok=True)
    obs = weather_now.fetch_all(cfg)            # ค่าวัดจริงจากสถานี ดึงครั้งเดียวใช้ทุกตำแหน่ง
    index = []
    for i, r in enumerate(results):
        fname = f"loc-{i}.json"
        (out / "data" / fname).write_text(
            json.dumps(location_payload(r, cfg, obs), ensure_ascii=False, allow_nan=False, separators=(",", ":")),
            encoding="utf-8")
        d1 = r["tables"]["rain"].filter(like="D+1").iloc[:, 0] if not r["tables"]["rain"].filter(like="D+1").empty else None
        index.append({"file": fname, "name": r["name"], "lat": r["lat"], "lon": r["lon"],
                      "rain_d1_median": _num(d1.median()) if d1 is not None else None})
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    meta = {
        "generated": pd.Timestamp.now(tz="Asia/Bangkok").isoformat(timespec="minutes"),
        "repo": repo,
        "locations": index,
        "observations": observations_summary(),
    }
    (out / "data" / "index.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    # สำหรับ "📍 พยากรณ์ตรงที่ฉันอยู่": เบราว์เซอร์ดึง Open-Meteo เองแล้วคำนวณแบบเดียวกับฝั่ง Python
    (out / "data" / "meta.json").write_text(json.dumps(client_meta(cfg), ensure_ascii=False), encoding="utf-8")
    (out / "data" / "now_obs.json").write_text(json.dumps(
        {"tmd": obs.get("tmd", []), "air": obs.get("air", [])}, ensure_ascii=False), encoding="utf-8")
    (out / ".nojekyll").write_text("")
    return out


def observations_summary(archive=core.ROOT / "archive", last=5):
    """สรุปการบันทึกฝนจากปุ่มบนหน้าเว็บ (อ่านจาก branch data ที่ checkout ไว้ที่ archive/)"""
    files = sorted((Path(archive) / "observations").glob("*.json"))
    recs = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    recs.sort(key=lambda o: o["obs_time"])
    return {
        "count": len(recs),
        "rain": sum(bool(o["rained"]) for o in recs),
        "recent": [{k: o.get(k) for k in ("obs_time", "location", "rained", "amount_mm")} for o in recs[-last:][::-1]],
    }


def client_meta(cfg=None):
    """ค่าตั้งที่หน้าเว็บต้องใช้คำนวณพยากรณ์ของพิกัดผู้ชมเอง (เฉพาะโมเดลที่ไม่ต้องใช้ key)"""
    cfg = cfg or core.CFG
    return {
        "models": [{"key": k, "name": m["name"], "grid_km": m["grid_km"], "grid": m["grid"], "agency": m["agency"],
                    "type": m["type"]} for k, m in core.active_models(cfg).items()],
        "keyed_models": [{"name": m["name"], "grid_km": m["grid_km"], "grid": m["grid"], "agency": m["agency"],
                          "type": m["type"]} for m in (core.TMD_MODEL, core.GOOGLE_MODEL)],
        "ensembles": [{"key": k, "name": m["name"], "members": m["members"], "grid_km": m["grid_km"]}
                      for k, m in core.active_ensembles(cfg).items()],
        "variables": {k: {kk: v[kk] for kk in ("api", "label", "unit", "hour", "day")} for k, v in core.VARIABLES.items()},
        "api_variables": core.API_VARIABLES,
        "hour_windows": cfg["hour_windows"], "day_leads": cfg["day_leads"],
        "forecast_days": core.forecast_days(cfg), "rain_threshold_mm": cfg["rain_threshold_mm"],
        "hourly_vars": HOURLY_VARS, "hourly_days": HOURLY_DAYS, "max_station_km": weather_now.MAX_STATION_KM,
    }
