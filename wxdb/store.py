"""คลังข้อมูลบน GitHub Releases (ไม่มีค่าใช้จ่ายสำหรับ repository สาธารณะ)

โครงสร้าง
  release "db-catalog"   ตารางมิติ (<table>.parquet) และ manifest.json (รายการไฟล์ทั้งหมดพร้อมสถิติ)
  release "db-YYYY-MM"   ตารางข้อเท็จจริงของเดือนนั้น
      ระหว่างเดือน: ไฟล์รายวัน   <table>__YYYY-MM-DD[_<ส่วนย่อย>].parquet
      หลังรวมรายเดือน: ไฟล์เดียว  <table>__YYYY-MM.parquet  (ตัดรายการซ้ำตามคีย์หลักแล้ว)

ไฟล์ Parquet บีบอัด zstd เรียงตามคีย์หลัก — DuckDB อ่านผ่าน HTTP range request เฉพาะคอลัมน์/ช่วงที่ต้องการ
การอัปโหลดใช้ gh CLI (ใน GitHub Actions ใช้ GITHUB_TOKEN) ส่วนการอ่านไม่ต้องยืนยันตัวตน
"""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import schema

REPO = os.environ.get("GITHUB_REPOSITORY", "Navavit/consolidated-weather")
BASE_URL = f"https://github.com/{REPO}/releases/download"
CATALOG_TAG = "db-catalog"


