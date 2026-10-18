"""นิยามโครงสร้างฐานข้อมูล (schema) — แหล่งอ้างอิงเดียวของตาราง คอลัมน์ ชนิดข้อมูล คีย์ และความสัมพันธ์

หลักการออกแบบ
  • แบบจำลองดาว (star schema): ตารางมิติ (dimension) ขนาดเล็กอธิบาย "สิ่งที่ถูกวัด/พยากรณ์"
    และตารางข้อเท็จจริง (fact) ขนาดใหญ่เก็บ "ค่า" โดยอ้างอิงมิติผ่านคีย์
  • เวลาทั้งหมดเป็น UTC (timestamp ไม่มีเขตเวลา) — แปลงเป็นเวลาไทยด้วย +7 ชม. เมื่อแสดงผล
  • หน่วยเป็นหน่วย SI ที่ใช้ในอุตุนิยมวิทยา (°C, %, มม., ม./วินาที, W/m², hPa) ตามตาราง variable
  • ตารางกริดเก็บแบบกว้าง (หนึ่งคอลัมน์ต่อตัวแปร) เพราะทุกโมเดลมีตัวแปรชุดเดียวกันและมีปริมาณมาก
    ตารางค่าตรวจวัดและพยากรณ์รายจุดเก็บแบบยาว (var_id, value) เพราะแต่ละเครือข่ายมีตัวแปรต่างกัน
  • ค่าสะสม (ฝน, รังสี) ระบุช่วงสะสมด้วย period_h / step_h เสมอ: ค่าเป็นผลรวม (หรือค่าเฉลี่ย) ของช่วง
    (valid_time − period_h, valid_time]
"""
import pyarrow as pa

TS = pa.timestamp("s")

