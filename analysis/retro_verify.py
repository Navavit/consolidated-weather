"""วัดความแม่นย้อนหลัง: พยากรณ์ฝน/อุณหภูมิรายวัน เทียบสถานีอุตุฯ ทั่วประเทศไทย (ฐานสำหรับ paper)

ค่าวัด       NOAA GSOD (วัน UTC = 07:00–07:00 น. เวลาไทย) ฝนใช้เฉพาะที่ครบ 24 ชม. (flag G)
พยากรณ์     Open-Meteo Previous Runs API ล่วงหน้า 1, 3, 7 วัน (timezone UTC ให้ตรงกับวันของ GSOD)
ช่วงประเมิน  2024-01-01 – 2025-08-24 (วันสุดท้ายที่ GSOD มีข้อมูล ณ ก.ย. 2026)
ภูมิอากาศ   GSOD 2000–2023 ใช้เป็นค่าอ้างอิงสำหรับ skill score
หมายเหตุ    ก่อนปี 2024 ไม่มีคลังพยากรณ์ย้อนหลังของโมเดลชุดนี้ ปี 2000–2023 จึงใช้เฉพาะภูมิอากาศ

  python3 analysis/retro_verify.py          # รันซ้ำได้ ใช้ cache และทำต่อจากจุดเดิมถ้าติดโควตา API

ผลลัพธ์: analysis/out/*.csv, analysis/out/summary.json
"""
import io
import json
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import weather_core as core  # noqa: E402

import os
EVAL_START = os.environ.get("EVAL_START", "2024-01-01")
EVAL_END = os.environ.get("EVAL_END", "2025-08-24")          # ตั้ง env เพื่อดูผลเบื้องต้นจากข้อมูลใน cache
OUT_TAG = os.environ.get("OUT_TAG", "")
CLIM_YEARS = range(2000, 2024)
LEADS = [1, 3, 7]
MODELS = {"ecmwf_ifs025": "ECMWF IFS", "ecmwf_aifs025_single": "ECMWF AIFS", "gfs_global": "NOAA GFS",
          "icon_global": "DWD ICON", "gem_global": "ECCC GEM", "jma_gsm": "JMA GSM", "cma_grapes_global": "CMA GRAPES",
          "meteofrance_arpege_world": "MF ARPEGE", "ukmo_global_deterministic_10km": "UKMO UM"}
AI_MODELS = {"ECMWF AIFS"}
THRESHOLDS = [1.0, 10.0, 35.1]          # มม./วัน: มีฝน, ปานกลางขึ้นไป, หนัก (เกณฑ์กรมอุตุฯ)
# GSOD flag "I" = สถานีไม่ได้รายงานฝนและไม่มีรายงานฝนในข้อมูลรายชั่วโมง ส่วนใหญ่คือวันแห้ง
# ถ้าตัดทิ้ง ตัวอย่างจะเอียงไปทางวันที่ฝนตก (ค่าเฉลี่ยสูงเกินจริง) จึงนับ I + 0.00 เป็นวันแห้งเป็นค่าเริ่มต้น
I_AS_DRY = os.environ.get("I_AS_DRY", "1") == "1"
# ข้อมูลย้อนหลังที่ผิดปกติจากการตรวจ (ตัดออกและรายงานใน paper)
DATA_ISSUES = [("CMA GRAPES", "tmax", None, "Tmax/Tmin ต่ำกว่าค่าวัด ~6 °C ทุกสถานี"),
               ("CMA GRAPES", "tmin", None, "Tmax/Tmin ต่ำกว่าค่าวัด ~6 °C ทุกสถานี"),
               ("NOAA GFS", "rain", 7, "ฝนเฉลี่ย D+7 ต่ำกว่า D+1 ~60% และแทบไม่ทายฝนหนัก (FBI 0.14)")]
COVERAGE_MIN = 0.9                      # โมเดลต้องมีข้อมูล ≥ 90% ของสถานี-วันในช่วง จึงนับเข้าตารางหลัก
BOOT = 1000
OUT = Path(__file__).resolve().parent / "out" / OUT_TAG
CACHE = Path(__file__).resolve().parent / "out" / "cache"


