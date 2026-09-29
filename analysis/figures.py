"""รูปสำหรับ paper (PNG 300 dpi + PDF) → <out>/figures/

  Fig 1  แผนที่สถานีตามประเภทความเป็นเมือง
  Fig 2  เกาะความร้อนเมืองตอนกลางคืน: ที่วัดได้ vs ที่โมเดลทาย (หลังควบคุมตัวแปรกวน)
  Fig 3  ETS (frequency-matched) ฝน ≥ 10 มม. ตามช่วงล่วงหน้า
  Fig 4  ECMWF AIFS − IFS แบบจับคู่ (ETS, frequency-matched ETS, SEDI)

  OUT_TAG=interim python3 analysis/figures.py
"""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_verify import OUT, CACHE  # noqa: E402

# ชุดสีตามลำดับคงที่ (ผ่านการตรวจ CVD แบบติดกัน) · ตัวอักษรใช้สีหมึก ไม่ใช้สีของ series
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
INK, INK2, MUTED, GRID = "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
MODEL_ORDER = ["ECMWF IFS", "DWD ICON", "NOAA GFS", "MF ARPEGE", "JMA GSM", "ECCC GEM", "CMA GRAPES", "ECMWF AIFS"]
FIG = OUT / "figures"

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9, "axes.edgecolor": MUTED, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False, "legend.frameon": False, "savefig.dpi": 300,
})


def save(fig, name):
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / f"{name}.png", bbox_inches="tight")
    fig.savefig(FIG / f"{name}.pdf", bbox_inches="tight")
    plt.close(fig)


def fig1_map():
    st = pd.read_csv(Path(__file__).resolve().parent / "out" / "station_urban.csv", dtype={"id": str})
    coast = json.loads((CACHE / "ne_10m_coastline.geojson").read_text())
    fig, ax = plt.subplots(figsize=(4.2, 6.2))
    for f in coast["features"]:
        xy = np.array(f["geometry"]["coordinates"])
        if ((xy[:, 0] > 95) & (xy[:, 0] < 108) & (xy[:, 1] > 4) & (xy[:, 1] < 22)).any():
            ax.plot(xy[:, 0], xy[:, 1], color=MUTED, lw=0.5, zorder=1)
    styles = {"urban centre": ("o", SLOTS[0], "Urban centre (≥1,500 km⁻²)"),
              "urban cluster": ("o", SLOTS[1], "Urban cluster (300–1,500)"),
              "rural": ("o", SLOTS[2], "Rural (<300)"),
              "airport": ("^", INK2, "Airport")}
    for g, (mk, col, lab) in styles.items():
        s = st[st["group"] == g]
        ax.scatter(s.g_lon, s.g_lat, marker=mk, s=26, color=col, edgecolor="white", linewidth=0.8,
                   label=f"{lab}, n = {len(s)}", zorder=3)
    ax.set_xlim(97.2, 106); ax.set_ylim(5.5, 20.7); ax.set_aspect("equal")
    ax.set_xlabel("Longitude (°E)"); ax.set_ylabel("Latitude (°N)")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.09), ncol=2, fontsize=7.5, handletextpad=0.3, columnspacing=1.0)
    ax.set_title("GSOD stations by degree of urbanisation (WorldPop 2020)", fontsize=9, color=INK, loc="left")
    save(fig, "fig1_station_map")