# ชื่อตาราง → นิยาม: kind (dim/fact), คำอธิบาย, คีย์หลัก, คีย์นอก {คอลัมน์: "ตาราง.คอลัมน์"}, คอลัมน์ [(ชื่อ, ชนิด, คำอธิบาย)]
TABLES = {
    # ------------------------------------------------------------------ มิติ
    "source": {
        "kind": "dim", "desc": "แหล่งข้อมูล (หน่วยงาน บริการ หรือชุดข้อมูล) พร้อมสัญญาอนุญาต",
        "pk": ["source_id"], "fk": {},
        "cols": [("source_id", pa.string(), "รหัสแหล่งข้อมูล เช่น tmd, thaiwater, ecmwf_opendata"),
                 ("name", pa.string(), "ชื่อแหล่งข้อมูล"),
                 ("agency", pa.string(), "หน่วยงานผู้ผลิตข้อมูล"),
                 ("kind", pa.string(), "ประเภท: station_network | model_api | model_grib | model_image | reanalysis | satellite | crowd"),
                 ("url", pa.string(), "ที่อยู่บริการ"),
                 ("license", pa.string(), "สัญญาอนุญาต/เงื่อนไขการใช้"),
                 ("time_res", pa.string(), "ความละเอียดเชิงเวลา"),
                 ("notes", pa.string(), "หมายเหตุ")]},
    "model": {
        "kind": "dim", "desc": "แบบจำลองพยากรณ์อากาศเชิงตัวเลข (NWP) แบบจำลองปัญญาประดิษฐ์ และระบบ ensemble",
        "pk": ["model_id"], "fk": {"source_id": "source.source_id"},
        "cols": [("model_id", pa.string(), "รหัสแบบจำลอง เช่น ecmwf_ifs"),
                 ("name", pa.string(), "ชื่อแบบจำลอง เช่น ECMWF IFS"),
                 ("agency", pa.string(), "หน่วยงานผู้พัฒนา"),
                 ("kind", pa.string(), "det (deterministic) | ens (ensemble) | regional"),
                 ("physics", pa.string(), "Physics | AI/ML"),
                 ("grid_km", pa.float32(), "ระยะห่างกริดโดยประมาณ (กม.)"),
                 ("members", pa.int16(), "จำนวนสมาชิก ensemble (det = 1)"),
                 ("source_id", pa.string(), "ช่องทางที่ระบบนี้ใช้ดึงข้อมูล")]},
    "variable": {
        "kind": "dim", "desc": "ตัวแปรอุตุนิยมวิทยา หน่วย และวิธีสรุปค่าเชิงเวลา (CF cell_methods)",
        "pk": ["var_id"], "fk": {},
        "cols": [("var_id", pa.string(), "รหัสตัวแปร เช่น t2m, precip"),
                 ("name_th", pa.string(), "ชื่อภาษาไทย"),
                 ("name_en", pa.string(), "ชื่อภาษาอังกฤษ"),
                 ("unit", pa.string(), "หน่วย"),
                 ("cell_method", pa.string(), "point | sum | mean | max | min (ตลอดช่วง period_h)"),
                 ("cf_name", pa.string(), "CF standard name (ถ้ามี)")]},
    "site": {
        "kind": "dim", "desc": "จุดตรวจวัดหรือจุดพยากรณ์: สถานีอุตุนิยมวิทยา โทรมาตร จุดผู้ใช้ และจุดตัวแทนเขต",
        "pk": ["site_id"], "fk": {"source_id": "source.source_id", "cell_id": "grid_cell.cell_id"},
        "cols": [("site_id", pa.string(), "รหัสจุด <เครือข่าย>:<รหัสเดิม> เช่น tmd:48455, tw:1, bkkdist:1001"),
                 ("network", pa.string(), "เครือข่าย: tmd | tw | air4thai | ghcnh | user | bkkdist | prov | loc"),
                 ("native_id", pa.string(), "รหัสจุดของหน่วยงานต้นทาง"),
                 ("name", pa.string(), "ชื่อจุด"),
                 ("province", pa.string(), "จังหวัด"),
                 ("agency", pa.string(), "หน่วยงานเจ้าของจุด"),
                 ("lat", pa.float64(), "ละติจูด (WGS84)"),
                 ("lon", pa.float64(), "ลองจิจูด (WGS84)"),
                 ("elev_m", pa.float32(), "ความสูงจากระดับทะเล (ม.)"),
                 ("cell_id", pa.int32(), "เซลล์กริด th025 ที่ใกล้ที่สุด (เชื่อมค่าตรวจวัดกับพยากรณ์กริด)"),
                 ("source_id", pa.string(), "แหล่งข้อมูลของจุด"),
                 ("first_seen", TS, "เวลาที่พบในระบบครั้งแรก (UTC)"),
                 ("last_seen", TS, "เวลาที่พบในระบบล่าสุด (UTC)")]},
    "grid_cell": {
        "kind": "dim", "desc": "เซลล์ของกริด th025 (0.25°) ครอบคลุมประเทศไทยและแนวกันชน 0.25°",
        "pk": ["cell_id"], "fk": {},
        "cols": [("cell_id", pa.int32(), "row × 37 + col ในกรอบ 21°N–5°N, 97°E–106°E"),
                 ("row", pa.int16(), "แถว (0 = 21°N)"),
                 ("col", pa.int16(), "คอลัมน์ (0 = 97°E)"),
                 ("lat", pa.float32(), "ละติจูดจุดศูนย์กลาง"),
                 ("lon", pa.float32(), "ลองจิจูดจุดศูนย์กลาง"),
                 ("in_thailand", pa.bool_(), "จุดศูนย์กลางอยู่ภายในประเทศไทย"),
                 ("province_th", pa.string(), "จังหวัด (หรือจังหวัดที่ใกล้ที่สุด)"),
                 ("province_en", pa.string(), "จังหวัด (อังกฤษ)")]},
    "run": {
        "kind": "dim", "desc": "รอบการประมวลผลของแบบจำลอง (forecast cycle) ที่ระบบได้รับ",
        "pk": ["model_id", "init_time"], "fk": {"model_id": "model.model_id", "source_id": "source.source_id"},
        "cols": [("model_id", pa.string(), "แบบจำลอง"),
                 ("init_time", TS, "เวลาเริ่มต้นของรอบ (UTC)"),
                 ("first_fetched", TS, "เวลาที่ระบบได้รับรอบนี้ครั้งแรก (UTC) — ใช้ประเมินความล่าช้าในการเผยแพร่"),
                 ("source_id", pa.string(), "ช่องทางที่ได้รับ")]},
    # ------------------------------------------------------------------ ข้อเท็จจริง
    "forecast_grid": {
        "kind": "fact", "desc": "พยากรณ์บนกริด th025 จากไฟล์ GRIB ของศูนย์พยากรณ์ (รอบ 00/12 UTC, ล่วงหน้า 0–168 ชม.)",
        "pk": ["model_id", "init_time", "lead_h", "cell_id"],
        "fk": {"model_id": "model.model_id", "cell_id": "grid_cell.cell_id", "(model_id, init_time)": "run.(model_id, init_time)"},
        "cols": [("model_id", pa.string(), "แบบจำลอง"),
                 ("init_time", TS, "เวลาเริ่มต้นของรอบ (UTC)"),
                 ("lead_h", pa.int16(), "ระยะล่วงหน้า (ชม.)"),
                 ("valid_time", TS, "เวลาที่พยากรณ์ (UTC) = init_time + lead_h"),
                 ("step_h", pa.int8(), "ช่วงสะสมของ precip และ ssrd (ชม.) — 3 หรือ 6"),
                 ("cell_id", pa.int32(), "เซลล์กริด"),
                 ("t2m", pa.float32(), "อุณหภูมิอากาศที่ 2 ม. (°C)"),
                 ("td2m", pa.float32(), "อุณหภูมิจุดน้ำค้างที่ 2 ม. (°C)"),
                 ("rh2m", pa.float32(), "ความชื้นสัมพัทธ์ที่ 2 ม. (%)"),
                 ("precip", pa.float32(), "ฝนสะสมในช่วง step_h ชม. ก่อน valid_time (มม.)"),
                 ("u10", pa.float32(), "ลมองค์ประกอบตะวันออก–ตะวันตกที่ 10 ม. (ม./วินาที)"),
                 ("v10", pa.float32(), "ลมองค์ประกอบเหนือ–ใต้ที่ 10 ม. (ม./วินาที)"),
                 ("tcc", pa.float32(), "เมฆปกคลุมทั้งหมด (%)"),
                 ("ssrd", pa.float32(), "รังสีคลื่นสั้นขาลงที่ผิวโลก เฉลี่ยในช่วง step_h (W/m²)")]},
    "forecast_point": {
        "kind": "fact", "desc": "พยากรณ์รายชั่วโมง ณ จุด จาก Open-Meteo / TMD WRF / Google (เก็บค่าแรกที่ได้รับของแต่ละรอบ)",
        "pk": ["model_id", "init_time", "site_id", "valid_time", "var_id"],
        "fk": {"model_id": "model.model_id", "site_id": "site.site_id", "var_id": "variable.var_id"},
        "cols": [("model_id", pa.string(), "แบบจำลอง"),
                 ("init_time", TS, "เวลาเริ่มต้นของรอบ (UTC; ประมาณจาก metadata เมื่อแหล่งไม่ระบุ)"),
                 ("fetched_at", TS, "เวลาที่ดึงข้อมูล (UTC)"),
                 ("site_id", pa.string(), "จุด"),
                 ("valid_time", TS, "เวลาที่พยากรณ์ (UTC; สิ้นสุดช่วงสะสม)"),
                 ("lead_h", pa.int16(), "ระยะล่วงหน้า (ชม.)"),
                 ("var_id", pa.string(), "ตัวแปร"),
                 ("value", pa.float32(), "ค่า (หน่วยตาม variable)")]},
    "forecast_district": {
        "kind": "fact", "desc": "พยากรณ์รายวันต่อแบบจำลองรายพื้นที่ (ออกวันละครั้งหลัง 07:00 น.): เขตของกรุงเทพมหานคร (ปรับแก้ความเอนเอียงของ HI) และ 77 จังหวัด (ไม่ปรับแก้)",
        "pk": ["issued", "site_id", "model_id", "date"],
        "fk": {"model_id": "model.model_id", "site_id": "site.site_id"},
        "cols": [("issued", TS, "เวลาออกพยากรณ์ (UTC)"),
                 ("site_id", pa.string(), "จุดตัวแทนพื้นที่ (bkkdist:<รหัสเขต> หรือ prov:<รหัส ISO 3166-2>)"),
                 ("model_id", pa.string(), "แบบจำลอง"),
                 ("date", pa.date32(), "วันที่ (เวลาไทย)"),
                 ("lead_day", pa.int8(), "ระยะล่วงหน้า (วัน)"),
                 ("tmax", pa.float32(), "อุณหภูมิสูงสุด (°C)"),
                 ("tmin", pa.float32(), "อุณหภูมิต่ำสุด (°C)"),
                 ("rh_min", pa.float32(), "ความชื้นสัมพัทธ์ต่ำสุด (%)"),
                 ("hi_max_raw", pa.float32(), "ดัชนีความร้อนสูงสุดก่อนปรับแก้ (°C)"),
                 ("bias", pa.float32(), "ความเอนเอียงที่ใช้ปรับแก้ (°C)"),
                 ("hi_max", pa.float32(), "ดัชนีความร้อนสูงสุดหลังปรับแก้ (°C)"),
                 ("precip", pa.float32(), "ฝนรวมรายวัน (มม.)")]},
    "observation": {
        "kind": "fact", "desc": "ค่าตรวจวัดจากสถานีและรายงานของผู้ใช้",
        "pk": ["site_id", "time", "var_id", "period_h", "source_id"],
        "fk": {"site_id": "site.site_id", "var_id": "variable.var_id", "source_id": "source.source_id"},
        "cols": [("site_id", pa.string(), "จุดตรวจวัด"),
                 ("time", TS, "เวลาตรวจวัด (UTC; สิ้นสุดช่วงสะสม)"),
                 ("var_id", pa.string(), "ตัวแปร"),
                 ("period_h", pa.int16(), "ช่วงสะสม/สรุป (ชม.); 0 = ค่า ณ เวลา"),
                 ("value", pa.float32(), "ค่า"),
                 ("source_id", pa.string(), "แหล่ง/บริการที่ให้ค่า")]},
    "forecast_hii": {
        "kind": "fact", "desc": "พยากรณ์ฝนรายวัน HII WRF-ROMS (สสน.) อ่านจากภาพ: ช่วงฝนตามแถบสี",
        "pk": ["init_time", "site_id", "day"],
        "fk": {"site_id": "site.site_id"},
        "cols": [("init_time", TS, "เวลาเริ่มต้นของรอบ (UTC)"),
                 ("site_id", pa.string(), "จุด"),
                 ("day", pa.int8(), "วันล่วงหน้า (1 = 19:00–19:00 น. ถัดไป)"),
                 ("domain_km", pa.int8(), "ความละเอียดโดเมน (กม.)"),
                 ("valid_start", TS, "ต้นช่วง (UTC)"),
                 ("valid_end", TS, "ปลายช่วง (UTC)"),
                 ("low", pa.float32(), "ขอบล่างของช่วงฝน (มม.)"),
                 ("high", pa.float32(), "ขอบบนของช่วงฝน (มม.)"),
                 ("mid", pa.float32(), "ค่ากลางของช่วงฝน (มม.)")]},
    "verification_pair": {
        "kind": "fact", "desc": "คู่ค่าพยากรณ์–ค่าตรวจวัดที่จับคู่แล้วตามเกณฑ์ WMO (ใช้คำนวณคะแนน MAE, RMSE, CSI, Brier ฯลฯ)",
        "pk": ["obs_source", "site_id", "valid_time", "var_id", "model_id", "kind", "lead", "init_time", "scheme"],
        "fk": {"site_id": "site.site_id", "var_id": "variable.var_id", "model_id": "model.model_id"},
        "cols": [("obs_source", pa.string(), "ชุดค่าตรวจวัดที่ใช้: tmd3h | tmdday | tw24h | hii24h | user"),
                 ("site_id", pa.string(), "จุดตรวจวัด"),
                 ("valid_time", TS, "เวลาสิ้นสุดช่วงตรวจวัด (UTC)"),
                 ("var_id", pa.string(), "ตัวแปร"),
                 ("model_id", pa.string(), "แบบจำลอง"),
                 ("kind", pa.string(), "det | ens"),
                 ("lead", pa.string(), "ระยะล่วงหน้าที่รายงาน เช่น H24, D+1"),
                 ("lead_h", pa.float32(), "ระยะล่วงหน้าจริง (ชม. จาก init_time ถึงกลางช่วง)"),
                 ("init_time", TS, "เวลาเริ่มต้นของรอบ (UTC)"),
                 ("scheme", pa.string(), "วิธีจับคู่"),
                 ("fc", pa.float32(), "ค่าพยากรณ์"),
                 ("ob", pa.float32(), "ค่าตรวจวัด"),
                 ("ev_fc", pa.bool_(), "พยากรณ์ว่าเกิดเหตุการณ์ (ฝน ≥ เกณฑ์)"),
                 ("ev_ob", pa.bool_(), "เกิดเหตุการณ์จริง")]},
    "reanalysis_daily": {
        "kind": "fact", "desc": "ข้อมูลย้อนหลังเชิงกริดรายวัน (วัน UTC) ณ จุดศูนย์กลางเซลล์ th025: ERA5-Land และ CHIRPS ตั้งแต่ปี 2016",
        "pk": ["dataset_id", "cell_id", "date"],
        "fk": {"dataset_id": "source.source_id", "cell_id": "grid_cell.cell_id"},
        "cols": [("dataset_id", pa.string(), "ชุดข้อมูล: era5_land | chirps"),
                 ("cell_id", pa.int32(), "เซลล์กริด th025"),
                 ("date", pa.date32(), "วันที่ (UTC 00–24)"),
                 ("t2m_mean", pa.float32(), "อุณหภูมิเฉลี่ยรายวัน (°C)"),
                 ("t2m_max", pa.float32(), "อุณหภูมิสูงสุด (°C)"),
                 ("t2m_min", pa.float32(), "อุณหภูมิต่ำสุด (°C)"),
                 ("td2m_mean", pa.float32(), "จุดน้ำค้างเฉลี่ย (°C)"),
                 ("precip", pa.float32(), "ฝนรวมรายวัน (มม.)"),
                 ("ssrd", pa.float32(), "รังสีคลื่นสั้นขาลงรวมรายวัน (MJ/m²)"),
                 ("channel", pa.string(), "ช่องทางที่ดึง: gee | openmeteo | chc")]},
}

