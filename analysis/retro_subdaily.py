"""วัดความแม่นย้อนหลังแบบละเอียดกว่ารายวัน ทั่วประเทศ (ต่อยอดจาก retro_verify.py)

ค่าวัด   NOAA ISD-Lite ~119 สถานีอุตุฯ ไทย 2024-01 → 2025-08-24 (รายงาน SYNOP ชุดเดียวกับที่ใช้ทำ GSOD)
         ใช้เฉพาะ **อุณหภูมิทุก 3 ชม.** (00, 03, …, 21 UTC = 07, 10, …, 04 น.)
         ฝนรายช่วงใน ISD ของไทยใช้ไม่ได้: มีทั้งฝน 1/3/6/12/18/24 ชม. ปนกัน, ส่วนใหญ่รายงานเฉพาะเมื่อฝนตก,
         ค่า "24 ชม." ที่ 06/12 UTC เป็นค่าซ้ำของยอด 00 UTC ส่วนรายชั่วโมงเป็นยอด 24 ชม. แบบเลื่อน
         (ทดลองแก้สมการเป็นฝนราย 6 ชม. แล้วรวมเทียบ GSOD ได้ r = 0.72 เท่านั้น) → ฝนละเอียดกว่ารายวันใช้ข้อมูลเก็บสด
         (กรมอุตุฯ ราย 3 ชม. ทุกสถานี และ ThaiWater รายชั่วโมง ตั้งแต่ ก.ย. 2026) แทน
พยากรณ์ Open-Meteo Previous Runs ล่วงหน้า ≥ 24 ชม. (previous_day1) รายชั่วโมง → ค่า ณ เวลา synoptic

  python3 analysis/retro_subdaily.py fetch     # ดึงพยากรณ์รายชั่วโมง (cache ต่อเดือนต่อ 25 สถานี · ทำต่อได้)
  python3 analysis/retro_subdaily.py obs       # อ่าน ISD-Lite (out/cache/isd/lite_<ปี>_<สถานี>.gz)
  python3 analysis/retro_subdaily.py verify    # MAE/bias รายชั่วโมง + เกาะความร้อนเมืองตามชั่วโมง → out/subdaily_*.csv
"""
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_verify import (CACHE, EVAL_END, EVAL_START, MODELS, OUT, QuotaExceeded, core,  # noqa: E402
                          gsod_coords, stations)

LEAD = 1                                   # previous_day1 = ค่าที่ทายไว้ล่วงหน้า ≥ 24 ชม. (hour-matched)
SUB = CACHE / "subdaily"
ISD = CACHE / "isd"


def station_table():
    """สถานีชุดเดียวกับ retro_verify ที่พิกัด GSOD (ถ้ามี)"""
    st = stations()
    g = gsod_coords()
    st["lat"] = [g.get(i, (a, b))[0] for i, a, b in zip(st.id, st.lat, st.lon)]
    st["lon"] = [g.get(i, (a, b))[1] for i, a, b in zip(st.id, st.lat, st.lon)]
    return st


# ---------------------------------------------------------------------------
# พยากรณ์รายชั่วโมง → ราย 3 ชม.
# ---------------------------------------------------------------------------
def _fetch(part, a, b):
    hv = [f"{v}_previous_day{LEAD}" for v in ("precipitation", "temperature_2m")]
    for attempt in range(8):
        try:
            r = requests.get(core.PREVIOUS_RUNS_URL, params={
            "latitude": ",".join(f"{x:.4f}" for x in part["lat"]), "longitude": ",".join(f"{x:.4f}" for x in part["lon"]),
            "hourly": ",".join(hv), "models": ",".join(MODELS), "timezone": "GMT",
            "start_date": str(a.date()), "end_date": str(b.date())}, timeout=300)
        except requests.RequestException as e:                 # เครือข่ายช้า/หลุด: รอแล้วลองใหม่
            print(f"    ลองใหม่ ({e.__class__.__name__})", flush=True)
            time.sleep(30 * (attempt + 1))
            continue
        if r.status_code == 429 or (r.status_code == 400 and "limit" in r.text.lower()):
            if "daily" in r.text.lower():
                raise QuotaExceeded(r.text[:200])
            time.sleep(65)
            continue
        r.raise_for_status()
        js = r.json()
        return js if isinstance(js, list) else [js]
    raise QuotaExceeded("ลองซ้ำครบแล้วยังติด rate limit")


def _to3h(part, js):
    """ฝนรวม (t-3h, t] และอุณหภูมิ ณ t ที่ t = 00, 03, …, 21 UTC"""
    rows = []
    for sid, j in zip(part["id"], js):
        h = j["hourly"]
        t = pd.to_datetime(h["time"])
        for k, name in MODELS.items():
            p = pd.Series(h.get(f"precipitation_previous_day{LEAD}_{k}"), index=t, dtype=float)
            tt = pd.Series(h.get(f"temperature_2m_previous_day{LEAD}_{k}"), index=t, dtype=float)
            if p.isna().all() and tt.isna().all():
                continue
            r3 = p.rolling(3, min_periods=3).sum()
            m = t.hour % 3 == 0
            df = pd.DataFrame({"time": t[m], "rain3": r3[m].values, "temp": tt[m].values}).dropna(how="all", subset=["rain3", "temp"])
            df["id"], df["model"] = sid, name
            rows.append(df)
    out = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    for c in ("rain3", "temp"):
        if c in out:
            out[c] = out[c].astype("float32")
    return out