# ---------------------------------------------------------------------------
# สถานีและค่าวัด
# ---------------------------------------------------------------------------
def stations():
    csv = subprocess.run(["git", "show", "origin/data:obs/tmdday/2026-09-27.csv"], cwd=core.ROOT,
                         capture_output=True, text=True, check=True).stdout
    s = pd.read_csv(io.StringIO(csv), dtype={"id": str})[["id", "name", "lat", "lon"]].drop_duplicates("id")
    s["region"] = [region(a, b) for a, b in zip(s["lat"], s["lon"])]
    return s.reset_index(drop=True)


def region(lat, lon):
    if lat < 11.5:
        return "ใต้"
    if lat >= 17.0 and lon < 101.3:
        return "เหนือ"
    if lon >= 101.3 and lat >= 14.0:
        return "ตะวันออกเฉียงเหนือ"
    if lon >= 101.0 and lat < 14.0:
        return "ตะวันออก"
    return "กลาง"


def season(dates, reg):
    """มรสุมตะวันตกเฉียงใต้ (พ.ค.–ต.ค.) / อื่น ๆ — ภาคใต้ฝั่งตะวันออกฝนมากช่วง พ.ย.–ม.ค. จึงแยกดูตามภูมิภาคประกอบ"""
    return np.where(dates.dt.month.between(5, 10), "SW monsoon (พ.ค.–ต.ค.)", "Dry/NE (พ.ย.–เม.ย.)")


def _gsod_file(args):
    year, sid = args
    f = CACHE / f"gsod_{year}_{sid}.csv"
    if f.exists():
        return f
    for attempt in range(3):
        try:
            r = requests.get(f"https://www.ncei.noaa.gov/data/global-summary-of-the-day/access/{year}/{sid}099999.csv", timeout=90)
            f.write_text(r.text if r.ok else "")
            return f
        except requests.RequestException:
            time.sleep(3 * (attempt + 1))
    return None


def gsod(st, years):
    CACHE.mkdir(parents=True, exist_ok=True)
    jobs = [(y, sid) for y in years for sid in st["id"]]
    with ThreadPoolExecutor(8) as ex:
        files = [f for f in ex.map(_gsod_file, jobs) if f is not None]
    frames = []
    for f in files:
        if f.stat().st_size == 0:
            continue
        d = pd.read_csv(f, usecols=["DATE", "PRCP", "PRCP_ATTRIBUTES", "MAX", "MIN"])
        d["id"] = f.stem.split("_")[-1]
        frames.append(d)
    g = pd.concat(frames, ignore_index=True)
    g["date"] = pd.to_datetime(g["DATE"])
    flag = g["PRCP_ATTRIBUTES"].astype(str).str.strip()
    ok = (g["PRCP"] < 99) & ((flag == "G") | (I_AS_DRY & (flag == "I") & (g["PRCP"] == 0)))
    g["rain"] = np.where(ok, g["PRCP"] * 25.4, np.nan)
    g["tmax"] = np.where(g["MAX"] < 999, (g["MAX"] - 32) * 5 / 9, np.nan)
    g["tmin"] = np.where(g["MIN"] < 999, (g["MIN"] - 32) * 5 / 9, np.nan)
    return g[["id", "date", "rain", "tmax", "tmin"]].drop_duplicates(["id", "date"])


def climatology(ob):
    """ค่าเฉลี่ยรายสถานีรายเดือน (ฝน) และตามวันของปี ±7 วัน (อุณหภูมิ) จาก 2000–2023"""
    c = ob[ob["date"].dt.year.isin(CLIM_YEARS)].copy()
    c["month"] = c["date"].dt.month
    rain = c.groupby(["id", "month"])["rain"].mean().rename("rain_clim")
    c["doy"] = c["date"].dt.dayofyear
    t = []
    for sid, g in c.groupby("id"):
        daily = g.groupby("doy")[["tmax", "tmin"]].mean().reindex(range(1, 367))
        pad = pd.concat([daily.iloc[-7:], daily, daily.iloc[:7]])
        sm = pad.rolling(15, center=True, min_periods=5).mean().iloc[7:-7]
        sm["id"] = sid
        t.append(sm.reset_index(names="doy"))
    temp = pd.concat(t).rename(columns={"tmax": "tmax_clim", "tmin": "tmin_clim"})
    return rain.reset_index(), temp


