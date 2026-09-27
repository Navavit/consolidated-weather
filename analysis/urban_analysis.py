"""เมือง vs ชนบท: ความแม่นของฝนและอุณหภูมิ + เกาะความร้อนเมืองที่โมเดลจับได้

อ่าน analysis/out/pairs.pkl (จาก retro_verify.py) + analysis/out/station_urban.csv (จาก station_urban.py)
ช่วงความเชื่อมั่น 95% แบบ block bootstrap รายวัน (สุ่มวันทั้งวันพร้อมกัน เพราะสถานีในวันเดียวกันสัมพันธ์กัน)

  python3 analysis/urban_analysis.py      → analysis/out/urban_*.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_verify import OUT, contingency, cat_scores, DATA_ISSUES  # noqa: E402

MAIN = ("2024-03-01", "2025-08-24")
URBAN, RURAL = "urban centre", "rural"
BOOT = 1000


def load():
    df = pd.read_pickle(OUT / "pairs.pkl")
    u = pd.read_csv(Path(__file__).resolve().parent / "out" / "station_urban.csv", dtype={"id": str})[["id", "group", "degurba", "pop_density"]]
    df = df.merge(u, on="id")
    df = df[(df["date"] >= MAIN[0]) & (df["date"] <= MAIN[1])]
    for model, var, lead, _ in DATA_ISSUES:
        m = (df["model"] == model) & ((df["lead"] == lead) if lead else True)
        df.loc[m, f"{var}_fc"] = np.nan
    return df


def _day_sums(x, fn):
    """สรุปต่อวันต่อกลุ่ม: fn(group df) → เวกเตอร์ผลรวมที่บวกกันได้ (เช่น [ผลรวม, จำนวน] หรือ contingency)"""
    days = sorted(pd.to_datetime(x["date"].unique()))
    idx = {pd.Timestamp(d): i for i, d in enumerate(days)}
    k = len(fn(x.iloc[:0]))
    arr = {gname: np.zeros((len(days), k)) for gname in (URBAN, RURAL)}
    for (d, gname), g in x.groupby(["date", "group"]):
        if gname in arr:
            arr[gname][idx[pd.Timestamp(d)]] = fn(g)
    return arr


def boot_diff(x, fn, stat, rng):
    """stat(ผลรวมเมือง) − stat(ผลรวมชนบท) พร้อม 95% CI (block bootstrap รายวัน)"""
    a = _day_sums(x, fn)
    point = stat(a[URBAN].sum(0)) - stat(a[RURAL].sum(0))
    n = len(a[URBAN])
    vals = []
    for _ in range(BOOT):
        pick = rng.integers(0, n, n)
        vals.append(stat(a[URBAN][pick].sum(0)) - stat(a[RURAL][pick].sum(0)))
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    return point, lo, hi


def main():
    df = load()
    rng = np.random.default_rng(0)

    # 1) เกาะความร้อนเมือง: ความต่าง (เมือง − ชนบท) ของค่าวัด ค่าพยากรณ์ และ bias บนสถานี-วันเดียวกัน
    heat = []
    for (lead, model), g in df.groupby(["lead", "model"]):
        for var in ("tmin", "tmax"):
            x = g.dropna(subset=[f"{var}_fc", f"{var}_ob"])
            x = x[x["group"].isin([URBAN, RURAL])]
            if x["group"].nunique() < 2:
                continue
            sums = lambda s, v=var: np.array([s[f"{v}_ob"].sum(), s[f"{v}_fc"].sum(), len(s)], dtype=float)
            ratio = lambda i: (lambda c: c[i] / c[2] if c[2] else np.nan)
            uhi_o, olo, ohi = boot_diff(x, sums, ratio(0), rng)
            uhi_f, flo, fhi = boot_diff(x, sums, ratio(1), rng)
            bias = lambda c: (c[1] - c[0]) / c[2] if c[2] else np.nan
            b, lo, hi = boot_diff(x, sums, bias, rng)
            heat.append({"lead": f"D+{lead}", "model": model, "var": var, "UHI_obs": uhi_o, "UHI_obs_lo": olo, "UHI_obs_hi": ohi,
                         "UHI_model": uhi_f, "bias_diff": b, "ci_lo": lo, "ci_hi": hi, "n": len(x)})
    heat = pd.DataFrame(heat)
    heat.to_csv(OUT / "urban_heat.csv", index=False)

    # 2) ฝน ≥10 มม.: ETS เมือง − ชนบท
    rain = []
    ets = lambda c: cat_scores(c)["ETS"]
    for (lead, model), g in df.dropna(subset=["rain_fc", "rain_ob"]).groupby(["lead", "model"]):
        x = g[g["group"].isin([URBAN, RURAL])]
        if x["group"].nunique() < 2:
            continue
        cont = lambda s: contingency(s["rain_fc"], s["rain_ob"], 10.0)
        d, lo, hi = boot_diff(x, cont, ets, rng)
        rain.append({"lead": f"D+{lead}", "model": model,
                     "ETS_urban": ets(cont(x[x.group == URBAN])), "ETS_rural": ets(cont(x[x.group == RURAL])),
                     "diff": d, "ci_lo": lo, "ci_hi": hi, "n": len(x)})
    rain = pd.DataFrame(rain)
    rain.to_csv(OUT / "urban_rain.csv", index=False)

    pd.set_option("display.width", 200)
    print("=== อุณหภูมิ: (bias เมือง − bias ชนบท) °C · ติดลบ = โมเดลทายเมืองเย็นเกินเมื่อเทียบชนบท ===")
    t = heat[heat["lead"] == "D+1"].copy()
    t["sig"] = np.where((t.ci_lo > 0) | (t.ci_hi < 0), "*", "")
    print(t[["var", "model", "UHI_obs", "UHI_model", "bias_diff", "ci_lo", "ci_hi", "sig", "n"]].round(2).to_string(index=False))
    print("\n=== ฝน ≥10 มม.: ETS เมือง − ชนบท (D+1) ===")
    r = rain[rain["lead"] == "D+1"].copy()
    r["sig"] = np.where((r.ci_lo > 0) | (r.ci_hi < 0), "*", "")
    print(r.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
