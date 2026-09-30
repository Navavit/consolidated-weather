# Consolidated Weather: เปรียบเทียบพยากรณ์อากาศหลายโมเดล

เปรียบเทียบพยากรณ์ฝน อุณหภูมิ ความชื้น ลม UV และ PM2.5 จาก **12 โมเดล** (โมเดลระดับโลก 10 + TMD WRF 2 กม. + Google Weather)
**ensemble 7 ระบบ** และ **HII WRF-ROMS** (สสน.) ล่วงหน้า **3, 6, 12, 24 ชั่วโมง และ 1, 3, 7 วัน**
แล้ว **ตรวจความแม่นอัตโนมัติ** กับค่าวัดจริงในไทย: สถานีอุตุฯ ~124 แห่ง, โทรมาตร ThaiWater ~4,300 เครื่อง และการแจ้งฝนจากผู้ใช้

🌐 **หน้าเว็บ (อัปเดตประมาณทุกชั่วโมง): https://navavit.github.io/consolidated-weather/**

📖 แหล่งข้อมูลและหลักการของแต่ละโมเดล: **[docs/MODELS.md](docs/MODELS.md)** ·
ตั้งให้รันทุกชั่วโมง: **[docs/SCHEDULER.md](docs/SCHEDULER.md)** · งานวิจัย: **[docs/paper_outline.md](docs/paper_outline.md)**

