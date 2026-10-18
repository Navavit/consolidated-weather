"""พยากรณ์ภัยความร้อนรายเขต กรุงเทพมหานคร สำหรับโครงการ HEAT-SAFE Bangkok (วช. 2570)

วิธีการ
  1. พยากรณ์รายชั่วโมงล่วงหน้า 7 วัน ณ จุดตัวแทนของ 50 เขต (resources/bkk_districts.csv)
     จาก Open-Meteo Forecast API เฉพาะโมเดลที่มีค่าปรับแก้ (resources/heatsafe_calibration.json)
  2. ดัชนีความร้อน (Heat Index) รายชั่วโมงของแต่ละโมเดลด้วยสมการของ NWS (Rothfusz 1990; Steadman 1979)
  3. ปรับแก้ความเอนเอียง (bias) ของ HI สูงสุดรายวันต่อโมเดลต่อระยะล่วงหน้า ด้วยผลทดสอบย้อนหลัง
     ที่สถานีในกรุงเทพมหานคร ฤดูร้อน 2024–2026 (HEAT-SAFE NB10)
  4. ระดับเตือนภัยตามเกณฑ์กรุงเทพมหานคร (สอดคล้องกรมอนามัย): เขียว 27.0–32.9 · เหลือง 33.0–41.9 · ส้ม 42.0–51.9 · แดง ≥ 52.0 °C
     ความน่าจะเป็นเกินเกณฑ์ = สัดส่วนของโมเดลที่ HI สูงสุด (ปรับแก้แล้ว) ถึงเกณฑ์ (multi-model ensemble)
  5. ฝนรายวัน: มัธยฐานของโมเดล และสัดส่วนของโมเดลที่ฝน ≥ rain_threshold_mm

ผลลัพธ์
  archive/heatsafe/latest.json                ผลรอบล่าสุด (คัดลอกขึ้นหน้าเว็บทุกรอบ → site/data/heatsafe.json)
  archive/heatsafe/daily/<วันที่>.csv.gz      ค่ารายวันต่อโมเดลต่อเขต (เก็บวันละครั้ง สำหรับทดสอบย้อนหลัง)

ดึงพยากรณ์ใหม่ทุก RUN_EVERY_MIN นาที (โมเดลโลกออกรอบใหม่ทุก 6–12 ชม.) เพื่อประหยัดโควตา Open-Meteo
"""
import gzip
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core

RES = core.ROOT / "resources"
RUN_EVERY_MIN = 170
BATCH = 10                                   # จำนวนเขตต่อคำขอ
DAYS = 7
LEVELS = [(27.0, "เขียว", "เฝ้าระวัง"), (33.0, "เหลือง", "เตือนภัย"), (42.0, "ส้ม", "อันตราย"), (52.0, "แดง", "อันตรายมาก")]


def heat_index_c(t_c, rh):
    """Heat Index (°C) ตามสมการ NWS — ใช้สมการอย่างง่ายเมื่อ HI < 80 °F และปรับแก้ช่วงความชื้นต่ำ/สูง"""
    t = np.asarray(t_c, dtype=float) * 9 / 5 + 32
    rh = np.asarray(rh, dtype=float)
    hi = 0.5 * (t + 61 + (t - 68) * 1.2 + rh * 0.094)
    full = (-42.379 + 2.04901523 * t + 10.14333127 * rh - 0.22475541 * t * rh - 6.83783e-3 * t ** 2
            - 5.481717e-2 * rh ** 2 + 1.22874e-3 * t ** 2 * rh + 8.5282e-4 * t * rh ** 2 - 1.99e-6 * t ** 2 * rh ** 2)
    adj1 = ((13 - rh) / 4) * np.sqrt(np.clip((17 - np.abs(t - 95)) / 17, 0, None))
    full = np.where((rh < 13) & (t >= 80) & (t <= 112), full - adj1, full)
    full = np.where((rh > 85) & (t >= 80) & (t <= 87), full + ((rh - 85) / 10) * ((87 - t) / 5), full)
    return (np.where((hi + t) / 2 >= 80, full, hi) - 32) * 5 / 9


