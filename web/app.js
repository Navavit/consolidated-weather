/* เทียบพยากรณ์อากาศหลายโมเดล — หน้าเว็บ static อ่าน data/*.json ที่ GitHub Actions สร้างทุกชั่วโมง */
"use strict";

// ---------------------------------------------------------------------------
// ค่าคงที่
// ---------------------------------------------------------------------------
// ramp สีฟ้าแบบลำดับ (sequential) จากอ่อน → เข้ม ใช้กับ heatmap และแผนที่
const BLUE = ["#cde2fb", "#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5",
              "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"];
const RAIN_STEPS = [0.1, 0.5, 1, 2, 5, 10, 15, 20, 30, 50, 75, 100];      // มม. → ขั้นของ ramp
const PROB_STEPS = [5, 10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100];      // %
const STATUS = {
  "ปกติ": "good", "เฝ้าระวัง": "warning", "เตือนภัย": "serious", "อันตราย": "critical", "อันตรายมาก": "critical",
  "ต่ำ": "good", "ปานกลาง": "warning", "สูง": "serious", "สูงมาก": "critical",
  "ดีมาก": "good", "ดี": "good", "เริ่มมีผลต่อสุขภาพ": "serious", "มีผลต่อสุขภาพ": "critical",
};
const CHARTS = [
  { key: "precipitation", title: "ฝนรายชั่วโมง", unit: "มม./ชม.", bars: true, zero: true, wide: true, height: 220 },
  { key: "temperature_2m", title: "อุณหภูมิ", unit: "°C" },
  { key: "apparent_temperature", title: "อุณหภูมิที่รู้สึก", unit: "°C",
    lines: [{ y: 33, label: "เตือนภัย 33", status: "serious" }, { y: 42, label: "อันตราย 42", status: "critical" }] },
  { key: "relative_humidity_2m", title: "ความชื้นสัมพัทธ์", unit: "%", max: 100 },
  { key: "wind_gusts_10m", title: "ลมกระโชก", unit: "กม./ชม.", zero: true },
  { key: "pm25", title: "PM2.5 (CAMS)", unit: "มคก./ลบ.ม.", zero: true, single: true,
    lines: [{ y: 37.5, label: "มาตรฐาน 37.5", status: "serious" }] },
];
const DAYS = ["อา.", "จ.", "อ.", "พ.", "พฤ.", "ศ.", "ส."];

const state = { index: null, loc: null, locIdx: 0, highlight: null, param: "tmax", map: null, markers: [], gps: null };

// ---------------------------------------------------------------------------
// เครื่องมือ
// ---------------------------------------------------------------------------
const $ = (id) => document.getElementById(id);
function el(tag, attrs = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k === "style") n.style.cssText = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
const SVGNS = "http://www.w3.org/2000/svg";
function sv(tag, attrs = {}) {
  const n = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) n.setAttribute(k, v);
  return n;
}
const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* ไม่มี storage ก็ใช้งานได้ */ } },
};
const fmt = (v, d = 1) => (v == null ? "–" : Number(v).toLocaleString("th-TH", { minimumFractionDigits: d, maximumFractionDigits: d }));
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
function stepColor(v, steps) {
  if (v == null) return null;
  let i = steps.findIndex((s) => v < s);
  if (i === -1) i = steps.length;
  return i === 0 ? null : BLUE[Math.min(i - 1, BLUE.length - 1)];
}
function inkOn(hex) {
  const n = parseInt(hex.slice(1), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    c /= 255; return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.36 ? "#0b0b0b" : "#ffffff";
}
function statusPill(text) {
  if (!text || text === "–") return null;
  const [icon, ...rest] = text.split(" ");
  const label = rest.join(" ");
  return el("span", { class: "pill", "data-status": STATUS[label] || "" }, icon, " ", label);
}
function timeAgo(iso) {
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "เมื่อสักครู่";
  if (min < 60) return `${min} นาทีที่แล้ว`;
  const h = Math.round(min / 60);
  return h < 48 ? `${h} ชั่วโมงที่แล้ว` : `${Math.round(h / 24)} วันที่แล้ว`;
}
const localTime = (iso) => new Date(iso.length <= 19 ? iso + "+07:00" : iso);

// ---------------------------------------------------------------------------
// tooltip เดียวใช้ร่วมกันทั้งหน้า
// ---------------------------------------------------------------------------
const tip = {
  show(x, y, head, rows) {
    const t = $("tooltip");
    t.replaceChildren(el("div", { class: "tt-h" }, head),
      ...rows.map((r) => el("div", { class: "row" },
        el("span", {}, r.color ? el("i", { class: "key-line", style: `background:${r.color}` }) : null, r.label),
        el("b", {}, r.value))));
    t.hidden = false;
    const w = t.offsetWidth, h = t.offsetHeight;
    const left = Math.min(window.innerWidth - w - 8, Math.max(8, x + 14));
    const top = y - h - 12 < 8 ? y + 16 : y - h - 12;
    t.style.left = `${left}px`;
    t.style.top = `${top}px`;
  },
  hide() { $("tooltip").hidden = true; },
};

// ---------------------------------------------------------------------------
// โหลดข้อมูล
// ---------------------------------------------------------------------------
async function getJSON(url) {
  const r = await fetch(url, { cache: "no-cache" });
  if (!r.ok) throw new Error(`${url}: ${r.status}`);
  return r.json();
}

async function init() {
  applyTheme(store.get("theme"));
  $("theme").addEventListener("click", () => {
    const dark = document.documentElement.getAttribute("data-theme") === "dark" ||
      (!document.documentElement.hasAttribute("data-theme") && matchMedia("(prefers-color-scheme: dark)").matches);
    applyTheme(dark ? "light" : "dark");
  });
  try {
    state.index = await getJSON("data/index.json");
  } catch (e) {
    $("updated").textContent = "ยังไม่มีข้อมูล — รอ GitHub Actions รันรอบแรก";
    return;
  }
  const repo = state.index.repo;
  if (repo) {
    $("edit-loc").href = `https://github.com/${repo}/actions/workflows/location.yml`;
    $("docs-link").href = `https://github.com/${repo}/blob/main/docs/MODELS.md`;
    $("repo-link").href = `https://github.com/${repo}`;
  } else {
    $("edit-loc").hidden = true;
  }
  $("updated").textContent = `อัปเดต ${timeAgo(state.index.generated)} · ${new Date(state.index.generated).toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short" })}`;
  const wantAbout = location.hash === "#about";
  initFolds();     // อ่านก่อน เพราะการเลือกตำแหน่งจะล้าง hash
  initRainBar();
  getJSON("data/verification.json").then((v) => {
    state.verify = v; renderVerify(); renderUserMode();
    if (state.loc) { renderNext(); renderBrief(); }
    if (document.body.classList.contains("about-mode")) showAbout(true);
  }).catch(() => {
    $("v-note").textContent = "ยังไม่มีข้อมูล — ระบบเริ่มเก็บค่าวัดจากสถานีแล้ว ผลจะเริ่มแสดงในไม่กี่ชั่วโมง";
  });
  state.meta = await getJSON("data/meta.json").catch(() => null);
  // ค่าเริ่มต้น = 📍 ตรงที่ฉันอยู่ · แสดงตำแหน่งแรกไปก่อนระหว่างรออนุญาตตำแหน่ง/คำนวณ (ถ้า GPS ใช้ไม่ได้ก็ค้างที่ตำแหน่งแรก)
  const tab = store.get("tab") || "gps";
  const saved = tab.startsWith("loc:") ? parseInt(tab.slice(4), 10) : 0;
  renderChips();
  initMap();
  await selectLocation(saved < state.index.locations.length ? saved : 0, false);
  if (wantAbout) showAbout(true);
  else if (tab === "gps") selectGps(false);
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(renderCharts, 150); });
}

// ส่วน ③–⑤ พับได้ (ปิดไว้เป็นค่าเริ่มต้น จำสถานะต่อเครื่อง) · กดลำดับเนื้อหาด้านบนแล้วเปิดให้เอง
function initFolds() {
  document.querySelectorAll("details.fold").forEach((d) => {
    if (store.get(`fold:${d.id}`) === "1") d.open = true;
    d.addEventListener("toggle", () => {
      store.set(`fold:${d.id}`, d.open ? "1" : "0");
      if (d.open && d.id === "step-4") renderCharts();
    });
  });
  document.querySelectorAll("#steps a").forEach((a) => a.addEventListener("click", () => {
    const t = document.querySelector(a.getAttribute("href"));
    if (t && t.tagName === "DETAILS") t.open = true;
  }));
}

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
    store.set("theme", theme);
  }
  if (state.loc) { renderCharts(); updateMapTiles(); }
}

function renderChips() {
  $("locations").replaceChildren(
    el("button", { class: "chip", id: "gps-chip", role: "tab", type: "button", "aria-selected": String(state.locIdx === -1),
      onclick: () => selectGps() }, "📍 ตรงที่ฉันอยู่"),
    ...state.index.locations.map((l, i) =>
      el("button", { class: "chip", role: "tab", type: "button", "data-idx": String(i), "aria-selected": String(i === state.locIdx),
        onclick: () => selectLocation(i) }, l.name)),
    el("button", { class: "chip", id: "about-chip", role: "tab", type: "button", "aria-selected": "false",
      onclick: () => showAbout(true) }, "ℹ️ เกี่ยวกับโครงการ"));
}
function markChips() {
  const about = document.body.classList.contains("about-mode");
  [...$("locations").children].forEach((c) => c.setAttribute("aria-selected", String(
    c.id === "about-chip" ? about : !about && (c.id === "gps-chip" ? state.locIdx === -1 : Number(c.dataset.idx) === state.locIdx))));
  $("gps-note").hidden = about || state.locIdx !== -1;
}

