"""พยากรณ์ฝน HII WRF-ROMS (สถาบันสารสนเทศทรัพยากรน้ำ, สสน.) — ความละเอียด 3 กม. ล่วงหน้า 7 วัน

สสน. เผยแพร่ผลเป็น "ภาพแผนที่" (ไม่มี API ตัวเลข) ผ่าน ThaiWater:
  https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain7day_forecast  → pre_rain (ไทย 3 กม.) d03_day01 … day07
แต่ละภาพ = ฝนสะสม 24 ชม. 19:00–19:00 น. เวลาไทย (12–12 UTC) จากรอบรัน 19:00 น. (12 UTC) วันละครั้ง

โมดูลนี้แปลงสีในภาพกลับเป็น "ช่วงปริมาณฝน" ตามแถบสีของภาพเอง (1, 5, 10, 20, 35, 50, 70, 90, 150, 200, 300 มม./วัน)
  - ระบุพิกัดจากป้าย 100/105/110°E และ 5–20°N ของภาพ (แผนที่แบบ lat/lon) ตรวจความถูกต้องกับเมืองหลักแล้ว
  - ใช้พิกเซล 3×3 รอบจุด (~12 กม.) ข้ามเส้นเขตแดน/ตัวหนังสือ แล้วใช้ค่ามัธยฐานของช่วง
  - ถ้ากรอบแผนที่ในภาพเลื่อนจากตำแหน่งที่คาดไว้ (สสน. เปลี่ยนรูปแบบภาพ) จะไม่อ่านค่า
"""
import io
import re
from pathlib import Path

import numpy as np
import pandas as pd
from PIL import Image

import weather_core as core

LIST_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain7day_forecast"
IMAGE_URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/shared/image?_csrf=&image="
MODEL = {"name": "HII WRF-ROMS", "agency": "สสน. (HII)", "type": "Physics (regional)", "grid_km": 3,
         "grid": "3 กม. (ฝน 24 ชม. 19:00–19:00 น., 7 วัน · อ่านจากภาพ)"}

# ตำแหน่งในภาพ (ตรวจกรอบทุกภาพ) · x = x0 + (lon − lon0)·px_x, y = y0 + (lat0 − lat)·px_y
DOMAINS = {
    "d03": {"key": "pre_rain", "km": 3, "size": (727, 792), "frame": (147, 579, 55, 594),
            "x0": 274.5, "lon0": 100, "px_x": 28.7, "y0": 128.0, "lat0": 20, "px_y": 28.6,
            "legend_y": 674, "legend_x": [104, 167, 221, 279, 336, 390, 447, 503, 559, 619, 660]},
    "d02": {"key": "pre_rain_sea", "km": 9, "size": (1016, 708), "frame": (48, 1000, 65, 522),
            "x0": 266.6, "lon0": 100, "px_x": 14.238, "y0": 213.0, "lat0": 20, "px_y": 14.3,
            "legend_y": 597, "legend_x": [271, 327, 384, 440, 496, 553, 609, 665, 722, 778, 820]},
}
BINS = [(1, 5), (5, 10), (10, 20), (20, 35), (35, 50), (50, 70), (70, 90), (90, 150), (150, 200), (200, 300), (300, 400)]


def _get(url, **kw):
    r = core.SESSION.get(url, timeout=90, headers={"User-Agent": "consolidated-weather"}, **kw)
    r.raise_for_status()
    return r


def frame_ok(a, dom):
    left, right, top, bottom = dom["frame"]
    dark = a.sum(2) < 180
    col_ok = (dark[top + 5:bottom - 5, left - 2:left + 2].mean(0) > 0.8).any()       # เส้นกรอบซ้าย
    row_ok = (dark[bottom - 2:bottom + 2, left + 5:right - 5].mean(1) > 0.8).any()   # เส้นกรอบล่าง
    return bool(col_ok and row_ok)


def legend(a, dom):
    y = dom["legend_y"]
    cols = [a[y - 1:y + 2, x - 2:x + 3].reshape(-1, 3).mean(0) for x in dom["legend_x"]]
    return np.array([[255, 255, 255]] + cols, dtype=float)          # index 0 = ขาว = ฝน < 1 มม.


