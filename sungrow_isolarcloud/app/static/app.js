/* SungrowFarm dashboard – vanilla JS, no dependencies.
   All API paths are relative so the app works behind HA ingress. */
"use strict";

/* ------------------------------------------------------------ helpers --- */
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const nf1 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 2 });
const nf0 = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 0 });

function fmtPower(w) {
  if (w == null || isNaN(w)) return "–";
  const a = Math.abs(w);
  if (a >= 100000) return { v: nf1.format(w / 1000), u: "kW" };
  if (a >= 1000) return { v: nf2.format(w / 1000), u: "kW" };
  return { v: nf0.format(w), u: "W" };
}
function fmtPowerStr(w) { const f = fmtPower(w); return typeof f === "string" ? f : `${f.v} ${f.u}`; }
function fmtEnergy(wh) {
  if (wh == null || isNaN(wh)) return { v: "–", u: "" };
  const a = Math.abs(wh);
  if (a >= 10_000_000) return { v: nf1.format(wh / 1e6), u: "MWh" };
  if (a >= 1000) return { v: nf1.format(wh / 1000), u: "kWh" };
  return { v: nf0.format(wh), u: "Wh" };
}
function fmtValue(value, unit) {
  if (value == null) return "–";
  if (typeof value !== "number") return String(value);
  if (unit === "Wh") { const e = fmtEnergy(value); return `${e.v} ${e.u}`; }
  if (unit === "W") return fmtPowerStr(value);
  const num = Math.abs(value) < 10 ? nf2.format(value) : nf1.format(value);
  return unit ? `${num} ${unit}` : num;
}
function fmtTime(ts) {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
}
function fmtDateTime(ts) {
  if (!ts) return "–";
  return new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}
async function api(path, opts) {
  const r = await fetch(path, { cache: "no-store", ...opts });
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json()).detail || ""; } catch { /* ignore */ }
    throw new Error(detail || `${path} → HTTP ${r.status}`);
  }
  return r.json();
}
const apiPost = (path, body) => api(path, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body || {}),
});
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("show"), 1800);
}
async function copyText(text) {
  try { await navigator.clipboard.writeText(text); }
  catch {
    const ta = document.createElement("textarea");
    ta.value = text; document.body.appendChild(ta);
    ta.select(); document.execCommand("copy"); ta.remove();
  }
  toast("Kopiert: " + text);
}
const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* -------------------------------------------------------------- theme --- */
(function initTheme() {
  const saved = localStorage.getItem("sgf-theme");
  const prefers = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  document.documentElement.dataset.theme = saved || prefers;
  $("#theme-toggle").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("sgf-theme", next);
    App.redrawCharts();
  });
})();

/* --------------------------------------------------------------- tabs --- */
$$(".tab").forEach((btn) => btn.addEventListener("click", () => {
  $$(".tab").forEach((b) => b.classList.toggle("active", b === btn));
  $$(".view").forEach((v) => v.classList.toggle("active", v.id === "view-" + btn.dataset.tab));
  $("#tooltip").classList.remove("show");
  if (btn.dataset.tab === "history") App.loadHistory();
  App.redrawCharts();
}));

/* ============================== chart engine ============================= */
class TimeChart {
  constructor(canvas, { unit = "W" } = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.unit = unit;
    this.series = []; // {name,color,data:[[sec,val]],hidden}
    this.tooltip = $("#tooltip");
    this._bindPointer();
    new ResizeObserver(() => this.draw()).observe(canvas.parentElement);
  }

  setSeries(series) { this.series = series; this.draw(); }
  toggle(i) { this.series[i].hidden = !this.series[i].hidden; this.draw(); }

  _visible() { return this.series.filter((s) => !s.hidden && s.data.length); }

  _bounds() {
    let t0 = Infinity, t1 = -Infinity, v0 = 0, v1 = -Infinity;
    for (const s of this._visible()) for (const [t, v] of s.data) {
      if (t < t0) t0 = t; if (t > t1) t1 = t;
      if (v < v0) v0 = v; if (v > v1) v1 = v;
    }
    if (!isFinite(t0)) return null;
    if (v1 <= v0) v1 = v0 + 1;
    const pad = (v1 - v0) * 0.08;
    return { t0, t1, v0: v0 < 0 ? v0 - pad : 0, v1: v1 + pad };
  }