### ความละเอียดเชิงพื้นที่ (วัดจริงที่ประเทศไทย)
TMD WRF **2 กม.** (ต้องมี token) · HII WRF-ROMS **3 กม.** (อ่านจากภาพ) · Google Weather ฝน **~5 กม.** (ต้องมี key) · ECMWF IFS **9 กม.** · UKMO 10×15 · GFS 13 · ICON 14 · CMA 14 · GEM 16 · AIFS 28 · NOAA AIGFS 28 · ARPEGE 28 · JMA 55 กม. · ensemble 25–55 กม.
รายละเอียดอยู่ใน [docs/MODELS.md § 3](docs/MODELS.md#3-รายละเอียดแต่ละโมเดล)

## โครงสร้างไฟล์

```
Consolidated_Weather/
├── rain_model_comparison.ipynb   # notebook หลัก: ตาราง กราฟ แผนที่ บันทึกฝน วิเคราะห์ความแม่นยำ
├── config.json                   # ตำแหน่ง, threshold, รอบการอัปเดต (ขึ้น GitHub)
├── config.local.json             # token ของ TMD — เฉพาะเครื่องนี้ ไม่ขึ้น GitHub
├── requirements.txt              # สำหรับ collector / GitHub Actions
├── requirements-notebook.txt     # สำหรับ notebook
├── .github/workflows/            # collect.yml (ดึงทุกชั่วโมง), location.yml (ฟอร์มตั้งค่าตำแหน่ง), observe.yml (ปุ่มแจ้งฝน)
├── weather_core.py               # ดึงข้อมูลจาก Open-Meteo และสรุปผล
├── weather_store.py              # SQLite: เก็บพยากรณ์, บันทึกฝนจริง, คำนวณคะแนน
├── collector.py                  # CLI: ดึงอัตโนมัติ / บันทึกฝน / ดูคะแนน
├── weather_site.py               # สร้างข้อมูล JSON สำหรับหน้าเว็บ
├── weather_now.py                # ค่าวัดจริงล่าสุด: สถานีกรมอุตุฯ, ThaiWater, Air4Thai
├── weather_verify.py             # วัดความแม่นอัตโนมัติ (มาตรฐาน WMO) เทียบสถานีตรวจวัดในไทย + จุดตรวจ
├── weather_usermode.py           # ความแม่นแบบมุมผู้ใช้ (ช่วงเวลาเดียวกับหน้าเว็บ) + ป้าย "เชื่อได้แค่ไหน"
├── weather_gauges.py             # ฝนรายวันที่โทรมาตรทุกเครื่อง (ในเขตเมือง / ทั่วประเทศ) เทียบ Previous Runs
├── weather_hii.py                # อ่านพยากรณ์ฝน HII WRF-ROMS จากภาพของ ThaiWater
├── resources/gauges.csv          # โทรมาตรทุกเครื่อง + ความหนาแน่นประชากร (สร้างด้วย analysis/gauge_density.py)
├── web/                          # หน้าเว็บ (index.html, app.js, style.css) — GitHub Pages
├── analysis/                     # วิเคราะห์ย้อนหลังสำหรับ paper (GSOD/ISD 2024–2025, เมือง vs ชนบท, รูป)
├── docs/                         # MODELS.md (แหล่งข้อมูล/หลักการ), SCHEDULER.md (cron-job.org), paper_outline.md
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
| `site_order` | `[]` | ลำดับแท็บบนหน้าเว็บ เช่น `["กรุงเทพมหานคร", "พระนครศรีอยุธยา"]` (ไม่กระทบว่าตำแหน่งไหนเป็นตำแหน่งของฉัน) |
| `site_locations` | ทั้งหมด | แสดงบนหน้าเว็บเฉพาะตำแหน่งเหล่านี้ (ตำแหน่งอื่นยังเก็บข้อมูล) · ตอนนี้ `["กรุงเทพมหานคร"]` |
| `dense_gauges` | `{}` | จุดตรวจหนาแน่นของตำแหน่ง เช่น กทม.: โทรมาตรทุกเครื่องใน `provinces` ที่ความหนาแน่น ≥ `min_density` คน/ตร.กม. ห่างกัน ≥ `min_spacing_km` สูงสุด `n` จุด |
| `day_start_hour` | `7` | วันพยากรณ์ = 07:00 → 07:00 น. (00–00 UTC ตรงกับค่าวัดรายวันของกรมอุตุฯ) |
| `hour_windows` / `day_leads` | `[3,6,12,24]` / `[1,3,7]` | ช่วงเวลาล่วงหน้า |
| `rain_threshold_mm` | `1.0` | ฝน ≥ ค่านี้ = "ฝนตก" (ใช้กับตารางเปรียบเทียบ/ensemble) |
| `collect_every_minutes` | `60` | รอบการดึงอัตโนมัติ |
| `verify_window_hours` | `3` | หน้าต่างเวลารอบเวลาที่สังเกต ใช้ตัดสินความแม่น |
| `verify_threshold_mm` | `0.2` | ฝนในหน้าต่าง ≥ ค่านี้ = โมเดลทายว่าตก |
| `max_grid_km` | `null` | ตัดโมเดลที่กริดหยาบกว่านี้ออก เช่น `16` = ใช้เฉพาะโมเดล 9–16 กม. |
| `google_weather_api_key` | – | key ของ Google Weather API **ใส่ใน `config.local.json` หรือ GitHub Secret `GOOGLE_WEATHER_API_KEY` เท่านั้น** |
| `google_monthly_cap` | `9500` | เพดานจำนวนครั้ง/เดือนของ Google (โควตาฟรี 10,000) ถึงแล้วหยุดเรียกเอง |
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
`.github/workflows/collect.yml` รัน `collector.py` ประมาณทุกชั่วโมง แล้วเก็บแต่ละรอบเป็นไฟล์ `.csv.gz` ไว้ใน **branch `data`** (แยกจากโค้ดใน `main`)
- ตารางของ GitHub (นาทีที่ 7 และ 37) มักถูกข้ามเมื่อเครื่อง GitHub แน่น จึงให้ **cron-job.org** สั่งรันผ่าน API ด้วย ([docs/SCHEDULER.md](docs/SCHEDULER.md))
- รอบที่ห่างจากรอบก่อนไม่ถึง 50 นาทีจะข้ามเอง (ไม่เปลืองโควตา) · สั่งรันทันทีได้ด้วย `gh workflow run collect.yml -f force=true`

ตั้งค่าครั้งแรก:
1. push โปรเจกต์ขึ้น GitHub (repo public ใช้ Actions ได้ไม่จำกัด)
2. **Settings → Secrets and variables → Actions → New repository secret** ชื่อ `TMD_NWP_TOKEN` ใส่ token ของ TMD
   (หรือรัน `gh secret set TMD_NWP_TOKEN`)
3. แท็บ **Actions → ดึงพยากรณ์อัตโนมัติ → Run workflow** เพื่อทดสอบรอบแรก

ดึงข้อมูลลงเครื่อง: `python3 collector.py sync` (notebook ข้อ ⑩ ทำให้เองอัตโนมัติ)

ข้อควรรู้:
- ใน config ห้ามตั้ง `my_location` เป็น `"auto"` เพราะบน GitHub จะได้ตำแหน่งของเครื่อง GitHub ในต่างประเทศ
- GitHub จะหยุด schedule ของ repo public ที่ไม่มีความเคลื่อนไหว 60 วัน (มีอีเมลเตือนก่อน) ถ้าถูกหยุด ให้กด Enable ที่แท็บ Actions
- การแจ้งฝนจากปุ่มบนหน้าเว็บเก็บใน branch `data` (`observations/`) · ที่บันทึกผ่าน notebook/CLI เก็บในเครื่อง (`data/weather.db`)

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

### Google Weather API (ไม่บังคับ · อยู่ในโควตาฟรี)
ใช้กับ **ตำแหน่งของฉัน (`my_location` = กรุงเทพมหานคร) เท่านั้น** เพื่อไม่ให้เกินโควตาฟรี 10,000 ครั้ง/เดือน
- กรุงเทพมหานคร: รายชั่วโมง 10 วัน ทุกรอบ (≈ 10 ครั้ง × 24 × 31 = 7,440/เดือน)
- จุดตรวจของ กทม. **เพียง 2 จุด** (สถานีอุตุฯ + โทรมาตรที่ใกล้ที่สุด) แม้ กทม. จะมีจุดตรวจหลายสิบจุด: 48 ชม. ทุก 2 ชม. (≈ 1,488/เดือน)
- รวม ≈ 8,900/เดือน → **ฟรี** · ระบบบันทึกเวลาเรียกล่าสุดและจำนวนครั้งต่อเดือนใน `google_usage.json` (branch data)
  กดรัน workflow ซ้ำก็ไม่เรียกเกินกำหนด และหยุดเองเมื่อถึง `google_monthly_cap`

ตั้งค่า: สร้าง API key (Google Cloud → เปิด Weather API + billing) แล้ว `gh secret set GOOGLE_WEATHER_API_KEY`
แนะนำให้ตั้ง quota ใน Cloud Console ไว้ที่ 300 ครั้ง/วัน และตั้ง budget alert ไว้ด้วย

### หน้าเว็บ (GitHub Pages)
workflow `ดึงพยากรณ์อัตโนมัติ` สร้างหน้าเว็บจากผลรอบนั้นและ deploy ขึ้น GitHub Pages ทุกรอบ อ่านตามลำดับ
- แท็บ **📍 ตรงที่ฉันอยู่** (ค่าเริ่มต้น, อยู่แรกสุด): เบราว์เซอร์ขอตำแหน่ง แล้วดึง Open-Meteo เองและคำนวณแบบเดียวกับ `weather_core.py`
  (พิกัดส่งไปที่ Open-Meteo โดยตรง โครงการไม่เก็บ · ไม่มี TMD WRF/Google เพราะต้องใช้ key) · ถ้าไม่อนุญาตตำแหน่ง แสดง **กรุงเทพมหานคร** แทน
- **① ตอนนี้**: ค่าวัดจากสถานีกรมอุตุฯ ใกล้สุด, PM2.5 (Air4Thai), ค่าปัจจุบันจากโมเดล, **แผนที่เรดาร์ฝน + สถานีวัดทุกเครื่อง**
  (โทรมาตร ThaiWater แยกตามหน่วยงาน, สถานีอุตุฯ, PM2.5, วงแหวน = จุดตรวจ · เปิด/ปิดชั้นข้อมูลได้ · สี = ฝน 24 ชม.)
- **② พยากรณ์**: ฝน +3/+6/+12/+24 ชม. และสรุปรายวัน D+1/D+3/D+7 (07:00 → 07:00 น.) จากค่ากลางทุกโมเดล
  ทุกช่องมีเวลาจริงกำกับ และป้าย **“📊 ที่ผ่านมา: บอกว่าตก → ตกจริง x/10 · ฝนตก → เตือนไว้ y/10”**
- **③–⑤ (พับไว้ กดเปิดดู)**: ฝนแยกโมเดล, โอกาสฝน ensemble, HII 7 วัน, พารามิเตอร์อื่น · กราฟรายชั่วโมง 7 วัน ·
  รอบรันของแต่ละโมเดล และ **🏆 โมเดลไหนแม่น** 2 โหมด: 👤 ผู้ใช้ทั่วไป (ค่าเริ่มต้น) และ 🔬 มาตรฐาน WMO (สำหรับงานวิจัย)
- **ℹ️ เกี่ยวกับโครงการ**: เป้าหมาย, วิธีทำงาน, เทียบอะไรกับอะไร, งานวิจัย, ข้อจำกัด, แหล่งข้อมูล · โหมดมืด · ใช้บนมือถือได้

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
- หน้าเว็บ ส่วน **🏆 โมเดลไหนแม่น** (อัปเดตทุกรอบ)
  - **👤 ผู้ใช้ทั่วไป**: ช่วงเดียวกับหน้าเว็บ นับจากเวลาที่ดึงพยากรณ์ ที่จุดตรวจ (โทรมาตรในเขตเมือง กทม. + ปริมณฑล, อยุธยา, สถานีอุตุฯ) · `weather_usermode.py`
  - **🔬 มาตรฐาน WMO**: T+ ชม. จากรอบรัน / Day N ที่สถานีอุตุฯ ทั่วประเทศ (รายวัน + ราย 3 ชม.), โทรมาตรทุกเครื่อง
    (ในเขตเมือง 153 เครื่อง ล่วงหน้า ≥ 24/72/168 ชม.; ทั่วประเทศ ~3,200 เครื่อง ≥ 24 ชม. แยกเมือง/ชานเมือง/ชนบท), จุดตรวจ, HII และการแจ้งของผู้ใช้
- notebook ข้อ ⑩ หรือ `python3 collector.py verify` (ข้อมูลในเครื่อง)

รายละเอียดวิธีคิดคะแนนอยู่ใน [docs/MODELS.md § 5](docs/MODELS.md#5-การวัดความแม่นยำ-verification)

## ข้อจำกัดการใช้งาน
- Open-Meteo API ฟรีใช้ได้เฉพาะงาน **ไม่เชิงพาณิชย์** (~10,000 calls/วัน ถ้าเกินจะถูกปฏิเสธชั่วคราว ไม่มีค่าใช้จ่าย)
  ระบบใช้ส่วนใหญ่กับ 2 ตำแหน่ง + ~29 จุดตรวจทุกชั่วโมง และโทรมาตรทุกเครื่องวันละครั้ง (ทยอยรอบละ ≤ 4 นาที) · แผนที่กริดใน notebook ใช้โควตามาก
- แอปที่เผยแพร่ต้องแสดงที่มา "Weather data by Open-Meteo.com" (CC BY 4.0)