# ---------------------------------------------------------------------------- ไฟล์ในเครื่อง
def write_parquet(tbl: pa.Table, path, sort=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if sort:
        tbl = tbl.sort_by([(c, "ascending") for c in sort if c in tbl.column_names])
    pq.write_table(tbl, path, compression="zstd", compression_level=9, row_group_size=250_000,
                   use_dictionary=True, write_statistics=True)
    return path


def write_fact(df, table, outdir, suffix=""):
    """เขียนตารางข้อเท็จจริงแยกไฟล์ตามวันของคอลัมน์เวลาแบ่งไฟล์ → [(month, path)]"""
    if df is None or not len(df):
        return []
    tcol = schema.PARTITION_TIME[table]
    df = df.copy()
    pk = [c for c in schema.TABLES[table]["pk"] if c in df]
    df = df.drop_duplicates(pk, keep="first")
    day = pd.to_datetime(df[tcol]).dt.strftime("%Y-%m-%d")
    out = []
    for d, g in df.groupby(day):
        name = f"{table}__{d}{('_' + suffix) if suffix else ''}.parquet"
        out.append((d[:7], write_parquet(schema.conform(g, table), Path(outdir) / name, sort=pk)))
    return out


def file_stats(path, table):
    t = pq.read_table(path)
    tcol = schema.PARTITION_TIME.get(table)
    st = {"table": table, "name": Path(path).name, "rows": t.num_rows, "bytes": Path(path).stat().st_size,
          "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]}
    if tcol and t.num_rows:
        c = t.column(tcol)
        st["tmin"], st["tmax"] = str(pa.compute.min(c).as_py()), str(pa.compute.max(c).as_py())
    return st


# ---------------------------------------------------------------------------- GitHub Releases
def gh(*args, check=True, capture=True):
    for attempt in range(4):
        r = subprocess.run(["gh", *args], capture_output=capture, text=True)
        if r.returncode == 0 or not check:
            return r
        if attempt < 3 and any(s in (r.stderr or "") for s in ("502", "503", "timeout", "EOF", "connection reset")):
            time.sleep(10 * (attempt + 1))
            continue
        raise RuntimeError(f"gh {' '.join(args[:3])}: {r.stderr.strip()[:300]}")


def ensure_release(tag, title, notes):
    if gh("release", "view", tag, "-R", REPO, check=False).returncode != 0:
        gh("release", "create", tag, "-R", REPO, "--title", title, "--notes", notes, "--prerelease")


def month_release(month):
    ensure_release(f"db-{month}", f"ฐานข้อมูล {month}",
                   f"ตารางข้อเท็จจริงของเดือน {month} (Parquet, zstd) — โครงสร้างและวิธีใช้: docs/DATABASE.md\n\n"
                   "ไฟล์ <table>__YYYY-MM-DD*.parquet เป็นไฟล์รายวันระหว่างเดือน และจะถูกรวมเป็น <table>__YYYY-MM.parquet เมื่อสิ้นเดือน")
    return f"db-{month}"


def upload(tag, paths):
    paths = [str(p) for p in paths]
    for i in range(0, len(paths), 20):
        gh("release", "upload", tag, *paths[i:i + 20], "-R", REPO, "--clobber")


def list_assets(tag):
    r = gh("api", f"repos/{REPO}/releases/tags/{tag}", "--jq", "[.assets[]|{name,size,id}]", check=False)
    return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else []


def delete_asset(tag, name):
    gh("release", "delete-asset", tag, name, "-R", REPO, "--yes", check=False)


def download(tag, name, dest):
    """ดาวน์โหลดไฟล์จาก release (URL สาธารณะ ไม่ต้องยืนยันตัวตน)"""
    import requests
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(f"{BASE_URL}/{tag}/{name}", stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return dest


# ---------------------------------------------------------------------------- manifest
def load_manifest(fresh=None):
    """อ่าน manifest — ฝั่งเขียน (มี gh) อ่านผ่าน API เพื่อให้ได้ฉบับล่าสุดเสมอ
    ส่วน URL สาธารณะของ release อาจถูกแคชไว้หลายนาทีหลังแทนที่ไฟล์"""
    import requests
    if fresh is None:
        fresh = shutil.which("gh") is not None and os.environ.get("WXDB_READONLY") != "1"
    if fresh:
        ids = [x["id"] for x in list_assets(CATALOG_TAG) if x["name"] == "manifest.json"]
        if not ids:
            return {"files": []}
        r = gh("api", f"repos/{REPO}/releases/assets/{ids[0]}", "-H", "Accept: application/octet-stream")
        return json.loads(r.stdout)
    r = requests.get(f"{BASE_URL}/{CATALOG_TAG}/manifest.json", timeout=60)
    if r.status_code == 404:
        return {"files": []}
    r.raise_for_status()
    return r.json()


def save_manifest(man):
    man["generated"] = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    man["repo"], man["base_url"] = REPO, BASE_URL
    man["files"] = sorted(man["files"], key=lambda f: (f["table"], f["release"], f["name"]))
    tot = {}
    for f in man["files"]:
        t = tot.setdefault(f["table"], {"files": 0, "rows": 0, "bytes": 0})
        t["files"] += 1
        t["rows"] += f.get("rows", 0)
        t["bytes"] += f.get("bytes", 0)
    man["totals"] = tot
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "manifest.json"
        p.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
        upload(CATALOG_TAG, [p])


def register(man, tag, paths_tables, drop_names=()):
    """เพิ่ม/แทนที่รายการไฟล์ใน manifest (ยังไม่บันทึก)"""
    names = {Path(p).name for p, _ in paths_tables} | set(drop_names)
    man["files"] = [f for f in man["files"] if not (f["release"] == tag and f["name"] in names)]
    for p, table in paths_tables:
        man["files"].append({**file_stats(p, table), "release": tag})
    return man


def publish_facts(parts, man=None):
    """parts = [(table, month, path)] → อัปโหลดไปยัง release ของแต่ละเดือนและปรับ manifest"""
    man = man if man is not None else load_manifest()
    by_month = {}
    for table, month, path in parts:
        by_month.setdefault(month, []).append((path, table))
    for month, items in sorted(by_month.items()):
        tag = month_release(month)
        upload(tag, [p for p, _ in items])
        register(man, tag, items)
    save_manifest(man)
    return man


def publish_dims(dims, outdir, man=None):
    """dims = {table: DataFrame} → db-catalog/<table>.parquet"""
    ensure_release(CATALOG_TAG, "ฐานข้อมูล: แค็ตตาล็อกและตารางมิติ",
                   "ตารางมิติ (source, model, variable, site, grid_cell, run) และ manifest.json ของทุกไฟล์ในฐานข้อมูล\n\n"
                   "โครงสร้างและวิธีใช้: docs/DATABASE.md")
    paths = [(write_parquet(schema.conform(df, t), Path(outdir) / f"{t}.parquet", sort=schema.TABLES[t]["pk"]), t)
             for t, df in dims.items()]
    upload(CATALOG_TAG, [p for p, _ in paths])
    man = man if man is not None else load_manifest()
    register(man, CATALOG_TAG, paths)
    save_manifest(man)
    return man
