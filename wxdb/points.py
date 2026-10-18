"""พยากรณ์ ณ สถานีอุตุนิยมวิทยาทุกแห่ง จากทุกแบบจำลองที่มีใน Open-Meteo (รวมแบบจำลองที่ไม่มีไฟล์ GRIB สาธารณะ เช่น UKMO, JMA, CMA)

รายชั่วโมง ล่วงหน้า 7 วัน: อุณหภูมิ 2 ม., ความชื้นสัมพัทธ์ 2 ม., ฝน · วันละ 2 รอบ (database.yml)
ปริมาณคำขอ ≈ สถานี × ⌈ตัวแปร × แบบจำลอง / 10⌉ ≈ 127 × 4 ≈ 510 คำขอ/รอบ ภายใต้โควตาฟรีของ Open-Meteo
init_time จาก metadata ของ Open-Meteo (weather_core.model_runs) — เก็บลงตาราง forecast_point
"""
import pandas as pd

import weather_core as core
import weather_heatsafe as hsafe

from . import catalog

TZ = pd.Timedelta(hours=7)


def fetch(sites, cfg=None):
    """sites: DataFrame(site_id, lat, lon) → forecast_point แบบยาว (UTC)"""
    cfg = cfg or core.CFG
    models = {k: m["name"] for k, m in core.active_models(cfg).items()}
    fetched = pd.Timestamp.now(tz="UTC").tz_localize(None).floor("s")
    df = hsafe.fetch(sites.rename(columns={"site_id": "dcode"}), models)       # เวลาท้องถิ่น
    runs = core.model_runs(fetched)
    inits = {n: pd.Timestamp(r["init"]) for n, r in runs.items() if r.get("init")}
    long = df.melt(id_vars=["dcode", "time", "model"], value_vars=["t", "rh", "rain"], var_name="v", value_name="value").dropna(subset=["value"])
    out = pd.DataFrame({"model_id": long.model.map(catalog.model_id), "init_time": long.model.map(inits),
                        "fetched_at": fetched, "site_id": long.dcode, "valid_time": long.time - TZ,
                        "var_id": long.v.map({"t": "t2m", "rh": "rh2m", "rain": "precip"}), "value": long.value})
    out["init_time"] = pd.to_datetime(out.init_time).fillna(fetched.floor("h"))
    out["lead_h"] = ((out.valid_time - out.init_time).dt.total_seconds() / 3600).round().astype("int16")
    # ชั่วโมงก่อนเวลาที่ดึง Open-Meteo เติมจากรอบก่อนหน้า (ไม่ใช่ผลของรอบนี้) จึงตัดออก
    return out[(out.lead_h >= 0) & (out.valid_time >= fetched.floor("h"))]
