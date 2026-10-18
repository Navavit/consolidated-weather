"""สร้างข้อมูลอ้างอิงรายจังหวัด → resources/th_provinces.csv และ web/th_provinces.geojson

  python scripts/build_provinces.py <ne_10m_admin_1_states_provinces.geojson>

ขอบเขตจังหวัด: Natural Earth 1:10m Admin-1 (สาธารณสมบัติ) ทำให้เรียบด้วย tolerance 0.01° สำหรับแผนที่บนเว็บ
ภาคตามการแบ่งของกรมอุตุนิยมวิทยา (6 ภาค) · จุดตัวแทน = representative point ของรูปจังหวัด (อยู่ภายในรูปเสมอ)
"""
import json
import sys
from pathlib import Path

import pandas as pd
from shapely.geometry import mapping, shape

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_grid import th_name  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
REGIONS = {
    "ภาคเหนือ": ["Chiang Rai", "Chiang Mai", "Mae Hong Son", "Lampang", "Lamphun", "Phayao", "Phrae", "Nan", "Uttaradit", "Tak",
                 "Sukhothai", "Phitsanulok", "Phichit", "Kamphaeng Phet", "Phetchabun", "Nakhon Sawan", "Uthai Thani"],
    "ภาคตะวันออกเฉียงเหนือ": ["Loei", "Nong Khai", "Bueng Kan", "Udon Thani", "Nong Bua Lam Phu", "Sakon Nakhon", "Nakhon Phanom",
                              "Mukdahan", "Kalasin", "Khon Kaen", "Chaiyaphum", "Maha Sarakham", "Roi Et", "Yasothon", "Amnat Charoen",
                              "Ubon Ratchathani", "Si Sa Ket", "Surin", "Buri Ram", "Nakhon Ratchasima"],
    "ภาคกลาง": ["Bangkok Metropolis", "Nonthaburi", "Pathum Thani", "Samut Prakan", "Samut Sakhon", "Samut Songkhram", "Nakhon Pathom",
                "Phra Nakhon Si Ayutthaya", "Ang Thong", "Sing Buri", "Chai Nat", "Lop Buri", "Saraburi", "Suphan Buri", "Kanchanaburi",
                "Ratchaburi", "Phetchaburi", "Prachuap Khiri Khan", "Nakhon Nayok"],
    "ภาคตะวันออก": ["Chachoengsao", "Prachin Buri", "Sa Kaeo", "Chon Buri", "Rayong", "Chanthaburi", "Trat"],
    "ภาคใต้ฝั่งตะวันออก": ["Chumphon", "Surat Thani", "Nakhon Si Thammarat", "Phatthalung", "Songkhla", "Pattani", "Yala", "Narathiwat"],
    "ภาคใต้ฝั่งตะวันตก": ["Ranong", "Phangnga", "Phuket", "Krabi", "Trang", "Satun"],
}
REGION_OF = {p: r for r, ps in REGIONS.items() for p in ps}


def main(path):
    feats = [f for f in json.loads(Path(path).read_text())["features"] if f["properties"].get("adm0_a3") == "THA"]
    rows, out = [], []
    for f in feats:
        pr = f["properties"]
        g = shape(f["geometry"])
        rp = g.representative_point()
        code = pr.get("iso_3166_2") or pr["name"]
        rows.append({"pcode": code, "province_th": th_name(pr), "province_en": pr["name"], "region": REGION_OF[pr["name"]],
                     "lat": round(rp.y, 4), "lon": round(rp.x, 4)})
        out.append({"type": "Feature", "properties": {"pcode": code, "name": th_name(pr), "region": REGION_OF[pr["name"]]},
                    "geometry": mapping(g.simplify(0.01, preserve_topology=True))})
    df = pd.DataFrame(rows).sort_values(["region", "province_th"])
    assert len(df) == 77 and df.region.notna().all(), len(df)
    df.to_csv(ROOT / "resources" / "th_provinces.csv", index=False)
    gj = {"type": "FeatureCollection", "features": out}
    txt = json.dumps(gj, ensure_ascii=False, separators=(",", ":"))
    # ลดทศนิยมพิกัดเหลือ 3 ตำแหน่ง (~100 ม.) เพื่อลดขนาดไฟล์
    import re
    txt = re.sub(r"(-?\d+\.\d{3})\d+", r"\1", txt)
    (ROOT / "web" / "th_provinces.geojson").write_text(txt, encoding="utf-8")
    print(f"th_provinces.csv: {len(df)} จังหวัด · {df.region.value_counts().to_dict()} · geojson {len(txt) / 1e3:.0f} KB")


if __name__ == "__main__":
    main(sys.argv[1])
