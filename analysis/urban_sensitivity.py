"""ความไวต่อนิยาม "เมือง": ผลของศูนย์กลางเมืองต่อ bias ของ Tmin ยังอยู่ไหมเมื่อเปลี่ยนเกณฑ์

ลองทุกชุดของ
  หน้าต่างความหนาแน่น  1×1, 3×3, 5×5 กม. (WorldPop 2020)
  เกณฑ์ศูนย์กลางเมือง    1,000 / 1,500 / 2,500 คน/ตร.กม.
  เกณฑ์ชนบท            150 / 300 / 500 คน/ตร.กม.
แล้วประมาณผลของ "ศูนย์กลางเมือง" (เทียบชนบท) หลังควบคุมความสูง ระยะทะเล ภูมิภาค (regression ระดับสถานี)

  OUT_TAG=interim python3 analysis/urban_sensitivity.py      → <out>/urban_sensitivity.csv
"""
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_verify import OUT  # noqa: E402
from station_urban import load_worldpop, density  # noqa: E402
import urban_analysis as ua  # noqa: E402

MODELS = ["ECMWF IFS", "DWD ICON", "NOAA GFS", "ECCC GEM"]
WINDOWS = {"1×1 กม.": 0, "3×3 กม.": 1, "5×5 กม.": 2}
CENTRE = [1000, 1500, 2500]
RURAL = [150, 300, 500]


def main():
    st = pd.read_csv(ua.STATIONS, dtype={"id": str})
    pop = load_worldpop()
    dens = {w: np.array([density(*pop, la, lo, k) for la, lo in zip(st.g_lat, st.g_lon)]) for w, k in WINDOWS.items()}
    base = ua.load().drop(columns=["group"])
    rng = np.random.default_rng(0)
    ua.BOOT = 300                                          # พอสำหรับดูความไว (ผลหลักใช้ 1,000)
    rows = []
    for (w, dvals), c, r in product(dens.items(), CENTRE, RURAL):
        grp = np.select([st.airport, dvals >= c, dvals >= r], ["airport", "urban centre", "urban cluster"], "rural")
        df = base.merge(pd.DataFrame({"id": st.id, "group": grp}), on="id")
        n_u, n_r = int((grp == "urban centre").sum()), int((grp == "rural").sum())
        if n_u < 8 or n_r < 8:
            continue
        for model in MODELS:
            res, n = ua.station_regression(df, model, "tmin", rng)
            rows.append({"window": w, "centre_thr": c, "rural_thr": r, "n_urban": n_u, "n_rural": n_r, "model": model,
                         "urban_effect_on_bias": res["bias"][0], "lo": res["bias"][1], "hi": res["bias"][2],
                         "urban_effect_on_obs": res["ob"][0]})
        print(f"  {w} centre≥{c} rural<{r}: urban {n_u}, rural {n_r} ✓", flush=True)
    out = pd.DataFrame(rows)
    out.to_csv(OUT / "urban_sensitivity.csv", index=False)
    out["sig"] = out["hi"] < 0
    pd.set_option("display.width", 200)
    print("\n=== ผลของศูนย์กลางเมืองต่อ bias Tmin (°C) ในทุกนิยาม ===")
    print(out.groupby("model").agg(configs=("sig", "size"), significant=("sig", "sum"),
                                   effect_min=("urban_effect_on_bias", "min"), effect_median=("urban_effect_on_bias", "median"),
                                   effect_max=("urban_effect_on_bias", "max"),
                                   obs_UHI_median=("urban_effect_on_obs", "median")).round(2).to_string())
    print("\nชุดที่ไม่มีนัยสำคัญ:")
    print(out[~out["sig"]][["window", "centre_thr", "rural_thr", "n_urban", "n_rural", "model", "urban_effect_on_bias", "lo", "hi"]]
          .round(2).to_string(index=False))


if __name__ == "__main__":
    main()
