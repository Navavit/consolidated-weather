"""ความแม่นแบบ "มุมผู้ใช้": พยากรณ์ที่หน้าเว็บแสดง ณ เวลาที่เปิดดู แม่นแค่ไหน

ต่างจากมาตรฐาน WMO (นับจากเวลาเริ่มรัน) ตรงที่
  - ช่วงเวลาเหมือนตารางบนหน้าเว็บทุกประการ: +3, +6, +12, +24 ชม. นับจากเวลาที่ดึง (ปัดลงเป็นชั่วโมง)
    และวันอุตุนิยมวิทยา D+1, D+3, D+7 (07:00 → 07:00 น.)
  - ใช้พยากรณ์ที่ "มีให้ใช้จริง" ณ เวลานั้น (รวมความช้าในการส่งผลของแต่ละโมเดล และ Google)
  - ให้คะแนนสิ่งที่ผู้ใช้เห็นด้วย: ค่ากลางทุกโมเดล และโอกาสฝนจาก ensemble
ค่าวัด
  - เครื่องวัดโทรมาตรจุดตรวจ: ฝนรายชั่วโมง (รวมเป็นช่วงใดก็ได้) · รายวันใช้ฝน 24 ชม. ถึง 07 น.
  - สถานีอุตุฯ จุดตรวจ: ฝนราย 3 ชม. (ใช้เวลาเริ่ม 01, 04, 07, … น. ให้ตรงรอบวัด) · รายวันใช้สรุป 07 น.
  โทรมาตรใช้ทุกชั่วโมงที่ดึง ส่วนสถานีอุตุฯ ใช้เฉพาะรอบที่เริ่ม 01, 04, 07, … น. (ตรงรอบวัด 3 ชม.)
เกณฑ์ "ฝนตก" = ≥ 1 มม. ในช่วงนั้น (เหมือนหน้าเว็บ) · "ฝนหนัก" = +3/+6 ชม. ≥ 10 มม., +12/+24 ชม. ≥ 20 มม., รายวัน ≥ 35 มม.

เก็บเป็นยอดรวมรายวัน (ไม่เก็บทุกคู่) ที่ archive/verification/usr/YYYY-MM.csv.gz
"""
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

import weather_core as core
import weather_verify as v

WINDOWS = [("+3", 3), ("+6", 6), ("+12", 12), ("+24", 24), ("D+1", None), ("D+3", None), ("D+7", None)]
HEAVY = {"+3": 10, "+6": 10, "+12": 20, "+24": 20, "D+1": 35, "D+3": 35, "D+7": 35}
MEDIAN = "ค่ากลางทุกโมเดล"
ENS = "โอกาสฝน (ensemble)"
TOD = {h: ("เช้า" if 7 <= h < 13 else "บ่าย" if 13 <= h < 19 else "ค่ำ" if h >= 19 or h < 1 else "ดึก") for h in range(24)}
KEYS = ["date", "loc", "src", "window", "tod", "model", "thr"]
COUNTS = ["hits", "misses", "fa", "cn", "abs_err"]


def _series(df, value):
    return {sid: g.drop_duplicates("time", keep="last").set_index("time")[value].sort_index()
            for sid, g in df.groupby("id")} if len(df) else {}


def _daily_at7(df, value):
    """ค่าวันละ 1 ค่าที่รายงานใกล้ 07:00 น. ที่สุด (±2 ชม.) → index = 07:00 ของวันนั้น"""
    if df.empty:
        return {}
    df = df.copy()
    df["end"] = df["time"].dt.normalize() + pd.Timedelta(hours=7)
    df["off"] = (df["time"] - df["end"]).abs()
    df = df[df["off"] <= pd.Timedelta(hours=2)]
    df = df.loc[df.groupby(["id", "end"])["off"].idxmin()]
    return {sid: g.set_index("end")[value] for sid, g in df.groupby("id")}


class Obs:
    def __init__(self, archive):
        self.tw1h = _series(v.load_obs(archive, "tw1h"), "rain_1h")
        self.tmd3h = _series(v.load_obs(archive, "tmd3h"), "rain_3h")
        self.tw_day = _daily_at7(v.load_obs(archive, "thaiwater"), "rain_24h")
        self.tmd_day = _daily_at7(v.load_obs(archive, "tmdday"), "rain_24h")

    def window(self, kind, sid, start, end):
        """ฝนที่วัดได้ใน (start, end] หรือ None ถ้าไม่ครบ"""
        sid = str(sid)
        if kind == "tw":
            s = self.tw1h.get(sid)
            if s is not None:
                h = s.reindex(pd.date_range(start + pd.Timedelta(hours=1), end, freq="h"))
                if h.notna().all():
                    return float(h.sum())
        else:
            s = self.tmd3h.get(sid)
            if s is not None:
                h = s.reindex(pd.date_range(start + pd.Timedelta(hours=3), end, freq="3h"))
                if h.notna().all():
                    return float(h.sum())
        if end - start == pd.Timedelta(hours=24) and end.hour == 7:
            d = (self.tw_day if kind == "tw" else self.tmd_day).get(sid)
            if d is not None and end in d.index and pd.notna(d[end]):
                return float(d[end])
        return None