  _niceTicks(min, max, n = 4) {
    const span = max - min;
    const step0 = span / n;
    const mag = Math.pow(10, Math.floor(Math.log10(step0)));
    const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n + 1) || mag * 10;
    const ticks = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) ticks.push(v);
    return ticks;
  }

  draw() {
    const c = this.canvas, ctx = this.ctx;
    const holder = c.parentElement;
    const W = holder.clientWidth, H = holder.clientHeight;
    if (!W || !H) return;
    const dpr = devicePixelRatio || 1;
    c.width = W * dpr; c.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    const b = this._bounds();
    const ML = 52, MR = 10, MT = 10, MB = 24;
    const iw = W - ML - MR, ih = H - MT - MB;
    this._geo = { b, ML, MT, iw, ih, W, H };
    if (!b) {
      ctx.fillStyle = cssVar("--muted");
      ctx.font = "13px " + cssVar("--font");
      ctx.textAlign = "center";
      ctx.fillText("Keine Daten", W / 2, H / 2);
      return;
    }
    const X = (t) => ML + ((t - b.t0) / (b.t1 - b.t0)) * iw;
    const Y = (v) => MT + (1 - (v - b.v0) / (b.v1 - b.v0)) * ih;
    this._X = X; this._Y = Y;

    // gridlines + y labels
    const yTicks = this._niceTicks(b.v0, b.v1);
    ctx.strokeStyle = cssVar("--grid-line");
    ctx.lineWidth = 1;
    ctx.fillStyle = cssVar("--muted");
    ctx.font = "11px " + cssVar("--font");
    ctx.textAlign = "right"; ctx.textBaseline = "middle";
    for (const v of yTicks) {
      const y = Math.round(Y(v)) + 0.5;
      ctx.beginPath(); ctx.moveTo(ML, y); ctx.lineTo(ML + iw, y); ctx.stroke();
      const lab = this.unit === "W" ? (Math.abs(b.v1) >= 2000 ? nf1.format(v / 1000) + " kW" : nf0.format(v) + " W")
                                    : nf1.format(v) + (this.unit ? " " + this.unit : "");
      ctx.fillText(lab, ML - 8, y);
    }
    // zero baseline emphasised when negatives exist
    if (b.v0 < 0) {
      ctx.strokeStyle = cssVar("--baseline");
      const y = Math.round(Y(0)) + 0.5;
      ctx.beginPath(); ctx.moveTo(ML, y); ctx.lineTo(ML + iw, y); ctx.stroke();
    }

    // x labels
    const span = b.t1 - b.t0;
    const stepH = span > 3 * 86400 ? 24 : span > 86400 ? 12 : span > 43200 ? 6 : 3;
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const d0 = new Date(b.t0 * 1000); d0.setMinutes(0, 0, 0);
    for (let t = d0.getTime() / 1000; t <= b.t1; t += 3600) {
      const dt = new Date(t * 1000);
      if (dt.getHours() % stepH !== 0 || t < b.t0) continue;
      const lab = span > 3 * 86400
        ? dt.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" })
        : dt.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
      ctx.fillStyle = cssVar("--muted");
      ctx.fillText(lab, X(t), MT + ih + 7);
    }

    // series: soft area fill + 2px line
    for (const s of this._visible()) {
      const col = s.color();
      ctx.beginPath();
      s.data.forEach(([t, v], i) => { const x = X(t), y = Y(v); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
      ctx.strokeStyle = col; ctx.lineWidth = 2;
      ctx.lineJoin = "round"; ctx.lineCap = "round";
      ctx.stroke();
      if (s.fill) {
        ctx.lineTo(X(s.data[s.data.length - 1][0]), Y(0));
        ctx.lineTo(X(s.data[0][0]), Y(0));
        ctx.closePath();
        ctx.globalAlpha = 0.10; ctx.fillStyle = col; ctx.fill(); ctx.globalAlpha = 1;
      }
    }

    if (this._hoverT != null) this._drawCrosshair(this._hoverT);
  }

  _drawCrosshair(t) {
    const { b, MT, ih } = this._geo || {};
    if (!b) return;
    const ctx = this.ctx, x = Math.round(this._X(t)) + 0.5;
    ctx.strokeStyle = cssVar("--baseline");
    ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(x, MT); ctx.lineTo(x, MT + ih); ctx.stroke();
    ctx.setLineDash([]);
    for (const s of this._visible()) {
      const p = nearestPoint(s.data, t);
      if (!p) continue;
      ctx.beginPath();
      ctx.arc(this._X(p[0]), this._Y(p[1]), 4, 0, Math.PI * 2);
      ctx.fillStyle = s.color();
      ctx.strokeStyle = cssVar("--surface"); ctx.lineWidth = 2;
      ctx.fill(); ctx.stroke();
    }
  }

  _bindPointer() {
    const c = this.canvas;
    c.addEventListener("pointermove", (e) => {
      const g = this._geo;
      if (!g || !g.b) return;
      const rect = c.getBoundingClientRect();
      const px = e.clientX - rect.left;
      const t = g.b.t0 + ((px - g.ML) / g.iw) * (g.b.t1 - g.b.t0);
      if (t < g.b.t0 || t > g.b.t1) return this._hideTip();
      // snap to nearest sample of the first visible series
      const first = this._visible()[0];
      const snap = first ? nearestPoint(first.data, t) : null;
      this._hoverT = snap ? snap[0] : t;
      this.draw();
      this._showTip(e.clientX, e.clientY);
    });
    c.addEventListener("pointerleave", () => this._hideTip());
  }

  _showTip(cx, cy) {
    const t = this._hoverT;
    const rows = this._visible().map((s) => {
      const p = nearestPoint(s.data, t);
      return p ? `<div class="tt-row"><span class="tt-l"><span class="swatch" style="background:${s.color()}"></span>${s.name}</span><b>${fmtValue(p[1], this.unit)}</b></div>` : "";
    }).join("");
    const tip = this.tooltip;
    tip.innerHTML = `<div class="tt-time">${fmtDateTime(t)}</div>${rows}`;
    tip.classList.add("show");
    const tw = tip.offsetWidth, th = tip.offsetHeight;
    let x = cx + 16, y = cy - th / 2;
    if (x + tw > innerWidth - 10) x = cx - tw - 16;
    y = Math.max(10, Math.min(innerHeight - th - 10, y));
    tip.style.left = x + "px"; tip.style.top = y + "px";
  }

  _hideTip() {
    this._hoverT = null;
    this.tooltip.classList.remove("show");
    this.draw();
  }
}

function nearestPoint(data, t) {
  if (!data.length) return null;
  let lo = 0, hi = data.length - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; (data[m][0] < t) ? lo = m : hi = m; }
  return Math.abs(data[lo][0] - t) < Math.abs(data[hi][0] - t) ? data[lo] : data[hi];
}