def fetch():
    st = station_table()
    SUB.mkdir(parents=True, exist_ok=True)
    months = pd.date_range(EVAL_START, EVAL_END, freq="MS")
    todo = [(m, i) for m in months for i in range(0, len(st), 25) if not (SUB / f"fc3h_{m:%Y%m}_{i:03d}.pkl").exists()]
    print(f"ต้องดึง {len(todo)} ชุด (จาก {len(months) * len(range(0, len(st), 25))})", flush=True)
    for m, i in todo:
        a = m - pd.Timedelta(days=1)                      # เผื่อ 1 วันให้ผลรวม 3 ชม. แรกของเดือนครบ
        b = min(m + pd.offsets.MonthEnd(0), pd.Timestamp(EVAL_END))
        part = st.iloc[i:i + 25]
        try:
            chunk = _to3h(part, _fetch(part, a, b))
        except QuotaExceeded as e:
            print(f"⏸️ ติดโควตารายวันของ Open-Meteo: {e} — รันคำสั่งเดิมอีกครั้งพรุ่งนี้ จะทำต่อจากจุดนี้", flush=True)
            return False
        chunk = chunk[(chunk["time"] >= m) & (chunk["time"] <= b + pd.Timedelta(days=1))]
        chunk.to_pickle(SUB / f"fc3h_{m:%Y%m}_{i:03d}.pkl")
        print(f"  {m:%Y-%m} สถานี {i + 1}–{i + len(part)} ✓", flush=True)
        time.sleep(2)
    print("DONE fetch", flush=True)
    return True


# ---------------------------------------------------------------------------
# ค่าวัด ISD-Lite → อุณหภูมิราย 3 ชม.
# ---------------------------------------------------------------------------
LITE_COLS = ["y", "m", "d", "h", "temp", "dew", "slp", "wdir", "wspd", "sky", "p1", "p6"]
PERIOD = ("2024-03-01", EVAL_END)          # ช่วงหลักเหมือน retro_verify (ทุกโมเดลมีข้อมูลครบ)
BOOT = 500


def obs():
    st = station_table()
    frames, miss = [], []
    for sid in st["id"]:
        files = [ISD / f"lite_{y}_{sid}.gz" for y in (2024, 2025)]
        files = [f for f in files if f.exists() and f.stat().st_size]
        if not files:
            miss.append(sid)
            continue
        d = pd.concat([pd.read_csv(f, sep=r"\s+", names=LITE_COLS) for f in files])
        d = d[(d["temp"] != -9999) & (d["h"] % 3 == 0)]
        t = pd.to_datetime(dict(year=d.y, month=d.m, day=d.d, hour=d.h))
        frames.append(pd.DataFrame({"id": sid, "time": t.values, "temp": (d["temp"] / 10).astype("float32").values}))
    o = pd.concat(frames, ignore_index=True).drop_duplicates(["id", "time"])
    o = o[(o["time"] >= pd.Timestamp(EVAL_START)) & (o["time"] <= pd.Timestamp(EVAL_END) + pd.Timedelta(days=1))]
    o.to_pickle(SUB / "obs_temp3h.pkl")
    print(f"อุณหภูมิราย 3 ชม.: {len(o):,} ค่า จาก {o['id'].nunique()} สถานี · ไม่มี ISD-Lite: {', '.join(miss) or '–'}")
    # ตรวจกับ GSOD: ค่าสูงสุด/ต่ำสุดจาก 8 ครั้งต่อวันต้องใกล้ Tmax/Tmin (ต่ำ/สูงกว่าเล็กน้อยเพราะวัดแค่ทุก 3 ชม.)
    from retro_verify import gsod
    g = gsod(st, [2024, 2025])
    o["date"] = o["time"].dt.normalize()
    day = o.groupby(["id", "date"])["temp"].agg(["max", "min", "size"]).reset_index()
    j = day[day["size"] == 8].merge(g, on=["id", "date"]).dropna(subset=["tmax", "tmin"])
    print(f"เทียบ GSOD {len(j):,} สถานี-วัน: Tmax − max(3 ชม.) เฉลี่ย {(j.tmax - j['max']).mean():.2f} °C, "
          f"min(3 ชม.) − Tmin เฉลี่ย {(j['min'] - j.tmin).mean():.2f} °C")


