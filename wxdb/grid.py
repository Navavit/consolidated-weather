"""พยากรณ์บนกริด th025 จากไฟล์ GRIB ของศูนย์พยากรณ์โดยตรง (ไม่ใช้โควตา Open-Meteo)

  ECMWF IFS  0.25°  ECMWF Open Data  รอบ 00/12 UTC  ราย 3 ชม. ถึง 144 ชม. แล้วราย 6 ชม. ถึง 168 ชม.
  ECMWF AIFS 0.25°  ECMWF Open Data  รอบ 00/12 UTC  ราย 6 ชม. ถึง 168 ชม.
  NOAA GFS   0.25°  NOMADS GRIB filter (ตัดกรอบประเทศไทยฝั่งผู้ให้บริการ)  รอบ 00/12 UTC  ราย 3 ชม. ถึง 168 ชม.

การแปลงให้เป็นนิยามเดียวกัน
  • อุณหภูมิ K → °C; ความชื้นสัมพัทธ์จากจุดน้ำค้าง (สมการ Magnus, Alduchov & Eskridge 1996) หรือกลับกัน
  • ฝนและรังสี: แปลงค่าสะสมตั้งแต่ต้นรอบ (ECMWF) หรือค่าสะสม/เฉลี่ยเป็นช่วง (GFS bucket)
    เป็นค่าของช่วง (lead_h − step_h, lead_h] — ฝนเป็นผลรวม (มม.) รังสีเป็นค่าเฉลี่ย (W/m²)
"""
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from . import catalog

LAT_N, LON_W, RES = 21.0, 97.0, 0.25
MAX_LEAD = 168
COLS = ["t2m", "td2m", "rh2m", "precip", "u10", "v10", "tcc", "ssrd"]

MODELS = {
    "ecmwf_ifs": {"src": "ecmwf", "om": "ifs", "steps": list(range(0, 145, 3)) + list(range(150, MAX_LEAD + 1, 6))},
    "ecmwf_aifs025_single": {"src": "ecmwf", "om": "aifs-single", "steps": list(range(0, MAX_LEAD + 1, 6))},
    "gfs_global": {"src": "nomads", "steps": list(range(0, MAX_LEAD + 1, 3))},
}
ECMWF_PARAMS = ["2t", "2d", "tp", "10u", "10v", "tcc", "ssrd"]
NOMADS = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"


def rh_from_td(t, td):
    a, b = 17.625, 243.04
    return np.clip(100 * np.exp(a * td / (b + td) - a * t / (b + t)), 0, 100)


def td_from_rh(t, rh):
    a, b = 17.625, 243.04
    g = np.log(np.clip(rh, 0.1, 100) / 100) + a * t / (b + t)
    return b * g / (a - g)


def _cells():
    g = catalog.grid_cells()
    return g.cell_id.to_numpy(), g.row.to_numpy(), g.col.to_numpy()


def read_grib(path):
    """อ่านทุก message → [(shortName, stepType, startStep, endStep, ค่าบนเซลล์ th025)]"""
    import eccodes
    cid, rows, cols = _cells()
    out = []
    with open(path, "rb") as fh:
        while True:
            h = eccodes.codes_grib_new_from_file(fh)
            if h is None:
                break
            try:
                g = lambda k: eccodes.codes_get(h, k)
                ni, nj = g("Ni"), g("Nj")
                la0, lo0 = g("latitudeOfFirstGridPointInDegrees"), g("longitudeOfFirstGridPointInDegrees")
                dla, dlo = g("jDirectionIncrementInDegrees"), g("iDirectionIncrementInDegrees")
                sgn = -1 if g("jScansPositively") == 0 else 1
                vals = eccodes.codes_get_values(h).reshape(nj, ni)
                miss = g("missingValue")
                lat = LAT_N - rows * RES
                lon = LON_W + cols * RES
                r = np.rint((lat - la0) / (sgn * dla)).astype(int)
                c = np.rint(((lon - lo0) % 360) / dlo).astype(int)
                v = vals[r, c].astype("float64")
                v[v == miss] = np.nan
                step_range = str(g("stepRange"))
                a, b = (step_range.split("-") + [step_range])[:2]
                out.append((g("shortName"), g("stepType"), int(a), int(b), v))
            finally:
                eccodes.codes_release(h)
    return out