# ---------------------------------------------------------------------------
# พยากรณ์ย้อนหลัง (cache ต่อเดือนต่อกลุ่มสถานี · ทำต่อได้ถ้าติดโควตา)
# ---------------------------------------------------------------------------
class QuotaExceeded(Exception):
    pass


def _fetch_chunk(part, a, b):
    hv = [f"{v}_previous_day{d}" for v in ("precipitation", "temperature_2m") for d in LEADS]
    for attempt in range(6):
        r = requests.get(core.PREVIOUS_RUNS_URL, params={
            "latitude": ",".join(f"{x:.4f}" for x in part["lat"]), "longitude": ",".join(f"{x:.4f}" for x in part["lon"]),
            "hourly": ",".join(hv), "models": ",".join(MODELS), "timezone": "GMT",
            "start_date": str(a.date()), "end_date": str(b.date())}, timeout=300)
        if r.status_code == 429 or (r.status_code == 400 and "limit" in r.text.lower()):
            if "daily" in r.text.lower():
                raise QuotaExceeded(r.text[:200])
            time.sleep(65)                      # โควตารายนาที: รอแล้วลองใหม่
            continue
        r.raise_for_status()
        js = r.json()
        return js if isinstance(js, list) else [js]
    raise QuotaExceeded("ลองซ้ำครบแล้วยังติด rate limit")


def forecasts(st):
    months = pd.date_range(EVAL_START, EVAL_END, freq="MS")
    parts = []
    for m in months:
        a, b = m, min(m + pd.offsets.MonthEnd(0), pd.Timestamp(EVAL_END))
        for i in range(0, len(st), 25):
            f = CACHE / f"fc_{a:%Y%m}_{i:03d}.pkl"
            if f.exists():
                parts.append(pd.read_pickle(f))
                continue
            part = st.iloc[i:i + 25]
            js = _fetch_chunk(part, a, b)
            rows = []
            for sid, j in zip(part["id"], js):
                h = j["hourly"]
                t = pd.to_datetime(h["time"])
                day = t.normalize()
                for k, name in MODELS.items():
                    for d in LEADS:
                        p = pd.Series(h.get(f"precipitation_previous_day{d}_{k}"), index=t, dtype=float)
                        tt = pd.Series(h.get(f"temperature_2m_previous_day{d}_{k}"), index=t, dtype=float)
                        if p.isna().all() and tt.isna().all():
                            continue
                        gp, gt = p.groupby(day), tt.groupby(day)
                        df = pd.DataFrame({"rain": gp.sum().where(gp.count() == 24),
                                           "tmax": gt.max().where(gt.count() == 24),
                                           "tmin": gt.min().where(gt.count() == 24)}).dropna(how="all")
                        df = df.reset_index(names="date")
                        df["id"], df["model"], df["lead"] = sid, name, d
                        rows.append(df)
            chunk = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
            chunk.to_pickle(f)
            parts.append(chunk)
            print(f"  พยากรณ์ {a:%Y-%m} สถานี {i + 1}–{i + len(part)} ✓", flush=True)
            time.sleep(2)
    return pd.concat(parts, ignore_index=True)


# ---------------------------------------------------------------------------
# คะแนน
# ---------------------------------------------------------------------------
def contingency(fc, ob, thr):
    f, o = np.asarray(fc) >= thr, np.asarray(ob) >= thr
    return np.array([(f & o).sum(), (~f & o).sum(), (f & ~o).sum(), (~f & ~o).sum()], dtype=float)


def cat_scores(c):
    h, m, fa, cn = c
    n = c.sum()
    hr = (h + m) * (h + fa) / n if n else 0
    return {"CSI": h / (h + m + fa) if h + m + fa else np.nan,
            "ETS": (h - hr) / (h + m + fa - hr) if (h + m + fa - hr) else np.nan,
            "POD": h / (h + m) if h + m else np.nan, "FAR": fa / (h + fa) if h + fa else np.nan,
            "FBI": (h + fa) / (h + m) if h + m else np.nan, "n": int(n), "events": int(h + m)}


