"""เก็บพยากรณ์แต่ละรอบลง SQLite, บันทึกฝนที่ตกจริง และวิเคราะห์ย้อนกลับว่าโมเดลไหนแม่น

ตาราง
  runs          รอบการดึงข้อมูล (เวลา, ตำแหน่ง)
  forecast      ฝนรายชั่วโมงของแต่ละโมเดล (det_mm) และโอกาสฝนจาก ensemble (ens_prob) ของแต่ละรอบ
  observations  สิ่งที่ผู้ใช้บันทึกว่าฝนตกจริงหรือไม่

เวลาทั้งหมดเป็นเวลาท้องถิ่นของตำแหน่งนั้น (naive, ไม่มี timezone)
"""
import sqlite3
from contextlib import closing
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core

LEAD_BINS = [0, 3, 6, 12, 24, 48, 96, 192]
LEAD_LABELS = ["≤3 ชม.", "3–6 ชม.", "6–12 ชม.", "12–24 ชม.", "1–2 วัน", "2–4 วัน", "4–8 วัน"]
NEAR_DEG = 0.05                         # ระยะที่ถือว่าเป็นตำแหน่งเดียวกัน (ประมาณ 5 กม.)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    fetched_at TEXT NOT NULL,
    location   TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    tz         TEXT
);
CREATE TABLE IF NOT EXISTS forecast (
    run_id     INTEGER NOT NULL REFERENCES runs(run_id),
    source     TEXT NOT NULL,          -- det_mm | ens_prob
    model      TEXT NOT NULL,
    valid_time TEXT NOT NULL,
    value      REAL,
    PRIMARY KEY (run_id, source, model, valid_time)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS ix_forecast_valid ON forecast(valid_time);
CREATE TABLE IF NOT EXISTS observations (
    obs_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    obs_time   TEXT NOT NULL,
    location   TEXT NOT NULL,
    lat        REAL NOT NULL,
    lon        REAL NOT NULL,
    rained     INTEGER NOT NULL,       -- 1 = ฝนตก, 0 = ไม่ตก
    amount_mm  REAL,                   -- ถ้าวัดได้ (ไม่บังคับ)
    note       TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS imported_files (
    path       TEXT PRIMARY KEY,       -- ไฟล์จาก GitHub (branch data) ที่นำเข้าแล้ว
    run_id     INTEGER
);
"""


def connect(cfg=None):
    cfg = cfg or core.CFG
    path = core.ROOT / cfg["db_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    return con


def _ts(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S")


def window_offsets(cfg=None):
    """ชั่วโมงที่ใช้ตัดสิน รอบชั่วโมงที่มีเวลาสังเกตอยู่ตรงกลาง เช่น W=3 → [-1, 0, +1]"""
    w = int((cfg or core.CFG)["verify_window_hours"])
    return list(range(-(w // 2), w - w // 2))


def window_sum(hourly, cfg=None):
    """ผลรวมฝนในหน้าต่าง W ชม. ที่มีชั่วโมง t อยู่ตรงกลาง (NaN ถ้าข้อมูลไม่ครบ)"""
    return sum(hourly.shift(-k) for k in window_offsets(cfg))    # NaN + x = NaN จึงได้ NaN ถ้าข้อมูลไม่ครบ


# ---------------------------------------------------------------------------
# บันทึกพยากรณ์
# ---------------------------------------------------------------------------
def snapshot_frame(result, cfg=None):
    """แปลงผลการดึง 1 รอบเป็นตารางกว้าง: index=valid_time, คอลัมน์ "det_mm|<โมเดล>" และ "ens_prob|<ระบบ>"

    เก็บเฉพาะฝน (ใช้ตรวจสอบความแม่นยำ) ตั้งแต่ก่อนเวลาดึงเล็กน้อยถึง 8 วันข้างหน้า
    คืน (fetched_at, DataFrame)
    """
    cfg = cfg or core.CFG
    fetched_at = pd.Timestamp.now(tz=result["tz"]).tz_localize(None).floor("s")
    horizon = fetched_at + pd.Timedelta(hours=LEAD_BINS[-1])
    keep_from = fetched_at - pd.Timedelta(hours=int(cfg["verify_window_hours"]) + 1)

    cols = {f"det_mm|{m}": s for m, s in result["data"]["precipitation"].items()}
    thr = cfg["verify_threshold_mm"]
    for name, members in result.get("ensembles", {}).items():
        ws = window_sum(members, cfg)
        cols[f"ens_prob|{name}"] = ws.ge(thr).astype(float).where(ws.notna()).mean(axis=1) * 100
    wide = pd.DataFrame(cols)
    wide = wide[(wide.index > keep_from) & (wide.index <= horizon)].dropna(how="all")
    wide.index.name = "valid_time"
    return fetched_at, wide


def _insert_run(con, fetched_at, name, lat, lon, tz, wide):
    cur = con.execute("INSERT INTO runs(fetched_at, location, lat, lon, tz) VALUES (?,?,?,?,?)",
                      (_ts(fetched_at), name, float(lat), float(lon), tz))
    run_id = cur.lastrowid
    rows = []
    for col in wide:
        source, model = col.split("|", 1)
        rows += [(run_id, source, model, _ts(t), float(v)) for t, v in wide[col].dropna().items()]
    con.executemany("INSERT OR REPLACE INTO forecast VALUES (?,?,?,?,?)", rows)
    return run_id, len(rows)


def save_snapshot(result, cfg=None):
    """เก็บผลจาก weather_core.analyze_location หรือ collect_location ลงฐานข้อมูล"""
    fetched_at, wide = snapshot_frame(result, cfg)
    with closing(connect(cfg)) as con, con:
        return _insert_run(con, fetched_at, result["name"], result["lat"], result["lon"], result["tz"], wide)


def export_snapshot(result, export_dir, cfg=None):
    """เขียนผล 1 รอบเป็นไฟล์ .csv.gz (ใช้บน GitHub Actions แทน SQLite เพื่อให้ git เก็บทีละไฟล์เล็ก ๆ)

    archive/runs/YYYY/MM/DD/YYYYmmddTHHMMSS_<lat>_<lon>.csv.gz
    """
    fetched_at, wide = snapshot_frame(result, cfg)
    out = wide.round(2).reset_index()
    out.insert(0, "tz", result["tz"])
    out.insert(0, "lon", result["lon"])
    out.insert(0, "lat", result["lat"])
    out.insert(0, "location", result["name"])
    out.insert(0, "fetched_at", _ts(fetched_at))
    path = (Path(export_dir) / "runs" / f"{fetched_at:%Y/%m/%d}"
            / f"{fetched_at:%Y%m%dT%H%M%S}_{result['lat']:.3f}_{result['lon']:.3f}.csv.gz")
    path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(path, index=False, compression="gzip", encoding="utf-8")
    return path, int(wide.notna().sum().sum())


def import_archive(archive_dir, cfg=None):
    """นำเข้าไฟล์ .csv.gz จาก GitHub (branch data) ลง SQLite ข้ามไฟล์ที่เคยนำเข้าแล้ว"""
    files = sorted(Path(archive_dir).glob("runs/**/*.csv.gz"))
    n_new = 0
    with closing(connect(cfg)) as con, con:
        done = {r[0] for r in con.execute("SELECT path FROM imported_files")}
        for f in files:
            key = f.relative_to(archive_dir).as_posix()
            if key in done:
                continue
            df = pd.read_csv(f, parse_dates=["valid_time"])
            meta = df.iloc[0]
            wide = df.drop(columns=["fetched_at", "location", "lat", "lon", "tz"]).set_index("valid_time")
            run_id, _ = _insert_run(con, meta.fetched_at, meta.location, meta.lat, meta.lon, meta.tz, wide)
            con.execute("INSERT INTO imported_files(path, run_id) VALUES (?, ?)", (key, run_id))
            n_new += 1
    return n_new, len(files)


def collect_location(lat, lon, name, cfg=None, export_dir=None, to_db=True):
    """ดึงเฉพาะที่ต้องใช้ตรวจสอบย้อนหลัง (ฝน deterministic + ensemble) แล้วบันทึก"""
    data, now, tz = core.fetch_deterministic(lat, lon, cfg)
    ens = core.fetch_all_ensembles(lat, lon, cfg)
    result = {"name": name, "lat": lat, "lon": lon, "tz": tz, "now": now, "data": data, "ensembles": ens}
    out = {}
    if to_db:
        out["run_id"], out["rows"] = save_snapshot(result, cfg)
    if export_dir:
        path, out["rows"] = export_snapshot(result, export_dir, cfg)
        out["file"] = str(path)
    return out


def collect_all(cfg=None, locations=None, export_dir=None, to_db=True):
    cfg = cfg or core.CFG
    locations = locations or core.resolve_locations(cfg)
    log = []
    for lat, lon, name in locations:
        try:
            log.append({"location": name, **collect_location(lat, lon, name, cfg, export_dir, to_db), "status": "ok"})
        except Exception as e:                     # เก็บตำแหน่งอื่นต่อแม้ตำแหน่งหนึ่งล้มเหลว
            log.append({"location": name, "rows": 0, "status": f"error: {e}"})
    return pd.DataFrame(log)


# ---------------------------------------------------------------------------
# บันทึกฝนที่ตกจริง
# ---------------------------------------------------------------------------
def add_observation(name, lat, lon, rained, amount_mm=None, obs_time=None, note="", cfg=None):
    obs_time = pd.Timestamp(obs_time) if obs_time is not None else pd.Timestamp.now()
    with closing(connect(cfg)) as con, con:
        cur = con.execute(
            "INSERT INTO observations(obs_time, location, lat, lon, rained, amount_mm, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (_ts(obs_time), name, float(lat), float(lon), int(bool(rained)),
             None if amount_mm is None or pd.isna(amount_mm) else float(amount_mm),
             note, _ts(pd.Timestamp.now())))
        return cur.lastrowid


def delete_observation(obs_id, cfg=None):
    with closing(connect(cfg)) as con, con:
        con.execute("DELETE FROM observations WHERE obs_id = ?", (int(obs_id),))


def load_observations(cfg=None):
    with closing(connect(cfg)) as con:
        df = pd.read_sql("SELECT * FROM observations ORDER BY obs_time", con, parse_dates=["obs_time", "created_at"])
    return df


def load_runs(cfg=None):
    with closing(connect(cfg)) as con:
        return pd.read_sql("SELECT r.*, COUNT(f.value) AS n_values FROM runs r "
                           "LEFT JOIN forecast f USING(run_id) GROUP BY r.run_id ORDER BY r.fetched_at",
                           con, parse_dates=["fetched_at"])


# ---------------------------------------------------------------------------
# วิเคราะห์ย้อนกลับ (verification)
# ---------------------------------------------------------------------------
def _obs_hours(obs_time, cfg):
    """เวลา valid ของแต่ละชั่วโมงในหน้าต่าง และชั่วโมงกลาง"""
    center = pd.Timestamp(obs_time).floor("h") + pd.Timedelta(hours=1)   # ชั่วโมงที่มีเวลาสังเกตอยู่
    return [center + pd.Timedelta(hours=k) for k in window_offsets(cfg)], center


def _records_from_db(obs, cfg):
    recs = []
    w = len(window_offsets(cfg))
    with closing(connect(cfg)) as con:
        for o in obs.itertuples():
            hours, center = _obs_hours(o.obs_time, cfg)
            q = """
                SELECT r.run_id, r.fetched_at, f.source, f.model, f.valid_time, f.value
                FROM runs r JOIN forecast f USING(run_id)
                WHERE ABS(r.lat - ?) < ? AND ABS(r.lon - ?) < ?
                  AND r.fetched_at < ? AND r.fetched_at >= ?
                  AND f.valid_time IN ({})
            """.format(",".join("?" * len(hours)))
            params = [o.lat, NEAR_DEG, o.lon, NEAR_DEG, _ts(o.obs_time),
                      _ts(o.obs_time - pd.Timedelta(hours=LEAD_BINS[-1])), *[_ts(h) for h in hours]]
            df = pd.read_sql(q, con, params=params, parse_dates=["fetched_at", "valid_time"])
            if df.empty:
                continue
            det = df[df.source == "det_mm"].groupby(["run_id", "fetched_at", "model"])["value"].agg(["sum", "count"])
            for (run_id, fetched_at, model), r in det[det["count"] == w].iterrows():
                recs.append({"obs_id": o.obs_id, "kind": "det", "source": "collector", "model": model,
                             "lead_h": (o.obs_time - fetched_at).total_seconds() / 3600, "value": r["sum"]})
            ens = df[(df.source == "ens_prob") & (df.valid_time == center)]
            for r in ens.itertuples():
                recs.append({"obs_id": o.obs_id, "kind": "ens", "source": "collector", "model": r.model,
                             "lead_h": (o.obs_time - r.fetched_at).total_seconds() / 3600, "value": r.value})
    return recs


def _records_from_previous_runs(obs, cfg):
    """ใช้ Previous Runs API: ได้พยากรณ์ล่วงหน้า 1–7 วันย้อนหลังทันที ไม่ต้องรอ collector"""
    recs = []
    groups = obs.assign(_la=obs.lat.round(2), _lo=obs.lon.round(2)).groupby(["_la", "_lo"])
    for (la, lo), g in groups:
        start = g.obs_time.min().date()
        end = (g.obs_time.max() + pd.Timedelta(days=1)).date()
        try:
            prev = core.fetch_previous_runs(la, lo, start, end)
        except Exception as e:
            print(f"⚠️ previous runs ({la}, {lo}): {e}")
            continue
        if prev.empty:
            continue
        piv = prev.pivot_table(index="time", columns=["model", "lead_days"], values="precipitation")
        for o in g.itertuples():
            hours, _ = _obs_hours(o.obs_time, cfg)
            if not all(h in piv.index for h in hours):
                continue
            block = piv.loc[hours]
            totals = block.sum().where(block.notna().all())
            for (model, lead_days), v in totals.dropna().items():
                recs.append({"obs_id": o.obs_id, "kind": "det", "source": "previous_runs", "model": model,
                             "lead_h": 24.0 * lead_days, "value": v})
    return recs


def verification_records(cfg=None, use_previous_runs=True):
    """คืนตาราง: หนึ่งแถวต่อ (การสังเกต × โมเดล × ช่วงล่วงหน้า) ใช้รอบพยากรณ์ล่าสุดในแต่ละช่วง"""
    cfg = cfg or core.CFG
    obs = load_observations(cfg)
    obs = obs[obs.obs_time <= pd.Timestamp.now()]          # ยังไม่ถึงเวลา = ยังตรวจไม่ได้
    cols = ["obs_id", "obs_time", "location", "kind", "source", "model", "lead_h", "lead_bin",
            "value", "predicted", "observed", "amount_mm"]
    if obs.empty:
        return pd.DataFrame(columns=cols)
    recs = _records_from_db(obs, cfg)
    if use_previous_runs:
        recs += _records_from_previous_runs(obs, cfg)
    if not recs:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(recs)
    df = df[(df.lead_h > 0) & (df.lead_h <= LEAD_BINS[-1])]
    df["lead_bin"] = pd.cut(df.lead_h, LEAD_BINS, labels=LEAD_LABELS)
    # ในแต่ละช่วงล่วงหน้า ใช้รอบที่ใกล้เวลาจริงที่สุด เพื่อไม่ให้รอบที่ดึงบ่อยมีน้ำหนักมากเกินไป
    df = df.sort_values("lead_h").drop_duplicates(["obs_id", "kind", "model", "lead_bin"])
    df = df.merge(obs[["obs_id", "obs_time", "location", "rained", "amount_mm"]], on="obs_id")
    df["observed"] = df.pop("rained").astype(bool)
    thr = cfg["verify_threshold_mm"]
    df["predicted"] = np.where(df.kind == "det", df.value >= thr, df.value >= 50)
    return df[cols].sort_values(["obs_time", "kind", "model", "lead_h"]).reset_index(drop=True)


def _scores(g):
    hits = int((g.predicted & g.observed).sum())
    misses = int((~g.predicted & g.observed).sum())
    fa = int((g.predicted & ~g.observed).sum())
    cn = int((~g.predicted & ~g.observed).sum())
    n = hits + misses + fa + cn
    div = lambda a, b: a / b if b else np.nan
    out = {
        "n": n,
        "ทายถูก %": 100 * div(hits + cn, n),
        "POD %": 100 * div(hits, hits + misses),          # ฝนตกจริง แล้วโมเดลทายถูกกี่ %
        "FAR %": 100 * div(fa, hits + fa),                # โมเดลว่าตก แต่ไม่ตกจริงกี่ %
        "CSI %": 100 * div(hits, hits + misses + fa),
        "Bias": div(hits + fa, hits + misses),            # >1 ทายว่าตกบ่อยเกิน, <1 ทายว่าตกน้อยเกิน
        "hits": hits, "misses": misses, "false_alarms": fa, "correct_neg": cn,
    }
    if (g.kind == "det").all():
        amt = g.dropna(subset=["amount_mm"])
        out["MAE มม."] = (amt.value - amt.amount_mm).abs().mean() if len(amt) else np.nan   # เฉพาะที่ใส่ปริมาณฝน
    else:
        out["Brier"] = ((g.value / 100 - g.observed.astype(float)) ** 2).mean()   # 0 = ดีที่สุด
    return pd.Series(out)


def verification_scores(records, by_lead=True):
    """คะแนนแยกโมเดล (และช่วงล่วงหน้า) — det กับ ens แยกกัน"""
    if records.empty:
        return pd.DataFrame(), pd.DataFrame()
    keys = ["model", "lead_bin"] if by_lead else ["model"]
    out = []
    for kind in ("det", "ens"):
        g = records[records.kind == kind]
        if g.empty:
            out.append(pd.DataFrame())
            continue
        s = pd.DataFrame({k: _scores(grp) for k, grp in g.groupby(keys if by_lead else keys[0], observed=True)}).T
        s = s.astype(float)
        s.index.names = keys
        if not by_lead:
            s = s.sort_values(["CSI %", "ทายถูก %"], ascending=False)
        out.append(s)
    return tuple(out)
