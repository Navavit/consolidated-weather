"""ความหนาแน่นประชากรรอบเครื่องวัดฝนโทรมาตร (ThaiWater) ทุกเครื่อง → resources/gauges.csv

ใช้แยกกลุ่ม เมือง / ชานเมือง / ชนบท ในการเทียบฝนรายวันทุกเครื่อง (weather_gauges.py) บน GitHub Actions
ซึ่งไม่มีไฟล์ WorldPop จึงคำนวณไว้ล่วงหน้าที่นี่แล้ว commit ผลไว้
  - WorldPop 2020 1 กม. เฉลี่ย 3×3 กม. (เหมือน analysis/station_urban.py ที่ใช้กับสถานีใน paper)
  - เมือง ≥ 1,500 · ชานเมือง 300–1,500 · ชนบท < 300 คน/ตร.กม. (Degree of Urbanisation)
รวมเครื่องที่เคยอยู่ในไฟล์เดิมและจุดตรวจเดิม (เผื่อหยุดส่งข้อมูลชั่วคราว) · เครื่องใหม่ที่ไม่มีในไฟล์จะถูกจัดเป็น "ไม่ทราบ"

  python3 analysis/gauge_density.py      # รันใหม่เป็นครั้งคราวเพื่อเพิ่มเครื่องใหม่
"""
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))
import weather_gauges  # noqa: E402
import weather_now  # noqa: E402
from station_urban import density, load_worldpop  # noqa: E402

OUT = ROOT / "resources" / "gauges.csv"
COLS = ["id", "name", "agency", "lat", "lon"]


def main():
    frames = [pd.DataFrame(weather_now.fetch_thaiwater_rain24())[COLS]]
    frames.append(weather_gauges.fetch_yesterday()[COLS])
    pts = ROOT / "archive" / "verification" / "points.json"
    if pts.exists():
        frames.append(pd.DataFrame([{"id": str(p["id"]), "name": p["name"], "agency": p.get("agency") or "",
                                     "lat": p["lat"], "lon": p["lon"]}
                                    for p in json.loads(pts.read_text(encoding="utf-8")) if p["kind"] == "tw"]))
    if OUT.exists():
        frames.append(pd.read_csv(OUT, dtype={"id": str})[COLS])
    g = pd.concat(frames, ignore_index=True)
    g["id"] = g["id"].astype(str)
    g = g.drop_duplicates("id").dropna(subset=["lat", "lon"])
    pop = load_worldpop()
    g["pop_density"] = [round(density(*pop, la, lo, 1)) for la, lo in zip(g["lat"], g["lon"])]
    g = g.sort_values("id").reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    g.to_csv(OUT, index=False)
    grp = pd.cut(g["pop_density"], [-1, 300, 1500, 1e9], labels=["ชนบท", "ชานเมือง", "เมือง"])
    print(f"โทรมาตร {len(g):,} เครื่อง → {OUT.relative_to(ROOT)}")
    print(grp.value_counts().to_string())


if __name__ == "__main__":
    main()