def boot_ets(g, thr, rng):
    """95% CI ของ ETS แบบ block bootstrap รายวัน (สถานีในวันเดียวกันสัมพันธ์กันเชิงพื้นที่)"""
    per_day = {d: contingency(x["rain_fc"], x["rain_ob"], thr) for d, x in g.groupby("date")}
    days = np.array(list(per_day))
    vals = [cat_scores(sum(per_day[d] for d in rng.choice(days, len(days))))["ETS"] for _ in range(BOOT)]
    return np.nanpercentile(vals, [2.5, 97.5])


def common_sample(df, var, window=None):
    """เลือกโมเดลที่มีข้อมูล ≥ COVERAGE_MIN แล้วใช้เฉพาะ สถานี-วัน ที่ทุกโมเดลที่เลือกมีครบ"""
    d = df.dropna(subset=[f"{var}_fc", f"{var}_ob"])
    if window:
        d = d[(d["date"] >= window[0]) & (d["date"] <= window[1])]
    total = d[["id", "date"]].drop_duplicates().shape[0]
    cov = d.drop_duplicates(["model", "id", "date"]).groupby("model").size() / max(total, 1)
    keep = cov[cov >= COVERAGE_MIN].index
    d = d[d["model"].isin(keep)]
    n = d.groupby(["id", "date"])["model"].nunique()
    idx = n[n == len(keep)].index
    return d.set_index(["id", "date"]).loc[idx].reset_index(), cov.round(3).to_dict()


def rain_table(df, window, label):
    rng = np.random.default_rng(0)
    rows, coverage = [], {}
    for lead, g in df.groupby("lead"):
        s, cov = common_sample(g, "rain", window)
        coverage[f"D+{lead}"] = cov
        for model, gm in s.groupby("model"):
            mae = (gm["rain_fc"] - gm["rain_ob"]).abs().mean()
            mae_clim = (gm["rain_clim"] - gm["rain_ob"]).abs().mean()
            r = {"period": label, "lead": f"D+{lead}", "model": model, "AI": model in AI_MODELS,
                 "n_station_days": len(gm), "n_days": gm["date"].nunique(), "n_stations": gm["id"].nunique(),
                 "MAE_mm": mae, "MAE_skill_vs_clim": 1 - mae / mae_clim if mae_clim else np.nan,
                 "bias_mm": (gm["rain_fc"] - gm["rain_ob"]).mean()}
            for thr in THRESHOLDS:
                r.update({f"{k}@{thr:g}": v for k, v in cat_scores(contingency(gm["rain_fc"], gm["rain_ob"], thr)).items()})
            lo, hi = boot_ets(gm, 10.0, rng)
            r["ETS@10_lo"], r["ETS@10_hi"] = lo, hi
            rows.append(r)
    return pd.DataFrame(rows), coverage