def _deaccumulate(series, steps, kind):
    """series: {(a, b): ค่า} ของฝน/รังสี → {step: ค่าในช่วง (prev, step]}
    kind = 'accum' (ผลรวมในช่วง a→b) หรือ 'avg' (ค่าเฉลี่ยในช่วง a→b)"""
    out, prev = {}, None
    for s in steps:
        if prev is None:
            prev = s
            continue
        cand = [(a, b) for (a, b) in series if b == s]
        if not cand:
            prev = s
            continue
        a, b = min(cand)
        tot = series[(a, b)] * (b - a if kind == "avg" else 1)
        if a < prev:
            base = series.get((a, prev))
            if base is None:
                prev = s
                continue
            tot = tot - base * ((prev - a) if kind == "avg" else 1)
        out[s] = tot / (s - prev) if kind == "avg" else tot
        prev = s
    return out


def to_frame(msgs, model_id, init, steps):
    """แปลง message ของรอบเดียวเป็น DataFrame ตาม forecast_grid"""
    cid, _, _ = _cells()
    inst, acc = {}, {}
    for name, stype, a, b, v in msgs:
        if name in ("tp", "ssrd", "sdswrf", "dswrf") or stype in ("accum", "avg"):
            if name == "tcc":                         # GFS มี tcc ทั้งแบบ instant และ avg — ใช้ instant
                continue
            key = "tp" if name == "tp" else "ssrd"
            acc.setdefault(key, {"kind": stype, "s": {}})["s"][(a, b)] = v
        else:
            inst[(name, b)] = v
    if "tp" in acc and acc["tp"]["kind"] == "accum" and all(a == 0 for a, _ in acc["tp"]["s"]):
        # ECMWF: สะสมตั้งแต่ต้นรอบ (ม. และ J/m²) → (0, b) ใช้กับ _deaccumulate ได้โดยตรง
        acc["tp"]["s"] = {k: v * 1000 for k, v in acc["tp"]["s"].items()}
        if "ssrd" in acc:
            acc["ssrd"] = {"kind": "avg", "s": {(0, b): v / (b * 3600) for (_, b), v in acc["ssrd"]["s"].items() if b > 0}}
    tp = _deaccumulate(acc["tp"]["s"], steps, "accum") if "tp" in acc else {}
    sw = _deaccumulate(acc["ssrd"]["s"], steps, "avg" if acc["ssrd"]["kind"] == "avg" else "accum") if "ssrd" in acc else {}
    frames, prev = [], None
    for s in steps:
        t = inst.get(("2t", s), inst.get(("t2m", s)))
        if t is None:
            prev = s
            continue
        t = t - 273.15
        td = inst.get(("2d", s), inst.get(("d2m", s)))
        rh = inst.get(("2r", s), inst.get(("r2", s)))
        if td is not None:
            td = td - 273.15
            rh = rh_from_td(t, td)
        elif rh is not None:
            td = td_from_rh(t, rh)
        nan = np.full(len(cid), np.nan)
        frames.append(pd.DataFrame({
            "model_id": model_id, "init_time": init, "lead_h": s, "valid_time": init + pd.Timedelta(hours=s),
            "step_h": (s - prev) if prev is not None else 0, "cell_id": cid,
            "t2m": t, "td2m": td if td is not None else nan, "rh2m": rh if rh is not None else nan,
            "precip": np.clip(tp[s], 0, None) if s in tp else nan,
            "u10": inst.get(("10u", s), inst.get(("u10", s), nan)), "v10": inst.get(("10v", s), inst.get(("v10", s), nan)),
            "tcc": inst.get(("tcc", s), nan), "ssrd": np.clip(sw[s], 0, None) if s in sw else nan}))
        prev = s
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    if df.tcc.max(skipna=True) <= 1.0001:            # ECMWF รายงานเมฆเป็นสัดส่วน 0–1 → %
        df["tcc"] = df.tcc * 100
    df[COLS] = df[COLS].astype("float32").round(2)
    return df