// ---------------------------------------------------------------------------
// ℹ️ เกี่ยวกับโครงการ (แท็บสุดท้าย · ลิงก์ตรงด้วย #about)
// ---------------------------------------------------------------------------
async function showAbout(on) {
  document.body.classList.toggle("about-mode", on);
  if (on && location.hash !== "#about") history.replaceState(null, "", "#about");
  if (!on && location.hash === "#about") history.replaceState(null, "", location.pathname);
  markChips();
  if (!on) { renderCharts(); return; }
  window.scrollTo({ top: 0 });
  const repo = state.index.repo;
  if (repo) {
    $("about-docs").href = `https://github.com/${repo}/blob/main/docs/MODELS.md`;
    $("about-repo").href = `https://github.com/${repo}`;
  }
  if (!state.meta) state.meta = await getJSON("data/meta.json").catch(() => null);
  const M = state.meta;
  if (M) {
    const rows = [...M.keyed_models, ...M.models].sort((a, b) => (a.grid_km ?? 99) - (b.grid_km ?? 99));
    // โมเดลที่มีข้อมูลจริงในรอบล่าสุด (ดูจากตำแหน่งแรกในรายการ)
    const first = state.index.locations[0];
    const locData = state.aboutLoc || (state.aboutLoc = await getJSON(`data/${first.file}`).catch(() => null));
    const avail = new Set((locData?.models || []).map((m) => m.name));
    const TYPE_TH = { "AI/ML": "AI", "Physics": "ฟิสิกส์", "Physics (regional)": "ฟิสิกส์ (ภูมิภาค)" };
    $("about-models").replaceChildren(el("table", { class: "about-table" },
      el("thead", {}, el("tr", {}, ...["โมเดล", "ผู้พัฒนา", "ประเภท", "ความละเอียด"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rows.map((m) => el("tr", {},
        el("td", {}, el("b", {}, m.name), avail.size && !avail.has(m.name) ? el("div", { class: "muted" }, "ขณะนี้ไม่มีข้อมูล") : null),
        el("td", {}, m.agency || ""),
        el("td", {}, TYPE_TH[m.type] || m.type || ""),
        el("td", {}, m.grid_km ? `~${m.grid_km} กม. ` : "", el("small", {}, m.grid || "")))))));
    $("about-ens").textContent = "ensemble ที่ใช้: " + M.ensembles.map((e) => `${e.name} (${e.members})`).join(", ");
  }
  const V = state.verify;
  const n = V ? V.sections.reduce((a, x) => a + (x.n_obs || 0), 0) : 0;
  $("about-stats").textContent = `อัปเดตอัตโนมัติทุกชั่วโมง · เริ่มเก็บข้อมูล ${V?.since || "–"} · ` +
    `เทียบกับค่าวัดจริงแล้ว ${n.toLocaleString("th-TH")} ครั้ง · ใช้งานอยู่ ${state.aboutLoc ? state.aboutLoc.models.length : "–"} โมเดล + ${M ? M.ensembles.length : "–"} ensemble`;
}

async function selectLocation(i, remember = true) {
  document.body.classList.remove("about-mode");
  if (location.hash === "#about") history.replaceState(null, "", location.pathname);
  state.locIdx = i;
  state.gps = null;                         // เปลี่ยนแท็บ = กลับมาใช้ตำแหน่งของแท็บนั้น
  if (remember) store.set("tab", `loc:${i}`);
  markChips();
  document.body.style.opacity = "0.6";      // คงหน้าเดิมไว้ระหว่างโหลด ไม่กระพริบ
  try {
    state.loc = await getJSON(`data/${state.index.locations[i].file}`);
  } finally {
    document.body.style.opacity = "";
  }
  showLocation();
}

function showLocation() {
  const i = state.locIdx;
  const models = state.loc.models.map((m) => m.name);
  const want = store.get("highlight");
  state.highlight = models.includes(want) ? want : models[0];
  $("highlight").replaceChildren(...state.loc.models.map((m) =>
    el("option", { value: m.name, selected: m.name === state.highlight },
      `${m.name}${m.grid_km ? ` (${m.grid_km} กม.)` : ""}`)));
  $("highlight").onchange = (e) => { state.highlight = e.target.value; store.set("highlight", state.highlight); renderCharts(); };
  renderAll();
  if (state.map) state.map.setView([state.loc.lat, state.loc.lon], Math.max(state.map.getZoom(), 8));   // ดูฝนรอบตำแหน่งที่เลือก
  state.markers.forEach((m, j) => m.setStyle({ weight: j === i ? 3 : 1.5, radius: j === i ? 10 : 8,
    fillColor: j === i ? cssVar("--ink") : cssVar("--surface") }));
  if (state.gpsMarker) state.gpsMarker.remove();
  if (i === -1 && state.map) {
    state.gpsMarker = L.circleMarker([state.loc.lat, state.loc.lon], {
      radius: 10, color: cssVar("--ink"), weight: 3, fillColor: cssVar("--ink"), fillOpacity: 1,
    }).bindTooltip("ตรงที่ฉันอยู่", { direction: "top", className: "map-label" }).addTo(state.map);
  }
}

function renderAll() {
  renderTimebase();
  renderRainBar();
  renderNow();
  renderBrief();
  renderNext();
  renderRainTable();
  renderHii();
  renderRuns();
  renderEnsemble();
  renderCharts();
  renderParam();
}

// ---------------------------------------------------------------------------
// 📍 พยากรณ์ตรงที่ฉันอยู่: ดึง Open-Meteo ในเบราว์เซอร์แล้วคำนวณแบบเดียวกับ weather_core.py
// (ไม่มี TMD WRF / Google Weather เพราะต้องใช้ key ลับ)
// ---------------------------------------------------------------------------
const HOUR = 3600e3, DAY = 86400e3;
const naiveMs = (s) => Date.parse(s.length === 16 ? `${s}:00Z` : `${s}Z`);   // เวลาท้องถิ่นแบบไม่มี timezone → ms
const median = (a) => { const v = a.filter((x) => x != null).sort((x, y) => x - y); const n = v.length;
  return n ? (n % 2 ? v[(n - 1) / 2] : (v[n / 2 - 1] + v[n / 2]) / 2) : null; };
const pickLevel = (v, table) => (v == null || Number.isNaN(v) ? "–" : table.find(([lim]) => v < lim)[1]);
// ---------------------------------------------------------------------------
// ป้ายเวลาเดียวกันทั้งหน้า: ทุกตาราง/การ์ดใช้เวลาอ้างอิงเดียวกัน (เวลาที่ดึงพยากรณ์) และวัน 07:00 → 07:00 น.
// ---------------------------------------------------------------------------
const dayStart = () => state.meta?.day_start_hour ?? 7;
const baseMs = (L = state.loc) => (L?.fetched_at ? naiveMs(L.fetched_at.slice(0, 16)) : null);
const pad2 = (n) => String(n).padStart(2, "0");
const hhmmMs = (ms) => { const d = new Date(ms); return `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}`; };
const dMon = (ms) => new Date(ms).toLocaleDateString("th-TH", { timeZone: "UTC", day: "numeric", month: "short" });
const dayWord = (ms, base) => {
  const dd = Math.round((Date.UTC(...ymd(ms)) - Date.UTC(...ymd(base))) / DAY);
  return dd === 0 ? "วันนี้" : dd === 1 ? "พรุ่งนี้" : dMon(ms);
};
function ymd(ms) { const d = new Date(ms); return [d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()]; }
/** ช่วงเวลา "+N ชม." / "D+N (dd/mm)" → {top, sub, start, end} (เวลาท้องถิ่นแบบ naive ms) */
function winInfo(w, L = state.loc) {
  const base = baseMs(L);
  let m = /^\+(\d+)/.exec(w);
  if (m && base != null) {
    const end = base + m[1] * HOUR;
    return { top: `+${m[1]} ชม.`, sub: `ถึง ${dayWord(end, base) === "วันนี้" ? "" : dayWord(end, base) + " "}${hhmmMs(end)}`, start: base, end };
  }
  m = /^D\+(\d+)/.exec(w);
  if (m && base != null) {
    const h0 = dayStart() * HOUR, today = base - ((base - h0) % DAY + DAY) % DAY, start = today + m[1] * DAY;
    return { top: m[1] === "1" ? "พรุ่งนี้ (D+1)" : `D+${m[1]}`, sub: `${dMon(start)} ${pad2(dayStart())}:00 → ${dMon(start + DAY)}`, start, end: start + DAY };
  }
  return { top: w, sub: "" };
}
const winHead = (w) => { const i = winInfo(w); return [i.top, i.sub ? el("span", { class: "wsub" }, i.sub) : null]; };
function renderTimebase() {
  const L = state.loc, base = baseMs(L);
  $("timebase").textContent = base == null ? "" :
    `⏱️ เวลาอ้างอิงของพยากรณ์ทั้งหน้า: ${dMon(base)} ${hhmmMs(base)} น. · “+N ชม.” นับจากเวลานี้ · 1 วัน = ${pad2(dayStart())}:00 → ${pad2(dayStart())}:00 น. (ตรงกับค่าวัดรายวันของกรมอุตุฯ) · ` +
    `HII ใช้ 19:00 → 19:00 น. · ที่มาและรอบรันดูที่ ⑤`;
}

const HEAT_LV = [[27, "🟢 ปกติ"], [33, "🟡 เฝ้าระวัง"], [42, "🟠 เตือนภัย"], [52, "🔴 อันตราย"], [Infinity, "🟣 อันตรายมาก"]];
const UV_LV = [[3, "🟢 ต่ำ"], [6, "🟡 ปานกลาง"], [8, "🟠 สูง"], [11, "🔴 สูงมาก"], [Infinity, "🟣 อันตราย"]];
const PM_LV = [[15.01, "🔵 ดีมาก"], [25.01, "🟢 ดี"], [37.51, "🟡 ปานกลาง"], [75.01, "🟠 เริ่มมีผลต่อสุขภาพ"], [Infinity, "🔴 มีผลต่อสุขภาพ"]];
function distKm(a, b, c, d) {
  const p = Math.PI / 180, x = Math.sin((c - a) * p / 2) ** 2 + Math.cos(a * p) * Math.cos(c * p) * Math.sin((d - b) * p / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(x));
}

function makeWindows(T, now, M) {
  const w = M.hour_windows.map((n) => ({ label: `+${n} ชม.`, kind: "hour",
    idx: T.flatMap((t, i) => (t > now && t <= now + n * HOUR ? [i] : [])), at: T.indexOf(now + n * HOUR) }));
  const h0 = (M.day_start_hour || 0) * HOUR;              // วันอุตุนิยมวิทยา 07:00–07:00 น. (= 00–00 UTC)
  const today = now - ((now - h0) % DAY + DAY) % DAY;
  for (const d of M.day_leads) {
    const start = today + d * DAY, dt = new Date(start);
    const dd = String(dt.getUTCDate()).padStart(2, "0"), mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
    w.push({ label: `D+${d} (${dd}/${mm})`, kind: "day", idx: T.flatMap((t, i) => (t > start && t <= start + DAY ? [i] : [])) });
  }
  return w;
}
function aggregate(arr, win, how) {
  if (how === "at") return win.at >= 0 ? arr[win.at] ?? null : null;
  const v = win.idx.map((i) => arr[i]);
  if (!v.length || v.some((x) => x == null)) return null;      // ไม่ครบช่วง = ไม่แสดง
  if (how === "sum") return v.reduce((a, b) => a + b, 0);
  if (how === "mean") return v.reduce((a, b) => a + b, 0) / v.length;
  return how === "max" ? Math.max(...v) : Math.min(...v);
}
const r1 = (v) => (v == null ? null : Math.round(v * 10) / 10);

async function buildLocalPayload(lat, lon) {
  if (!state.meta) state.meta = await getJSON("data/meta.json");
  if (!state.nowObs) state.nowObs = await getJSON("data/now_obs.json").catch(() => ({ tmd: [], air: [] }));
  const M = state.meta, thr = M.rain_threshold_mm;
  const q = (base, p) => `${base}?${new URLSearchParams({ latitude: lat.toFixed(4), longitude: lon.toFixed(4), timezone: "auto", ...p })}`;
  const [fc, aq, ...ens] = await Promise.all([
    getJSON(q("https://api.open-meteo.com/v1/forecast", { hourly: M.api_variables.join(","),
      models: M.models.map((m) => m.key).join(","), forecast_days: M.forecast_days })),
    getJSON(q("https://air-quality-api.open-meteo.com/v1/air-quality", { hourly: "pm2_5,uv_index", forecast_days: 7 })).catch(() => null),
    ...M.ensembles.map((e) => getJSON(q("https://ensemble-api.open-meteo.com/v1/ensemble",
      { hourly: "precipitation", models: e.key, forecast_days: M.forecast_days })).catch(() => null)),
  ]);
  const T = fc.hourly.time.map(naiveMs);
  const now = Math.floor((Date.now() + fc.utc_offset_seconds * 1000) / HOUR) * HOUR;
  const wins = makeWindows(T, now, M);

  // ข้อมูลรายชั่วโมง: data[ตัวแปร][ชื่อโมเดล] (ตัดโมเดลที่ไม่มีค่า)
  const data = {};
  for (const v of M.api_variables) {
    data[v] = {};
    for (const m of M.models) {
      const a = fc.hourly[`${v}_${m.key}`];
      if (a && a.some((x) => x != null)) data[v][m.name] = a;
    }
  }
  const tables = {};
  for (const [k, spec] of Object.entries(M.variables)) {
    const use = wins.filter((w) => (w.kind === "hour" ? spec.hour : spec.day));
    const rows = {};
    for (const [model, a] of Object.entries(data[spec.api] || {})) {
      const vals = use.map((w) => r1(aggregate(a, w, w.kind === "hour" ? spec.hour : spec.day)));
      if (vals.some((x) => x != null)) rows[model] = vals;
    }
    if (Object.keys(rows).length) tables[k] = { label: spec.label, unit: spec.unit, columns: use.map((w) => w.label), rows };
  }
  const rainRows = Object.values(tables.rain.rows);
  const col = (j) => rainRows.map((r) => r[j]).filter((x) => x != null);
  const consensus = {
    median: wins.map((_, j) => r1(median(col(j)))),
    min: wins.map((_, j) => (col(j).length ? Math.min(...col(j)) : null)),
    max: wins.map((_, j) => (col(j).length ? Math.max(...col(j)) : null)),
    n_models: wins.map((_, j) => col(j).length),
    agree_pct: wins.map((_, j) => (col(j).length ? Math.round(100 * col(j).filter((x) => x >= thr).length / col(j).length) : null)),
  };

  // ensemble: โอกาส (%) = สัดส่วนสมาชิกที่ฝนรวมในช่วง ≥ threshold
  const ensemble = {};
  ens.forEach((j, n) => {
    if (!j) return;
    const members = Object.keys(j.hourly).filter((k) => k.startsWith("precipitation") && j.hourly[k].some((x) => x != null));
    if (!members.length) return;
    const W = makeWindows(j.hourly.time.map(naiveMs), now, M);
    ensemble[`${M.ensembles[n].name} (${members.length})`] = W.map((w) => {
      const sums = members.map((k) => aggregate(j.hourly[k], w, "sum")).filter((x) => x != null);
      return sums.length >= 0.8 * members.length ? Math.round(100 * sums.filter((x) => x >= thr).length / sums.length) : null;
    });
  });

  // สรุปรายวัน (median ของทุกโมเดล) + PM2.5/UV จาก CAMS
  const aqT = aq ? aq.hourly.time.map(naiveMs) : [];
  const brief = wins.filter((w) => w.kind === "day").map((w) => {
    const med = (k) => { const t = tables[k]; if (!t) return null; const j = t.columns.indexOf(w.label);
      return j < 0 ? null : r1(median(Object.values(t.rows).map((r) => r[j]))); };
    const j = tables.rain.columns.indexOf(w.label);
    const vals = rainRows.map((r) => r[j]).filter((x) => x != null);
    const start = T[w.idx[0]] - HOUR, end = start + DAY;
    const aqIdx = aqT.flatMap((t, i) => (t > start && t <= end ? [i] : []));
    const pmV = aqIdx.map((i) => aq.hourly.pm2_5[i]).filter((x) => x != null);
    const uvA = aqIdx.map((i) => aq.hourly.uv_index[i]).filter((x) => x != null);
    const pm = pmV.length ? r1(pmV.reduce((a, b) => a + b, 0) / pmV.length) : null;
    const uvCand = [med("uv"), uvA.length ? Math.max(...uvA) : null].filter((x) => x != null);
    const uv = uvCand.length ? r1(Math.max(...uvCand)) : null;
    const feels = med("feels");
    return { label: w.label, rain: med("rain"), n_rain: vals.filter((x) => x >= thr).length, n_models: vals.length,
      tmin: med("tmin"), tmax: med("tmax"), feels, heat: pickLevel(feels, HEAT_LV), rh: med("rh"), gust: med("gust"),
      uv, uv_level: pickLevel(uv, UV_LV), pm25: pm, pm25_level: pickLevel(pm, PM_LV) };
  });

  // รายชั่วโมง 7 วันสำหรับกราฟ
  const hIdx = T.flatMap((t, i) => (t > now && t <= now + M.hourly_days * DAY ? [i] : []));
  const series = {};
  for (const v of M.hourly_vars) {
    series[v] = Object.fromEntries(Object.entries(data[v] || {}).map(([m, a]) => [m, hIdx.map((i) => a[i])]));
  }
  const aqByT = new Map(aqT.map((t, i) => [t, aq.hourly.pm2_5[i]]));
  const nearest = (list) => {
    let best = null, bd = Infinity;
    for (const s of list || []) { const d = distKm(lat, lon, s.lat, s.lon); if (d < bd) { bd = d; best = s; } }
    return best && bd <= M.max_station_km ? { ...best, dist_km: Math.round(bd * 10) / 10 } : null;
  };
  return {
    name: "📍 ตรงที่ฉันอยู่", lat, lon, threshold_mm: thr, fetched_at: new Date(now).toISOString().slice(0, 19),
    windows: wins.map((w) => w.label),
    models: Object.keys(data.precipitation).map((n) => ({ name: n, grid_km: (M.models.find((m) => m.name === n) || {}).grid_km })),
    tables, consensus, ensemble, brief,
    hourly: { time: hIdx.map((i) => fc.hourly.time[i]), series, pm25: aq ? hIdx.map((i) => aqByT.get(T[i]) ?? null) : null },
    now: { station: nearest(state.nowObs.tmd), air: nearest(state.nowObs.air) },
  };
}

async function selectGps(remember = true) {
  document.body.classList.remove("about-mode");
  if (location.hash === "#about") history.replaceState(null, "", location.pathname);
  if (remember) store.set("tab", "gps");
  markChips();
  const chip = $("gps-chip");
  if (!navigator.geolocation) { chip.textContent = "📍 เบราว์เซอร์นี้ไม่รองรับ GPS"; return; }
  chip.textContent = "📍 กำลังหาตำแหน่ง…";
  const pos = await new Promise((ok) => navigator.geolocation.getCurrentPosition(ok, () => ok(null),
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 5 * 60000 }));
  if (!pos) { chip.textContent = "📍 เปิด GPS ไม่ได้ (อนุญาตตำแหน่งในเบราว์เซอร์)"; return; }
  chip.textContent = "📍 กำลังคำนวณพยากรณ์…";
  document.body.style.opacity = "0.6";
  try {
    const { latitude: lat, longitude: lon } = pos.coords;
    state.loc = await buildLocalPayload(lat, lon);
    state.gps = { lat, lon };
    state.locIdx = -1;
    markChips();
    showLocation();
    chip.textContent = `📍 ตรงที่ฉันอยู่ (${lat.toFixed(3)}, ${lon.toFixed(3)})`;
  } catch (e) {
    chip.textContent = "📍 โหลดพยากรณ์ไม่ได้ ลองใหม่";
  } finally {
    document.body.style.opacity = "";
  }
}

// ---------------------------------------------------------------------------
// แถบบันทึกฝน: เปิดฟอร์ม GitHub Issue ที่กรอกไว้แล้ว → GitHub Actions เก็บลง branch data
// ---------------------------------------------------------------------------
function bangkokNow() {
  return new Date().toLocaleString("sv-SE", { timeZone: "Asia/Bangkok" }).slice(0, 16);   // "YYYY-MM-DD HH:MM"
}
function reportTarget() {
  if (state.gps) return { name: state.locIdx === -1 ? "ตรงที่ฉันอยู่ (GPS)" : "ตำแหน่ง GPS", lat: state.gps.lat, lon: state.gps.lon };
  const l = state.index.locations[state.locIdx];
  return { name: l.name, lat: l.lat, lon: l.lon };
}
function report(rained) {
  const t = reportTarget(), time = bangkokNow();
  const u = new URL(`https://github.com/${state.index.repo}/issues/new`);
  u.searchParams.set("template", "rain.yml");
  u.searchParams.set("labels", "rain-obs");
  u.searchParams.set("title", `${rained ? "🌧️ ฝนตก" : "☀️ ฝนไม่ตก"} · ${t.name} · ${time}`);
  u.searchParams.set("rain", rained ? "ตก" : "ไม่ตก");
  u.searchParams.set("location", t.name);
  u.searchParams.set("coords", `${t.lat.toFixed(5)}, ${t.lon.toFixed(5)}`);
  u.searchParams.set("time", time);
  window.open(u.toString(), "_blank", "noopener");
}
function initRainBar() {
  if (!state.index.repo) return;           // ดูในเครื่อง (ไม่มี repo) ก็ไม่แสดง
  $("rainbar").hidden = false;
  $("rb-rain").onclick = () => report(true);
  $("rb-dry").onclick = () => report(false);
  $("rb-gps").onclick = () => {
    if (!navigator.geolocation) { $("rb-gps").textContent = "📍 ไม่รองรับ GPS"; return; }
    $("rb-gps").textContent = "📍 กำลังหา…";
    navigator.geolocation.getCurrentPosition(
      (p) => { state.gps = { lat: p.coords.latitude, lon: p.coords.longitude }; $("rb-gps").textContent = "📍 ใช้ GPS แล้ว"; renderRainBar(); },
      () => { $("rb-gps").textContent = "📍 เปิด GPS ไม่ได้"; },
      { enableHighAccuracy: true, timeout: 15000 });
  };
}
function renderRainBar() {
  if ($("rainbar").hidden) return;
  const t = reportTarget();
  $("rb-loc").textContent = state.gps ? `ตำแหน่ง GPS (${t.lat.toFixed(4)}, ${t.lon.toFixed(4)})` : t.name;
  if (!state.gps) $("rb-gps").textContent = "📍 ใช้ GPS";
  const o = state.index.observations || { count: 0, recent: [] };
  const last = o.recent[0];
  $("rb-hint").textContent = "กดแล้วจะเปิดฟอร์มบน GitHub ที่กรอกไว้ให้ → กด “Create” (หรือ Submit) เพื่อยืนยัน" +
    (o.count ? ` · บันทึกแล้ว ${o.count} ครั้ง (ตก ${o.rain}) · ล่าสุด ${last.rained ? "🌧️" : "☀️"} ${last.location} ${last.obs_time.slice(5, 16)}` : "");
}

// ---------------------------------------------------------------------------
// ตอนนี้: ค่าวัดจริง (สถานีกรมอุตุฯ, Air4Thai) + ค่าจากโมเดลที่ดึงสดในเบราว์เซอร์
// ---------------------------------------------------------------------------
const WMO = {
  0: "☀️ ท้องฟ้าแจ่มใส", 1: "🌤️ ส่วนใหญ่แจ่มใส", 2: "⛅ มีเมฆบางส่วน", 3: "☁️ เมฆมาก",
  45: "🌫️ หมอก", 48: "🌫️ หมอกน้ำค้างแข็ง", 51: "🌦️ ฝนละอองเบา", 53: "🌦️ ฝนละออง", 55: "🌦️ ฝนละอองหนาแน่น",
  61: "🌧️ ฝนเล็กน้อย", 63: "🌧️ ฝนปานกลาง", 65: "🌧️ ฝนหนัก", 80: "🌦️ ฝนซู่เล็กน้อย", 81: "🌧️ ฝนซู่ปานกลาง",
  82: "⛈️ ฝนซู่หนัก", 95: "⛈️ พายุฝนฟ้าคะนอง", 96: "⛈️ พายุฝนฟ้าคะนอง ลูกเห็บ", 99: "⛈️ พายุฝนฟ้าคะนอง ลูกเห็บหนัก",
};
const DIRS = ["เหนือ", "ตะวันออกเฉียงเหนือ", "ตะวันออก", "ตะวันออกเฉียงใต้", "ใต้", "ตะวันตกเฉียงใต้", "ตะวันตก", "ตะวันตกเฉียงเหนือ"];
const dirText = (deg) => (deg == null ? "" : `จากทิศ${DIRS[Math.round(deg / 45) % 8]}`);
const hhmm = (iso) => (iso ? localTime(iso).toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" }) : "–");
const currentCache = {};

function stationCard(s) {
  if (!s) {
    return el("article", { class: "card" }, el("h3", {}, "📡 วัดจริง (กรมอุตุฯ)"),
      el("p", { class: "warn" }, "ไม่มีสถานีอุตุฯ ในรัศมี 60 กม."));
  }
  const age = (Date.now() - localTime(s.time).getTime()) / 3600000;
  return el("article", { class: "card" },
    el("h3", {}, `📡 วัดจริง · สถานี${s.name}`),
    el("div", { class: "src" }, `ห่าง ${fmt(s.dist_km)} กม. · เวลา ${hhmm(s.time)} น. (อัปเดตทุก 3 ชม.)`),
    el("div", { class: "hero" }, fmt(s.temp), el("small", {}, " °C")),
    el("dl", { class: "kv" },
      el("dt", {}, "🌧️ ฝน 3 ชม.ล่าสุด"), el("dd", {}, `${fmt(s.rain_3h)} มม.`),
      el("dt", {}, "🌧️ ฝน 24 ชม."), el("dd", {}, `${fmt(s.rain_24h)} มม.`),
      el("dt", {}, "💧 ความชื้น"), el("dd", {}, `${fmt(s.rh, 0)} %`),
      el("dt", {}, "💨 ลม"), el("dd", {}, `${fmt(s.wind_kmh, 0)} กม./ชม. ${dirText(s.wind_dir)}`),
      el("dt", {}, "👁️ ทัศนวิสัย"), el("dd", {}, `${fmt(s.visibility_km, 0)} กม.`)),
    s.dist_km > 20 ? el("p", { class: "warn" }, "ℹ️ สถานีอยู่ไกล ค่าอาจต่างจากจุดของคุณ โดยเฉพาะฝน") : null,
    age > 4 ? el("p", { class: "warn" }, `⚠️ ข้อมูลเก่า ${Math.round(age)} ชม.`) : null);
}

function airCard(a) {
  if (!a) {
    return el("article", { class: "card" }, el("h3", {}, "😷 PM2.5 วัดจริง (Air4Thai)"),
      el("p", { class: "warn" }, "ไม่มีสถานีตรวจวัดในรัศมี 60 กม."));
  }
  return el("article", { class: "card" },
    el("h3", {}, `😷 PM2.5 วัดจริง · ${a.name}`),
    el("div", { class: "src" }, `ห่าง ${fmt(a.dist_km)} กม. · เวลา ${hhmm(a.time)} น. (รายชั่วโมง)`),
    el("div", { class: "hero" }, fmt(a.pm25), el("small", {}, " มคก./ลบ.ม."), " ", statusPill(a.level)),
    el("div", { class: "hero-sub" }, a.area || ""));
}

function modelCard(c) {
  const card = el("article", { class: "card", id: "now-model" }, el("h3", {}, "🖥️ ตอนนี้จากโมเดล (Open-Meteo)"));
  if (!c) { card.append(el("div", { class: "src" }, "กำลังโหลด…")); return card; }
  if (c.error) { card.append(el("p", { class: "warn" }, "โหลดไม่ได้")); return card; }
  card.append(
    el("div", { class: "src" }, `เวลา ${hhmm(c.time)} น. · ค่าจากโมเดล (ไม่ใช่ค่าวัด) อัปเดตทุก 15 นาที`),
    el("div", { class: "hero" }, fmt(c.temperature_2m), el("small", {}, " °C")),
    el("div", { class: "hero-sub" }, WMO[c.weather_code] || `รหัสอากาศ ${c.weather_code}`),
    el("dl", { class: "kv" },
      el("dt", {}, "🌧️ ฝน 15 นาทีล่าสุด"), el("dd", {}, `${fmt(c.precipitation)} มม.`),
      el("dt", {}, "🥵 รู้สึกเหมือน"), el("dd", {}, `${fmt(c.apparent_temperature, 0)} °C`),
      el("dt", {}, "💧 ความชื้น"), el("dd", {}, `${fmt(c.relative_humidity_2m, 0)} %`),
      el("dt", {}, "💨 ลมกระโชก"), el("dd", {}, `${fmt(c.wind_gusts_10m, 0)} กม./ชม.`),
      el("dt", {}, "☁️ เมฆ"), el("dd", {}, `${fmt(c.cloud_cover, 0)} %`)));
  return card;
}

async function fetchCurrent(lat, lon) {
  const key = `${lat},${lon}`, hit = currentCache[key];
  if (hit && Date.now() - hit.at < 10 * 60000) return hit.data;
  const u = new URL("https://api.open-meteo.com/v1/forecast");
  u.search = new URLSearchParams({
    latitude: lat, longitude: lon, timezone: "Asia/Bangkok",
    current: "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,cloud_cover,wind_gusts_10m",
  });
  const j = await getJSON(u.toString());
  currentCache[key] = { at: Date.now(), data: j.current };
  return j.current;
}

function renderNow() {
  const L = state.loc, n = L.now || {};
  $("now").replaceChildren(stationCard(n.station), airCard(n.air), modelCard(null));
  const idx = state.locIdx;
  fetchCurrent(L.lat, L.lon)
    .then((c) => { if (state.locIdx === idx) $("now-model").replaceWith(modelCard(c)); })
    .catch(() => { if (state.locIdx === idx) $("now-model").replaceWith(modelCard({ error: true })); });
}

// ---------------------------------------------------------------------------
// โมเดลไหนแม่น: คะแนนจากการเทียบกับค่าวัดจริง (data/verification.json)
// ---------------------------------------------------------------------------
// แหล่งค่าวัดในโหมด WMO: เฉพาะที่ใช้ตอบคำถามวิจัยใน paper (docs/paper_outline.md)
//   RQ1–2 เมือง vs ชนบท / เกาะความร้อน → สถานีอุตุฯ ทั่วประเทศ
//   RQ3 โมเดลละเอียดสูงในเมือง (TMD WRF 2 กม., HII 3 กม., Google) → เครื่องวัดฝนหนาแน่นในเมือง
//   RQ4 การแจ้งของประชาชน → ปุ่มบนหน้าเว็บ
// (สถานีอุตุฯ ใกล้ตำแหน่ง tmd3h ยังคำนวณอยู่ แต่ซ้ำกับส่วนทั่วประเทศ จึงไม่แสดง)
const V_SOURCES = [
  { key: "national", icon: "🗺️", title: "สถานีอุตุฯ ทั่วประเทศ", desc: "~120 สถานี + โทรมาตร ~3,200 เครื่อง · เมือง vs ชนบท (RQ1–2)",
    vars: [["national_rain", "ฝนรายวัน"], ["national3h_rain", "ฝน 3 ชม."], ["national_tmin", "อุณหภูมิต่ำสุด"],
      ["national_tmax", "อุณหภูมิสูงสุด"], ["national3h_temp", "อุณหภูมิ 3 ชม."], ["allgauge_rain", "ฝนรายวัน · โทรมาตรทุกเครื่อง"]],
    wait: "คำนวณวันละครั้งหลังกรมอุตุฯ สรุปผล 07.00 น." },
  { key: "tw", icon: "🏙️", title: "เครื่องวัดฝนในเมือง", desc: "โทรมาตรทุกเครื่องในเขตเมือง + จุดตรวจ กทม./อยุธยา · โมเดลละเอียดสูง (RQ3)",
    vars: [["urbangauge_rain", "ฝนรายวัน · ทุกเครื่องในเขตเมือง"], ["thaiwater24h_rain", "ฝนรายวัน · จุดตรวจ"], ["tw3h_rain", "ฝน 3 ชม."],
      ["hii24h_rain", "ฝน 19–19 น. (เทียบ HII)"]], kind: "tw",
    wait: "ใช้รอบรันที่ออกก่อนต้นช่วงวัด · HII ออกผลวันละรอบ 19:00 น." },
  { key: "user", icon: "🙋", title: "คนแจ้งผ่านปุ่ม", desc: "ตก / ไม่ตก จากปุ่มด้านบนสุด (RQ4)",
    vars: [["user_rain", "ตก/ไม่ตก"]], wait: "ยังไม่มีการกดปุ่ม 🌧️/☀️ ด้านบน" },
];
function leadText(l) {
  if (l.includes("|")) { const [b, g] = l.split("|"); return `${leadText(b)} · ${g}`; }
  if (l.startsWith("T+")) return `T+${l.slice(2).replace("-", "–")} ชม.`;     // นับจากรอบรัน (WMO)
  if (l.startsWith("D+")) return `Day ${l.slice(2)}`;
  if (/^H\d+$/.test(l)) return `ล่วงหน้า ≥ ${l.slice(1)} ชม.`;
  return `ล่วงหน้า ${l}`;
}
function habit(r) {
  if (r.bias == null) return el("span", { class: "habit" }, "–");
  if (r.bias > 1.25) return el("span", { class: "habit" }, "↑ ทายฝนบ่อยเกิน");
  if (r.bias < 0.8) return el("span", { class: "habit" }, "↓ ทายฝนน้อยเกิน");
  return el("span", { class: "habit ok" }, "✓ พอดี");
}
function tempHabit(r) {
  if (r.bias == null) return "–";
  if (Math.abs(r.bias) < 0.3) return "✓ ใกล้เคียง";
  return `${r.bias > 0 ? "↑ ทายสูงไป" : "↓ ทายต่ำไป"} ${fmt(Math.abs(r.bias))} °C`;
}

// ---------------------------------------------------------------------------
// 👤 มุมผู้ใช้: ถ้าเว็บบอกว่าฝนจะตก เชื่อได้แค่ไหน (verification.json → user_mode)
// ---------------------------------------------------------------------------
const U_WIN_TEXT = { "+3": "อีก 3 ชม.", "+6": "อีก 6 ชม.", "+12": "อีก 12 ชม.", "+24": "อีก 24 ชม.", "D+1": "พรุ่งนี้", "D+3": "อีก 3 วัน", "D+7": "อีก 7 วัน" };
const winKey = (w) => (/^\+(\d+)/.exec(w) ? `+${/^\+(\d+)/.exec(w)[1]}` : /^D\+(\d+)/.exec(w) ? `D+${/^D\+(\d+)/.exec(w)[1]}` : w);
function userScope() {
  const U = state.verify?.user_mode;
  if (!U) return null;
  const name = state.loc?.name;
  return { name: name && U.scopes[name] ? name : "ทุกตำแหน่ง", data: U.scopes[name] || U.scopes["ทุกตำแหน่ง"] || {} };
}
function medianRow(w) {
  const U = state.verify?.user_mode, sc = userScope();
  return U && sc ? ((sc.data[w] || {}).rows || []).find((r) => r.model === U.median) : null;
}
/** ป้ายสั้น ๆ ใต้การ์ดพยากรณ์: ที่ผ่านมาค่ากลางทุกโมเดลของช่วงนี้แม่นแค่ไหน */
function trustBadge(w) {
  const r = medianRow(winKey(w));
  if (!state.verify?.user_mode) return null;
  if (!r || r.n < 10 || r.n_rain < 3)
    return el("div", { class: "trust wait", title: "ต้องมีข้อมูลอย่างน้อย 10 ครั้ง และฝนตกจริงอย่างน้อย 3 ครั้ง" }, `📊 กำลังสะสมข้อมูล (${r ? r.n : 0} ครั้ง)`);
  return el("div", { class: "trust", title: `ค่ากลางทุกโมเดล · ${r.n} ครั้ง · ${state.verify.user_mode.days} วันล่าสุด` },
    `📊 ที่ผ่านมา: บอกว่าตก → ตกจริง ${fmt(r.sr10 ?? 0, 0)}/10 · ฝนตก → เตือนไว้ ${fmt(r.pod10 ?? 0, 0)}/10`);
}
function renderUserMode() {
  const V = state.verify, U = V?.user_mode;
  const mode = store.get("v-mode") || "user";
  $("vm-user").setAttribute("aria-selected", String(mode === "user"));
  $("vm-wmo").setAttribute("aria-selected", String(mode === "wmo"));
  $("vm-user").onclick = () => { store.set("v-mode", "user"); renderUserMode(); };
  $("vm-wmo").onclick = () => { store.set("v-mode", "wmo"); renderUserMode(); };
  $("v-user").hidden = mode !== "user";
  $("v-wmo").hidden = mode !== "wmo";
  if (mode !== "user") return;
  const sc = userScope();
  if (!U || !sc) {
    $("u-summary").replaceChildren(el("p", {}, "⏳ กำลังสะสมข้อมูล — ระบบเริ่มเก็บแบบมุมผู้ใช้แล้ว"));
    return;
  }
  const wins = U.windows.filter((w) => sc.data[w]);
  const w = wins.includes(state.uWin) ? state.uWin : (wins.includes("+3") ? "+3" : wins[0]);
  state.uWin = w;
  $("u-wins").replaceChildren(...wins.map((x) => el("button", { class: "chip", role: "tab", type: "button",
    "aria-selected": String(x === w), onclick: () => { state.uWin = x; renderUserMode(); } }, U_WIN_TEXT[x] || x)));
  const rows = ((sc.data[w] || {}).rows || []).filter((r) => r.n >= 3);
  const med = rows.find((r) => r.model === U.median);
  const models = rows.filter((r) => r.model !== U.median && r.model !== U.ens && r.n_rain >= 3);
  const best = [...models].sort((a, b) => (b.csi ?? 0) - (a.csi ?? 0))[0];
  const scopeTxt = sc.name === "ทุกตำแหน่ง" ? "ทุกจุดตรวจ" : `จุดตรวจของ${sc.name}`;
  $("u-summary").replaceChildren(el("p", {}, ...(med ? [
    el("b", {}, U_WIN_TEXT[w]), `: ถ้าเว็บนี้ (ค่ากลางทุกโมเดล) บอกว่าฝนตก → `, el("b", {}, `ตกจริง ${fmt(med.sr10 ?? 0, 0)} ใน 10 ครั้ง`),
    ` · ถ้าฝนตกจริง → `, el("b", {}, `เตือนไว้ก่อน ${fmt(med.pod10 ?? 0, 0)} ใน 10 ครั้ง`),
    ` (${med.n.toLocaleString("th-TH")} ครั้ง, ฝนตกจริง ${med.n_rain} ครั้ง, ${scopeTxt})`,
    best ? [` · แหล่งที่ทายฝนเก่งสุดช่วงนี้: `, el("b", {}, best.model)] : "",
    med.n < 30 ? " · ข้อมูลยังน้อย ผลอาจเปลี่ยน" : ""].flat() : ["⏳ ยังไม่มีข้อมูลพอสำหรับช่วงนี้"])));
  const bar = (x) => el("td", { class: "barcell" }, el("span", { class: "meter wide", "aria-hidden": "true" },
    el("i", { style: `width:${Math.max(3, (x ?? 0) * 10)}%` })), " ", el("b", {}, x == null ? "–" : `${fmt(x, 0)}/10`));
  const order = [med, rows.find((r) => r.model === U.ens), ...rows.filter((r) => r.model !== U.median && r.model !== U.ens)].filter(Boolean);
  $("u-table").replaceChildren(el("table", { class: "v-simple" },
    el("thead", {}, el("tr", {}, ...["แหล่ง", "บอกว่าตก → ตกจริง", "ฝนตก → เตือนไว้", "ทายถูก", `ฝนหนัก (≥ ${U.heavy_mm[w]} มม.) เตือนได้`, "ครั้ง"]
      .map((h, i) => el("th", { style: i === 0 ? "text-align:left" : null }, h)))),
    el("tbody", {}, order.map((r) => el("tr", { class: r.model === U.median ? "accent" : null },
      el("th", { scope: "row" }, r.model, r.model === U.median ? el("span", { class: "km" }, "ตัวที่หน้าเว็บแสดง") : null),
      bar(r.sr10), bar(r.pod10), el("td", { class: "num" }, r.acc == null ? "–" : `${fmt(r.acc, 0)}%`),
      el("td", { class: "num" }, r.heavy_n ? `${Math.round((r.heavy_pod10 ?? 0) * r.heavy_n / 10)}/${r.heavy_n}` : "ยังไม่มีฝนหนัก"),
      el("td", { class: "num" }, r.n.toLocaleString("th-TH")))))));
  // ช่วงของวัน (ค่ากลางทุกโมเดล)
  const tod = (sc.data[w] || {}).tod || {};
  const TODS = [["เช้า", "07–13 น."], ["บ่าย", "13–19 น."], ["ค่ำ", "19–01 น."], ["ดึก", "01–07 น."]].filter(([t]) => tod[t]);
  $("u-tod").replaceChildren(...(TODS.length ? [el("h4", {}, `ช่วงของวัน (เริ่มช่วง${U_WIN_TEXT[w]})`),
    el("table", { class: "v-simple" }, el("thead", {}, el("tr", {}, ...["ช่วง", "บอกตก → ตกจริง", "ฝนตก → เตือนไว้", "ครั้ง"].map((h) => el("th", {}, h)))),
      el("tbody", {}, TODS.map(([t, hh]) => el("tr", {}, el("th", { scope: "row" }, `${t} `, el("span", { class: "km" }, hh)),
        el("td", { class: "num" }, tod[t].sr10 == null ? "–" : `${fmt(tod[t].sr10, 0)}/10`),
        el("td", { class: "num" }, tod[t].pod10 == null ? "–" : `${fmt(tod[t].pod10, 0)}/10`),
        el("td", { class: "num" }, tod[t].n)))))] : []));
  // โอกาสฝน % เชื่อได้ไหม
  const rel = (sc.data._reliability || []).filter((b) => b.n);
  $("u-rel").replaceChildren(...(w === "+3" && rel.length ? [el("h4", {}, "“โอกาสฝน %” เชื่อได้ไหม (อีก 3 ชม.)"),
    el("table", { class: "v-simple" }, el("thead", {}, el("tr", {}, ...["เว็บบอก", "ฝนตกจริง", "ครั้ง"].map((h) => el("th", {}, h)))),
      el("tbody", {}, rel.map((b) => el("tr", {}, el("th", { scope: "row" }, `${b.lo}–${b.hi}%`),
        el("td", { class: "num" }, b.freq == null ? "–" : `${b.freq}%`), el("td", { class: "num" }, b.n))))),
    el("p", { class: "note" }, "ถ้าเชื่อได้ ตัวเลขสองคอลัมน์ควรใกล้กัน เช่น บอก 60–80% → ฝนตกจริงราว 70%")] : []));
  $("u-note").textContent = `${U.days} วันล่าสุด (เริ่ม ${U.since || "–"}) · ค่าวัด: เครื่องวัดฝนโทรมาตรรายชั่วโมง + สถานีอุตุฯ ราย 3 ชม. ที่จุดตรวจ · ` +
    `Google Weather มีเฉพาะจุดตรวจของตำแหน่งแรกและล่วงหน้า 48 ชม. · ฝนหนัก: +3/+6 ชม. ≥ 10, +12/+24 ชม. ≥ 20, รายวัน ≥ 35 มม.`;
}

function renderVerify() {
  const V = state.verify;
  if (!V) return;
  const byId = Object.fromEntries(V.sections.map((x) => [x.id, x]));
  const obsOf = (src) => src.vars.reduce((a, [id]) => a + (byId[id]?.n_obs || 0), 0);
  const saved = store.get("v-source");
  const src = V_SOURCES.find((x) => x.key === (state.vSource || saved)) || V_SOURCES.find((x) => obsOf(x)) || V_SOURCES[0];
  state.vSource = src.key;

  // การ์ดแหล่งค่าวัดจริง
  $("v-sources").replaceChildren(...V_SOURCES.map((x) => {
    const n = obsOf(x);
    const pts = (V.points || []).filter((p) => p.kind === x.kind).length;
    return el("button", { class: "v-card", role: "tab", type: "button", "aria-selected": String(x.key === src.key),
      onclick: () => { state.vSource = x.key; state.vSection = null; state.vLead = null; store.set("v-source", x.key); renderVerify(); } },
      el("span", { class: "v-ico" }, x.icon),
      el("b", {}, x.title),
      el("span", { class: "v-desc" }, x.desc + (pts ? ` · ${pts} จุดตรวจ` : "")),
      el("span", { class: n ? "v-badge ok" : "v-badge" }, n ? `มีผลแล้ว ${n.toLocaleString("th-TH")} ครั้ง` : "⏳ กำลังสะสมข้อมูล"));
  }));

  // ตัวแปร (ฝน/อุณหภูมิ) และช่วงล่วงหน้า
  const secId = src.vars.some(([id]) => id === state.vSection) ? state.vSection : src.vars[0][0];
  state.vSection = secId;
  $("v-vars").replaceChildren(...(src.vars.length > 1 ? src.vars.map(([id, lab]) =>
    el("button", { class: "chip", role: "tab", type: "button", "aria-selected": String(id === secId),
      onclick: () => { state.vSection = id; state.vLead = null; renderVerify(); } }, lab)) : []));
  const pick = byId[secId] || { leads: [], tables: {}, n_obs: 0 };
  // ค่าเริ่มต้น: ช่วงที่มีโมเดลให้เทียบมากที่สุด (เท่ากันใช้ที่มีจำนวนครั้งมากกว่า)
  const score = (l) => { const t = (pick.tables || {})[l] || []; return t.length * 1e6 + t.reduce((a, r) => a + (r.n || 0), 0); };
  const best = [...pick.leads].sort((a, b) => score(b) - score(a))[0];
  const lead = pick.leads.includes(state.vLead) ? state.vLead : best;
  state.vLead = lead;
  $("v-leads").replaceChildren(...pick.leads.map((l) =>
    el("button", { class: "chip", role: "tab", type: "button", "aria-selected": String(l === lead),
      onclick: () => { state.vLead = l; renderVerify(); } }, leadText(l),
      el("span", { class: "chip-n" }, ` · ${((pick.tables || {})[l] || []).length} โมเดล`))));
  $("v-sub").textContent = `${V.days} วันล่าสุด · เริ่มเก็บ ${V.since || "วันนี้"} · อัปเดต ${timeAgo(V.updated)}`;

  const rows = (pick.tables || {})[lead] || [];
  const rain = pick.variable === "rain";
  if (!rows.length) {
    $("v-summary").replaceChildren(el("p", {}, `⏳ กำลังสะสมข้อมูล — ${src.wait}`));
    ["v-table", "v-table-full", "v-ens"].forEach((id) => $(id).replaceChildren());
    $("v-note").textContent = "";
  } else {
    // สรุปเป็นประโยคธรรมดา
    const best = rows[0], worst = rows[rows.length - 1];
    const few = best.n < 30 ? " (ข้อมูลยังน้อย ผลอาจเปลี่ยน)" : "";
    const sum = rain
      ? [el("b", {}, leadText(lead)), `: `, el("b", {}, best.model), ` ทายฝนเก่งที่สุด — คะแนนทายฝน ${fmt(best.csi, 0)} จาก 100 · `,
         `ทายตก/ไม่ตกถูก ${fmt(best.acc, 0)}% จาก ${best.n.toLocaleString("th-TH")} ครั้ง`, few,
         rows.length > 1 ? ` · ต่ำสุดคือ ${worst.model} (${fmt(worst.csi, 0)})` : ""]
      : [el("b", {}, leadText(lead)), `: `, el("b", {}, best.model), ` แม่นที่สุด — คลาดเฉลี่ย ${fmt(best.mae)} °C (${tempHabit(best)})`, few,
         rows.length > 1 ? ` · คลาดมากสุดคือ ${worst.model} (${fmt(worst.mae)} °C)` : ""];
    $("v-summary").replaceChildren(el("p", {}, ...sum));

    // ตารางแบบง่าย
    const worstMae = Math.max(...rows.map((r) => r.mae || 0), 0.1);
    const barCell = (pct, text) => el("td", { class: "barcell" },
      el("span", { class: "meter wide", "aria-hidden": "true" }, el("i", { style: `width:${Math.max(3, pct)}%` })), " ", el("b", {}, text));
    const head = rain ? ["#", "โมเดล", "คะแนนทายฝน (0–100)", "ทายถูก", "นิสัย", "ครั้ง"]
                      : ["#", "โมเดล", "คลาดเฉลี่ย (ยิ่งสั้นยิ่งดี)", "มักทาย", "ครั้ง"];
    $("v-table").replaceChildren(el("table", { class: "v-simple" },
      el("thead", {}, el("tr", {}, ...head.map((h, i) => el("th", { style: i === 1 ? "text-align:left" : null }, h)))),
      el("tbody", {}, rows.map((r, i) => el("tr", { class: r.model === state.highlight ? "accent" : null },
        el("td", { class: "rank" }, i === 0 ? "🥇" : i === 1 ? "🥈" : i === 2 ? "🥉" : i + 1),
        el("th", { scope: "row" }, r.model, r.grid_km ? el("span", { class: "km" }, `${r.grid_km} กม.`) : null),
        ...(rain
          ? [barCell(r.csi ?? 0, fmt(r.csi, 0)), el("td", { class: "num" }, `${fmt(r.acc, 0)}%`), el("td", {}, habit(r)),
             el("td", { class: "num" }, r.n.toLocaleString("th-TH"))]
          : [barCell(100 * (r.mae || 0) / worstMae, `${fmt(r.mae)} °C`), el("td", {}, tempHabit(r)),
             el("td", { class: "num" }, r.n.toLocaleString("th-TH"))]))))));

    // รายละเอียดเชิงเทคนิค
    const cols = rain
      ? [["csi", "CSI %"], ["acc", "ทายถูก %"], ["pod", "POD %"], ["far", "FAR %"], ["bias", "Bias"], ["mae", "MAE มม."], ["n", "n"]]
      : [["mae", "MAE °C"], ["bias", "Bias °C"], ["rmse", "RMSE °C"], ["n", "n"]];
    $("v-table-full").replaceChildren(el("table", {},
      el("thead", {}, el("tr", {}, el("th", { style: "text-align:left" }, "โมเดล"), ...cols.map(([, h]) => el("th", {}, h)))),
      el("tbody", {}, rows.map((r) => el("tr", {}, el("th", { scope: "row" }, r.model),
        ...cols.map(([k]) => el("td", { class: "num" }, r[k] == null ? "–" : fmt(r[k], k === "n" ? 0 : k === "bias" && rain ? 2 : 1))))))));
    const ens = (pick.ens_tables || {})[lead] || [];
    $("v-ens").replaceChildren(...(ens.length ? [el("table", {},
      el("thead", {}, el("tr", {}, el("th", { style: "text-align:left" }, "Ensemble (โอกาสฝน)"),
        ...["Brier (ต่ำดี)", "ทายถูก %", "POD %", "FAR %", "n"].map((h) => el("th", {}, h)))),
      el("tbody", {}, ens.map((r) => el("tr", {}, el("th", { scope: "row" }, r.model),
        ...["brier", "acc", "pod", "far", "n"].map((k) => el("td", { class: "num" }, r[k] == null ? "–" : fmt(r[k], k === "brier" ? 3 : k === "n" ? 0 : 1)))))))] : []));
    const thr = !rain ? "" : ["tmd3h", "tw3h", "user", "national3h"].includes(pick.source) ? "นับว่า “ฝนตก” เมื่อ ≥ 0.2 มม. ใน 3 ชม." : "นับว่า “ฝนตก” เมื่อ ≥ 1 มม. ต่อวัน";
    $("v-note").textContent = `ค่าวัด ${pick.n_obs.toLocaleString("th-TH")} ครั้ง จาก ${pick.n_points} จุด${thr ? " · " + thr : ""}`;
  }
  const pts = (V.points || []).filter((p) => !src.kind || p.kind === src.kind);
  $("v-points").replaceChildren(...(pts.length ? [el("b", {}, "จุดตรวจที่ใช้:"),
    el("ul", {}, pts.map((p) => el("li", {}, `${p.for} → ${p.name} (ห่าง ${fmt(p.dist_km)} กม.)`)))] :
    src.key === "national" ? [el("div", {}, "ทั่วประเทศ: สถานีอุตุฯ ~124 แห่ง (สรุป 07.00 น.) เทียบพยากรณ์ที่ทายไว้ล่วงหน้า ≥ 24, 72, 168 ชม. ทุกชั่วโมง (Open-Meteo Previous Runs)")] : []));
}
// ---------------------------------------------------------------------------
// 🕐 รอบรันของแต่ละโมเดล (init time) ของข้อมูลที่แสดงอยู่
// ---------------------------------------------------------------------------
const thTime = (ms) => new Date(ms).toLocaleString("th-TH", { timeZone: "Asia/Bangkok", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
function renderRuns() {
  const L = state.loc, runs = L.runs || {};
  const names = [...L.models.map((m) => m.name), ...Object.keys(L.ensemble || {}).map((k) => k.replace(/ \(\d+\)$/, ""))];
  const rows = [...new Set(names)].filter((n) => runs[n]);
  if (state.hii && L.name in (state.hii.points || {})) rows.push("HII WRF-ROMS");
  const hiiRun = state.hii ? { init: state.hii.init_utc.replace(" ", "T"), source: "hii" } : null;
  const SRC = { meta: "✓ ยืนยันแล้ว", estimated: "≈ ประมาณ", continuous: "ต่อเนื่อง", hii: "✓ จากภาพ สสน." };
  const fetched = L.fetched_at ? naiveMs(L.fetched_at.slice(0, 16)) - 7 * HOUR : Date.now();                     // เวลาที่ดึง (UTC)
  $("runs-sub").textContent = rows.length ? `ดึงเมื่อ ${thTime(fetched)} น. (เวลาอ้างอิงของหน้านี้)` : "ตำแหน่งนี้คำนวณในเบราว์เซอร์ ใช้รอบล่าสุดของ Open-Meteo";
  $("runs-table").replaceChildren(...(rows.length ? [el("table", {},
    el("thead", {}, el("tr", {}, ...["โมเดล", "รอบรัน (UTC)", "เวลาไทย", "อายุรอบ ณ เวลาดึง", "ที่มา"].map((h, i) =>
      el("th", { style: i === 0 ? "text-align:left" : null }, h)))),
    el("tbody", {}, rows.map((n) => {
      const r = n === "HII WRF-ROMS" ? hiiRun : runs[n];
      const t = naiveMs(r.init.slice(0, 16));
      const age = Math.round((fetched - t) / HOUR);
      const utc = new Date(t).toISOString().slice(5, 16).replace("T", " ").replace("-", "/");
      return el("tr", {}, el("th", { scope: "row" }, n),
        el("td", { class: "num" }, r.source === "continuous" ? "–" : `${utc}Z`),
        el("td", { class: "num" }, r.source === "continuous" ? "–" : thTime(t)),
        el("td", { class: "num" }, r.source === "continuous" ? "อัปเดตต่อเนื่อง" : `${age} ชม.`),
        el("td", {}, SRC[r.source] || r.source));
    })))] : []));
}

// ---------------------------------------------------------------------------
// 🌊 HII WRF-ROMS: ช่วงฝน 7 วันที่อ่านจากภาพของ สสน. (data/hii.json)
// ---------------------------------------------------------------------------
function renderHii() {
  const sec = $("hii-section");
  if (state.hii === undefined) {
    state.hii = null;
    getJSON("data/hii.json").then((j) => { state.hii = j; if (state.loc) { renderHii(); renderRuns(); } }).catch(() => {});
  }
  const H = state.hii, days = H && state.loc && (H.points || {})[state.loc.name];
  sec.hidden = !days;
  if (!days) return;
  const init = naiveMs(H.init_utc.replace(" ", "T").slice(0, 16));
  $("hii-sub").textContent = `รอบรัน ${new Date(init).toISOString().slice(0, 16).replace("T", " ")}Z (${thTime(init)} น.) · ${H.model.agency}`;
  const ms = (x) => naiveMs(x.replace(" ", "T").slice(0, 16));
  const base = baseMs() ?? Date.now();
  const left = days.filter((d) => ms(d.end) > base);                    // ช่วงที่ยังไม่จบ
  $("hii-tiles").replaceChildren(...left.map((d) => {
    const s0 = ms(d.start), e0 = ms(d.end);
    return el("div", { class: "tile" },
      el("div", { class: "t" }, `${dayWord(s0, base) === "วันนี้" ? "คืนนี้" : dMon(s0)} 19:00 →`,
        el("span", { class: "wsub" }, `${dayWord(e0, base)} 19:00 · รอบ Day ${d.day}`)),
      el("div", { class: "v" }, d.high <= 1 ? "< 1" : `${fmt(d.low, 0)}–${fmt(d.high, 0)}`, el("small", {}, " มม.")),
      el("div", { class: "d" }, d.high <= 1 ? "☀️ ไม่มีฝน" : d.low >= 35 ? "⛈️ ฝนหนัก" : d.low >= 10 ? "🌧️ ปานกลาง" : "🌦️ เล็กน้อย"),
      el("div", { class: "d" }, `กริด ${d.domain_km} กม.${s0 < base ? " · กำลังอยู่ในช่วงนี้" : ""}`));
  }));
  $("hii-note").textContent = "สสน. เผยแพร่เป็นภาพแผนที่ (ไม่มีตัวเลข) ระบบอ่านสีที่พิกัดเป็นช่วงฝนตามแถบสีของภาพ · Day 1–3 จากโดเมนไทย 3 กม., Day 4–7 จากโดเมนอาเซียน 9 กม. · " +
    "แต่ละช่อง = ฝนสะสม 24 ชม. 19:00 → 19:00 น. (12–12 UTC) ของ สสน. ซึ่งเลื่อนจากวัน 07:00 → 07:00 น. ของตารางด้านบน 12 ชม. จึงแสดงแยก (ไม่รวมใน median)";
}

// ---------------------------------------------------------------------------
// การ์ดสรุปรายวัน
// ---------------------------------------------------------------------------
function renderBrief() {
  const L = state.loc;
  $("brief").replaceChildren(...L.brief.map((b, i) => {
    const wi = winInfo(b.label);
    const title = [wi.top, el("span", { class: "wsub" }, wi.sub)];
    return el("article", { class: "card" },
      el("h3", {}, ...title),
      el("div", { class: "hero" }, fmt(b.rain), el("small", {}, " มม.")),
      el("div", { class: "hero-sub" }, `🌧️ ${b.n_rain} จาก ${b.n_models} โมเดลว่าฝนตก (≥ ${L.threshold_mm} มม.)`),
      trustBadge(b.label),
      el("dl", { class: "kv" },
        el("dt", {}, "🌡️ อุณหภูมิ"), el("dd", {}, `${fmt(b.tmin, 0)}–${fmt(b.tmax, 0)} °C`),
        el("dt", {}, "🥵 รู้สึกเหมือน"), el("dd", {}, `${fmt(b.feels, 0)} °C`, statusPill(b.heat)),
        el("dt", {}, "💧 ความชื้น"), el("dd", {}, `${fmt(b.rh, 0)} %`),
        el("dt", {}, "💨 ลมกระโชก"), el("dd", {}, `${fmt(b.gust, 0)} กม./ชม.`),
        el("dt", {}, "☀️ UV"), el("dd", {}, fmt(b.uv, 0), statusPill(b.uv_level)),
        el("dt", {}, "😷 PM2.5"), el("dd", {}, fmt(b.pm25, 0), statusPill(b.pm25_level))));
  }));
}

// ---------------------------------------------------------------------------
// tiles ฝนช่วงสั้น
// ---------------------------------------------------------------------------
function ensembleMean(j) {
  const vals = Object.values(state.loc.ensemble).map((r) => r[j]).filter((v) => v != null);
  return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null;
}
function renderNext() {
  const L = state.loc, C = L.consensus;
  const tiles = L.windows.map((w, j) => ({ w, j })).filter(({ w }) => w.startsWith("+"));
  $("next").replaceChildren(...tiles.map(({ w, j }) => {
    const p = ensembleMean(j);
    return el("div", { class: "tile" },
      el("div", { class: "t" }, `อีก ${w}`, el("span", { class: "wsub" }, winInfo(w).sub)),
      el("div", { class: "v" }, fmt(C.median[j]), el("small", {}, " มม.")),
      el("div", { class: "d" }, `ช่วง ${fmt(C.min[j])}–${fmt(C.max[j])} มม.`),
      el("div", { class: "d" }, `โอกาสฝน (ensemble) ${p == null ? "–" : Math.round(p) + "%"}`),
      el("div", { class: "bar", role: "img", "aria-label": `โอกาสฝน ${p == null ? "ไม่มีข้อมูล" : Math.round(p) + "%"}` },
        el("i", { style: `width:${p ?? 0}%` })), trustBadge(w));
  }));
}

// ---------------------------------------------------------------------------
// ตาราง heatmap
// ---------------------------------------------------------------------------
function heatTable(columns, rows, { steps, digits = 1, unit = "", meta = {}, accent, summary } = {}) {
  const cell = (v, rowName, col) => {
    const bg = steps ? stepColor(v, steps) : null;
    return el("td", {
      class: v == null ? "na" : null,
      style: bg ? `background:${bg};color:${inkOn(bg)}` : null,
      title: `${rowName} · ${col}: ${v == null ? "ไม่มีข้อมูล" : fmt(v, digits) + " " + unit}`,
    }, v == null ? "–" : fmt(v, digits));
  };
  const body = Object.entries(rows).map(([name, vals]) =>
    el("tr", { class: name === accent ? "accent" : null },
      el("th", { scope: "row" }, name, meta[name] ? el("span", { class: "km" }, `${meta[name]} กม.`) : null),
      ...vals.map((v, j) => cell(v, name, columns[j]))));
  if (summary) {
    body.push(el("tr", { class: "sum" }, el("th", { scope: "row" }, summary.name),
      ...summary.values.map((v, j) => cell(v, summary.name, columns[j]))));
  }
  return el("table", {},
    el("thead", {}, el("tr", {}, el("th", {}, ""), ...columns.map((c) => el("th", { scope: "col" }, ...winHead(c))))),
    el("tbody", {}, body));
}

function renderRainTable() {
  const L = state.loc, t = L.tables.rain;
  const meta = Object.fromEntries(L.models.map((m) => [m.name, m.grid_km]));
  $("rain-table").replaceChildren(heatTable(t.columns, t.rows, {
    steps: RAIN_STEPS, unit: "มม.", meta, accent: L.models[0]?.grid_km <= 4 ? L.models[0].name : null,
    summary: { name: "Median ทุกโมเดล", values: L.consensus.median },
  }));
  const agree = L.windows.map((w, j) => `${winInfo(w).top} ${L.consensus.agree_pct[j] ?? "–"}%`).join(" · ");
  $("rain-note").textContent = `% โมเดลที่ว่าฝนตก (≥ ${L.threshold_mm} มม.): ${agree} · ช่อง “–” คือโมเดลพยากรณ์ไปไม่ถึงช่วงนั้น`;
}

function renderEnsemble() {
  const L = state.loc;
  $("ens-sub").textContent = `ช่วงเวลาเดียวกับตารางฝน · สัดส่วนสมาชิกที่ฝน ≥ ${L.threshold_mm} มม.`;
  $("ens-table").replaceChildren(heatTable(L.windows, L.ensemble, { steps: PROB_STEPS, digits: 0, unit: "%" }));
}

function renderParam() {
  const L = state.loc;
  const keys = Object.keys(L.tables).filter((k) => k !== "rain");
  if (!keys.includes(state.param)) state.param = keys[0];
  $("param").replaceChildren(...keys.map((k) =>
    el("option", { value: k, selected: k === state.param }, `${L.tables[k].label}${L.tables[k].unit ? ` (${L.tables[k].unit})` : ""}`)));
  $("param").onchange = (e) => { state.param = e.target.value; renderParam(); };
  const t = L.tables[state.param];
  const meta = Object.fromEntries(L.models.map((m) => [m.name, m.grid_km]));
  $("param-table").replaceChildren(heatTable(t.columns, t.rows, {
    digits: 0, unit: t.unit, meta, accent: state.highlight,
    steps: state.param === "rain_prob" ? PROB_STEPS : null,
  }));
}

// ---------------------------------------------------------------------------
// กราฟรายชั่วโมง (SVG): median + ช่วง min–max + โมเดลที่เน้น
// ---------------------------------------------------------------------------
function niceTicks(lo, hi, n = 4) {
  const span = hi - lo || 1;
  const step0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const ticks = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) ticks.push(+v.toFixed(6));
  return ticks;
}

function seriesStats(series, times) {
  const names = Object.keys(series);
  const med = [], lo = [], hi = [];
  times.forEach((_, i) => {
    const v = names.map((n) => series[n][i]).filter((x) => x != null).sort((a, b) => a - b);
    if (!v.length) { med.push(null); lo.push(null); hi.push(null); return; }
    const m = v.length % 2 ? v[(v.length - 1) / 2] : (v[v.length / 2 - 1] + v[v.length / 2]) / 2;
    med.push(m); lo.push(v[0]); hi.push(v[v.length - 1]);
  });
  return { med, lo, hi, n: names.length };
}

function drawChart(spec, width) {
  const L = state.loc, H = L.hourly;
  const times = H.time.map(localTime);
  const s1 = cssVar("--series-1"), s2 = cssVar("--series-2");
  let med, lo, hi, hl = null, count = 0;
  if (spec.single) {
    med = H.pm25 || [];
    if (!med.some((v) => v != null)) return null;
  } else {
    const series = H.series[spec.key];
    if (!series) return null;
    ({ med, lo, hi, n: count } = seriesStats(series, times));
    hl = series[state.highlight] || null;
  }

  const card = el("div", { class: spec.wide ? "chart wide" : "chart" });
  card.append(el("h3", {}, `${spec.title} (${spec.unit})`));
  if (!spec.single) {
    card.append(el("div", { class: "legend" },
      el("span", {}, el("i", { class: "key-line", style: `background:${s1}` }), `Median ${count} โมเดล`),
      el("span", {}, el("i", { class: "key-band" }), "ช่วงต่ำสุด–สูงสุด"),
      hl ? el("span", {}, el("i", { class: "key-line", style: `background:${s2}` }), state.highlight) : null));
  }

  const W = Math.max(280, Math.round(width) - 24), Hh = spec.height || 190;   // 24 = padding ของการ์ด
  const m = { l: 38, r: 10, t: 10, b: 26 };
  const pw = W - m.l - m.r, ph = Hh - m.t - m.b;
  const all = [...med, ...(lo || []), ...(hi || []), ...(hl || [])].filter((v) => v != null);
  (spec.lines || []).forEach((l) => all.push(l.y));
  let yMin = spec.zero ? 0 : Math.floor(Math.min(...all) - 1);
  let yMax = spec.max ?? Math.max(...all, spec.zero ? 1 : -Infinity);
  if (!spec.max) yMax = yMax + (yMax - yMin) * 0.06;
  const ticks = niceTicks(yMin, yMax);
  yMin = Math.min(yMin, ticks[0]);
  yMax = Math.max(yMax, ticks[ticks.length - 1]);
  const t0 = times[0].getTime(), t1 = times[times.length - 1].getTime();
  const X = (i) => m.l + ((times[i].getTime() - t0) / (t1 - t0 || 1)) * pw;
  const Y = (v) => m.t + ph - ((v - yMin) / (yMax - yMin || 1)) * ph;

  const svg = sv("svg", { viewBox: `0 0 ${W} ${Hh}`, role: "img", tabindex: "0",
    "aria-label": `${spec.title} รายชั่วโมง ${spec.unit} — ใช้ลูกศรซ้าย/ขวาเพื่ออ่านค่า` });
  // gridlines + y ticks
  ticks.forEach((v) => {
    svg.append(sv("line", { x1: m.l, x2: W - m.r, y1: Y(v), y2: Y(v), stroke: cssVar("--grid"), "stroke-width": 1 }));
    const tx = sv("text", { x: m.l - 6, y: Y(v) + 4, "text-anchor": "end", class: "tick" });
    tx.textContent = v.toLocaleString("th-TH");
    svg.append(tx);
  });
  // x ticks ที่เที่ยงคืน
  times.forEach((t, i) => {
    if (t.getHours() !== 0) return;
    svg.append(sv("line", { x1: X(i), x2: X(i), y1: m.t, y2: m.t + ph, stroke: cssVar("--grid"), "stroke-width": 1 }));
    const tx = sv("text", { x: X(i) + 3, y: Hh - 8, class: "tick" });
    tx.textContent = `${DAYS[t.getDay()]} ${t.getDate()}`;
    svg.append(tx);
  });
  // เส้นเกณฑ์ (สถานะ)
  (spec.lines || []).forEach((l) => {
    if (l.y > yMax) return;
    svg.append(sv("line", { x1: m.l, x2: W - m.r, y1: Y(l.y), y2: Y(l.y), stroke: cssVar(`--${l.status}`), "stroke-width": 1 }));
    const tx = sv("text", { x: W - m.r - 2, y: Y(l.y) - 4, "text-anchor": "end", class: "tick" });
    tx.textContent = l.label;
    svg.append(tx);
  });

  const path = (arr) => {
    let d = "", pen = false;
    arr.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  };
  // ช่วง min–max
  if (lo) {
    let d = "", seg = [];
    const flush = () => {
      if (seg.length > 1) {
        d += "M" + seg.map((i) => `${X(i).toFixed(1)},${Y(hi[i]).toFixed(1)}`).join("L");
        d += "L" + seg.slice().reverse().map((i) => `${X(i).toFixed(1)},${Y(lo[i]).toFixed(1)}`).join("L") + "Z";
      }
      seg = [];
    };
    lo.forEach((v, i) => (v == null ? flush() : seg.push(i)));
    flush();
    svg.append(sv("path", { d, fill: cssVar("--band"), stroke: "none" }));
  }
  // median: แท่งสำหรับฝน เส้นสำหรับอย่างอื่น
  if (spec.bars) {
    const bw = Math.max(1, Math.min(24, pw / times.length - 1));
    med.forEach((v, i) => {
      if (v == null || v <= 0) return;
      const h = Math.max(1, Y(0) - Y(v)), x = X(i) - bw / 2, y = Y(v), r = Math.min(bw / 2, 4, h);
      svg.append(sv("path", { d: `M${x},${Y(0)}V${y + r}Q${x},${y} ${x + r},${y}H${x + bw - r}Q${x + bw},${y} ${x + bw},${y + r}V${Y(0)}Z`, fill: s1 }));
    });
  } else {
    svg.append(sv("path", { d: path(med), fill: "none", stroke: s1, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  if (hl) svg.append(sv("path", { d: path(hl), fill: "none", stroke: s2, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));

  // crosshair + tooltip (ชี้ที่ไหนก็ได้ สแนปไปชั่วโมงที่ใกล้ที่สุด)
  const cross = sv("line", { y1: m.t, y2: m.t + ph, stroke: cssVar("--axis"), "stroke-width": 1, visibility: "hidden" });
  const dot1 = sv("circle", { r: 4, fill: s1, stroke: cssVar("--surface"), "stroke-width": 2, visibility: "hidden" });
  const dot2 = sv("circle", { r: 4, fill: s2, stroke: cssVar("--surface"), "stroke-width": 2, visibility: "hidden" });
  svg.append(cross, dot1, dot2);
  let cur = -1;
  const show = (i, cx, cy) => {
    cur = i;
    const x = X(i);
    cross.setAttribute("x1", x); cross.setAttribute("x2", x); cross.setAttribute("visibility", "visible");
    const place = (dot, v) => {
      if (v == null || spec.bars && dot === dot1) { dot.setAttribute("visibility", "hidden"); return; }
      dot.setAttribute("cx", x); dot.setAttribute("cy", Y(v)); dot.setAttribute("visibility", "visible");
    };
    place(dot1, med[i]); place(dot2, hl ? hl[i] : null);
    const t = times[i];
    const head = `${DAYS[t.getDay()]} ${t.toLocaleString("th-TH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}`;
    const rows = [{ label: spec.single ? spec.title : "Median", value: `${fmt(med[i])} ${spec.unit}`, color: s1 }];
    if (lo) rows.push({ label: "ต่ำสุด–สูงสุด", value: `${fmt(lo[i])}–${fmt(hi[i])}` });
    if (hl) rows.push({ label: state.highlight, value: `${fmt(hl[i])} ${spec.unit}`, color: s2 });
    const box = svg.getBoundingClientRect();
    tip.show(cx ?? box.left + (x / W) * box.width, cy ?? box.top + 20, head, rows);
  };
  const hide = () => {
    cur = -1; tip.hide();
    [cross, dot1, dot2].forEach((n) => n.setAttribute("visibility", "hidden"));
  };
  const nearest = (clientX) => {
    const box = svg.getBoundingClientRect();
    const x = ((clientX - box.left) / box.width) * W;
    const t = t0 + ((x - m.l) / pw) * (t1 - t0);
    let best = 0, bd = Infinity;
    times.forEach((tt, i) => { const d = Math.abs(tt.getTime() - t); if (d < bd) { bd = d; best = i; } });
    return best;
  };
  svg.addEventListener("pointermove", (e) => show(nearest(e.clientX), e.clientX, e.clientY));
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("blur", hide);
  svg.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
    e.preventDefault();
    const next = Math.max(0, Math.min(times.length - 1, (cur < 0 ? 0 : cur) + (e.key === "ArrowRight" ? 1 : -1)));
    show(next);
  });
  card.append(svg);
  return card;
}

function renderCharts() {
  if (!state.loc || !$("step-4").open) return;                   // กล่องพับอยู่ = ไม่รู้ความกว้าง วาดตอนเปิด
  const box = $("charts");
  box.replaceChildren();
  CHARTS.forEach((spec) => {
    // วางการ์ดก่อนเพื่อให้รู้ความกว้างจริง แล้วค่อยวาด
    const tmp = el("div", { class: spec.wide ? "chart wide" : "chart" });
    box.append(tmp);
    const card = drawChart(spec, tmp.clientWidth);
    if (card) tmp.replaceWith(card); else tmp.remove();
  });
}

// ---------------------------------------------------------------------------
// แผนที่
// ---------------------------------------------------------------------------
let tiles = null;
function isDark() {
  const t = document.documentElement.getAttribute("data-theme");
  return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
}
function updateMapTiles() {
  if (!state.map) return;
  if (tiles) tiles.remove();
  tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    maxZoom: 18,
  }).addTo(state.map);
  $("map").classList.toggle("dark-tiles", isDark());
}
function initMap() {
  if (typeof L === "undefined") { $("map").textContent = "โหลดแผนที่ไม่ได้"; return; }
  const locs = state.index.locations;
  state.map = L.map("map", { scrollWheelZoom: false });
  updateMapTiles();
  state.markers = locs.map((l, i) => {
    const mk = L.circleMarker([l.lat, l.lon], {
      radius: 8, color: cssVar("--ink"), weight: 1.5, fillColor: cssVar("--surface"), fillOpacity: 1,
    }).addTo(state.map);
    mk.bindTooltip(l.name, { direction: "top", className: "map-label" });
    mk.on("click", () => selectLocation(i));
    return mk;
  });
  state.map.setView([locs[0].lat, locs[0].lon], 8);
  requestAnimationFrame(() => state.map.invalidateSize());   // ขนาดกล่องแผนที่อาจยังไม่ถูกคำนวณตอนสร้าง
  loadRadar();
  loadStations();
}

// ---------------------------------------------------------------------------
// สถานีวัดบนแผนที่: โทรมาตร ThaiWater ทุกเครื่อง (แยกตามหน่วยงาน) + สถานีอุตุฯ + Air4Thai · กรองชั้นได้
// ---------------------------------------------------------------------------
const MAP_RAIN = [0.1, 1, 5, 10, 20, 35, 50, 90];                 // มม./24 ชม.
const PM_COLOR = [[15, "#2a78d6"], [25, "#1baf7a"], [37.5, "#eda100"], [75, "#eb6834"], [Infinity, "#e34948"]];
const AGENCY_FULL = { "สสน.": "สถาบันสารสนเทศทรัพยากรน้ำ", "ทน.": "กรมทรัพยากรน้ำ", "ปภ.": "กรมป้องกันและบรรเทาสาธารณภัย",
  "ชป.": "กรมชลประทาน", "อต.": "กรมอุตุนิยมวิทยา (โทรมาตร)", "กฟผ.": "การไฟฟ้าฝ่ายผลิตแห่งประเทศไทย", "สนน. กทม.": "สำนักการระบายน้ำ กทม." };
const mapLayers = { groups: {}, meta: [] };
const rainFill = (v) => (v == null ? "#9a9a9a" : v < MAP_RAIN[0] ? cssVar("--surface") : stepColor(v, MAP_RAIN) || cssVar("--surface"));
async function loadStations() {
  const [st, now] = await Promise.all([getJSON("data/stations.json").catch(() => null),
    state.nowObs ? state.nowObs : getJSON("data/now_obs.json").catch(() => ({ tmd: [], air: [] }))]);
  state.nowObs = now;
  if (!st && !now.tmd.length) return;
  state.map.createPane("stations").style.zIndex = 390;             // ใต้วงกลมตำแหน่ง เหนือเรดาร์
  const renderer = L.canvas({ pane: "stations", padding: 0.3 });
  const check = new Set(st?.check_ids || []);
  const add = (key, label, icon, markers, on) => {
    mapLayers.groups[key] = L.layerGroup(markers);
    mapLayers.meta.push({ key, label, icon, n: markers.length, on });
  };
  // สถานีอุตุฯ (กรมอุตุฯ)
  add("tmd", "สถานีอุตุฯ", "📡", now.tmd.map((x) => L.circleMarker([x.lat, x.lon], {
    renderer, radius: 6.5, weight: 2.2, color: cssVar("--ink"), fillColor: rainFill(x.rain_24h), fillOpacity: 1,
  }).bindPopup(() => el("div", { class: "pop" }, el("b", {}, `📡 สถานี${x.name}`), el("div", { class: "muted" }, `${x.province || ""} · กรมอุตุนิยมวิทยา`),
    el("div", {}, `ฝน 24 ชม. ${fmt(x.rain_24h)} มม. · 3 ชม. ${fmt(x.rain_3h)} มม.`),
    el("div", {}, `อุณหภูมิ ${fmt(x.temp)} °C · ความชื้น ${fmt(x.rh, 0)}%`),
    el("div", { class: "muted" }, `เวลา ${String(x.time || "").slice(0, 16).replace("T", " ")} น.${check.has(String(x.id)) ? " · ⭕ จุดตรวจ" : ""}`)))), true);
  // โทรมาตร ThaiWater แยกตามหน่วยงาน (หน่วยงานเล็กรวมเป็น "อื่น ๆ")
  if (st) {
    const C = Object.fromEntries(st.tw_cols.map((c, i) => [c, i]));
    const byAg = {};
    st.tw.forEach((r) => { (byAg[r[C.agency] || "อื่น ๆ"] ||= []).push(r); });
    const small = Object.keys(byAg).filter((a) => byAg[a].length < 30);
    small.forEach((a) => { if (a !== "อื่น ๆ") { (byAg["อื่น ๆ"] ||= []).push(...byAg[a]); delete byAg[a]; } });
    Object.entries(byAg).sort((a, b) => b[1].length - a[1].length).forEach(([ag, rows]) => {
      add(`tw:${ag}`, `โทรมาตร ${ag}`, "💧", rows.map((r) => L.circleMarker([r[C.lat], r[C.lon]], {
        renderer, radius: 3.6, weight: 0.8, color: "rgba(80,80,80,.55)", fillColor: rainFill(r[C.rain_24h]), fillOpacity: 0.95,
      }).bindPopup(() => el("div", { class: "pop" }, el("b", {}, `💧 ${r[C.name]}`), el("div", { class: "muted" }, r[C.area]),
        el("div", {}, `ฝน 24 ชม. ${fmt(r[C.rain_24h])} มม.`),
        el("div", { class: "muted" }, `${AGENCY_FULL[r[C.agency]] || r[C.agency] || "–"} · เวลา ${r[C.time]} น.`),
        r[C.density] != null ? el("div", { class: "muted" }, `ความหนาแน่นประชากร ${Number(r[C.density]).toLocaleString("th-TH")} คน/ตร.กม. (${r[C.density] >= 1500 ? "เขตเมือง" : r[C.density] >= 300 ? "ชานเมือง" : "ชนบท"})`) : null,
        r[C.check] ? el("div", {}, "⭕ จุดตรวจที่ใช้เทียบโมเดล") : null))), true);
    });
    // จุดตรวจ (วงแหวน)
    const rings = st.tw.filter((r) => r[C.check]).map((r) => L.circleMarker([r[C.lat], r[C.lon]], {
      renderer, radius: 8, weight: 2, color: "#eb6834", fill: false, interactive: false }));
    now.tmd.filter((x) => check.has(String(x.id))).forEach((x) => rings.push(L.circleMarker([x.lat, x.lon], {
      renderer, radius: 10, weight: 2, color: "#eb6834", fill: false, interactive: false })));
    add("check", "จุดตรวจเทียบโมเดล", "⭕", rings, true);
  }
  // PM2.5 (Air4Thai)
  add("air", "PM2.5", "😷", now.air.map((x) => L.circleMarker([x.lat, x.lon], {
    renderer, radius: 5, weight: 1, color: "#fff", fillColor: PM_COLOR.find(([lim]) => x.pm25 < lim)[1], fillOpacity: 0.95,
  }).bindPopup(() => el("div", { class: "pop" }, el("b", {}, `😷 ${x.name}`), el("div", { class: "muted" }, x.area),
    el("div", {}, `PM2.5 ${fmt(x.pm25)} มคก./ลบ.ม. · ${x.level}`),
    el("div", { class: "muted" }, `Air4Thai (กรมควบคุมมลพิษ) · ${String(x.time).replace("T", " ").slice(0, 16)} น.`)))), false);
  let saved = null;
  try { saved = JSON.parse(store.get("map-layers") || "null"); } catch { saved = null; }
  mapLayers.on = new Set(saved || mapLayers.meta.filter((m) => m.on).map((m) => m.key));
  renderMapLayers();
}
function renderMapLayers() {
  for (const m of mapLayers.meta) {
    const g = mapLayers.groups[m.key], want = mapLayers.on.has(m.key);
    if (want && !state.map.hasLayer(g)) g.addTo(state.map);
    if (!want && state.map.hasLayer(g)) g.remove();
  }
  const toggle = (k) => { mapLayers.on.has(k) ? mapLayers.on.delete(k) : mapLayers.on.add(k);
    store.set("map-layers", JSON.stringify([...mapLayers.on])); renderMapLayers(); };
  const tw = mapLayers.meta.filter((m) => m.key.startsWith("tw:"));
  const allTw = tw.every((m) => mapLayers.on.has(m.key));
  $("map-layers").replaceChildren(
    ...mapLayers.meta.filter((m) => !m.key.startsWith("tw:")).slice(0, 1).map(chip),
    tw.length ? el("button", { class: "chip", type: "button", "aria-pressed": String(allTw), onclick: () => {
      tw.forEach((m) => (allTw ? mapLayers.on.delete(m.key) : mapLayers.on.add(m.key)));
      store.set("map-layers", JSON.stringify([...mapLayers.on])); renderMapLayers(); } },
      `💧 โทรมาตรทั้งหมด`, el("span", { class: "chip-n" }, ` ${tw.reduce((a, m) => a + m.n, 0).toLocaleString("th-TH")}`)) : null,
    ...tw.map(chip), ...mapLayers.meta.filter((m) => !m.key.startsWith("tw:")).slice(1).map(chip));
  function chip(m) {
    return el("button", { class: "chip small-chip", type: "button", "aria-pressed": String(mapLayers.on.has(m.key)),
      title: AGENCY_FULL[m.label.replace("โทรมาตร ", "")] || m.label, onclick: () => toggle(m.key) },
      `${m.icon} ${m.label}`, el("span", { class: "chip-n" }, ` ${m.n.toLocaleString("th-TH")}`));
  }
  const sw = (bg, t, ring) => el("span", { class: "lg" }, el("i", { style: `background:${bg}${ring ? ";box-shadow:0 0 0 2px " + ring : ""}` }), t);
  $("map-legend").replaceChildren(el("span", { class: "muted" }, "ฝน 24 ชม. (มม.):"), sw(cssVar("--surface"), "0", "#bbb"),
    ...MAP_RAIN.slice(0, -1).map((v, i) => sw(stepColor(v, MAP_RAIN), `${v}–${MAP_RAIN[i + 1]}`)), sw(stepColor(999, MAP_RAIN), `≥ ${MAP_RAIN.at(-1)}`),
    sw("#9a9a9a", "ไม่มีค่า"));
}

// เรดาร์ฝนจาก RainViewer: ภาพทุก 10 นาที ย้อนหลัง ~2 ชม. (ความละเอียดสูงสุดที่ zoom 7)
const radar = { frames: [], layers: {}, idx: -1, timer: null, host: "" };
async function loadRadar() {
  try {
    const j = await getJSON("https://api.rainviewer.com/public/weather-maps.json");
    radar.host = j.host;
    radar.frames = [...(j.radar.past || []), ...(j.radar.nowcast || [])];
    showRadarFrame(radar.frames.length - 1);
    $("radar-play").onclick = toggleRadar;
    setInterval(async () => {                 // หน้าเปิดค้างไว้ ก็ได้ภาพล่าสุดเอง
      if (radar.timer) return;
      const k = await getJSON("https://api.rainviewer.com/public/weather-maps.json").catch(() => null);
      if (k) { radar.frames = [...(k.radar.past || []), ...(k.radar.nowcast || [])]; showRadarFrame(radar.frames.length - 1); }
    }, 10 * 60000);
  } catch {
    $("radar-time").textContent = "· โหลดเรดาร์ไม่ได้";
  }
}
function radarLayer(f) {
  if (!radar.layers[f.path]) {
    radar.layers[f.path] = L.tileLayer(`${radar.host}${f.path}/256/{z}/{x}/{y}/2/1_1.png`, {
      opacity: 0, maxNativeZoom: 7, maxZoom: 18, zIndex: 10,
      attribution: '<a href="https://www.rainviewer.com/" target="_blank" rel="noopener">RainViewer</a>',
    }).addTo(state.map);
  }
  return radar.layers[f.path];
}
function showRadarFrame(i) {
  if (!radar.frames.length) return;
  const f = radar.frames[i];
  if (radar.idx >= 0 && radar.frames[radar.idx]) radarLayer(radar.frames[radar.idx]).setOpacity(0);
  radarLayer(f).setOpacity(0.75);
  radarLayer(radar.frames[(i + 1) % radar.frames.length]);   // โหลดเฟรมถัดไปรอไว้
  radar.idx = i;
  const t = new Date(f.time * 1000);
  const latest = i === radar.frames.length - 1;
  $("radar-time").textContent = `· ${t.toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" })} น.${latest ? " (ล่าสุด)" : ""}`;
}
function toggleRadar() {
  if (radar.timer) {
    clearInterval(radar.timer); radar.timer = null;
    showRadarFrame(radar.frames.length - 1);
    $("radar-play").textContent = "▶︎ ย้อนหลัง 2 ชม.";
    return;
  }
  $("radar-play").textContent = "⏸ หยุด";
  let i = 0;
  showRadarFrame(i);
  radar.timer = setInterval(() => { i = (i + 1) % radar.frames.length; showRadarFrame(i); }, 700);
}

// pill สถานะ: จุดสีมากับไอคอนและข้อความเสมอ (ไม่ใช้สีอย่างเดียว)
const style = document.createElement("style");
style.textContent = Object.entries({ good: "--good", warning: "--warning", serious: "--serious", critical: "--critical" })
  .map(([k, v]) => `.pill[data-status="${k}"]{box-shadow:inset 3px 0 0 var(${v}),0 0 0 1px var(--ring)}`).join("");
document.head.append(style);

init();