function renderLegend(el, chart) {
  el.innerHTML = "";
  chart.series.forEach((s, i) => {
    const btn = document.createElement("button");
    btn.className = "legend-item" + (s.hidden ? " off" : "");
    btn.innerHTML = `<span class="swatch" style="background:${s.color()}"></span>${s.name}`;
    btn.addEventListener("click", () => { chart.toggle(i); btn.classList.toggle("off", s.hidden); });
    el.appendChild(btn);
  });
}

/* ============================== energy flow ============================= */
const ICONS = {
  sun: '<circle cx="12" cy="12" r="4"/><g stroke-linecap="round"><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M18.4 5.6L17 7M7 17l-1.4 1.4"/></g>',
  house: '<path d="M4 11.5 12 5l8 6.5"/><path d="M6 10.5V19h12v-8.5"/><path d="M10 19v-5h4v5"/>',
  battery: '<rect x="7" y="6" width="10" height="14" rx="2"/><path d="M10 4h4"/><path d="M12.8 10.5l-2 3h2.5l-2 3" stroke-linecap="round" stroke-linejoin="round"/>',
  tower: '<path d="M9 21 12 3l3 18"/><path d="M6.5 21h11"/><path d="M9.7 8.5h4.6M8.9 13.5h6.2"/>',
  meter: '<circle cx="12" cy="13" r="8"/><path d="M12 13l3.2-3.2" stroke-linecap="round"/><path d="M9 3.5h6"/>',
  chip: '<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3"/>',
  logger: '<rect x="4" y="5" width="16" height="12" rx="2"/><path d="M8 21h8M12 17v4"/><path d="M7.5 9.5h4M7.5 12.5h7"/>',
  bolt: '<path d="M13 2 5 13.5h5L11 22l8-11.5h-5L13 2z" stroke-linejoin="round"/>',
  leaf: '<path d="M6 15c0-6 5-10 13-11-1 8-5 13-11 13-1 0-2-.5-2-2z"/><path d="M5 20c2-4 5-7 9-9" stroke-linecap="round"/>',
  pct: '<circle cx="7.5" cy="7.5" r="2.6"/><circle cx="16.5" cy="16.5" r="2.6"/><path d="M18.5 5.5l-13 13" stroke-linecap="round"/>',
};
const iconSvg = (name, color) =>
  `<svg viewBox="0 0 24 24" style="stroke:${color}" aria-hidden="true">${ICONS[name] || ICONS.bolt}</svg>`;

class FlowView {
  constructor(container) {
    this.el = container;
    this.dots = [];
    this._build();
    requestAnimationFrame((ts) => this._tick(ts));
  }