# ---------------------------------------------------------------------------- ดาวน์โหลด
def fetch_ecmwf(model_id, init, workdir):
    from ecmwf.opendata import Client
    m = MODELS[model_id]
    path = Path(workdir) / f"{model_id}.grib2"
    last = None
    for src in ("aws", "ecmwf", "google"):
        try:
            Client(source=src, model=m["om"]).retrieve(date=init.strftime("%Y-%m-%d"), time=init.hour, type="fc",
                                                       step=m["steps"], param=ECMWF_PARAMS, target=str(path))
            return path
        except Exception as e:                        # noqa: BLE001 — ลองแหล่งสำรองถัดไป
            last = e
    raise RuntimeError(f"{model_id} {init}: {last}")


def fetch_gfs(init, workdir):
    import weather_core as core
    path = Path(workdir) / "gfs_global.grib2"
    with open(path, "wb") as out:
        for s in MODELS["gfs_global"]["steps"]:
            params = {"dir": f"/gfs.{init:%Y%m%d}/{init:%H}/atmos", "file": f"gfs.t{init:%H}z.pgrb2.0p25.f{s:03d}",
                      "var_TMP": "on", "var_RH": "on", "var_APCP": "on", "var_UGRD": "on", "var_VGRD": "on",
                      "var_TCDC": "on", "var_DSWRF": "on", "lev_2_m_above_ground": "on", "lev_10_m_above_ground": "on",
                      "lev_surface": "on", "lev_entire_atmosphere": "on",
                      "subregion": "", "toplat": 21, "leftlon": 97, "rightlon": 106, "bottomlat": 5}
            for attempt in range(5):
                r = core.SESSION.get(NOMADS, params=params, timeout=120)
                if r.status_code == 200 and r.content[:4] == b"GRIB":
                    out.write(r.content)
                    break
                if r.status_code == 404 and attempt >= 1:
                    raise FileNotFoundError(f"GFS {init:%Y%m%d %H}z f{s:03d} ยังไม่เผยแพร่")
                time.sleep(5 * (attempt + 1))
            else:
                raise RuntimeError(f"GFS f{s:03d}: HTTP {r.status_code}")
            time.sleep(0.6)                           # เคารพข้อจำกัดอัตราการเรียกของ NOMADS
    return path


def fetch_cycle(model_id, init):
    """ดาวน์โหลดและแปลงรอบเดียว → DataFrame (forecast_grid)"""
    init = pd.Timestamp(init)
    with tempfile.TemporaryDirectory() as d:
        path = fetch_gfs(init, d) if MODELS[model_id]["src"] == "nomads" else fetch_ecmwf(model_id, init, d)
        msgs = read_grib(path)
    # GFS: กรองให้เหลือเฉพาะระดับที่ต้องการ (t ที่ผิวพื้นเป็นอุณหภูมิผิวหน้า ไม่ใช้)
    msgs = [m for m in msgs if m[0] not in ("t", "sp", "orog")]
    return to_frame(msgs, model_id, init, MODELS[model_id]["steps"])


def due_cycles(have, days=3, now=None):
    """รอบ 00/12 UTC ในช่วง `days` วันที่ยังไม่มีใน manifest และน่าจะเผยแพร่ครบแล้ว (≥ 8 ชม. หลังเวลาเริ่ม)"""
    now = pd.Timestamp(now or pd.Timestamp.utcnow()).tz_localize(None)
    start = (now - pd.Timedelta(days=days)).floor("12h")
    out = []
    for init in pd.date_range(start, now - pd.Timedelta(hours=8), freq="12h"):
        for m in MODELS:
            if (m, init) not in have:
                out.append((m, init))
    return out
