# Consolidated Weather: เปรียบเทียบพยากรณ์อากาศหลายโมเดล

เปรียบเทียบพยากรณ์ฝน อุณหภูมิ ความชื้น ลม UV และ PM2.5 จาก **โมเดลพยากรณ์อากาศระดับโลก 11 โมเดล และ ensemble 6 ระบบ**
ที่ตำแหน่งของคุณและตำแหน่งที่ต้องการ ล่วงหน้า **3, 6, 12, 24 ชั่วโมง และ 1, 3, 7 วัน**
พร้อมระบบ **ดึงข้อมูลอัตโนมัติ** และ **บันทึกฝนที่ตกจริง** เพื่อวัดว่าโมเดลไหนแม่นที่สุดในพื้นที่ของคุณ

🌐 **หน้าเว็บ (อัปเดตทุกชั่วโมง): https://navavit.github.io/consolidated-weather/**

📖 แหล่งข้อมูลและหลักการของแต่ละโมเดล: **[docs/MODELS.md](docs/MODELS.md)**

### ความละเอียดเชิงพื้นที่ (วัดจริงที่ประเทศไทย)
TMD WRF **2 กม.** (ต้องมี token) · ECMWF IFS **9 กม.** · UKMO 10×15 · GFS 13 · ICON 14 · CMA 14 · GEM 16 · AIFS 28 · ARPEGE 28 · JMA 55 กม. · ensemble 25–55 กม.
รายละเอียดอยู่ใน [docs/MODELS.md § 3](docs/MODELS.md#3-รายละเอียดแต่ละโมเดล)

## โครงสร้างไฟล์

```
Consolidated_Weather/
├── rain_model_comparison.ipynb   # notebook หลัก: ตาราง กราฟ แผนที่ บันทึกฝน วิเคราะห์ความแม่นยำ
├── config.json                   # ตำแหน่ง, threshold, รอบการอัปเดต (ขึ้น GitHub)
├── config.local.json             # token ของ TMD — เฉพาะเครื่องนี้ ไม่ขึ้น GitHub
├── requirements.txt              # สำหรับ collector / GitHub Actions
├── requirements-notebook.txt     # สำหรับ notebook
├── .github/workflows/            # collect.yml (ดึงทุกชั่วโมง), location.yml (ฟอร์มตั้งค่าตำแหน่ง)
├── weather_core.py               # ดึงข้อมูลจาก Open-Meteo และสรุปผล
├── weather_store.py              # SQLite: เก็บพยากรณ์, บันทึกฝนจริง, คำนวณคะแนน
├── collector.py                  # CLI: ดึงอัตโนมัติ / บันทึกฝน / ดูคะแนน
├── weather_site.py               # สร้างข้อมูล JSON สำหรับหน้าเว็บ
├── web/                          # หน้าเว็บ (index.html, app.js, style.css) — GitHub Pages
├── docs/MODELS.md                # เอกสารแหล่งข้อมูลและหลักการของโมเดล
├── data/weather.db               # ฐานข้อมูล (สร้างเองอัตโนมัติ)
├── outputs/                      # CSV และ map.html
└── scripts/                      # ไฟล์ launchd (สร้างด้วย collector.py install-launchd)
```

## ติดตั้ง

```bash
pip3 install -r requirements-notebook.txt
```
เปิด `rain_model_comparison.ipynb` ใน VS Code (ติดตั้ง extension Jupyter) หรือรัน `jupyter lab` แล้วกด **Run All**
ไม่ต้องใช้ API key

## ตั้งค่า (`config.json`)

| คีย์ | ค่าเริ่มต้น | ความหมาย |
|---|---|---|
| `my_location` | `"auto"` | หาจาก IP หรือใส่ `[13.7563, 100.5018, "บ้าน"]` |
| `target_places` | `["เชียงใหม่", "ภูเก็ต"]` | ชื่อสถานที่ หรือ `[lat, lon, "ชื่อ"]` |
| `hour_windows` / `day_leads` | `[3,6,12,24]` / `[1,3,7]` | ช่วงเวลาล่วงหน้า |
| `rain_threshold_mm` | `1.0` | ฝน ≥ ค่านี้ = "ฝนตก" (ใช้กับตารางเปรียบเทียบ/ensemble) |
| `collect_every_minutes` | `60` | รอบการดึงอัตโนมัติ |
| `verify_window_hours` | `3` | หน้าต่างเวลารอบเวลาที่สังเกต ใช้ตัดสินความแม่น |
| `verify_threshold_mm` | `0.2` | ฝนในหน้าต่าง ≥ ค่านี้ = โมเดลทายว่าตก |
| `max_grid_km` | `null` | ตัดโมเดลที่กริดหยาบกว่านี้ออก เช่น `16` = ใช้เฉพาะโมเดล 9–16 กม. |
| `tmd_token` | – | token ของ [TMD NWP API](https://data.tmd.go.th/nwpapi/register) สำหรับ **TMD WRF 2 กม.** **ใส่ใน `config.local.json` เท่านั้น** (ไม่ขึ้น GitHub) หรือตั้ง env `TMD_NWP_TOKEN` |

## ใช้งาน

### 0. เลือกตำแหน่ง
- **ใน notebook** ข้อ ①-ข: คลิกแผนที่หรือลากหมุด / วาง `lat, lon` / วางลิงก์ Google Maps / พิมพ์ชื่อสถานที่ → ตั้งชื่อ → 🏠 หรือ ➕ → 💾 บันทึก → ⬆️ push
- **บนเว็บ GitHub (ใช้มือถือได้):** แท็บ **Actions → ตั้งค่าตำแหน่ง → Run workflow** แล้วกรอกฟอร์ม
- **command line:** `python3 collector.py location --action add --name "ที่ทำงาน" --coords "13.7246, 100.5299"`
  (`--action me` = ตั้งเป็นตำแหน่งของฉัน, `--action remove --name ...` = ลบ)

> repo เป็น public พิกัดใน `config.json` จึงเปิดให้คนอื่นเห็นได้ ถ้าไม่อยากให้เห็นจุดที่แน่นอน ให้ใช้พิกัดคร่าว ๆ เช่นทศนิยม 2 ตำแหน่ง (≈1 กม.)

### 1. ดูพยากรณ์เปรียบเทียบ
รัน notebook ตั้งแต่ข้อ ① ถึง ⑦ จะได้สิ่งต่อไปนี้สำหรับแต่ละตำแหน่ง
- การ์ดสรุปรายวัน (ฝน อุณหภูมิ รู้สึกเหมือน ความชื้น ลม UV PM2.5 พร้อมระดับความเสี่ยง)
- ตารางฝนแยกโมเดล + ฉันทามติ + โอกาสฝนจาก ensemble
- ตารางพารามิเตอร์อื่น ๆ แยกโมเดล
- Heatmap, กราฟแท่งรายวัน, meteogram รายชั่วโมง 7 วัน
- แผนที่ interactive (หมุดตำแหน่ง + กริดทั่วประเทศไทย สลับดูทีละโมเดลได้) และแผนที่ภาพนิ่งแบบ small multiples

### 2. ดึงข้อมูลอัตโนมัติ

#### แบบ A — GitHub Actions (แนะนำ ไม่ต้องมี server ไม่ต้องเปิดเครื่อง)
`.github/workflows/collect.yml` จะรัน `collector.py` ทุกชั่วโมง (นาทีที่ 7 UTC ซึ่ง GitHub อาจรันช้ากว่าที่ตั้งไว้ 5–30 นาที)
แล้วเก็บแต่ละรอบเป็นไฟล์ `.csv.gz` ขนาดประมาณ 3–4 KB ไว้ใน **branch `data`** (แยกจากโค้ดใน `main`)

ตั้งค่าครั้งแรก:
1. push โปรเจกต์ขึ้น GitHub (repo public ใช้ Actions ได้ไม่จำกัด)
2. **Settings → Secrets and variables → Actions → New repository secret** ชื่อ `TMD_NWP_TOKEN` ใส่ token ของ TMD
   (หรือรัน `gh secret set TMD_NWP_TOKEN`)
3. แท็บ **Actions → ดึงพยากรณ์อัตโนมัติ → Run workflow** เพื่อทดสอบรอบแรก

ดึงข้อมูลลงเครื่อง: `python3 collector.py sync` (notebook ข้อ ⑩ ทำให้เองอัตโนมัติ)

ข้อควรรู้:
- ใน config ห้ามตั้ง `my_location` เป็น `"auto"` เพราะบน GitHub จะได้ตำแหน่งของเครื่อง GitHub ในต่างประเทศ
- GitHub จะหยุด schedule ของ repo public ที่ไม่มีความเคลื่อนไหว 60 วัน (มีอีเมลเตือนก่อน) ถ้าถูกหยุด ให้กด Enable ที่แท็บ Actions
- การบันทึกฝนจริง (observations) ยังเก็บในเครื่อง (`data/weather.db`) ไม่ขึ้น GitHub

#### แบบ B — บนเครื่องตัวเอง
```bash
python3 collector.py run                          # ดึง 1 รอบ
python3 collector.py loop --every 60              # ดึงซ้ำทุก 60 นาที (เปิด terminal ค้างไว้)
python3 collector.py install-launchd --every 60   # macOS: สร้างไฟล์ให้รันเบื้องหลังเอง แล้วทำตามคำสั่งที่แสดง
```
ใช้ cron ก็ได้ (Linux/macOS):
```
0 * * * * cd /path/to/Consolidated_Weather && /usr/bin/python3 collector.py run >> logs/collector.log 2>&1
```
ใน notebook ข้อ ⑧ มีปุ่ม **▶️ เริ่มอัปเดตอัตโนมัติ** ซึ่งทำงานเฉพาะตอนที่ notebook เปิดอยู่

### หน้าเว็บ (GitHub Pages)
workflow `ดึงพยากรณ์อัตโนมัติ` สร้างหน้าเว็บจากผลรอบนั้นและ deploy ขึ้น GitHub Pages ทุกชั่วโมง มีดังนี้
- การ์ดสรุปรายวัน (ฝน, อุณหภูมิ, รู้สึกเหมือน + ระดับความร้อน, ความชื้น, ลมกระโชก, UV, PM2.5)
- ฝนช่วง +3/+6/+12/+24 ชม. พร้อมช่วงของโมเดลและโอกาสฝนจาก ensemble
- ตาราง heatmap ฝนแยกโมเดล (เรียงจากละเอียดไปหยาบ) และตาราง ensemble
- กราฟรายชั่วโมง 7 วัน: median ของทุกโมเดล + ช่วงต่ำสุด–สูงสุด + โมเดลที่เลือกเน้น (ค่าเริ่มต้น TMD WRF)
- ตารางพารามิเตอร์อื่นแยกโมเดล, แผนที่ตำแหน่ง, โหมดมืด, ใช้บนมือถือได้

ดูในเครื่องก่อน deploy:
```bash
python3 collector.py run --no-db --site-dir site && python3 -m http.server -d site 8000   # เปิด http://localhost:8000
```

### 3. บันทึกว่าฝนตกจริงหรือไม่
- **บนหน้าเว็บ (ง่ายที่สุด ใช้มือถือได้):** กดปุ่ม **🌧️ ตก / ☀️ ไม่ตก** ที่แถบบนสุด (ใช้ตำแหน่งของแท็บที่เลือก หรือกด 📍 ใช้ GPS)
  ระบบจะเปิดฟอร์ม GitHub Issue ที่กรอกไว้ให้แล้ว → กด **Create** → workflow `บันทึกฝนจาก issue` จะเก็บลง branch `data` และปิด issue ให้เอง
  (ต้อง login GitHub ด้วยบัญชีเจ้าของ repo ถ้าเป็นคนอื่นส่งมา ระบบจะไม่บันทึก)
- ใน notebook ข้อ ⑨: เลือกตำแหน่งและเวลา → กด 🌧️ ตก / ☀️ ไม่ตก → 💾 บันทึก
- หรือจาก command line:
```bash
python3 collector.py observe --place me --rain yes --mm 5
python3 collector.py observe --place "เชียงใหม่" --rain no --time "2026-09-27 15:30"
python3 collector.py observe --lat 13.75 --lon 100.50 --place "ที่ทำงาน" --rain yes
```

### 4. ดูว่าโมเดลไหนแม่น
- notebook ข้อ ⑩: ตารางอันดับ, heatmap ความแม่นแยกตามช่วงเวลาล่วงหน้า, Brier score ของ ensemble, รายละเอียดรายครั้ง
- หรือ `python3 collector.py verify`

การสังเกตย้อนหลัง 1–7 วันจะได้ผลทันทีจาก Open-Meteo Previous Runs API ส่วนช่วง 3/6/12 ชม. ต้องมีรอบที่ collector เก็บไว้ก่อนเวลานั้น
รายละเอียดวิธีคิดคะแนนอยู่ใน [docs/MODELS.md § 5](docs/MODELS.md#5-การวัดความแม่นยำ-verification)

## ข้อจำกัดการใช้งาน
- Open-Meteo API ฟรีใช้ได้เฉพาะงาน **ไม่เชิงพาณิชย์** (~10,000 calls/วัน) การดึงอัตโนมัติทุก 60 นาทีสำหรับ 3 ตำแหน่งใช้ประมาณ 500 คำขอ/วัน (คำขอหลายโมเดล/หลายตัวแปรอาจถูกนับเป็นหลาย call) ส่วนแผนที่กริดใช้โควตามาก
- แอปที่เผยแพร่ต้องแสดงที่มา "Weather data by Open-Meteo.com" (CC BY 4.0)