def fig2_uhi():
    reg = pd.read_csv(OUT / "urban_regression.csv")
    t = reg[reg["var"] == "tmin"].set_index("model").reindex([m for m in MODEL_ORDER if m in set(reg.model)])
    obs = float(t["urban_effect_on_obs"].median())
    obs_lo, obs_hi = float(t["obs_lo"].median()), float(t["obs_hi"].median())
    fig, ax = plt.subplots(figsize=(5.6, 3.2))
    y = np.arange(len(t))
    modelled = obs + t["urban_effect_on_bias"].values                     # UHI ที่โมเดลทาย = UHI จริง + ผลต่อ bias
    ax.axvspan(obs_lo, obs_hi, color=SLOTS[1], alpha=0.12, lw=0)
    ax.axvline(obs, color=SLOTS[1], lw=2)
    ax.text(obs + 0.03, -0.62, f"observed\n{obs:.2f} °C", color=INK2, fontsize=8, ha="left", va="top")
    ax.errorbar(obs + t["urban_effect_on_bias"], y, xerr=[t["urban_effect_on_bias"] - t["lo"], t["hi"] - t["urban_effect_on_bias"]],
                fmt="o", color=SLOTS[0], ecolor=SLOTS[0], elinewidth=1.5, capsize=0, ms=6, mec="white", mew=1.5)
    ax.axvline(0, color=MUTED, lw=1)
    ax.set_yticks(y, t.index)
    ax.set_ylim(len(t) - 0.5, -0.85)
    ax.set_xlabel("Urban-centre minus rural Tmin (°C)\nadjusted for elevation, distance to coast and region")
    ax.set_title("Nocturnal urban heat island: observed vs modelled (D+1)", fontsize=9, color=INK, loc="left")
    for yi, v, hi in zip(y, modelled, obs + t["hi"].values):
        ax.text(hi + 0.03, yi, f"{v:.2f}", fontsize=7.5, color=INK2, va="center")
    save(fig, "fig2_uhi")


def fig3_ets():
    mc = pd.read_csv(OUT / "model_compare.csv")
    t = mc[mc["period"] == "main"]
    fig, ax = plt.subplots(figsize=(5.2, 3.3))
    leads = ["D+1", "D+3", "D+7"]
    models = [m for m in MODEL_ORDER if m in set(t.model)]
    for i, m in enumerate(models):
        s = t[t.model == m].set_index("lead").reindex(leads)
        ax.plot(range(3), s["ETS_matched"], color=SLOTS[i], lw=2, marker="o", ms=5, mec="white", mew=1.2, label=m)
    ax.set_xticks(range(3), leads); ax.set_xlim(-0.2, 2.2)
    ax.set_ylabel("ETS, rain ≥ 10 mm d⁻¹ (frequency-matched)")
    ax.set_title("Daily rainfall skill by lead time (2024-03 →)", fontsize=9, color=INK, loc="left")
    ax.legend(fontsize=7, ncol=2, loc="lower left")
    save(fig, "fig3_ets_by_lead")


def fig4_aifs():
    mc = pd.read_csv(OUT / "model_compare.csv")
    t = mc[(mc["period"].str.startswith("AIFS")) & (mc["model"] == "ECMWF AIFS")].set_index("lead").reindex(["D+1", "D+3", "D+7"])
    fig, ax = plt.subplots(figsize=(5.2, 3.0))
    scores = [("ETS", "ETS"), ("ETS_matched", "ETS (frequency-matched)"), ("SEDI", "SEDI")]
    for i, (k, lab) in enumerate(scores):
        x = np.arange(3) + (i - 1) * 0.18
        ax.errorbar(x, t[f"{k}_vs_ref"], yerr=[t[f"{k}_vs_ref"] - t[f"{k}_lo"], t[f"{k}_hi"] - t[f"{k}_vs_ref"]],
                    fmt="o", color=SLOTS[i], elinewidth=1.5, capsize=0, ms=6, mec="white", mew=1.5, label=lab)
    ax.axhline(0, color=MUTED, lw=1)
    ax.set_xticks(range(3), t.index)
    ax.set_ylabel("AIFS minus IFS (rain ≥ 10 mm d⁻¹)")
    ax.set_title("AI (AIFS) vs physics (IFS), paired, 95 % two-way bootstrap CI", fontsize=9, color=INK, loc="left")
    ax.legend(fontsize=7.5, loc="upper left")
    save(fig, "fig4_aifs_vs_ifs")


if __name__ == "__main__":
    for f in (fig1_map, fig2_uhi, fig3_ets, fig4_aifs):
        f()
        print("✓", f.__name__)
    print("→", FIG)