def level(v):
    """0 = ต่ำกว่าเฝ้าระวัง, 1 เขียว, 2 เหลือง, 3 ส้ม, 4 แดง"""
    return None if v is None or np.isnan(v) else int(np.digitize(v, [lv[0] for lv in LEVELS]))


def _due(archive):
    f = Path(archive) / "heatsafe" / "last_run.txt"
    return not f.exists() or time.time() - float(f.read_text() or 0) > RUN_EVERY_MIN * 60


def fetch(districts, models):
    """พยากรณ์รายชั่วโมง (เวลาท้องถิ่น) ของทุกเขต → DataFrame ยาว: dcode, time, model, t, rh, rain"""
    rows = []
    for i in range(0, len(districts), BATCH):
        part = districts.iloc[i:i + BATCH]
        r = core.SESSION.get(core.FORECAST_URL, params={
            "latitude": ",".join(f"{x:.4f}" for x in part.lat), "longitude": ",".join(f"{x:.4f}" for x in part.lon),
            "hourly": "temperature_2m,relative_humidity_2m,precipitation", "models": ",".join(models),
            "forecast_days": DAYS, "timezone": "Asia/Bangkok"}, timeout=120)
        r.raise_for_status()
        js = r.json()
        js = js if isinstance(js, list) else [js]
        for dcode, j in zip(part.dcode, js):
            h = j["hourly"]
            t = pd.to_datetime(h["time"])
            for key, name in models.items():
                tt = h.get(f"temperature_2m_{key}")
                if tt is None or all(v is None for v in tt):
                    continue
                rows.append(pd.DataFrame({"dcode": dcode, "time": t, "model": name, "t": pd.to_numeric(pd.Series(tt), errors="coerce"),
                                          "rh": pd.to_numeric(pd.Series(h.get(f"relative_humidity_2m_{key}")), errors="coerce"),
                                          "rain": pd.to_numeric(pd.Series(h.get(f"precipitation_{key}")), errors="coerce")}))
    return pd.concat(rows, ignore_index=True)


def build(df, districts, cal, cfg):
    today = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None).normalize()
    df["hi"] = heat_index_c(df.t, df.rh)
    df["date"] = df.time.dt.normalize()
    d = df.groupby(["dcode", "model", "date"]).agg(n=("t", "size"), tmax=("t", "max"), tmin=("t", "min"), rh_min=("rh", "min"),
                                                   hi_max_raw=("hi", "max"), rain=("rain", "sum")).reset_index()
    d = d[d.n >= 20]                                                  # วันที่มีค่าเกือบครบ 24 ชม.
    d["lead_day"] = (d.date - today).dt.days
    d = d[d.lead_day.between(0, DAYS - 1)]
    bias = cal["bias_himax_by_model_leadday"]
    d["bias"] = [bias.get(m, {}).get(str(max(1, min(7, ld))), np.nan) for m, ld in zip(d.model, d.lead_day)]
    d["hi_max"] = d.hi_max_raw - d.bias
    thr = cfg.get("rain_threshold_mm", 1.0)
    out = []
    for dc, g in d.groupby("dcode"):
        info = districts.set_index("dcode").loc[dc]
        days = []
        for date, x in g.groupby("date"):
            hi = x.hi_max.dropna()
            med = float(hi.median()) if len(hi) else np.nan
            days.append({"date": str(date.date()), "lead_day": int(x.lead_day.iloc[0]), "n_models": int(len(hi)),
                         "hi": _r(med), "hi_raw": _r(x.hi_max_raw.median()), "hi_lo": _r(hi.min()), "hi_hi": _r(hi.max()),
                         "lvl": level(med), "p_yellow": _p(hi >= 33), "p_orange": _p(hi >= 42), "p_red": _p(hi >= 52),
                         "tmax": _r(x.tmax.median()), "tmin": _r(x.tmin.median()), "rh_min": _r(x.rh_min.median(), 0),
                         "rain": _r(x.rain.median()), "p_rain": _p(x.rain >= thr)})
        hh = df[(df.dcode == dc) & (df.time >= today)].groupby("time").hi.median()
        out.append({"dcode": str(dc), "dname": info.dname, "dname_en": info.dname_en, "lat": info.lat, "lon": info.lon,
                    "days": days, "hourly_hi_raw": {"start": str(hh.index.min()) if len(hh) else None, "values": [_r(v) for v in hh.values]}})
    return d, out