def sample(a, pal, dom, lat, lon, k=1):
    """ช่วงฝน (low, high) มม./วัน ที่พิกัด หรือ None ถ้าอยู่นอกภาพ/อ่านไม่ได้"""
    left, right, top, bottom = dom["frame"]
    x = int(round(dom["x0"] + (lon - dom["lon0"]) * dom["px_x"]))
    y = int(round(dom["y0"] + (dom["lat0"] - lat) * dom["px_y"]))
    if not (left + k < x < right - k and top + k < y < bottom - k):
        return None
    px = a[y - k:y + k + 1, x - k:x + k + 1].reshape(-1, 3).astype(float)
    d = np.sqrt(((px[:, None, :] - pal[None, :, :]) ** 2).sum(2))
    idx, dist = d.argmin(1), d.min(1)
    gray = (np.ptp(px, axis=1) < 25) & (px.mean(1) < 200)                  # เส้นเขตแดน/ตัวหนังสือ
    ok = (dist < 70) & ~gray
    if ok.sum() < 3:
        return None
    b = int(np.median(idx[ok]))
    return (0.0, 1.0) if b == 0 else BINS[b - 1]


def latest(points):
    """ดึงภาพรอบล่าสุด แล้วอ่านค่าที่ทุกจุด: วัน 1–3 จากโดเมนไทย 3 กม., วัน 4–7 จากโดเมนอาเซียน 9 กม.
    points = [(lat, lon, name), ...] → (init_local, DataFrame: point, lat, lon, day, domain_km, start, end, low, high, mid)"""
    data = _get(LIST_URL).json()["data"]
    have3 = {int(re.search(r"day(\d+)", i["filename"]).group(1)) for i in data[DOMAINS["d03"]["key"]]["data"]}
    rows, init = [], None
    for name, dom in DOMAINS.items():
        for it in sorted(data[dom["key"]]["data"], key=lambda d: d["filename"]):
            m = re.search(r"day(\d+)", it["filename"])
            if not m:
                continue
            day = int(m.group(1))
            if name == "d02" and day in have3:
                continue                                                    # ใช้ 3 กม. เมื่อมี
            end = pd.Timestamp(it["media_datetime"])                          # สิ้นสุดช่วง 24 ชม. (เวลาไทย)
            init = end - pd.Timedelta(days=day)
            a = np.asarray(Image.open(io.BytesIO(_get(IMAGE_URL + it["media_path"]).content)).convert("RGB")).astype(int)
            if a.shape[:2] != (dom["size"][1], dom["size"][0]) or not frame_ok(a, dom):
                print(f"⚠️ HII: รูปแบบภาพ {it['filename']} เปลี่ยน ข้ามการอ่านค่า")
                continue
            pal = legend(a, dom)
            for lat, lon, pname in points:
                v = sample(a, pal, dom, lat, lon)
                if v:
                    rows.append({"point": pname, "lat": lat, "lon": lon, "day": day, "domain_km": dom["km"],
                                 "start": end - pd.Timedelta(days=1), "end": end,
                                 "low": v[0], "high": v[1], "mid": (v[0] + v[1]) / 2})
    return init, pd.DataFrame(rows)


def archive(archive_dir, points):
    """เก็บผลของรอบใหม่ลง archive/hii/<init>.csv (รอบละครั้ง ไม่ดึงซ้ำ)"""
    items = _get(LIST_URL).json()["data"]["pre_rain_sea"]["data"]
    d1 = next((i for i in items if "day01" in i["filename"]), None)
    if not d1:
        return None, pd.DataFrame()
    init = pd.Timestamp(d1["media_datetime"]) - pd.Timedelta(days=1)
    path = Path(archive_dir) / "hii" / f"{init:%Y%m%dT%H%M}.csv"
    if path.exists():
        return init, pd.read_csv(path, parse_dates=["start", "end"])
    init, df = latest(points)
    if len(df):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.assign(init=init).to_csv(path, index=False)
    return init, df
