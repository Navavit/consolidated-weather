"""ฝนรายวันที่เครื่องวัดโทรมาตร ThaiWater จำนวนมาก เทียบพยากรณ์ย้อนหลัง (Open-Meteo Previous Runs · ฟรี)

ค่าวัด  ThaiWater rain_yesterday: ฝน 07:00 → 07:00 น. ของเมื่อวาน ~3,200 เครื่องทั่วประเทศ (ดึงวันละครั้ง)
        ใช้เฉพาะแถวที่ประทับเวลา "วันที่ 00:00" (ตรงกับ rain_24h ตอน 07:00 ถึง 85%) · แถวที่ประทับ 07:00 ระบุวันไม่ชัด จึงไม่ใช้
พยากรณ์ Previous Runs: แต่ละชั่วโมงใช้ค่าที่ทายไว้ล่วงหน้า ≥ 24N ชม. (H24/H72/H168 เหมือนส่วนสถานีอุตุฯ ทั่วประเทศ)
  A  เครื่องในเขตเมือง (≥ 1,500 คน/ตร.กม.) ที่พิกัดของแต่ละเครื่อง · ล่วงหน้า 24/72/168 ชม.
  B  ทุกเครื่องทั่วประเทศ · ล่วงหน้า 24 ชม. · เรียกหนึ่งครั้งต่อช่องกริด 0.1° (~11 กม.) แล้วใช้ค่ากับทุกเครื่องในช่อง
     (โมเดลระดับโลกละเอียด 9–28 กม. เครื่องในช่องเดียวกันได้ค่าใกล้กันมาก) เพื่อไม่ให้เกินโควตาฟรี 10,000 ครั้ง/วัน
ทยอยดึงรอบละไม่เกิน ~4 นาที ทำต่อในรอบถัดไปจนครบ (สถานะใน verification/gauges/partial_<วันที่>.json)
ผล  verification/gauges/<วันที่สิ้นสุด>.csv.gz: หนึ่งแถวต่อเครื่อง (ค่าวัด + พยากรณ์ทุกโมเดล)
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core

URL = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/rain_yesterday"
RESOURCES = core.ROOT / "resources" / "gauges.csv"
CELL = 0.1
BATCH = 40
URBAN, CLUSTER = 1500, 300
LEADS_A = [1, 3, 7]
KEEP_DAYS = 5                      # วันที่ยังพยายามทำให้ครบ (Previous Runs เก็บย้อนหลังนาน แต่ค่าวัดเก็บไว้แค่นี้พอ)


def models(cfg=None):
    return [k for k in core.active_models(cfg) if k not in ("kma_gdps", "bom_access_global")]


def fetch_yesterday():
    r = core.SESSION.get(URL, timeout=120, headers={"User-Agent": "consolidated-weather"})
    r.raise_for_status()
    j = r.json()
    rows = j.get("data", j)
    rows = rows.get("data", rows) if isinstance(rows, dict) else rows
    out = []
    for x in rows:
        t, v = str(x.get("rainfall_datetime") or ""), x.get("rainfall_value")
        lat, lon = x.get("tele_station_lat"), x.get("tele_station_long")
        name = (x.get("tele_station_name") or {}).get("th") or ""
        agency = ((x.get("agency") or {}).get("agency_shortname") or {}).get("th", "")
        out.append({"id": str(x.get("tele_station_id")), "name": name.strip(), "agency": agency.strip(),
                    "lat": lat, "lon": lon, "stamp": t, "rain": v})
    df = pd.DataFrame(out)
    ok = df["stamp"].str.endswith("00:00") & df["rain"].notna() & df["lat"].notna() & df["lon"].notna()
    df = df[ok].copy()
    df["rain"] = df["rain"].astype(float)
    df = df[(df["rain"] >= 0) & (df["rain"] < 1000)]
    df["end"] = (pd.to_datetime(df["stamp"]).dt.normalize() + pd.Timedelta(days=1, hours=7))   # ปลายช่วง 07:00 น.
    return df.drop(columns="stamp")


def archive_yesterday(archive):
    """เก็บค่าวัดของเมื่อวาน (ครั้งเดียวต่อวัน)"""
    base = Path(archive) / "obs" / "twday"
    today_end = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None).normalize() + pd.Timedelta(hours=7)
    if (base / f"{today_end:%Y-%m-%d}.csv.gz").exists():
        return 0
    df = fetch_yesterday()
    df = df[df["end"] == today_end]
    if df.empty:
        return 0
    base.mkdir(parents=True, exist_ok=True)
    df.drop(columns="end").to_csv(base / f"{today_end:%Y-%m-%d}.csv.gz", index=False, compression="gzip")
    return len(df)


def density_table():
    if not RESOURCES.exists():
        return {}
    d = pd.read_csv(RESOURCES, dtype={"id": str})
    return dict(zip(d["id"], d["pop_density"]))


def group_of(dens):
    if dens is None or pd.isna(dens):
        return "ไม่ทราบ"
    return "เมือง" if dens >= URBAN else "ชานเมือง" if dens >= CLUSTER else "ชนบท"


def _jobs(obs, dens):
    """รายการที่ต้องดึง: ('B', key, lat, lon) ต่อช่องกริด และ ('A', id, lat, lon) ต่อเครื่องในเมือง"""
    obs = obs.copy()
    obs["cell"] = [f"{round(la / CELL)}_{round(lo / CELL)}" for la, lo in zip(obs["lat"], obs["lon"])]
    cells = obs.groupby("cell")[["lat", "lon"]].mean()
    jobs = [("B", c, r.lat, r.lon) for c, r in cells.iterrows()]
    urban = obs[[(dens.get(i) or 0) >= URBAN for i in obs["id"]]]
    jobs += [("A", r.id, r.lat, r.lon) for r in urban.itertuples()]
    return obs, jobs


def _fetch(batch, kind, end, cfg):
    leads = [1] if kind == "B" else LEADS_A
    ms = models(cfg)
    hv = [f"precipitation_previous_day{d}" for d in leads]
    for attempt in range(4):
        r = core.SESSION.get(core.PREVIOUS_RUNS_URL, params={
            "latitude": ",".join(f"{b[2]:.4f}" for b in batch), "longitude": ",".join(f"{b[3]:.4f}" for b in batch),
            "hourly": ",".join(hv), "models": ",".join(ms), "timezone": "Asia/Bangkok",
            "start_date": str((end - pd.Timedelta(days=1)).date()), "end_date": str(end.date())}, timeout=180)
        if r.status_code == 429:
            if "daily" in r.text.lower() or "hourly" in r.text.lower():
                raise RuntimeError("ถึงโควตา Open-Meteo แล้ว (ทำต่อรอบหน้า)")
            time.sleep(60)
            continue
        r.raise_for_status()
        js = r.json()
        js = js if isinstance(js, list) else [js]
        hours = pd.date_range(end - pd.Timedelta(hours=23), end, freq="h")
        out = {}
        for b, j in zip(batch, js):
            h = j["hourly"]
            idx = pd.to_datetime(h["time"])
            vals = {}
            for k in ms:
                name = core.DETERMINISTIC_MODELS[k]["name"]
                for d in leads:
                    s = pd.Series(h.get(f"precipitation_previous_day{d}_{k}"), index=idx, dtype=float).reindex(hours)
                    if s.notna().all():
                        vals[f"{name}|H{24 * d}"] = round(float(s.sum()), 1)
            out[b[1]] = vals
        return out
    raise RuntimeError("Open-Meteo ปฏิเสธซ้ำหลายครั้ง")


def update(archive, cfg=None, budget_s=240):
    """ทำวันที่ยังไม่ครบ ภายในเวลาที่กำหนด แล้วเก็บสถานะไว้ทำต่อ"""
    cfg = cfg or core.CFG
    base = Path(archive) / "verification" / "gauges"
    base.mkdir(parents=True, exist_ok=True)
    dens = density_table()
    t0 = time.time()
    msgs = []
    obs_files = sorted((Path(archive) / "obs" / "twday").glob("*.csv.gz"))[-KEEP_DAYS:]
    for f in obs_files:
        day = f.name[:10]
        final = base / f"{day}.csv.gz"
        if final.exists():
            continue
        end = pd.Timestamp(day) + pd.Timedelta(hours=7)
        obs = pd.read_csv(f, dtype={"id": str})
        obs, jobs = _jobs(obs, dens)
        part_path = base / f"partial_{day}.json"
        part = json.loads(part_path.read_text()) if part_path.exists() else {"A": {}, "B": {}}
        todo = [j for j in jobs if j[1] not in part[j[0]]]
        for kind in ("B", "A"):
            pend = [j for j in todo if j[0] == kind]
            for i in range(0, len(pend), BATCH):
                if time.time() - t0 > budget_s:
                    break
                try:
                    part[kind].update(_fetch(pend[i:i + BATCH], kind, end, cfg))
                except Exception as e:
                    msgs.append(f"{day}: {e}")
                    budget_s = 0
                    break
                time.sleep(5 if kind == "B" else 14)          # ≤ 600 ครั้ง/นาที (A นับ 3 เท่า เพราะ 3 ช่วงล่วงหน้า)
        part_path.write_text(json.dumps(part))
        left = sum(1 for j in jobs if j[1] not in part[j[0]])
        if left:
            msgs.append(f"{day}: เหลือ {left} จุด")
            continue
        rows = []
        for r in obs.itertuples():
            row = {"id": r.id, "lat": r.lat, "lon": r.lon, "agency": r.agency, "pop_density": dens.get(r.id),
                   "group": group_of(dens.get(r.id)), "ob": r.rain}
            row.update({f"B|{k}": v for k, v in part["B"].get(r.cell, {}).items()})
            row.update({f"A|{k}": v for k, v in part["A"].get(r.id, {}).items()})
            rows.append(row)
        pd.DataFrame(rows).to_csv(final, index=False, compression="gzip")
        part_path.unlink()
        msgs.append(f"{day}: ครบ {len(rows)} เครื่อง")
    return msgs


def sections(archive, days=30, cfg=None):
    """ส่วนคะแนนสำหรับ verification.json (รูปแบบเดียวกับ weather_verify.leaderboard)"""
    import weather_verify as v
    cfg = cfg or core.CFG
    thr = cfg["rain_threshold_mm"]
    files = sorted((Path(archive) / "verification" / "gauges").glob("20*.csv.gz"))[-days:]
    meta = {m["name"]: m.get("grid_km") for m in core.DETERMINISTIC_MODELS.values()}
    empty = lambda sid, title, src: {"id": sid, "title": title, "variable": "rain", "source": src,
                                     "n_obs": 0, "n_points": 0, "leads": [], "tables": {}, "ens_tables": {}}
    secA = empty("urbangauge_rain", "ฝนรายวัน · โทรมาตรทุกเครื่องในเขตเมือง (ThaiWater)", "urbangauge")
    secB = empty("allgauge_rain", "ฝนรายวัน · โทรมาตรทุกเครื่องทั่วประเทศ (ThaiWater)", "allgauge")
    if not files:
        return [secA, secB]
    df = pd.concat([pd.read_csv(f, dtype={"id": str}).assign(date=f.name[:10]) for f in files], ignore_index=True)
    cols = [c for c in df.columns if c.startswith(("A|", "B|"))]
    long = df.melt(id_vars=["id", "date", "group", "ob"], value_vars=cols, var_name="k", value_name="fc").dropna(subset=["fc", "ob"])
    parts = long["k"].str.split("|", expand=True)
    long["set"], long["model"], long["lead"] = parts[0], parts[1], parts[2]
    long["ev_fc"], long["ev_ob"], long["kind"] = long["fc"] >= thr, long["ob"] >= thr, "det"
    for sec, sub, split in ((secA, long[long["set"] == "A"], False), (secB, long[long["set"] == "B"], True)):
        if sub.empty:
            continue
        sec["n_obs"] = int(sub[["id", "date"]].drop_duplicates().shape[0])
        sec["n_points"] = int(sub["id"].nunique())
        groups = [(l, g) for l, g in sub.groupby("lead")]
        if split:                                  # แยกเมือง / ชานเมือง / ชนบท ด้วย
            groups += [(f"{l}|{grp}", g) for (l, grp), g in sub.groupby(["lead", "group"]) if grp != "ไม่ทราบ"]
        order = ["H24", "H72", "H168", "H24|เมือง", "H24|ชานเมือง", "H24|ชนบท"]
        for lead, g in sorted(groups, key=lambda x: order.index(x[0]) if x[0] in order else 99):
            rows = [{"model": m, "grid_km": meta.get(m), **v._rain_scores(mg)} for m, mg in g.groupby("model")]
            rows.sort(key=lambda r: (-(r["csi"] or 0), -(r["acc"] or 0)))
            if len(rows) >= 2:
                sec["tables"][lead] = rows
                sec["leads"].append(lead)
    return [secA, secB]