  _build() {
    // node positions in a 460×440 viewBox (diamond layout)
    this.pos = { pv: [230, 78], grid: [82, 226], load: [378, 226], batt: [230, 352] };
    const P = this.pos;
    const edge = (a, b, bend = 0) => {
      const [x1, y1] = P[a], [x2, y2] = P[b];
      const mx = (x1 + x2) / 2 + (y2 - y1) * bend;
      const my = (y1 + y2) / 2 + (x1 - x2) * bend;
      return `M ${x1} ${y1} Q ${mx} ${my} ${x2} ${y2}`;
    };
    // outward arcs on the diagonals, straight lines through the middle
    this.edges = {
      pv_load:   { d: edge("pv", "load", 0.22), color: "--c-pv" },
      pv_grid:   { d: edge("pv", "grid", -0.22), color: "--c-pv" },
      pv_batt:   { d: edge("pv", "batt", 0), color: "--c-pv" },
      grid_load: { d: edge("grid", "load", 0), color: "--c-grid" },
      batt_load: { d: edge("batt", "load", -0.22), color: "--c-batt" },
    };
    const nodes = [
      { id: "pv", icon: "sun", label: "Photovoltaik", color: "--c-pv" },
      { id: "grid", icon: "tower", label: "Netz", color: "--c-grid" },
      { id: "load", icon: "house", label: "Haus", color: "--c-load" },
      { id: "batt", icon: "battery", label: "Batterie", color: "--c-batt" },
    ];
    let svg = `<svg viewBox="0 0 460 440" role="img" aria-label="Energiefluss">`;
    for (const [key, e] of Object.entries(this.edges)) {
      svg += `<path id="edge-${key}" class="flow-edge" d="${e.d}"/>`;
    }
    svg += `<g id="flow-dots"></g>`;
    for (const n of nodes) {
      const [x, y] = this.pos[n.id];
      const labelY = n.id === "batt" ? y + 52 : y - 44;
      const valueY = n.id === "batt" ? y + 70 : y + 50;
      svg += `
        <g class="flow-node" id="node-${n.id}">
          <circle class="flow-node-circle" cx="${x}" cy="${y}" r="30" style="stroke:var(${n.color})"/>
          <g transform="translate(${x - 13},${y - 13}) scale(1.08)">
            <g style="stroke:var(${n.color});fill:none;stroke-width:1.7" stroke-linecap="round" stroke-linejoin="round">${ICONS[n.icon]}</g>
          </g>
          <text class="flow-node-label" x="${x}" y="${labelY}">${n.label}</text>
          <text class="flow-node-value" x="${x}" y="${valueY}" id="val-${n.id}">–</text>
          <text class="flow-node-sub" x="${x}" y="${valueY + 15}" id="sub-${n.id}"></text>
        </g>`;
    }
    svg += `</svg>`;
    this.el.innerHTML = svg;
    this.dotLayer = $("#flow-dots", this.el);
    this.paths = {};
    for (const key of Object.keys(this.edges)) this.paths[key] = $(`#edge-${key}`, this.el);
  }

  update(k) {
    const pv = k.pv_power_w || 0;
    const load = k.load_power_w || 0;
    const battCharge = Math.max(0, k.battery_power_w || 0);
    const battDischarge = Math.max(0, -(k.battery_power_w || 0));
    const gridImport = Math.max(0, k.grid_power_w || 0);
    const gridExport = Math.max(0, -(k.grid_power_w || 0));
    const pvToBatt = Math.min(pv, battCharge);
    const pvToGrid = Math.min(Math.max(0, pv - pvToBatt), gridExport);
    const pvToLoad = Math.max(0, pv - pvToBatt - pvToGrid);

    this.flows = {
      pv_load: pvToLoad, pv_batt: pvToBatt, pv_grid: pvToGrid,
      grid_load: gridImport, batt_load: battDischarge,
    };
    for (const [key, w] of Object.entries(this.flows)) {
      this.paths[key].classList.toggle("on", w > 15);
      this.paths[key].style.stroke = w > 15 ? `var(${this.edges[key].color})` : "";
    }
    const set = (id, txt) => { const n = $(`#${id}`, this.el); if (n) n.textContent = txt; };
    set("val-pv", fmtPowerStr(pv));
    set("val-load", fmtPowerStr(load));
    set("val-grid", fmtPowerStr(Math.abs(k.grid_power_w || 0)));
    set("sub-grid", gridExport > 15 ? "Einspeisung" : gridImport > 15 ? "Bezug" : "");
    set("val-batt", k.battery_soc != null ? nf0.format(k.battery_soc) + " %" : "–");
    set("sub-batt", battCharge > 15 ? "lädt · " + fmtPowerStr(battCharge)
      : battDischarge > 15 ? "entlädt · " + fmtPowerStr(battDischarge) : "Standby");
    this._syncDots();
  }

