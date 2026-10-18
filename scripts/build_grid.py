"""สร้างตารางมิติ grid_cell ของกริด th025 (0.25° ครอบคลุมประเทศไทย) → resources/grid_th025.csv

กริดสอดคล้องกับกริดมาตรฐาน 0.25° ของ ECMWF Open Data และ NOAA GFS (จุดศูนย์กลางที่พิกัดลงตัว 0.25°)
เก็บเฉพาะเซลล์ที่อยู่ภายในประเทศไทยหรือห่างจากเขตแดนไม่เกิน BUFFER_DEG เพื่อลดปริมาณข้อมูล

ขอบเขตจังหวัด: Natural Earth 1:10m Admin-1 (สาธารณสมบัติ)
  python scripts/build_grid.py <ne_10m_admin_1_states_provinces.geojson>
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
BBOX = dict(south=5.0, north=21.0, west=97.0, east=106.0)    # ตรงกับ subregion ที่ขอจาก NOMADS
RES = 0.25
BUFFER_DEG = 0.25
# ชื่อภาษาไทยใน Natural Earth (name_local) ผิดหรือเป็นชื่ออำเภอ — แก้ตามชื่อทางการ
TH_FIX = {"Bangkok Metropolis": "กรุงเทพมหานคร", "Bueng Kan": "บึงกาฬ", "Chaiyaphum": "ชัยภูมิ"}


def th_name(pr):
    if pr["name"] in TH_FIX:
        return TH_FIX[pr["name"]]
    return (pr.get("name_local") or pr["name"]).replace("จังหวัด", "").replace("อำเภอเมือง", "").strip()


def main(path):
    feats = [f for f in json.loads(Path(path).read_text())["features"] if f["properties"].get("adm0_a3") == "THA"]
    provs = [(f["properties"]["name"], th_name(f["properties"]), shape(f["geometry"])) for f in feats]
    th = unary_union([g for _, _, g in provs])
    near = th.buffer(BUFFER_DEG)
    tree = STRtree([g for _, _, g in provs])
    lats = np.round(np.arange(BBOX["north"], BBOX["south"] - 1e-9, -RES), 2)    # เรียงเหนือ→ใต้ ตามลำดับใน GRIB
    lons = np.round(np.arange(BBOX["west"], BBOX["east"] + 1e-9, RES), 2)
    rows = []
    for i, la in enumerate(lats):
        for j, lo in enumerate(lons):
            p = Point(lo, la)
            if not near.contains(p):
                continue
            k = tree.query(p, predicate="intersects")
            if len(k):
                pe, pt = provs[k[0]][0], provs[k[0]][1]
            else:                                               # นอกเขตแดน (ทะเล/ประเทศเพื่อนบ้าน): จังหวัดที่ใกล้ที่สุด
                d = [g.distance(p) for _, _, g in provs]
                pe, pt = provs[int(np.argmin(d))][0], provs[int(np.argmin(d))][1]
            rows.append({"cell_id": i * len(lons) + j, "row": i, "col": j, "lat": la, "lon": lo,
                         "in_thailand": bool(th.contains(p)), "province_en": pe, "province_th": pt})
    df = pd.DataFrame(rows)
    out = ROOT / "resources" / "grid_th025.csv"
    df.to_csv(out, index=False)
    print(f"{out.name}: {len(df)} เซลล์ (ในประเทศ {df.in_thailand.sum()}) · {df.province_en.nunique()} จังหวัด · "
          f"กรอบ {len(lats)}×{len(lons)}")


if __name__ == "__main__":
    main(sys.argv[1])