def _windows_for(F, h0=7):
    today = (F - pd.Timedelta(hours=h0)).normalize() + pd.Timedelta(hours=h0)
    for label, n in WINDOWS:
        if n:
            yield label, F, F + pd.Timedelta(hours=n)
        else:
            d = int(label[2:])
            s = today + pd.Timedelta(days=d)
            yield label, s, s + pd.Timedelta(days=1)


def compute(archive, points, cfg=None, dates=None):
    """ยอดรวมรายวัน (วันที่ช่วงวัดสิ้นสุด) · dates = set ของวันที่ที่จะคำนวณ (None = ทุกวันที่ทำได้)"""
    cfg = cfg or core.CFG
    thr = float(cfg["rain_threshold_mm"])
    h0 = int(cfg.get("day_start_hour", 7))
    obs = Obs(archive)
    acc = defaultdict(lambda: np.zeros(len(COUNTS) + 1))                # + n
    rel = defaultdict(lambda: np.zeros(2))                              # ความน่าเชื่อถือของ %: (n, ฝนตกจริง)
    for p in points:
        kind = p["kind"]
        for fetched, wide, _ in v.load_point_runs(archive, p["lat"], p["lon"]):
            F = pd.Timestamp(fetched).floor("h")
            if kind == "tmd" and F.hour % 3 != 1:                      # สถานีอุตุฯ วัดราย 3 ชม. ที่ 01, 04, 07, … น.
                continue
            det = [c for c in wide.columns if c.startswith("det_mm|")]
            ens = [c for c in wide.columns if c.startswith("ens_prob|")]
            for label, start, end in _windows_for(F, h0):
                date = (end - pd.Timedelta(seconds=1)).date()
                if dates is not None and date not in dates:
                    continue
                ob = obs.window(kind, p["id"], start, end)
                if ob is None:
                    continue
                hours = list(pd.date_range(start + pd.Timedelta(hours=1), end, freq="h"))
                fcs = {c.split("|", 1)[1]: _sum(wide, c, hours) for c in det}
                fcs = {m: x for m, x in fcs.items() if x is not None}
                if len(fcs) >= 3:
                    fcs[MEDIAN] = float(np.median(list(fcs.values())))
                tod = TOD.get(start.hour, "all") if label.startswith("+") else "all"
                for m, fc in fcs.items():
                    for tname, t in (("rain", thr), ("heavy", HEAVY[label])):
                        _add(acc, (date, p["for"], kind, label, tod, m, tname), fc, ob, t, err=tname == "rain")
                # โอกาสฝน ensemble: เฉพาะ +3 ชม. (ค่าเก็บไว้เป็นช่วง 3 ชม. ที่ชั่วโมงกลาง)
                c = start + pd.Timedelta(hours=2)
                if label == "+3" and ens and c in wide.index:
                    pr = wide.loc[c, ens].astype(float).dropna()
                    if len(pr):
                        prob = float(pr.mean())
                        _add(acc, (date, p["for"], kind, label, tod, ENS, "rain"), prob, ob, thr, fc_event=prob >= 50)
                        b = min(int(prob // 20), 4)
                        rel[(date, p["for"], kind, b)] += (1, ob >= thr)
    rows = [dict(zip(KEYS, k), **dict(zip(COUNTS + ["n"], vals))) for k, vals in acc.items()]
    rel_rows = [{"date": k[0], "loc": k[1], "src": k[2], "bin": k[3], "n": n, "n_rain": r} for k, (n, r) in rel.items()]
    return pd.DataFrame(rows, columns=KEYS + COUNTS + ["n"]), pd.DataFrame(rel_rows, columns=["date", "loc", "src", "bin", "n", "n_rain"])


def _sum(wide, col, hours):
    x = wide[col].reindex(hours)
    return None if x.isna().any() else float(x.sum())


def _add(acc, key, fc, ob, t, err=False, fc_event=None):
    f, o = (fc >= t) if fc_event is None else fc_event, ob >= t
    a = acc[key]
    a[0] += f and o
    a[1] += (not f) and o
    a[2] += f and not o
    a[3] += (not f) and not o
    if err:
        a[4] += abs(fc - ob)
    a[5] += 1


def update(archive, points, cfg=None):
    """คำนวณวันที่ยังไม่มี + เมื่อวาน/วันนี้ (ยังไม่ครบ) แล้วเขียนทับเฉพาะวันที่นั้น"""
    base = Path(archive) / "verification" / "usr"
    base.mkdir(parents=True, exist_ok=True)
    have = set()
    for f in base.glob("counts_*.csv.gz"):
        have |= set(pd.read_csv(f, usecols=["date"])["date"].astype(str))
    today = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None).normalize()
    since = today - pd.Timedelta(days=v.LOOKBACK_DAYS + 1)
    todo = {d.date() for d in pd.date_range(since, today + pd.Timedelta(days=8))
            if d >= today - pd.Timedelta(days=1) or str(d.date()) not in have}
    counts, rel = compute(archive, points, cfg, todo)
    for name, df in (("counts", counts), ("rel", rel)):
        if df.empty:
            continue
        df["date"] = df["date"].astype(str)
        for month, g in df.groupby(df["date"].str[:7]):
            path = base / f"{name}_{month}.csv.gz"
            if path.exists():
                old = pd.read_csv(path)
                old = old[~old["date"].astype(str).isin(set(g["date"]))]
                g = pd.concat([old, g], ignore_index=True)
            g.sort_values(list(g.columns[:4])).to_csv(path, index=False, compression="gzip")
    return len(counts)


def _scores(c):
    h, m, fa, cn, n = c["hits"], c["misses"], c["fa"], c["cn"], c["n"]
    r = lambda a, b: round(10 * a / b, 1) if b else None                 # "x ใน 10 ครั้ง"
    return {"n": int(n), "n_rain": int(h + m), "n_said": int(h + fa),
            "sr10": r(h, h + fa), "pod10": r(h, h + m), "acc": round(100 * (h + cn) / n, 0) if n else None,
            "csi": round(100 * h / (h + m + fa), 1) if h + m + fa else None}


def summary(archive, days=30):
    """สรุปสำหรับหน้าเว็บ: ต่อ (ตำแหน่ง | ทุกตำแหน่ง) × ช่วง → ตารางโมเดล + ฝนหนัก + ช่วงของวัน + ความน่าเชื่อถือของ %"""
    base = Path(archive) / "verification" / "usr"
    files = sorted(base.glob("counts_*.csv.gz"))
    if not files:
        return None
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    since = (pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None) - pd.Timedelta(days=days)).date()
    df = df[pd.to_datetime(df["date"]).dt.date >= since]
    rel = pd.concat([pd.read_csv(f) for f in sorted(base.glob("rel_*.csv.gz"))], ignore_index=True) \
        if list(base.glob("rel_*.csv.gz")) else pd.DataFrame(columns=["date", "loc", "bin", "n", "n_rain"])
    rel = rel[pd.to_datetime(rel["date"]).dt.date >= since]
    out = {"days": days, "since": str(df["date"].min()) if len(df) else None,
           "windows": [w for w, _ in WINDOWS], "median": MEDIAN, "ens": ENS, "heavy_mm": HEAVY,
           "scopes": {}}
    for scope, g in [("ทุกตำแหน่ง", df)] + list(df.groupby("loc")):
        sc = {}
        for w in out["windows"]:
            gw = g[g["window"] == w]
            if gw.empty:
                continue
            rain = gw[gw["thr"] == "rain"].groupby("model")[COUNTS + ["n"]].sum()
            heavy = gw[gw["thr"] == "heavy"].groupby("model")[COUNTS + ["n"]].sum()
            rows = []
            for m, c in rain.iterrows():
                r = {"model": m, **_scores(c), "mae": round(c["abs_err"] / c["n"], 1) if c["n"] and m != ENS else None}
                if m in heavy.index:
                    hs = _scores(heavy.loc[m])
                    r.update(heavy_pod10=hs["pod10"], heavy_sr10=hs["sr10"], heavy_n=hs["n_rain"])
                rows.append(r)
            rows.sort(key=lambda r: -(r["csi"] or 0))
            tod = {}
            gm = gw[(gw["thr"] == "rain") & (gw["model"] == MEDIAN) & (gw["tod"] != "all")]
            for t, c in gm.groupby("tod")[COUNTS + ["n"]].sum().iterrows():
                tod[t] = _scores(c)
            sc[w] = {"rows": rows, "tod": tod}
        rg = rel if scope == "ทุกตำแหน่ง" else rel[rel["loc"] == scope]
        rb = rg.groupby("bin")[["n", "n_rain"]].sum()
        sc["_reliability"] = [{"bin": int(b), "lo": int(b) * 20, "hi": int(b) * 20 + 20, "n": int(r.n),
                               "freq": round(100 * r.n_rain / r.n) if r.n else None} for b, r in rb.iterrows()]
        out["scopes"][scope] = sc
    return out
