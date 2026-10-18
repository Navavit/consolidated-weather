"""คำสั่งของฐานข้อมูล

  python -m wxdb catalog                       เผยแพร่ตารางมิติคงที่ (source, model, variable, grid_cell)
  python -m wxdb etl --archive archive [--day YYYY-MM-DD | --from D1 --to D2]
                                               แปลงคลังรายชั่วโมงของวัน (ค่าเริ่มต้น: 2 วันก่อน) เข้าฐานข้อมูล
  python -m wxdb grid [--days 3]               ดึงพยากรณ์กริดรอบที่ยังขาดในช่วง N วันล่าสุด
  python -m wxdb pack [--month YYYY-MM]        รวมไฟล์รายวันของเดือน (ค่าเริ่มต้น: เดือนก่อน) เป็นไฟล์รายเดือน
  python -m wxdb prune --archive archive [--keep-days 35]
                                               ลบไฟล์ runs/ ที่เก่ากว่า N วันและอยู่ในฐานข้อมูลแล้ว ออกจากคลังรายชั่วโมง
  python -m wxdb status                        สรุปขนาดและช่วงเวลาของแต่ละตาราง
"""
import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

from . import catalog, etl, grid, schema, store


def _load_dim(name, workdir):
    try:
        return pd.read_parquet(store.download(store.CATALOG_TAG, f"{name}.parquet", Path(workdir) / f"old_{name}.parquet"))
    except Exception:                                   # noqa: BLE001 — ยังไม่มีตาราง (รอบแรก)
        return None


def _merge_runs(old, new):
    if new is None or not len(new):
        return old
    df = pd.concat([x for x in (old, new) if x is not None and len(x)], ignore_index=True)
    df["init_time"] = pd.to_datetime(df.init_time)
    df["first_fetched"] = pd.to_datetime(df.first_fetched)
    df = df.sort_values("first_fetched").drop_duplicates(["model_id", "init_time", "source_id"], keep="first")
    return df


def cmd_catalog(a):
    with tempfile.TemporaryDirectory() as d:
        man = store.publish_dims({"source": catalog.sources(), "model": catalog.models(), "variable": catalog.variables(),
                                  "grid_cell": catalog.grid_cells()}, d)
    print("db-catalog:", ", ".join(f"{f['name']} ({f['rows']})" for f in man["files"] if f["release"] == store.CATALOG_TAG))


def cmd_etl(a):
    if a.day:
        days = [pd.Timestamp(a.day)]
    elif a.start:
        days = list(pd.date_range(a.start, a.end or a.start))
    else:
        last = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None).normalize() - pd.Timedelta(days=2)
        done = set(store.load_manifest().get("etl_days", []))
        days = [d for d in pd.date_range(last - pd.Timedelta(days=6), last) if f"{d:%Y-%m-%d}" not in done]
    with tempfile.TemporaryDirectory() as d:
        sites = _load_dim("site", d)
        runs = _load_dim("run", d)
        site_parts = etl.static_sites(a.archive)
        man = store.load_manifest()
        for day in days:
            try:
                facts, sp, rn = etl.run_day(a.archive, day)
            except Exception as e:                      # noqa: BLE001 — วันที่ไฟล์เสีย: ข้ามและไม่บันทึกใน etl_days
                print(f"{day:%Y-%m-%d}: ผิดพลาด {type(e).__name__}: {e}")
                continue
            parts = []
            for table, df in facts.items():
                for month, path in store.write_fact(df, table, Path(d) / f"{day:%Y%m%d}", suffix=f"etl{day:%Y%m%d}"):
                    parts.append((table, month, path))
            man["etl_days"] = sorted(set(man.get("etl_days", [])) | {f"{day:%Y-%m-%d}"})
            man = store.publish_facts(parts, man) if parts else (store.save_manifest(man) or man)
            site_parts += sp
            runs = _merge_runs(runs, rn)
            print(f"{day:%Y-%m-%d}: " + " · ".join(f"{t} {len(v):,}" for t, v in facts.items()))
        sites = etl.merge_sites(sites, site_parts)
        dims = {"site": sites}
        if runs is not None and len(runs):
            dims["run"] = runs
        store.publish_dims(dims, d, man)
        print(f"site {len(sites):,} จุด · run {0 if runs is None else len(runs):,} รอบ")