  _syncDots() {
    // 1–3 dots per active edge, count scaling gently with power
    const wanted = [];
    for (const [key, w] of Object.entries(this.flows || {})) {
      if (w > 15) {
        const n = w > 3500 ? 3 : w > 1200 ? 2 : 1;
        for (let i = 0; i < n; i++) wanted.push({ key, phase: i / n });
      }
    }
    if (wanted.length === this.dots.length &&
        wanted.every((w, i) => this.dots[i].key === w.key)) return;
    this.dotLayer.innerHTML = "";
    this.dots = wanted.map((w) => {
      const c = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      c.setAttribute("class", "flow-dot");
      c.setAttribute("r", "3.4");
      c.style.fill = `var(${this.edges[w.key].color})`;
      this.dotLayer.appendChild(c);
      return { ...w, el: c, len: this.paths[w.key].getTotalLength() };
    });
  }

  _tick(ts) {
    const SPEED = 42; // px/s along the path
    for (const d of this.dots) {
      const t = ((ts / 1000 * SPEED / d.len) + d.phase) % 1;
      const p = this.paths[d.key].getPointAtLength(t * d.len);
      d.el.setAttribute("cx", p.x);
      d.el.setAttribute("cy", p.y);
    }
    requestAnimationFrame((t2) => this._tick(t2));
  }
}

