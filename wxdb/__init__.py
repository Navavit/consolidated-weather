"""wxdb — ฐานข้อมูลอุตุนิยมวิทยาและแบบจำลองพยากรณ์อากาศของประเทศไทย (Consolidated Weather)

ใช้งาน (ไม่ต้องดาวน์โหลดทั้งฐานข้อมูล — DuckDB อ่านเฉพาะคอลัมน์และช่วงที่ต้องการผ่าน HTTP):

    import wxdb
    con = wxdb.connect()                      # สร้าง view ของทุกตารางจาก manifest
    con.sql("SELECT * FROM site WHERE network = 'tmd'").df()

โครงสร้างตาราง: wxdb/schema.py และ docs/DATABASE.md
"""


def connect(tables=None, months=None, cache_dir=None):
    """คืน duckdb connection ที่มี view ชื่อเดียวกับตาราง

    tables: จำกัดเฉพาะบางตาราง · months: จำกัดเดือนของตารางข้อเท็จจริง เช่น ["2026-10"]
    cache_dir: ถ้าระบุ จะดาวน์โหลดไฟล์มาเก็บไว้ในเครื่องก่อน (ใช้ซ้ำโดยไม่ต้องดาวน์โหลดใหม่)
    """
    from pathlib import Path

    import duckdb

    from . import store
    man = store.load_manifest(fresh=False)
    con = duckdb.connect()
    con.sql("INSTALL httpfs; LOAD httpfs; SET enable_progress_bar = false;")
    by_table = {}
    for f in man["files"]:
        if tables and f["table"] not in tables:
            continue
        if months and f["release"] != store.CATALOG_TAG and f["release"].removeprefix("db-") not in months:
            continue
        url = f"{man['base_url']}/{f['release']}/{f['name']}"
        if cache_dir:
            p = Path(cache_dir) / f["release"] / f["name"]
            if not p.exists():
                store.download(f["release"], f["name"], p)
            url = str(p)
        by_table.setdefault(f["table"], []).append(url)
    for t, urls in by_table.items():
        lst = ", ".join(f"'{u}'" for u in urls)
        con.sql(f"CREATE VIEW {t} AS SELECT * FROM read_parquet([{lst}], union_by_name = true)")
    return con