def cmd_grid(a):
    with tempfile.TemporaryDirectory() as d:
        runs = _load_dim("run", d)
        have = set()
        if runs is not None and len(runs):
            g = runs[runs.source_id.isin(["ecmwf_opendata", "noaa_nomads"])]
            have = set(zip(g.model_id, pd.to_datetime(g.init_time)))
        todo = grid.due_cycles(have, days=a.days)
        if a.models:
            todo = [t for t in todo if t[0] in a.models]
        if not todo:
            print("พยากรณ์กริดครบทุกรอบแล้ว")
            return
        man, new_runs, failed = store.load_manifest(), [], []
        for model, init in todo:
            try:
                df = grid.fetch_cycle(model, init)
            except Exception as e:                      # noqa: BLE001 — รอบที่ยังไม่เผยแพร่/ขัดข้อง: ลองใหม่รอบหน้า
                failed.append(f"{model} {init:%Y-%m-%d %H}z: {str(e)[:120]}")
                continue
            if df is None or not len(df):
                failed.append(f"{model} {init:%Y-%m-%d %H}z: ไม่มีข้อมูล")
                continue
            parts = [("forecast_grid", m, p) for m, p in
                     store.write_fact(df, "forecast_grid", Path(d) / "grid", suffix=f"{init:%H}z_{model}")]
            man = store.publish_facts(parts, man)
            new_runs.append({"model_id": model, "init_time": init, "first_fetched": pd.Timestamp.now("UTC").tz_localize(None).floor("s"),
                             "source_id": catalog.GRIB_MODELS[model]})
            print(f"{model} {init:%Y-%m-%d %H}z: {len(df):,} แถว")
        if new_runs:
            store.publish_dims({"run": _merge_runs(runs, pd.DataFrame(new_runs))}, d, man)
        for f in failed:
            print("ข้าม:", f)


def cmd_pack(a):
    month = a.month or (pd.Timestamp.now(tz="UTC").tz_localize(None).to_period("M") - 1).strftime("%Y-%m")
    tag = f"db-{month}"
    assets = [x["name"] for x in store.list_assets(tag)]
    man = store.load_manifest()
    with tempfile.TemporaryDirectory() as d:
        for table in schema.PARTITION_TIME:
            daily = sorted(n for n in assets if n.startswith(f"{table}__{month}-") and n.endswith(".parquet"))
            if not daily:
                continue
            frames = [pd.read_parquet(store.download(tag, n, Path(d) / n)) for n in daily]
            packed = f"{table}__{month}.parquet"
            if packed in assets:
                frames.insert(0, pd.read_parquet(store.download(tag, packed, Path(d) / packed)))
            df = pd.concat(frames, ignore_index=True).drop_duplicates(schema.TABLES[table]["pk"], keep="first")
            path = store.write_parquet(schema.conform(df, table), Path(d) / "out" / packed, sort=schema.TABLES[table]["pk"])
            store.upload(tag, [path])
            store.register(man, tag, [(path, table)], drop_names=daily)
            store.save_manifest(man)                    # บันทึกก่อนลบไฟล์รายวัน
            for n in daily:
                store.delete_asset(tag, n)
                Path(d, n).unlink(missing_ok=True)
            print(f"{packed}: {len(df):,} แถว จาก {len(daily)} ไฟล์ · {path.stat().st_size / 1e6:.1f} MB")


def cmd_prune(a):
    """ลบ runs/YYYY/MM/DD ที่เก่ากว่า keep_days และแปลงเข้าฐานข้อมูลแล้ว (อยู่ใน manifest.etl_days)"""
    done = set(store.load_manifest().get("etl_days", []))
    cutoff = pd.Timestamp.now(tz="Asia/Bangkok").tz_localize(None).normalize() - pd.Timedelta(days=a.keep_days)
    removed = 0
    for d in sorted(Path(a.archive, "runs").glob("*/*/*")):
        day = pd.Timestamp("-".join(d.parts[-3:]))
        if day < cutoff and f"{day:%Y-%m-%d}" in done:
            shutil.rmtree(d)
            removed += 1
    print(f"ลบ runs รายวัน {removed} โฟลเดอร์ (เก่ากว่า {cutoff:%Y-%m-%d} และอยู่ในฐานข้อมูลแล้ว)")


def cmd_status(a):
    man = store.load_manifest()
    print(f"ปรับปรุง {man.get('generated', '–')}")
    for t, v in sorted(man.get("totals", {}).items()):
        fs = [f for f in man["files"] if f["table"] == t and f.get("tmin")]
        span = f"{min(f['tmin'] for f in fs)[:10]} → {max(f['tmax'] for f in fs)[:10]}" if fs else ""
        print(f"  {t:18s} {v['files']:4d} ไฟล์ {v['rows']:>13,} แถว {v['bytes'] / 1e6:9.1f} MB  {span}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="wxdb", description="ฐานข้อมูลอุตุนิยมวิทยาและแบบจำลองพยากรณ์อากาศของประเทศไทย")
    s = p.add_subparsers(dest="cmd", required=True)
    s.add_parser("catalog").set_defaults(fn=cmd_catalog)
    e = s.add_parser("etl")
    e.add_argument("--archive", default="archive")
    e.add_argument("--day")
    e.add_argument("--from", dest="start")
    e.add_argument("--to", dest="end")
    e.set_defaults(fn=cmd_etl)
    g = s.add_parser("grid")
    g.add_argument("--days", type=int, default=3)
    g.add_argument("--models", nargs="*")
    g.set_defaults(fn=cmd_grid)
    k = s.add_parser("pack")
    k.add_argument("--month")
    k.set_defaults(fn=cmd_pack)
    r = s.add_parser("prune")
    r.add_argument("--archive", default="archive")
    r.add_argument("--keep-days", type=int, default=35)
    r.set_defaults(fn=cmd_prune)
    s.add_parser("status").set_defaults(fn=cmd_status)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
