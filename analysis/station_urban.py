"""จัดประเภทความเป็นเมืองของสถานีอุตุฯ จากพิกัดใน GSOD + ความหนาแน่นประชากร WorldPop 2020 (1 กม.)

เกณฑ์ตาม Degree of Urbanisation (UN/EU) ใช้ความหนาแน่นเฉลี่ยในพื้นที่ 3×3 กม. รอบสถานี
  urban centre   ≥ 1,500 คน/ตร.กม.
  urban cluster  ≥ 300 คน/ตร.กม.   (เมืองเล็ก/ชานเมือง)
  rural          < 300 คน/ตร.กม.
แยก airport ถ้าชื่อสถานีใน GSOD หรือของกรมอุตุฯ บอกว่าเป็นสนามบิน

ตรวจความต่างของพิกัด GSOD กับพิกัดจาก API กรมอุตุฯ ด้วย (ถ้าต่างมาก พยากรณ์ที่ดึงอาจไม่ใช่จุดของสถานี)

  python3 analysis/station_urban.py      → analysis/out/station_urban.csv
"""
import glob
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
from retro_verify import stations, CACHE, OUT  # noqa: E402

COASTLINE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_coastline.geojson"
WORLDPOP = "https://data.worldpop.org/GIS/Population/Global_2000_2020_1km/2020/THA/tha_ppp_2020_1km_Aggregated.tif"
URBAN_CENTRE, URBAN_CLUSTER = 1500, 300


def load_worldpop():
    f = CACHE / "tha_ppp_2020_1km.tif"
    if not f.exists():
        f.write_bytes(requests.get(WORLDPOP, timeout=120).content)
    im = Image.open(f)
    sx, sy = im.tag_v2[33550][:2]
    x0, y0 = im.tag_v2[33922][3:5]
    a = np.array(im, dtype="float64")
    a[a < 0] = np.nan                                  # -99999 = นอกพื้นที่/ทะเล
    return a, x0, y0, sx, sy


def density(a, x0, y0, sx, sy, lat, lon, k=1):
    """คน/ตร.กม. เฉลี่ยในหน้าต่าง (2k+1)² ช่องรอบพิกัด"""
    r, c = int((y0 - lat) / sy), int((lon - x0) / sx)
    if not (0 <= r < a.shape[0] and 0 <= c < a.shape[1]):
        return np.nan
    win = a[max(r - k, 0):r + k + 1, max(c - k, 0):c + k + 1]
    cell_km2 = (sx * 111.32) * (sy * 111.32 * np.cos(np.radians(lat)))
    return float(np.nanmean(win) / cell_km2) if np.isfinite(win).any() else 0.0


def coast_points():
    """จุดบนแนวชายฝั่ง (Natural Earth 10 ม.) เฉพาะบริเวณรอบประเทศไทย"""
    f = CACHE / "ne_10m_coastline.geojson"
    if not f.exists():
        f.write_bytes(requests.get(COASTLINE, timeout=120).content)
    import json
    pts = []
    for feat in json.loads(f.read_text())["features"]:
        for lon, lat in feat["geometry"]["coordinates"]:
            if 0 <= lat <= 25 and 90 <= lon <= 110:
                pts.append((lat, lon))
    return np.array(pts)


def dist_coast_km(coast, lat, lon):
    p = np.radians
    a = (np.sin(p(coast[:, 0] - lat) / 2) ** 2
         + np.cos(p(lat)) * np.cos(p(coast[:, 0])) * np.sin(p(coast[:, 1] - lon) / 2) ** 2)
    return float(12742 * np.arcsin(np.sqrt(a)).min())


def gsod_coords():
    rows = []
    for f in sorted(glob.glob(str(CACHE / "gsod_2025_*.csv"))):
        try:
            d = pd.read_csv(f, nrows=1, usecols=["LATITUDE", "LONGITUDE", "ELEVATION", "NAME"])
        except (ValueError, pd.errors.EmptyDataError):
            continue
        if len(d):
            r = d.iloc[0]
            rows.append({"id": Path(f).stem.split("_")[-1], "gsod_name": r.NAME, "g_lat": r.LATITUDE,
                         "g_lon": r.LONGITUDE, "elev_m": r.ELEVATION})
    return pd.DataFrame(rows)


def main():
    st = stations()
    g = gsod_coords().merge(st, on="id", how="left")
    g["coord_diff_km"] = np.hypot(g.g_lat - g.lat, (g.g_lon - g.lon) * np.cos(np.radians(g.lat))) * 111.32
    pop = load_worldpop()
    g["pop_density"] = [density(*pop, la, lo) for la, lo in zip(g.g_lat, g.g_lon)]
    g["pop_density_tmd_coord"] = [density(*pop, la, lo) for la, lo in zip(g.lat, g.lon)]
    coast = coast_points()
    g["dist_coast_km"] = [dist_coast_km(coast, la, lo) for la, lo in zip(g.g_lat, g.g_lon)]
    g["degurba"] = np.select([g.pop_density >= URBAN_CENTRE, g.pop_density >= URBAN_CLUSTER],
                             ["urban centre", "urban cluster"], "rural")
    g["airport"] = (g.gsod_name.str.contains("INTL|INTERNATIONAL|AIRPORT|AIRFIELD|AB,", case=False, regex=True)
                    | g.name.fillna("").str.contains("สนามบิน"))
    g["agromet"] = g.gsod_name.str.contains("AGROMET", case=False) | g.name.fillna("").str.contains("สกษ")
    g["group"] = np.where(g.airport, "airport", g.degurba)
    OUT.mkdir(parents=True, exist_ok=True)
    g.to_csv(OUT / "station_urban.csv", index=False)

    print(f"สถานี {len(g)} แห่ง (พิกัดจาก GSOD)")
    print("\nจำนวนตามกลุ่ม:\n", g["group"].value_counts().to_string())
    print("\nระดับความเป็นเมือง × ภูมิภาค:\n", pd.crosstab(g["degurba"], g["region"]).to_string())
    print("\nความหนาแน่นประชากร (คน/ตร.กม.) ตามกลุ่ม:\n", g.groupby("group")["pop_density"].describe()[["count", "min", "50%", "max"]].round(0).to_string())
    print("\nความสูงและระยะจากทะเล ตามกลุ่ม (มัธยฐาน):\n",
          g.groupby("group")[["elev_m", "dist_coast_km"]].median().round(1).to_string())
    print(f"\nพิกัด GSOD vs กรมอุตุฯ: ต่าง > 2 กม. {int((g.coord_diff_km > 2).sum())} สถานี, > 10 กม. {int((g.coord_diff_km > 10).sum())} สถานี")
    changed = g[g.degurba != np.select([g.pop_density_tmd_coord >= URBAN_CENTRE, g.pop_density_tmd_coord >= URBAN_CLUSTER],
                                       ["urban centre", "urban cluster"], "rural")]
    print(f"ประเภทเปลี่ยนถ้าใช้พิกัดกรมอุตุฯ: {len(changed)} สถานี")
    print("\nตัวอย่างศูนย์กลางเมือง:", ", ".join(g[g.degurba == "urban centre"].sort_values("pop_density", ascending=False).name.fillna(g.gsod_name).head(12)))


if __name__ == "__main__":
    main()