# ตารางข้อเท็จจริงแต่ละตาราง: คอลัมน์เวลาที่ใช้แบ่งไฟล์รายเดือน
PARTITION_TIME = {"forecast_grid": "init_time", "forecast_point": "init_time", "forecast_district": "issued",
                  "observation": "time", "forecast_hii": "init_time", "verification_pair": "valid_time",
                  "reanalysis_daily": "date"}


def arrow_schema(table):
    return pa.schema([pa.field(c, t) for c, t, _ in TABLES[table]["cols"]])


def conform(df, table):
    """จัดคอลัมน์และชนิดของ DataFrame ให้ตรง schema แล้วคืน pyarrow.Table"""
    import pandas as pd
    sch = arrow_schema(table)
    df = df.copy()
    for f in sch:
        if f.name not in df:
            df[f.name] = None
        if pa.types.is_timestamp(f.type):
            df[f.name] = pd.to_datetime(df[f.name]).astype("datetime64[s]")
        elif pa.types.is_date32(f.type):
            df[f.name] = pd.to_datetime(df[f.name]).dt.date
    return pa.Table.from_pandas(df[[f.name for f in sch]], schema=sch, preserve_index=False)


def mermaid_er(keys_only=False):
    """ER diagram (Mermaid) สร้างจาก TABLES — ใช้ใน docs/DATABASE.md (ครบทุกคอลัมน์) และ README (เฉพาะคีย์)"""
    short = {"string": "string", "timestamp[s]": "timestamp", "float": "float", "double": "double", "int8": "int",
             "int16": "int", "int32": "int", "bool": "bool", "date32[day]": "date"}
    out = ["erDiagram"]
    for name, t in TABLES.items():
        out.append(f"    {name} {{")
        for c, typ, _ in t["cols"]:
            key = "PK" if c in t["pk"] else ("FK" if c in t["fk"] else "")
            if keys_only and not key:
                continue
            out.append(f"        {short.get(str(typ), str(typ))} {c} {key}".rstrip())
        out.append("    }")
    rel = set()
    for name, t in TABLES.items():
        for col, ref in t["fk"].items():
            parent = ref.split(".")[0]
            if parent != name:
                rel.add(f'    {parent} ||--o{{ {name} : "{col}"')
    return "\n".join(out + sorted(rel))