def _r(v, nd=1):
    return None if v is None or pd.isna(v) else round(float(v), nd)


def _p(mask):
    mask = pd.Series(mask)
    return None if not len(mask) else int(round(100 * mask.mean()))


def update(archive, site_dir=None, cfg=None):
    """เรียกจาก collector.py run ทุกรอบ: ดึงใหม่ถ้าถึงรอบ แล้วคัดลอกผลล่าสุดขึ้นหน้าเว็บ"""
    cfg = cfg or core.CFG
    archive = Path(archive)
    hdir = archive / "heatsafe"
    hdir.mkdir(parents=True, exist_ok=True)
    cal = json.loads((RES / "heatsafe_calibration.json").read_text(encoding="utf-8"))
    if _due(archive):
        districts = pd.read_csv(RES / "bkk_districts.csv", dtype={"dcode": str})
        models = {k: m["name"] for k, m in core.active_models(cfg).items() if m["name"] in cal["bias_himax_by_model_leadday"]}
        df = fetch(districts, models)
        daily, out = build(df, districts, cal, cfg)
        now = pd.Timestamp.now(tz="Asia/Bangkok")
        payload = {
            "generated": now.isoformat(timespec="minutes"),
            "models": sorted(df.model.unique()),
            "models_excluded": sorted(m["name"] for m in core.active_models(cfg).values() if m["name"] not in models.values()),
            "levels": [{"min": lv[0], "color": lv[1], "label": lv[2]} for lv in LEVELS],
            "rain_threshold_mm": cfg.get("rain_threshold_mm", 1.0),
            "calibration": {k: cal.get(k) for k in ("generated", "source", "method", "period", "stations", "skill_multimodel",
                                                    "skill_hi_hotdays", "alert_orange", "brier_skill")},
            "districts": out,
        }
        (hdir / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
                                          encoding="utf-8")
        # เก็บวันละครั้ง (รอบแรกหลัง 07:00 น.) ไว้ทดสอบย้อนหลังรายวันที่ระยะ 0–6 วัน
        dfile = hdir / "daily" / f"{now:%Y-%m-%d}.csv.gz"
        if now.hour >= 7 and not dfile.exists():
            dfile.parent.mkdir(exist_ok=True)
            with gzip.open(dfile, "wt", encoding="utf-8") as fh:
                daily.assign(issued=now.strftime("%Y-%m-%d %H:%M")).drop(columns=["n"]).round(2).to_csv(fh, index=False)
        (hdir / "last_run.txt").write_text(str(time.time()))
        print(f"HEAT-SAFE: {len(out)} เขต · {len(models)} โมเดล · {now:%Y-%m-%d %H:%M}")
    if site_dir and (hdir / "latest.json").exists():
        (Path(site_dir) / "data").mkdir(parents=True, exist_ok=True)
        (Path(site_dir) / "data" / "heatsafe.json").write_bytes((hdir / "latest.json").read_bytes())


if __name__ == "__main__":
    import sys
    update(sys.argv[1] if len(sys.argv) > 1 else core.ROOT / "archive", sys.argv[2] if len(sys.argv) > 2 else None)