def load_pairs():
    fc = pd.concat([pd.read_pickle(f) for f in sorted(SUB.glob("fc3h_*.pkl"))], ignore_index=True)
    fc = fc.dropna(subset=["temp"]).drop_duplicates(["id", "time", "model"])
    o = pd.read_pickle(SUB / "obs_temp3h.pkl")
    df = fc.merge(o, on=["id", "time"], suffixes=("_fc", "_ob"))
    df = df[(df["time"] >= pd.Timestamp(PERIOD[0])) & (df["time"] < pd.Timestamp(PERIOD[1]) + pd.Timedelta(days=1))]
    df = df[df["model"] != "CMA GRAPES"]                  # อุณหภูมิ CMA ผิดปกติ (~−6 °C) ตัดเหมือน retro_verify
    df["err"] = df["temp_fc"] - df["temp_ob"]
    df["local_hour"] = (df["time"].dt.hour + 7) % 24
    df["date"] = (df["time"] + pd.Timedelta(hours=7)).dt.normalize()     # วันตามเวลาไทย (ใช้ bootstrap รายวัน)
    # ชุดเดียวกันทุกโมเดล: สถานี-เวลา ที่ทุกโมเดล (ที่มีข้อมูล ≥ 90%) มีครบ
    cov = df.groupby("model").size() / df[["id", "time"]].drop_duplicates().shape[0]
    keep = cov[cov >= 0.9].index
    df = df[df["model"].isin(keep)]
    n = df.groupby(["id", "time"])["model"].transform("nunique")
    return df[n == len(keep)], cov.round(3)


def _uhi_boot(piv, urban, rural, rng):
    """piv: สถานี × วัน · ค่า (ในเมือง − ชนบท) และ 95% CI แบบ two-way bootstrap (วัน × สถานีในแต่ละกลุ่ม)"""
    U, R = piv.loc[piv.index.isin(urban)].values, piv.loc[piv.index.isin(rural)].values
    est = np.nanmean(U) - np.nanmean(R)
    nd = piv.shape[1]
    vals = []
    for _ in range(BOOT):
        d = rng.integers(0, nd, nd)
        u, r = U[rng.integers(0, len(U), len(U))][:, d], R[rng.integers(0, len(R), len(R))][:, d]
        vals.append(np.nanmean(u) - np.nanmean(r))
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return est, lo, hi


def verify():
    df, cov = load_pairs()
    print("coverage", cov.to_dict())
    OUT.mkdir(parents=True, exist_ok=True)
    by = df.groupby(["model", "local_hour"])["err"].agg(mae=lambda e: e.abs().mean(), bias="mean", n="size").reset_index()
    by.to_csv(OUT / "subdaily_temp_by_hour.csv", index=False)
    pd.set_option("display.width", 220)
    print("\n=== MAE อุณหภูมิ (°C) ตามเวลาไทย · ล่วงหน้า ≥ 24 ชม. ===")
    print(by.pivot(index="model", columns="local_hour", values="mae").round(2).to_string())
    print("\n=== Bias (°C) ===")
    print(by.pivot(index="model", columns="local_hour", values="bias").round(2).to_string())

    # เกาะความร้อนเมืองตามชั่วโมง: ในเมือง (urban centre) − ชนบท
    su = pd.read_csv(Path(__file__).resolve().parent / "out" / "station_urban.csv", dtype={"id": str})
    urban, rural = set(su.loc[su.group == "urban centre", "id"]), set(su.loc[su.group == "rural", "id"])
    rng = np.random.default_rng(0)
    rows = []
    one = df.drop_duplicates(["id", "time"])
    for h, g in one.groupby("local_hour"):
        piv = g.pivot_table(index="id", columns="date", values="temp_ob")
        e, lo, hi = _uhi_boot(piv, urban, rural, rng)
        rows.append({"local_hour": h, "model": "observed", "urban_minus_rural": e, "lo": lo, "hi": hi})
    for (m, h), g in df.groupby(["model", "local_hour"]):
        piv = g.pivot_table(index="id", columns="date", values="err")
        e, lo, hi = _uhi_boot(piv, urban, rural, rng)
        piv_fc = g.pivot_table(index="id", columns="date", values="temp_fc")
        e2 = np.nanmean(piv_fc.loc[piv_fc.index.isin(urban)].values) - np.nanmean(piv_fc.loc[piv_fc.index.isin(rural)].values)
        rows.append({"local_hour": h, "model": m, "urban_minus_rural": e2, "bias_diff": e, "lo": lo, "hi": hi})
    u = pd.DataFrame(rows)
    u.to_csv(OUT / "subdaily_uhi_by_hour.csv", index=False)
    print(f"\n=== เมือง − ชนบท (°C) ตามเวลาไทย · ในเมือง {len(urban)} ชนบท {len(rural)} สถานี ===")
    obs_row = u[u.model == "observed"].set_index("local_hour")
    print("ค่าวัด  ", "  ".join(f"{h:02d}:{r.urban_minus_rural:+.2f}[{r.lo:+.2f},{r.hi:+.2f}]" for h, r in obs_row.iterrows()))
    for m, g in u[u.model != "observed"].groupby("model"):
        g = g.set_index("local_hour")
        print(f"{m:11s}", "  ".join(f"{h:02d}:{r.urban_minus_rural:+.2f}{'*' if r.hi < 0 or r.lo > 0 else ' '}" for h, r in g.iterrows()),
              "  (* = bias ในเมืองต่างจากชนบทอย่างมีนัยสำคัญ)" if m == sorted(u.model.unique())[0] else "")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    {"fetch": fetch, "obs": obs, "verify": verify}[cmd]()