/* ================================= app ================================= */
const App = {
  psId: null,
  flow: null,
  dashChart: null,
  histChart: null,
  histHours: 24,
  pointsCache: [],
  pointsGroup: "all",

  async init() {
    this.flow = new FlowView($("#flow-wrap"));
    this.dashChart = new TimeChart($("#dash-chart"), { unit: "W" });
    this.histChart = new TimeChart($("#hist-chart"), { unit: "W" });
    $("#point-search").addEventListener("input", () => this.renderPoints());
    this.initDiagnostics();
    $$("#range-row .chip").forEach((c) => c.addEventListener("click", () => {
      $$("#range-row .chip").forEach((x) => x.classList.toggle("active", x === c));
      this.histHours = +c.dataset.hours;
      this.loadHistory(true);
    }));
    await this.refresh();
    await this.loadDashChart();
    setInterval(() => this.refresh(), 60_000);
    setInterval(() => this.loadDashChart(), 300_000);
  },

  redrawCharts() {
    this.dashChart?.draw();
    this.histChart?.draw();
  },

  async refresh() {
    try {
      const status = await api("api/status");
      this.renderStatusPill(status);
      this.renderStatusView(status);
      $("#demo-banner").classList.toggle("hidden", !status.demo_mode);

      const ov = await api("api/overview");
      this.psId = ov.ps_id;
      if (ov.plant) $("#plant-name").textContent = ov.plant.ps_name || `Anlage ${ov.ps_id}`;
      $("#flow-updated").textContent = ov.updated ? "Stand " + fmtTime(ov.updated) : "";
      this.flow.update(ov.kpis);
      this.renderKpis(ov.kpis);

      const [pts, devs] = await Promise.all([api("api/points"), api("api/devices")]);
      this.pointsCache = pts.points;
      this.renderPoints();
      this.renderDevices(devs.devices);
    } catch (err) {
      console.error(err);
      this.renderStatusPill(null, err);
    }
  },

  renderStatusPill(status, err) {
    const dot = $("#conn-dot"), label = $("#conn-label"), pill = $("#conn-pill");
    if (err || !status) {
      dot.className = "conn-dot err"; label.textContent = "Keine Verbindung";
      pill.title = err ? String(err) : ""; return;
    }
    if (status.demo_mode) {
      dot.className = "conn-dot warn"; label.textContent = "Demo";
      pill.title = "Demo-Modus aktiv"; return;
    }
    if (status.last_error) {
      dot.className = "conn-dot warn"; label.textContent = "Fehler";
      pill.title = status.last_error; return;
    }
    if (status.login_ok) {
      dot.className = "conn-dot ok"; label.textContent = "Verbunden";
      pill.title = "Letzte Aktualisierung: " + fmtDateTime(status.last_success); return;
    }
    dot.className = "conn-dot"; label.textContent = "Verbinde…";
  },

  renderKpis(k) {
    const e = fmtEnergy(k.daily_yield_wh);
    const cons = fmtEnergy(k.daily_load_wh);
    const imp = fmtEnergy(k.purchased_today_wh);
    const exp = fmtEnergy(k.feed_in_today_wh);
    const aut = k.self_sufficiency != null ? Math.round(k.self_sufficiency * 100) : null;
    const ev = k.self_consumption != null ? Math.round(k.self_consumption * 100) : null;
    const tiles = [
      { icon: "sun", c: "--c-pv", soft: "--c-pv-soft", label: "Tagesertrag",
        value: e.v, unit: e.u, sub: k.total_yield_wh != null ? "Gesamt " + fmtEnergy(k.total_yield_wh).v + " " + fmtEnergy(k.total_yield_wh).u : "" },
      { icon: "house", c: "--c-load", soft: "--c-load-soft", label: "Verbrauch heute",
        value: cons.v, unit: cons.u, sub: "aktuell " + fmtPowerStr(k.load_power_w) },
      { icon: "leaf", c: "--c-batt", soft: "--c-batt-soft", label: "Autarkie heute",
        value: aut != null ? nf0.format(aut) : "–", unit: aut != null ? "%" : "",
        meter: aut, sub: "Anteil des Verbrauchs aus PV + Speicher" },
      { icon: "pct", c: "--c-pv", soft: "--c-pv-soft", label: "Eigenverbrauch heute",
        value: ev != null ? nf0.format(ev) : "–", unit: ev != null ? "%" : "",
        meter: ev, sub: "Anteil des PV-Ertrags selbst genutzt" },
      { icon: "tower", c: "--c-grid", soft: "--c-grid-soft", label: "Netzbezug heute",
        value: imp.v, unit: imp.u, sub: "Einspeisung " + exp.v + " " + exp.u },
      { icon: "battery", c: "--c-batt", soft: "--c-batt-soft", label: "Batterie",
        value: k.battery_soc != null ? nf0.format(k.battery_soc) : "–", unit: k.battery_soc != null ? "%" : "",
        meter: k.battery_soc,
        sub: (k.daily_charge_wh != null ? "▲ " + fmtEnergy(k.daily_charge_wh).v + " " + fmtEnergy(k.daily_charge_wh).u : "") +
             (k.daily_discharge_wh != null ? "   ▼ " + fmtEnergy(k.daily_discharge_wh).v + " " + fmtEnergy(k.daily_discharge_wh).u : "") },
    ];
    $("#kpi-grid").innerHTML = tiles.map((t) => `
      <div class="kpi">
        <div class="kpi-top">
          <span class="kpi-icon" style="background:var(${t.soft})">${iconSvg(t.icon, `var(${t.c})`)}</span>
          <span class="kpi-label">${t.label}</span>
        </div>
        <div class="kpi-value">${t.value}<small>${t.unit}</small></div>
        ${t.meter != null ? `<div class="meter"><i style="width:${Math.max(0, Math.min(100, t.meter))}%;background:var(${t.c})"></i></div>` : ""}
        <div class="kpi-sub">${t.sub || ""}</div>
      </div>`).join("");
  },

  seriesDef() {
    return [
      { id: "83033", name: "Photovoltaik", cvar: "--c-pv", fill: true },
      { id: "83106", name: "Verbrauch", cvar: "--c-load" },
      { id: "83549", name: "Netz", cvar: "--c-grid" },
      { id: "83238", name: "Batterie", cvar: "--c-batt" },
    ];
  },

  _toChartSeries(apiSeries, defs, prev) {
    return defs.map((d, i) => {
      const s = apiSeries.find((x) => x.point_id === d.id);
      return {
        name: d.name,
        color: () => cssVar(d.cvar),
        fill: !!d.fill,
        hidden: prev?.[i]?.hidden || false,
        data: s ? s.data : [],
      };
    });
  },

  async loadDashChart() {
    try {
      const defs = this.seriesDef();
      const res = await api(`api/history?points=${defs.map((d) => d.id).join(",")}&hours=24&interval=5`);
      this.dashChart.setSeries(this._toChartSeries(res.series, defs, this.dashChart.series));
      renderLegend($("#dash-legend"), this.dashChart);
      $("#view-dashboard .chart-card h2").textContent = "Leistung · letzte 24 Stunden";
    } catch (err) { console.warn("dash chart", err); }
  },

  async loadHistory(force) {
    if (this._histLoaded === this.histHours && !force) return;
    try {
      const defs = this.seriesDef();
      const interval = this.histHours > 48 ? 30 : this.histHours > 24 ? 15 : 5;
      const res = await api(`api/history?points=${defs.map((d) => d.id).join(",")}&hours=${this.histHours}&interval=${interval}`);
      this.histChart.setSeries(this._toChartSeries(res.series, defs, this.histChart.series));
      renderLegend($("#hist-legend"), this.histChart);
      this._histLoaded = this.histHours;
    } catch (err) {
      console.warn("history", err);
      $("#hist-hint").textContent = "Verlauf konnte nicht geladen werden: " + err.message;
    }
  },

  renderDevices(devices) {
    const typeMeta = (t) => ({
      1: { icon: "chip", c: "--c-pv", soft: "--c-pv-soft", name: "Wechselrichter" },
      7: { icon: "meter", c: "--c-grid", soft: "--c-grid-soft", name: "Zähler" },
      9: { icon: "logger", c: "--c-load", soft: "--c-load-soft", name: "Datenlogger" },
      14: { icon: "battery", c: "--c-batt", soft: "--c-batt-soft", name: "Energiespeicher" },
      43: { icon: "battery", c: "--c-batt", soft: "--c-batt-soft", name: "Batterie" },
    }[t] || { icon: "bolt", c: "--c-grid", soft: "--c-grid-soft", name: "Gerät" });
    const statusPill = (d) => {
      const fault = String(d.dev_fault_status ?? "");
      const on = String(d.dev_status ?? "") === "1";
      if (fault === "1") return `<span class="pill err">Störung</span>`;
      if (fault === "2") return `<span class="pill warn">Alarm</span>`;
      if (!on) return `<span class="pill off">Offline</span>`;
      return `<span class="pill ok">In Betrieb</span>`;
    };
    $("#device-grid").innerHTML = (devices || []).map((d) => {
      const m = typeMeta(d.device_type);
      return `
      <div class="card device-card">
        <span class="device-icon" style="background:var(${m.soft})">${iconSvg(m.icon, `var(${m.c})`)}</span>
        <div class="device-body">
          <div class="device-name">${d.device_name || m.name}</div>
          <div class="device-model">${m.name}${d.device_model_code ? " · " + d.device_model_code : ""}</div>
          ${d.device_sn ? `<div class="device-sn">SN ${d.device_sn}</div>` : ""}
          ${statusPill(d)}
        </div>
      </div>`;
    }).join("") || `<div class="card"><p class="hint">Noch keine Geräte geladen.</p></div>`;
  },

  renderPoints() {
    const groups = [
      ["all", "Alle"], ["production", "Erzeugung"], ["consumption", "Verbrauch"],
      ["grid", "Netz"], ["battery", "Batterie"], ["plant", "Anlage"], ["other", "Sonstige"],
    ];
    const present = new Set(this.pointsCache.map((p) => p.group));
    const gf = $("#group-filter");
    gf.innerHTML = groups
      .filter(([g]) => g === "all" || present.has(g))
      .map(([g, label]) =>
        `<button class="chip ${this.pointsGroup === g ? "active" : ""}" data-g="${g}">${label}</button>`).join("");
    $$("button", gf).forEach((b) => b.addEventListener("click", () => {
      this.pointsGroup = b.dataset.g; this.renderPoints();
    }));

    const q = $("#point-search").value.trim().toLowerCase();
    const groupColor = { production: "--c-pv", consumption: "--c-load", grid: "--c-grid", battery: "--c-batt", plant: "--c-batt", other: "--muted" };
    const groupName = Object.fromEntries(groups);
    const rows = this.pointsCache
      .filter((p) => this.pointsGroup === "all" || p.group === this.pointsGroup)
      .filter((p) => !q || p.name.toLowerCase().includes(q) || p.entity_id.includes(q) || p.point_id.includes(q))
      .map((p) => `
        <tr>
          <td><span class="pt-name"><span class="pt-dot" style="background:var(${groupColor[p.group] || "--muted"})"></span>
            <span>${p.name}<span class="pt-group">${groupName[p.group] || p.group}</span></span></span></td>
          <td class="num"><b>${fmtValue(p.value, p.unit)}</b></td>
          <td><button class="copy-id" data-copy="${p.entity_id}" title="Entity-ID kopieren">${p.entity_id}</button></td>
          <td><span class="topic">${p.mqtt_topic}</span></td>
          <td class="num">${p.point_id}</td>
        </tr>`).join("");
    $("#points-table tbody").innerHTML = rows ||
      `<tr><td colspan="5" style="color:var(--muted)">Keine Parameter gefunden.</td></tr>`;
    $$(".copy-id", $("#points-table")).forEach((b) =>
      b.addEventListener("click", () => copyText(b.dataset.copy)));
  },

  renderStatusView(s) {
    const items = [
      { k: "Verbindung", v: s.demo_mode ? "Demo-Modus" : (s.login_ok ? "Verbunden" : "Nicht verbunden"),
        dot: s.demo_mode ? "warn" : (s.login_ok ? "ok" : "err") },
      { k: "Region", v: { eu: "Europa", international: "International", china: "China", australia: "Australien" }[s.region] || s.region },
      { k: "API-Variante", v: s.api_profile ? s.api_profile.label : "noch nicht ermittelt",
        dot: s.api_profile ? "ok" : "" },
      { k: "MQTT-Sensoren", v: s.mqtt.enabled ? (s.mqtt.connected ? "Verbunden" : "Getrennt") : "Deaktiviert",
        dot: s.mqtt.enabled ? (s.mqtt.connected ? "ok" : "err") : "" },
      { k: "Letzte Aktualisierung", v: fmtDateTime(s.last_success) },
      { k: "Abfrage-Intervall", v: `${Math.round(s.poll_interval / 60)} min` },
    ];
    // OAuth card only makes sense when an Application ID is configured
    $("#oauth-card").style.display = (!s.demo_mode && s.app_id_configured) ? "" : "none";
    $("#diag-card").style.display = s.demo_mode ? "none" : "";
    if (!this._negotiationShown && s.negotiation && s.negotiation.length && !s.api_profile) {
      this.renderDiagTable(s.negotiation);
      this._negotiationShown = true;
    }
    $("#status-grid").innerHTML = items.map((it) => `
      <div class="card status-item">
        <div class="k">${it.k}</div>
        <div class="v">${it.dot ? `<span class="conn-dot ${it.dot}"></span>` : ""}${it.v}</div>
      </div>`).join("");
    const ec = $("#error-card");
    if (s.last_error) {
      ec.style.display = "";
      $("#error-pre").textContent = `${fmtDateTime(s.last_error_ts)}\n${s.last_error}`;
    } else ec.style.display = "none";
  },

  /* ---------------------------- diagnostics & OAuth ---------------------- */
  initDiagnostics() {
    const runBtn = $("#diag-run");
    runBtn.addEventListener("click", async () => {
      runBtn.disabled = true;
      runBtn.textContent = "Diagnose läuft…";
      $("#diag-result").innerHTML = "";
      try {
        const res = await apiPost("api/diagnose");
        this.renderDiagTable(res.results);
        if (res.results.some((r) => r.ok)) {
          toast("Funktionierende API-Variante gefunden");
          this.refresh();
        }
      } catch (err) {
        $("#diag-result").innerHTML = `<p class="hint">Diagnose fehlgeschlagen: ${err.message}</p>`;
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = "Diagnose ausführen";
      }
    });

    const redirect = $("#oauth-redirect");
    redirect.value = localStorage.getItem("sgf-oauth-redirect") || "";
    redirect.addEventListener("input", () =>
      localStorage.setItem("sgf-oauth-redirect", redirect.value.trim()));
    $("#oauth-open").addEventListener("click", async () => {
      const uri = redirect.value.trim();
      if (!uri) { $("#oauth-msg").textContent = "Bitte zuerst die Redirect-URL eintragen."; return; }
      try {
        const res = await api("api/oauth/url?redirect_uri=" + encodeURIComponent(uri));
        window.open(res.url, "_blank");
        $("#oauth-msg").textContent = "Autorisierungsseite geöffnet – nach der Freigabe die Weiterleitungs-URL unten einfügen.";
      } catch (err) { $("#oauth-msg").textContent = err.message; }
    });
    $("#oauth-submit").addEventListener("click", async () => {
      const code = $("#oauth-code").value.trim();
      const uri = redirect.value.trim();
      if (!code) { $("#oauth-msg").textContent = "Bitte Code oder Weiterleitungs-URL einfügen."; return; }
      try {
        await apiPost("api/oauth/code", { code, redirect_uri: uri });
        $("#oauth-msg").textContent = "✓ Autorisierung erfolgreich – die Verbindung wird neu aufgebaut.";
        toast("OAuth-Autorisierung erfolgreich");
        this.refresh();
      } catch (err) { $("#oauth-msg").textContent = "Fehler: " + err.message; }
    });
  },

  renderDiagTable(results) {
    if (!results || !results.length) {
      $("#diag-result").innerHTML = `<p class="hint">Keine Ergebnisse.</p>`;
      return;
    }
    const rows = results.map((r) => `
      <tr>
        <td>${r.profile.label}</td>
        <td>${r.ok
          ? `<span class="pill ok">OK</span>`
          : `<span class="pill err">Fehler</span> <code>${r.code || "?"}</code>`}</td>
        <td class="diag-detail">${(r.detail || "").replace(/</g, "&lt;")}${r.endpoint ? `<br><code>${r.endpoint}</code>` : ""}</td>
      </tr>`).join("");
    $("#diag-result").innerHTML = `
      <table class="diag-table">
        <thead><tr><th>Variante</th><th>Ergebnis</th><th>Details</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  },
};

App.init();
