"""เมือง vs ชนบท: ความแม่นของฝนและอุณหภูมิ + เกาะความร้อนเมืองที่โมเดลจับได้

อ่าน pairs.pkl (จาก retro_verify.py) + out/station_urban.csv (จาก station_urban.py)

ความแฟร์ของการเปรียบเทียบ
  1. two-way bootstrap: สุ่มทั้ง "วัน" (สถานีในวันเดียวกันสัมพันธ์กัน) และ "สถานี" (มีแค่ ~25 vs ~36 สถานี)
  2. ปรับการทดสอบหลายคู่ด้วย Benjamini–Hochberg (FDR 5%)
  3. ตัวแปรกวน: regression ระดับสถานีของ bias Tmin/Tmax ด้วย ประเภทเมือง + ความสูง + ระยะจากทะเล + ภูมิภาค
     (สถานีชนบทอยู่สูงกว่าและห่างทะเลกว่า ซึ่งทำให้กลางคืนเย็นลงได้เอง)

  OUT_TAG=interim python3 analysis/urban_analysis.py      → <out>/urban_*.csv
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
CORE = ["ECMWF IFS", "DWD ICON", "NOAA GFS", "MF ARPEGE", "JMA GSM", "ECCC GEM"]
STATIONS = Path(__file__).resolve().parent / "out" / "station_urban.csv"


def load():
    df = pd.read_pickle(OUT / "pairs.pkl")
    u = pd.read_csv(STATIONS, dtype={"id": str})[["id", "group", "pop_density", "elev_m", "dist_coast_km"]]
    df = df.merge(u, on="id")
    df = df[(df["date"] >= MAIN[0]) & (df["date"] <= MAIN[1])]
    for model, var, lead, _ in DATA_ISSUES:
        m = (df["model"] == model) & ((df["lead"] == lead) if lead else True)
        df.loc[m, f"{var}_fc"] = np.nan
    return df


# ---------------------------------------------------------------------------
# two-way bootstrap (วัน × สถานี)
# ---------------------------------------------------------------------------
def cube(x, fn, k):
    """อาร์เรย์ [วัน, สถานี, k] ของผลรวมที่บวกกันได้ ต่อกลุ่ม (เมือง/ชนบท)"""
    days = pd.Index(np.sort(x["date"].unique()))
    out = {}
    for gname in (URBAN, RURAL):
        g = x[x["group"] == gname]
        st = pd.Index(np.sort(g["id"].unique()))
        a = np.zeros((len(days), len(st), k))
        for (d, sid), z in g.groupby(["date", "id"]):
            a[days.get_loc(d), st.get_loc(sid)] = fn(z)
        out[gname] = a
    return out


def boot_two_way(a, stat, rng):
    """stat(เมือง) − stat(ชนบท): จุดประมาณ, 95% CI, p-value สองทาง (สุ่มวันร่วมกัน สุ่มสถานีแยกกลุ่ม)"""
    total = lambda arr: arr.sum((0, 1))
    point = stat(total(a[URBAN])) - stat(total(a[RURAL]))
    nd, nu, nr = a[URBAN].shape[0], a[URBAN].shape[1], a[RURAL].shape[1]
    vals = np.empty(BOOT)
    for b in range(BOOT):
        d = rng.integers(0, nd, nd)
        vals[b] = (stat(total(a[URBAN][np.ix_(d, rng.integers(0, nu, nu))]))
                   - stat(total(a[RURAL][np.ix_(d, rng.integers(0, nr, nr))])))
    lo, hi = np.nanpercentile(vals, [2.5, 97.5])
    p = 2 * min((vals <= 0).mean(), (vals >= 0).mean())
    return point, lo, hi, min(p, 1.0)


def bh(p, q=0.05):
    """Benjamini–Hochberg: ผ่าน FDR q หรือไม่"""
    p = np.asarray(p, dtype=float)
    order = np.argsort(p)
    ranked = p[order] * len(p) / (np.arange(len(p)) + 1)
    passed = np.zeros(len(p), bool)
    below = np.where(ranked <= q)[0]
    if len(below):
        passed[order[:below.max() + 1]] = True
    return passed


# ---------------------------------------------------------------------------
# regression ระดับสถานี (ควบคุมตัวแปรกวน)
# ---------------------------------------------------------------------------
def station_regression(df, model, var, rng, lead=1):
    """ค่าเฉลี่ยรายสถานี ~ urban centre + urban cluster + airport + ความสูง + log(ระยะทะเล) + ภูมิภาค
    target 'bias' = พยากรณ์ − วัด, 'ob' = ค่าวัด (ขนาด UHI จริงหลังควบคุม) · 95% CI แบบ bootstrap สถานี"""
    x = df[(df["model"] == model) & (df["lead"] == lead)].dropna(subset=[f"{var}_fc", f"{var}_ob"])
    s = x.groupby("id").agg(fc=(f"{var}_fc", "mean"), ob=(f"{var}_ob", "mean"), n=(f"{var}_ob", "size"),
                            group=("group", "first"), region=("region", "first"),
                            elev=("elev_m", "first"), coast=("dist_coast_km", "first"))
    s["bias"] = s["fc"] - s["ob"]
    s = s[s["n"] >= 60]
    X = pd.DataFrame({"const": 1.0,
                      "urban_centre": (s["group"] == URBAN).astype(float),
                      "urban_cluster": (s["group"] == "urban cluster").astype(float),
                      "airport": (s["group"] == "airport").astype(float),
                      "elev_100m": s["elev"] / 100,
                      "log_coast": np.log1p(s["coast"])}, index=s.index)
    X = X.join(pd.get_dummies(s["region"], prefix="reg", drop_first=True).astype(float))

    def fit(idx, y):
        coef, *_ = np.linalg.lstsq(X.loc[idx].values, y.loc[idx].values, rcond=None)
        return dict(zip(X.columns, coef))

    out = {}
    for target in ("bias", "ob"):
        full = fit(s.index, s[target])
        boots = [fit(rng.choice(s.index, len(s)), s[target])["urban_centre"] for _ in range(BOOT)]
        lo, hi = np.nanpercentile(boots, [2.5, 97.5])
        out[target] = (full["urban_centre"], lo, hi, full["elev_100m"], full["log_coast"])
    return out, len(s)


def main():
    df = load()
    rng = np.random.default_rng(0)

    # 1) อุณหภูมิ: bias เมือง − ชนบท (two-way bootstrap + FDR)
    heat = []
    for (lead, model), g in df[df["model"].isin(CORE)].groupby(["lead", "model"]):
        for var in ("tmin", "tmax"):
            x = g.dropna(subset=[f"{var}_fc", f"{var}_ob"])
            x = x[x["group"].isin([URBAN, RURAL])]
            if x["group"].nunique() < 2:
                continue
            a = cube(x, lambda z, v=var: np.array([z[f"{v}_ob"].sum(), z[f"{v}_fc"].sum(), len(z)], float), 3)
            obs_mean = lambda c: c[0] / c[2] if c[2] else np.nan
            bias = lambda c: (c[1] - c[0]) / c[2] if c[2] else np.nan
            o, olo, ohi, _ = boot_two_way(a, obs_mean, rng)
            b, lo, hi, p = boot_two_way(a, bias, rng)
            heat.append({"lead": f"D+{lead}", "model": model, "var": var, "UHI_obs": o, "UHI_obs_lo": olo, "UHI_obs_hi": ohi,
                         "bias_diff": b, "ci_lo": lo, "ci_hi": hi, "p": p,
                         "n_urban_st": a[URBAN].shape[1], "n_rural_st": a[RURAL].shape[1]})
    heat = pd.DataFrame(heat)
    heat["fdr_sig"] = bh(heat["p"])
    heat.to_csv(OUT / "urban_heat.csv", index=False)

    # 2) ฝน ≥10 มม.: ETS เมือง − ชนบท
    rain = []
    ets = lambda c: cat_scores(c)["ETS"]
    for (lead, model), g in df[df["model"].isin(CORE)].dropna(subset=["rain_fc", "rain_ob"]).groupby(["lead", "model"]):
        x = g[g["group"].isin([URBAN, RURAL])]
        if x["group"].nunique() < 2:
            continue
        a = cube(x, lambda z: contingency(z["rain_fc"], z["rain_ob"], 10.0), 4)
        d, lo, hi, p = boot_two_way(a, ets, rng)
        rain.append({"lead": f"D+{lead}", "model": model, "ETS_urban": ets(a[URBAN].sum((0, 1))),
                     "ETS_rural": ets(a[RURAL].sum((0, 1))), "diff": d, "ci_lo": lo, "ci_hi": hi, "p": p})
    rain = pd.DataFrame(rain)
    rain["fdr_sig"] = bh(rain["p"])
    rain.to_csv(OUT / "urban_rain.csv", index=False)

    # 3) regression ควบคุมตัวแปรกวน (D+1)
    reg = []
    for model in CORE:
        for var in ("tmin", "tmax"):
            r, n = station_regression(df, model, var, rng)
            reg.append({"model": model, "var": var, "n_stations": n,
                        "urban_effect_on_bias": r["bias"][0], "lo": r["bias"][1], "hi": r["bias"][2],
                        "urban_effect_on_obs": r["ob"][0], "obs_lo": r["ob"][1], "obs_hi": r["ob"][2],
                        "elev_coef_bias": r["bias"][3], "coast_coef_bias": r["bias"][4]})
    reg = pd.DataFrame(reg)
    reg.to_csv(OUT / "urban_regression.csv", index=False)

    pd.set_option("display.width", 220)
    print("=== 1) bias เมือง − ชนบท (°C, D+1) · two-way bootstrap (วัน × สถานี) · FDR 5% ===")
    t = heat[heat["lead"] == "D+1"]
    print(t[["var", "model", "UHI_obs", "UHI_obs_lo", "UHI_obs_hi", "bias_diff", "ci_lo", "ci_hi", "p", "fdr_sig",
             "n_urban_st", "n_rural_st"]].round(3).to_string(index=False))
    print("\n=== 2) ฝน ≥10 มม.: ETS เมือง − ชนบท (D+1) ===")
    print(rain[rain["lead"] == "D+1"].round(3).to_string(index=False))
    print("\n=== 3) regression ระดับสถานี: ผลของ 'ศูนย์กลางเมือง' หลังควบคุมความสูง ระยะทะเล ภูมิภาค (D+1) ===")
    print(reg.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
