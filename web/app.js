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

const state = { index: null, loc: null, locIdx: 0, highlight: null, param: "tmax", map: null, markers: [] };

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
  const saved = parseInt(store.get("loc") || "0", 10);
  renderChips();
  initMap();
  await selectLocation(saved < state.index.locations.length ? saved : 0);
  let t;
  window.addEventListener("resize", () => { clearTimeout(t); t = setTimeout(renderCharts, 150); });
}

function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    document.documentElement.setAttribute("data-theme", theme);
    store.set("theme", theme);
  }
  if (state.loc) { renderCharts(); updateMapTiles(); }
}

function renderChips() {
  $("locations").replaceChildren(...state.index.locations.map((l, i) =>
    el("button", { class: "chip", role: "tab", type: "button", "aria-selected": String(i === state.locIdx),
      onclick: () => selectLocation(i) }, l.name)));
}

async function selectLocation(i) {
  state.locIdx = i;
  store.set("loc", String(i));
  [...$("locations").children].forEach((c, j) => c.setAttribute("aria-selected", String(j === i)));
  document.body.style.opacity = "0.6";      // คงหน้าเดิมไว้ระหว่างโหลด ไม่กระพริบ
  try {
    state.loc = await getJSON(`data/${state.index.locations[i].file}`);
  } finally {
    document.body.style.opacity = "";
  }
  const models = state.loc.models.map((m) => m.name);
  const want = store.get("highlight");
  state.highlight = models.includes(want) ? want : models[0];
  $("highlight").replaceChildren(...state.loc.models.map((m) =>
    el("option", { value: m.name, selected: m.name === state.highlight },
      `${m.name}${m.grid_km ? ` (${m.grid_km} กม.)` : ""}`)));
  $("highlight").onchange = (e) => { state.highlight = e.target.value; store.set("highlight", state.highlight); renderCharts(); };
  renderAll();
  if (state.map) state.map.panTo([state.loc.lat, state.loc.lon]);
  state.markers.forEach((m, j) => m.setStyle({ weight: j === i ? 3 : 1.5 }));
}

function renderAll() {
  renderBrief();
  renderNext();
  renderRainTable();
  renderEnsemble();
  renderCharts();
  renderParam();
}

// ---------------------------------------------------------------------------
// การ์ดสรุปรายวัน
// ---------------------------------------------------------------------------
function renderBrief() {
  const L = state.loc;
  $("brief").replaceChildren(...L.brief.map((b, i) => {
    const title = i === 0 ? `พรุ่งนี้ · ${b.label}` : b.label;
    return el("article", { class: "card" },
      el("h3", {}, title),
      el("div", { class: "hero" }, fmt(b.rain), el("small", {}, " มม.")),
      el("div", { class: "hero-sub" }, `🌧️ ${b.n_rain} จาก ${b.n_models} โมเดลว่าฝนตก (≥ ${L.threshold_mm} มม.)`),
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
      el("div", { class: "t" }, `ถึง ${w}`),
      el("div", { class: "v" }, fmt(C.median[j]), el("small", {}, " มม.")),
      el("div", { class: "d" }, `ช่วง ${fmt(C.min[j])}–${fmt(C.max[j])} มม.`),
      el("div", { class: "d" }, `โอกาสฝน (ensemble) ${p == null ? "–" : Math.round(p) + "%"}`),
      el("div", { class: "bar", role: "img", "aria-label": `โอกาสฝน ${p == null ? "ไม่มีข้อมูล" : Math.round(p) + "%"}` },
        el("i", { style: `width:${p ?? 0}%` })));
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
    el("thead", {}, el("tr", {}, el("th", {}, ""), ...columns.map((c) => el("th", { scope: "col" }, c)))),
    el("tbody", {}, body));
}

function renderRainTable() {
  const L = state.loc, t = L.tables.rain;
  const meta = Object.fromEntries(L.models.map((m) => [m.name, m.grid_km]));
  $("rain-table").replaceChildren(heatTable(t.columns, t.rows, {
    steps: RAIN_STEPS, unit: "มม.", meta, accent: L.models[0]?.grid_km <= 4 ? L.models[0].name : null,
    summary: { name: "Median ทุกโมเดล", values: L.consensus.median },
  }));
  const agree = L.windows.map((w, j) => `${w} ${L.consensus.agree_pct[j] ?? "–"}%`).join(" · ");
  $("rain-note").textContent = `% โมเดลที่ว่าฝนตก (≥ ${L.threshold_mm} มม.): ${agree} · ช่อง “–” คือโมเดลพยากรณ์ไปไม่ถึงช่วงนั้น`;
}

function renderEnsemble() {
  const L = state.loc;
  $("ens-sub").textContent = `สัดส่วนสมาชิกที่ฝน ≥ ${L.threshold_mm} มม.`;
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
  if (!state.loc) return;
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
    const c = stepColor(l.rain_d1_median, RAIN_STEPS) || cssVar("--surface");
    const mk = L.circleMarker([l.lat, l.lon], {
      radius: 11, color: cssVar("--ink"), weight: 1.5, fillColor: c, fillOpacity: 0.95,
    }).addTo(state.map);
    mk.bindTooltip(`${l.name}: ${fmt(l.rain_d1_median)} มม.`, { direction: "top", className: "map-label" });
    mk.on("click", () => { selectLocation(i); window.scrollTo({ top: 0, behavior: "smooth" }); });
    return mk;
  });
  const b = L.latLngBounds(locs.map((l) => [l.lat, l.lon]));
  const fit = () => { state.map.invalidateSize(); state.map.fitBounds(b.pad(0.3), { maxZoom: 9 }); };
  fit();
  requestAnimationFrame(fit);                // ขนาดกล่องแผนที่อาจยังไม่ถูกคำนวณตอนสร้าง
  const labels = ["0", "0.1", "1", "5", "10", "20", "50+"];
  const idx = [null, 0, 2, 4, 5, 7, 9];
  $("map-scale").replaceChildren(el("span", {}, "ฝน (มม.)"),
    ...idx.map((k, j) => [el("i", { style: `background:${k == null ? cssVar("--surface") : BLUE[k]};box-shadow:0 0 0 1px var(--ring)` }),
      el("span", {}, labels[j])]).flat());
}

// pill สถานะ: จุดสีมากับไอคอนและข้อความเสมอ (ไม่ใช้สีอย่างเดียว)
const style = document.createElement("style");
style.textContent = Object.entries({ good: "--good", warning: "--warning", serious: "--serious", critical: "--critical" })
  .map(([k, v]) => `.pill[data-status="${k}"]{box-shadow:inset 3px 0 0 var(${v}),0 0 0 1px var(--ring)}`).join("");
document.head.append(style);

init();