def temp_table(df, window, label):
    rows = []
    for lead, g in df.groupby("lead"):
        for var in ("tmax", "tmin"):
            s, _ = common_sample(g, var, window)
            for model, gm in s.groupby("model"):
                e = gm[f"{var}_fc"] - gm[f"{var}_ob"]
                ec = gm[f"{var}_clim"] - gm[f"{var}_ob"]
                rows.append({"period": label, "lead": f"D+{lead}", "var": var, "model": model, "AI": model in AI_MODELS,
                             "n": len(e), "MAE": e.abs().mean(), "bias": e.mean(),
                             "MAE_skill_vs_clim": 1 - e.abs().mean() / ec.abs().mean()})
    return pd.DataFrame(rows)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    st = stations()
    print(f"สถานี {len(st)} แห่ง · ดาวน์โหลด GSOD {min(CLIM_YEARS)}–2025 (cache) …", flush=True)
    ob = gsod(st, list(CLIM_YEARS) + [2024, 2025])
    ev = ob[(ob["date"] >= EVAL_START) & (ob["date"] <= EVAL_END)]
    print(f"GSOD: {ob['id'].nunique()} สถานี · ช่วงประเมิน {ev['rain'].notna().sum():,} สถานี-วัน "
          f"(ฝนเฉลี่ย {ev['rain'].mean():.2f} มม./วัน, วันฝน ≥1 มม. {(ev['rain'] >= 1).mean():.0%}) · "
          f"ภูมิอากาศ {ob[ob['date'].dt.year < 2024]['rain'].notna().sum():,} สถานี-วัน", flush=True)
    rain_clim, temp_clim = climatology(ob)
    print("ดึงพยากรณ์ย้อนหลัง …", flush=True)
    try:
        fc = forecasts(st)
    except QuotaExceeded as e:
        print(f"⚠️ ติดโควตา Open-Meteo: {e}\nรันคำสั่งเดิมอีกครั้งพรุ่งนี้ ระบบจะทำต่อจากจุดเดิม")
        return

    df = fc.merge(ev, on=["id", "date"], suffixes=("_fc", "_ob"))
    df = df.merge(st[["id", "region"]], on="id")
    df["month"], df["doy"] = df["date"].dt.month, df["date"].dt.dayofyear
    df = df.merge(rain_clim, on=["id", "month"], how="left").merge(temp_clim, on=["id", "doy"], how="left")
    df["season"] = season(df["date"], df["region"])
    for model, var, lead, why in DATA_ISSUES:          # ตัดข้อมูลย้อนหลังที่ผิดปกติ
        m = (df["model"] == model) & ((df["lead"] == lead) if lead else True)
        df.loc[m, f"{var}_fc"] = np.nan
    df.to_pickle(OUT / "pairs.pkl")

    # ช่วงเปรียบเทียบหลักเริ่ม มี.ค. 2024 (ECMWF IFS เริ่มมีข้อมูลย้อนหลัง ก.พ. 2024) · ช่วง AIFS เริ่ม มี.ค. 2025
    periods = {"main": (pd.Timestamp("2024-03-01"), pd.Timestamp(EVAL_END)),
               "AIFS (2025-03→)": (pd.Timestamp("2025-03-01"), pd.Timestamp(EVAL_END))}
    rain, cover = [], {}
    for label, w in periods.items():
        t, c = rain_table(df, w, label)
        rain.append(t)
        cover[label] = c
    rain = pd.concat(rain).sort_values(["period", "lead", "ETS@10"], ascending=[True, True, False])
    rain.to_csv(OUT / "rain_scores.csv", index=False)
    temp = pd.concat([temp_table(df, w, label) for label, w in periods.items()])
    temp.to_csv(OUT / "temp_scores.csv", index=False)

    by = []
    for (lead, model, reg, sea), g in df.dropna(subset=["rain_fc", "rain_ob"]).groupby(["lead", "model", "region", "season"]):
        s = cat_scores(contingency(g["rain_fc"], g["rain_ob"], 10.0))
        by.append({"lead": f"D+{lead}", "model": model, "region": reg, "season": sea, "ETS@10": s["ETS"], "FBI@10": s["FBI"], "n": s["n"]})
    pd.DataFrame(by).to_csv(OUT / "rain_by_region_season.csv", index=False)

    json.dump({"eval": [EVAL_START, EVAL_END], "i_as_dry": I_AS_DRY,
               "excluded": [f"{m} {v}{'' if l is None else f' D+{l}'}: {w}" for m, v, l, w in DATA_ISSUES],
               "obs_mean_rain_mm": float(ev["rain"].mean()), "obs_rain_days_ge1_pct": float((ev["rain"] >= 1).mean() * 100), "clim_years": [min(CLIM_YEARS), max(CLIM_YEARS)],
               "stations": int(ev["id"].nunique()), "eval_station_days_rain": int(ev["rain"].notna().sum()),
               "coverage": cover}, open(OUT / "summary.json", "w"), ensure_ascii=False, indent=1)

    pd.set_option("display.width", 220)
    cols = ["period", "lead", "model", "n_station_days", "CSI@1", "ETS@10", "ETS@10_lo", "ETS@10_hi", "ETS@35.1",
            "FBI@10", "MAE_mm", "MAE_skill_vs_clim"]
    print("\n=== ฝนรายวัน ===")
    print(rain[cols].round(3).to_string(index=False))
    print("\n=== Tmax/Tmin: MAE (°C) และ skill เทียบภูมิอากาศ (ช่วงทั้งหมด) ===")
    t = temp[temp["period"] == "main"]
    print(t.pivot_table(index="model", columns=["var", "lead"], values="MAE").round(2).to_string())
    print(t.pivot_table(index="model", columns=["var", "lead"], values="MAE_skill_vs_clim").round(2).to_string())
    print("\nความครอบคลุมของข้อมูล (สัดส่วนสถานี-วัน):", json.dumps(cover, ensure_ascii=False))


if __name__ == "__main__":
    main()
