"""เปรียบเทียบโมเดลอย่างแฟร์: ฝนรายวัน ≥ 10 มม.

ปัญหา: ETS ได้ประโยชน์จากการทายว่าฝนตกบ่อย (FBI > 1) ส่วน MAE ได้ประโยชน์จากการทายฝนน้อย
แก้โดย
  - SEDI (Ferro & Stephenson 2011) ไม่ขึ้นกับ bias ความถี่ และไม่เสื่อมเมื่อเหตุการณ์หายาก
  - ETS หลังปรับความถี่ (frequency-matched): ใช้ threshold ของแต่ละโมเดลที่ทำให้ความถี่ที่ทาย = ความถี่ที่เกิดจริง
  - เทียบแบบจับคู่กับโมเดลอ้างอิง (ECMWF IFS) บนสถานี-วันเดียวกัน · 95% CI แบบ two-way bootstrap (วัน × สถานี)
  - ปรับการทดสอบหลายคู่ด้วย Benjamini–Hochberg

  OUT_TAG=interim python3 analysis/model_compare.py      → <out>/model_compare.csv
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_verify import OUT, contingency, cat_scores, common_sample, DATA_ISSUES  # noqa: E402
from urban_analysis import bh  # noqa: E402

THR = 10.0
REF = "ECMWF IFS"
BOOT = 1000
PERIODS = {"main": ("2024-03-01", "2025-08-24"), "AIFS (2025-03→)": ("2025-03-01", "2025-08-24")}


def sedi(c):
    h, m, fa, cn = c
    H = h / (h + m) if h + m else np.nan
    F = fa / (fa + cn) if fa + cn else np.nan
    if not (0 < H < 1 and 0 < F < 1):
        return np.nan
    lf, lh, l1f, l1h = np.log(F), np.log(H), np.log(1 - F), np.log(1 - H)
    return (lf - lh - l1f + l1h) / (lf + lh + l1f + l1h)


def ets(c):
    return cat_scores(c)["ETS"]


def matched_threshold(fc, ob, thr=THR):
    """threshold ของโมเดลที่ทำให้สัดส่วนวันที่ทาย ≥ t เท่ากับสัดส่วนวันที่วัดได้ ≥ thr"""
    p = (np.asarray(ob) >= thr).mean()
    return float(np.quantile(np.asarray(fc), 1 - p)) if 0 < p < 1 else thr


def cubes(s, models, thresholds):
    """[วัน, สถานี, 4] contingency ต่อโมเดล (ต่อ threshold ของโมเดลนั้น) บนชุดสถานี-วันเดียวกัน"""
    days = pd.Index(np.sort(s["date"].unique()))
    st = pd.Index(np.sort(s["id"].unique()))
    out = {}
    for m in models:
        g = s[s["model"] == m]
        a = np.zeros((len(days), len(st), 4))
        di, si = days.get_indexer(g["date"]), st.get_indexer(g["id"])
        fc, ob = g["rain_fc"].values >= thresholds[m], g["rain_ob"].values >= THR
        for k, v in enumerate([fc & ob, ~fc & ob, fc & ~ob, ~fc & ~ob]):
            np.add.at(a[:, :, k], (di, si), v.astype(float))
        out[m] = a
    return out


def main():
    df = pd.read_pickle(OUT / "pairs.pkl")
    for model, var, lead, _ in DATA_ISSUES:
        m = (df["model"] == model) & ((df["lead"] == lead) if lead else True)
        df.loc[m, f"{var}_fc"] = np.nan
    rng = np.random.default_rng(0)
    rows = []
    for label, (a, b) in PERIODS.items():
        for lead, g in df.groupby("lead"):
            s, _ = common_sample(g, "rain", (pd.Timestamp(a), pd.Timestamp(b)))
            models = sorted(s["model"].unique())
            if REF not in models:
                continue
            raw = {m: THR for m in models}
            matched = {m: matched_threshold(s.loc[s.model == m, "rain_fc"], s.loc[s.model == m, "rain_ob"]) for m in models}
            C_raw, C_mat = cubes(s, models, raw), cubes(s, models, matched)
            nd, ns = C_raw[REF].shape[:2]
            picks = [(rng.integers(0, nd, nd), rng.integers(0, ns, ns)) for _ in range(BOOT)]
            for m in models:
                r = {"period": label, "lead": f"D+{lead}", "model": m, "n": int(C_raw[m].sum()),
                     "FBI": cat_scores(C_raw[m].sum((0, 1)))["FBI"], "matched_thr_mm": matched[m]}
                for name, C, fn in (("ETS", C_raw, ets), ("SEDI", C_raw, sedi), ("ETS_matched", C_mat, fn_ := ets)):
                    r[name] = fn(C[m].sum((0, 1)))
                    if m == REF:
                        continue
                    diffs = np.array([fn(C[m][np.ix_(d, st)].sum((0, 1))) - fn(C[REF][np.ix_(d, st)].sum((0, 1)))
                                      for d, st in picks])
                    r[f"{name}_vs_ref"] = r[name] - fn(C[REF].sum((0, 1)))
                    r[f"{name}_lo"], r[f"{name}_hi"] = np.nanpercentile(diffs, [2.5, 97.5])
                    r[f"{name}_p"] = min(1.0, 2 * min((diffs <= 0).mean(), (diffs >= 0).mean()))
                rows.append(r)
    res = pd.DataFrame(rows)
    for name in ("ETS", "SEDI", "ETS_matched"):
        mask = res[f"{name}_p"].notna()
        res.loc[mask, f"{name}_fdr"] = bh(res.loc[mask, f"{name}_p"])
    res.to_csv(OUT / "model_compare.csv", index=False)

    pd.set_option("display.width", 240)
    for label in PERIODS:
        t = res[res["period"] == label]
        print(f"\n=== {label}: ฝน ≥ {THR:g} มม. · ค่าต่างจาก {REF} [95% CI two-way] · * = ผ่าน FDR 5% ===")
        for lead, tl in t.groupby("lead"):
            print(f"\n{lead}")
            for _, r in tl.sort_values("SEDI", ascending=False).iterrows():
                def cell(n):
                    if r["model"] == REF:
                        return f"{r[n]:.3f} (ref)"
                    star = "*" if r.get(f"{n}_fdr") is True else " "
                    return f"{r[n]:.3f} {r[f'{n}_vs_ref']:+.3f}[{r[f'{n}_lo']:+.3f},{r[f'{n}_hi']:+.3f}]{star}"
                print(f"  {r['model']:11s} FBI {r['FBI']:.2f} | ETS {cell('ETS'):34s} | SEDI {cell('SEDI'):34s} | ETS_matched {cell('ETS_matched')}")


if __name__ == "__main__":
    main()
