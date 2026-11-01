/* Stalk Market UI helpers: formatting, icons, API, dialogs and hand-written SVG charts.
   Copied from the archived NexusQuant dashboard (same design system). Money is always ₹ (en-IN). */
"use strict";
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const isNum = (x) => x !== null && x !== undefined && x !== "" && !Number.isNaN(Number(x));
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));
const MINUS = "−";

const INR = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const INR0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const DEC2 = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const INT = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

function inr(x, o = {}) {
  if (!isNum(x)) return "—";
  const v = Number(x);
  const body = (o.whole ? INR0 : INR).format(Math.abs(v));
  if (o.sign) return (v > 0.004 ? "+" : v < -0.004 ? MINUS : "") + body;
  return (v < -0.004 ? MINUS : "") + body;
}
function inrShort(x) {
  if (!isNum(x)) return "—";
  const v = Number(x), a = Math.abs(v), s = v < 0 ? MINUS : "";
  if (a >= 1e7) return `${s}₹${(a / 1e7).toFixed(1)}Cr`;
  if (a >= 1e5) return `${s}₹${(a / 1e5).toFixed(1)}L`;
  if (a >= 1e3) return `${s}₹${(a / 1e3).toFixed(a >= 1e4 ? 0 : 1)}K`;
  return `${s}₹${INT.format(a)}`;
}
const price = (x) => (isNum(x) ? DEC2.format(x) : "—");
const int = (x) => (isNum(x) ? INT.format(x) : "—");
function fixed(x, d = 2, sign = false) {
  if (!isNum(x)) return "—";
  const v = Number(x);
  const s = Math.abs(v).toFixed(d);
  if (Number(s) === 0) return (0).toFixed(d);
  return (v < 0 ? MINUS : sign ? "+" : "") + s;
}
const pct = (x, d = 0) => (isNum(x) ? fixed(Number(x) * 100, d) + "%" : "—");
const rmult = (x, d = 2) => (isNum(x) ? fixed(x, d, true) + "R" : "—");
const tone = (x, eps = 0.005) => (!isNum(x) || Math.abs(x) < eps ? "" : x > 0 ? "pos" : "neg");
const TZ = { timeZone: "Asia/Kolkata", hour12: false };
// Building a date formatter is slow (toLocaleString makes a new one every call); make each kind once and reuse it
const _dtf = new Map();
function fmtDate(date, opts) {
  const k = JSON.stringify(opts);
  if (!_dtf.has(k)) _dtf.set(k, new Intl.DateTimeFormat("en-IN", opts));
  return _dtf.get(k).format(date);
}
const istDay = (d) => new Date(d + "T00:00:00+05:30");
const hhmm = (ts) => (ts ? fmtDate(new Date(ts * 1000), { ...TZ, hour: "2-digit", minute: "2-digit" }) : "—");
const dayLabel = (ts) => fmtDate(new Date(ts * 1000), { timeZone: "Asia/Kolkata", weekday: "short", day: "numeric", month: "short" });
const dateShort = (ts) => fmtDate(new Date(ts * 1000), { timeZone: "Asia/Kolkata", day: "numeric", month: "short" });
const dateTime = (ts) => (ts ? fmtDate(new Date(ts * 1000), { ...TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "—");
const isoLabel = (d) => (d ? fmtDate(istDay(d), { timeZone: "Asia/Kolkata", weekday: "short", day: "numeric", month: "short" }) : "—");
// Hour of the day in IST from epoch seconds, without any formatter (IST is UTC+5:30 all year)
const istHour = (ts) => Math.floor(((ts + 19800) % 86400) / 3600);
// Long curves are for the shape, not each day: keep every k-th point (and the last) so a chart has at most ~max points
function thin(pts, max = 260) {
  if (pts.length <= max) return pts;
  const k = Math.ceil(pts.length / max);
  return pts.filter((p, i) => i % k === 0 || i === pts.length - 1);
}
const mmss = (s) => (isNum(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}` : "—");
const plural = (n, w) => `${int(n)} ${w}${Number(n) === 1 ? "" : "s"}`;

// ------------------------------------------------------------ icons (Lucide)
/* Icons: Phosphor fill paths from icons.js, wrapped in an <svg> sized by CSS. */
function icon(name, cls = "") {
  const paths = typeof ICON_PATHS === "undefined" ? {} : ICON_PATHS;
  const d = paths[name] || paths.circle || "";
  return `<svg class="i ${cls}" viewBox="0 0 256 256" aria-hidden="true">${d}</svg>`;
}
function hydrateIcons(root = document) {
  $$("[data-icon]", root).forEach((el) => { el.outerHTML = icon(el.dataset.icon); });
}

// Away from localhost (the Netlify copy) the page reads saved answers from /snapshot (scripts/snapshot_site.py) and changes nothing.
const STATIC = !/^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
function staticPath(path) {
  const [p, q] = path.split("?");
  return `/snapshot${p}${q ? "__" + q.replace(/[^A-Za-z0-9_.-]/g, "_") : ""}.json`;
}

async function api(path, opts = {}) {
  if (STATIC) {
    if ((opts.method || "GET") !== "GET") throw new Error("This is a read-only copy. Run the app on your laptop to use this.");
    const r = await fetch(staticPath(path));
    if (!r.ok) throw new Error("Not in this read-only copy");
    return r.json();
  }
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch(path, init);
  const text = await r.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
  if (!r.ok) {
    const d = data && data.detail;
    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => x.msg).join("; ") : `HTTP ${r.status}`);
  }
  return data;
}

let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), 4500);
}
async function act(fn, okMsg) {
  try {
    const res = await fn();
    if (okMsg) toast(typeof okMsg === "function" ? okMsg(res) : okMsg);
    return res;
  } catch (e) {
    toast(e.message);
    return null;
  }
}
function ask(title, body, okLabel = "Confirm", danger = false) {
  const dlg = $("#dlg");
  $("#dlg-title").textContent = title;
  $("#dlg-body").textContent = body;
  const ok = $("#dlg-ok");
  ok.textContent = okLabel;
  ok.className = danger ? "btn danger" : "btn primary";
  return new Promise((resolve) => {
    const done = (v) => { dlg.close(); ok.onclick = null; $("#dlg-cancel").onclick = null; resolve(v); };
    ok.onclick = () => done(true);
    $("#dlg-cancel").onclick = () => done(false);
    dlg.oncancel = () => resolve(false);
    dlg.showModal();
  });
}
// Only touch the DOM when the markup changed: keeps hover, focus and scroll stable under 4 updates/s.
function setHTML(el, html) {
  if (el && el._html !== html) { el.innerHTML = html; el._html = html; }
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}
const SVGNS = 'xmlns="http://www.w3.org/2000/svg"';
function niceStep(range, count) {
  const raw = range / Math.max(1, count);
  const mag = Math.pow(10, Math.floor(Math.log10(raw || 1)));
  const n = raw / mag;
  return (n >= 5 ? 10 : n >= 2 ? 5 : n >= 1 ? 2 : 1) * mag;
}
function ticks(min, max, count = 4) {
  if (min === max) { min -= 1; max += 1; }
  const step = niceStep(max - min, count);
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-6; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  return { ticks: out, step };
}
function lin(d0, d1, r0, r1) {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return (v) => r0 + (v - d0) * k;
}
function size(el) { return { w: Math.max(200, el.clientWidth), h: Math.max(120, el.clientHeight) }; }
function tipEl(el) {
  let t = el.querySelector(".tip");
  if (!t) { t = document.createElement("div"); t.className = "tip"; el.appendChild(t); }
  return t;
}
function placeTip(el, tip, x, y) {
  const { w } = size(el);
  const tw = tip.offsetWidth || 160, th = tip.offsetHeight || 60;
  let left = x + 14;
  if (left + tw > w) left = x - tw - 14;
  tip.style.left = `${clamp(left, 0, Math.max(0, w - tw))}px`;
  tip.style.top = `${clamp(y - th / 2, -8, el.clientHeight - th + 8)}px`;
}
const tipRows = (rows) => rows.map(([k, v]) => `<div class="row"><span>${esc(k)}</span><span>${v}</span></div>`).join("");

// Draw only when data, size or theme changed. el._draw lets a resize redraw it.
function chart(el, sig, draw) {
  if (!el) return;
  const { w, h } = size(el);
  const key = `${sig}|${w}x${h}|${currentTheme()}`;
  el._draw = () => { el._sig = null; chart(el, sig, draw); };
  if (el._sig === key) return;
  el._sig = key;
  draw(el, w, h);
}
function redrawCharts() { $$("[data-chart]").forEach((el) => el.offsetParent && el._draw && el._draw()); }
function emptyChart(el, msg) { el.innerHTML = `<div class="empty">${msg}</div>`; }

/* Line chart. series: [{pts:[[x,y]], cls, split?, area?, dots?}] */
function lineChart(el, series, o = {}) {
  const all = series.flatMap((s) => s.pts);
  if (!all.length) return emptyChart(el, o.empty || "No data yet");
  const { w, h } = size(el);
  const pad = { l: o.padL ?? 56, r: 12, t: 10, b: 26 };
  const xs = all.map((p) => p[0]), ys = all.map((p) => p[1]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs);
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  let y0 = Math.min(...ys, o.zero ? 0 : Infinity), y1 = Math.max(...ys, o.zero ? 0 : -Infinity);
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const yt = ticks(y0, y1, 4);
  y0 = Math.min(y0, yt.ticks[0]); y1 = Math.max(y1, yt.ticks[yt.ticks.length - 1]);
  const X = lin(x0, x1, pad.l, w - pad.r), Y = lin(y0, y1, h - pad.b, pad.t);
  const fx = o.fmtX || ((v) => v), fy = o.fmtY || ((v) => v);
  let g = "";
  yt.ticks.forEach((v) => {
    g += `<line class="${v === 0 && o.zero ? "zeroline" : "gridline"}" x1="${pad.l}" x2="${w - pad.r}" y1="${Y(v)}" y2="${Y(v)}"/>`;
    g += `<text x="${pad.l - 10}" y="${Y(v) + 3.5}" text-anchor="end">${esc(fy(v))}</text>`;
  });
  const xticks = o.xTicks ? o.xTicks(x0, x1) : ticks(x0, x1, Math.max(2, Math.floor((w - pad.l) / 120))).ticks;
  let lastX = -Infinity;
  xticks.forEach((v) => {
    if (v < x0 || v > x1 || X(v) - lastX < 60) return;
    lastX = X(v);
    g += `<text x="${X(v)}" y="${h - 6}" text-anchor="middle">${esc(fx(v))}</text>`;
  });
  const id = `c${Math.random().toString(36).slice(2, 8)}`;
  const zy = Y(clamp(0, y0, y1));
  const grad = (n, c) => `<linearGradient id="${id}${n}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:var(${c});stop-opacity:.32"/><stop offset="1" style="stop-color:var(${c});stop-opacity:0"/></linearGradient>`;
  const defs = grad("gp", "--pos-mark") + grad("gn", "--neg-mark") + `<clipPath id="${id}a"><rect x="0" y="0" width="${w}" height="${zy}"/></clipPath><clipPath id="${id}b"><rect x="0" y="${zy}" width="${w}" height="${Math.max(0, h - zy)}"/></clipPath>`;
  let body = "";
  series.forEach((s) => {
    if (!s.pts.length) return;
    const d = s.pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)},${Y(p[1]).toFixed(1)}`).join("");
    const base = Y(clamp(o.zero ? 0 : y0, y0, y1));
    const area = `${d}L${X(s.pts[s.pts.length - 1][0]).toFixed(1)},${base}L${X(s.pts[0][0]).toFixed(1)},${base}Z`;
    if (s.split) {
      body += `<path fill="url(#${id}gp)" clip-path="url(#${id}a)" d="${area}"/><path fill="url(#${id}gn)" clip-path="url(#${id}b)" d="${area}"/>`;
      body += `<path class="series stroke-pos" clip-path="url(#${id}a)" d="${d}"/><path class="series stroke-neg" clip-path="url(#${id}b)" d="${d}"/>`;
    } else {
      if (s.area) body += `<path class="area-accent" d="${area}"/>`;
      body += `<path class="series ${s.cls || "accent"}" d="${d}"/>`;
    }
    if (s.dots) s.pts.forEach((p) => { body += `<circle class="${s.dotCls ? s.dotCls(p) : "fill-accent"} hoverdot" cx="${X(p[0])}" cy="${Y(p[1])}" r="4"/>`; });
  });
  const main = series[0].pts;
  const last = main[main.length - 1];
  const dotCls = (v) => (series[0].split ? (v >= 0 ? "fill-pos" : "fill-neg") : "fill-accent");
  if (o.endDot !== false && last) body += `<circle class="${dotCls(last[1])} hoverdot" cx="${X(last[0])}" cy="${Y(last[1])}" r="4"/>`;
  el.innerHTML = `<svg ${SVGNS} width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><defs>${defs}</defs>${g}${body}<line class="cross hidden" y1="${pad.t}" y2="${h - pad.b}"/><circle class="hoverdot hidden" id="${id}d" r="4"/><rect x="${pad.l}" y="0" width="${w - pad.l - pad.r}" height="${h}" fill="transparent"/></svg>`;
  const svg = el.querySelector("svg"), cross = svg.querySelector(".cross"), dot = svg.querySelector(`#${id}d`), tip = tipEl(el);
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const mx = ev.clientX - r.left;
    let best = 0, bd = Infinity;
    main.forEach((p, i) => { const dd = Math.abs(X(p[0]) - mx); if (dd < bd) { bd = dd; best = i; } });
    const p = main[best];
    cross.setAttribute("x1", X(p[0])); cross.setAttribute("x2", X(p[0])); cross.classList.remove("hidden");
    dot.setAttribute("cx", X(p[0])); dot.setAttribute("cy", Y(p[1]));
    dot.setAttribute("class", `hoverdot ${dotCls(p[1])}`);
    tip.innerHTML = o.tip ? o.tip(p, best) : `<b>${esc(fx(p[0]))}</b><br>${esc(fy(p[1]))}`;
    tip.classList.add("on");
    placeTip(el, tip, X(p[0]), Y(p[1]));
  });
  svg.addEventListener("mouseleave", () => { cross.classList.add("hidden"); dot.setAttribute("class", "hoverdot hidden"); tip.classList.remove("on"); });
}

/* Column chart. items: [{label, value, tip, baseline?}]. Positive green, negative red. */
function columnChart(el, items, o = {}) {
  if (!items.length) return emptyChart(el, o.empty || "No data yet");
  const { w, h } = size(el);
  const pad = { l: o.padL ?? 48, r: 8, t: 10, b: 26 };
  const vals = items.flatMap((d) => [d.value, isNum(d.baseline) ? d.baseline : d.value]);
  let y0 = Math.min(0, ...vals), y1 = Math.max(0, ...vals);
  const yt = ticks(y0, y1, 4);
  y0 = Math.min(y0, yt.ticks[0]); y1 = Math.max(y1, yt.ticks[yt.ticks.length - 1]);
  const Y = lin(y0, y1, h - pad.b, pad.t);
  const band = (w - pad.l - pad.r) / items.length;
  const bw = Math.min(24, band * 0.55);
  const fy = o.fmtY || ((v) => v);
  let g = "";
  yt.ticks.forEach((v) => {
    g += `<line class="${v === 0 ? "zeroline" : "gridline"}" x1="${pad.l}" x2="${w - pad.r}" y1="${Y(v)}" y2="${Y(v)}"/>`;
    g += `<text x="${pad.l - 10}" y="${Y(v) + 3.5}" text-anchor="end">${esc(fy(v))}</text>`;
  });
  const every = Math.ceil(items.length / Math.max(1, Math.floor((w - pad.l) / 56)));
  let bars = "";
  items.forEach((d, i) => {
    const cx = pad.l + band * i + band / 2;
    const v = d.value, yz = Y(0), yv = Y(v);
    const hgt = Math.abs(yz - yv);
    const r = Math.min(4, hgt, bw / 2);
    const x = cx - bw / 2;
    const cls = v >= 0 ? "fill-pos" : "fill-neg";
    const path = v >= 0
      ? `M${x},${yz}V${yv + r}Q${x},${yv} ${x + r},${yv}H${x + bw - r}Q${x + bw},${yv} ${x + bw},${yv + r}V${yz}Z`
      : `M${x},${yz}V${yv - r}Q${x},${yv} ${x + r},${yv}H${x + bw - r}Q${x + bw},${yv} ${x + bw},${yv - r}V${yz}Z`;
    bars += hgt > 0.5 ? `<path class="${cls}" d="${path}"/>` : `<line class="axisline" x1="${x}" x2="${x + bw}" y1="${yz}" y2="${yz}"/>`;
    if (isNum(d.baseline)) bars += `<line class="baseline-tick" x1="${cx - bw / 2 - 3}" x2="${cx + bw / 2 + 3}" y1="${Y(d.baseline)}" y2="${Y(d.baseline)}"/>`;
    if (i % every === 0) bars += `<text x="${cx}" y="${h - 6}" text-anchor="middle">${esc(d.label)}</text>`;
    bars += `<rect class="bar-hit" data-i="${i}" x="${pad.l + band * i}" y="${pad.t}" width="${band}" height="${h - pad.t - pad.b}"/>`;
  });
  el.innerHTML = `<svg ${SVGNS} width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${g}${bars}</svg>`;
  const tip = tipEl(el);
  $$(".bar-hit", el).forEach((r) => {
    r.addEventListener("mouseenter", () => {
      const i = Number(r.dataset.i), d = items[i];
      tip.innerHTML = d.tip || `<b>${esc(d.label)}</b><br>${esc(fy(d.value))}`;
      tip.classList.add("on");
      placeTip(el, tip, pad.l + band * i + band / 2, Y(d.value));
    });
    r.addEventListener("mouseleave", () => tip.classList.remove("on"));
  });
}

/* Candlesticks for one day: session VWAP, volume, price tags for stop/entry/target, entry and exit markers. */
function candleChart(el, bars, o = {}) {
  if (!bars.length) return emptyChart(el, o.empty || "No bars for this day yet");
  const { w, h } = size(el);
  const pad = { l: 4, r: 76, t: 12, b: 26 };
  const volH = Math.round((h - pad.t - pad.b) * 0.15);
  const plotB = h - pad.b - volH - 8;
  const levels = (o.levels || []).filter((l) => isNum(l.y));
  let y0 = Math.min(...bars.map((b) => b[3]), ...levels.map((l) => l.y));
  let y1 = Math.max(...bars.map((b) => b[2]), ...levels.map((l) => l.y));
  const span = (y1 - y0) || y1 * 0.01 || 1;
  y0 -= span * 0.06; y1 += span * 0.06;
  const Y = lin(y0, y1, plotB, pad.t);
  const n = Math.max(bars.length + 4, 30);
  const band = (w - pad.l - pad.r) / n;
  const bw = clamp(band * 0.62, 1.5, 10);
  const X = (i) => pad.l + band * i + band / 2;
  const vmax = Math.max(...bars.map((b) => b[5] || 0), 1);
  const tagYs = levels.map((l) => Y(l.y));
  let g = "";
  ticks(y0, y1, 5).ticks.forEach((v) => {
    if (v < y0 || v > y1) return;
    g += `<line class="gridline" x1="${pad.l}" x2="${w - pad.r}" y1="${Y(v)}" y2="${Y(v)}"/>`;
    if (tagYs.every((ty) => Math.abs(ty - Y(v)) > 14)) g += `<text x="${w - pad.r + 10}" y="${Y(v) + 3.5}">${esc(price(v))}</text>`;
  });
  let lastX = -Infinity;
  bars.forEach((b, i) => {
    const mm = hhmm(b[0]).slice(3);
    if ((mm === "00" || i === 0) && X(i) - lastX >= 48) {
      g += `<text x="${X(i)}" y="${h - 6}" text-anchor="middle">${esc(hhmm(b[0]))}</text>`;
      lastX = X(i);
    }
  });
  let body = "";
  bars.forEach((b, i) => {
    const cls = b[4] >= b[1] ? "candle-up" : "candle-down";
    const yo = Y(b[1]), yc = Y(b[4]);
    body += `<line class="wick ${cls}" x1="${X(i)}" x2="${X(i)}" y1="${Y(b[2])}" y2="${Y(b[3])}"/>`;
    body += `<rect class="${cls}" x="${X(i) - bw / 2}" y="${Math.min(yo, yc)}" width="${bw}" height="${Math.max(1, Math.abs(yc - yo))}" rx="${bw > 4 ? 1 : 0}"/>`;
    const vh = ((b[5] || 0) / vmax) * volH;
    if (vh > 0) body += `<rect class="fill-neutral" x="${X(i) - bw / 2}" y="${h - pad.b - vh}" width="${bw}" height="${vh}"/>`;
  });
  const vw = bars.map((b, i) => (isNum(b[6]) ? `${X(i).toFixed(1)},${Y(b[6]).toFixed(1)}` : null)).filter(Boolean);
  if (vw.length > 1) body += `<polyline class="series vwap" points="${vw.join(" ")}"/>`;
  levels.forEach((l) => {
    const y = Y(l.y);
    body += `<line class="level ${l.kind}" x1="${pad.l}" x2="${w - pad.r}" y1="${y}" y2="${y}"/>`;
    body += `<rect class="tagbox ${l.kind}" x="${w - pad.r + 4}" y="${y - 10}" width="${pad.r - 4}" height="20" rx="5"/><text class="tagtext" x="${w - pad.r + 10}" y="${y + 4}">${esc(price(l.y))}</text>`;
  });
  (o.markers || []).forEach((m) => {
    const i = bars.findIndex((b) => b[0] <= m.ts && m.ts < b[0] + (o.barSeconds || 300));
    if (i < 0) return;
    const x = X(i), y = Y(m.price);
    if (m.kind === "entry") {
      const dir = m.side === "long" ? 1 : -1;
      body += `<path class="${m.side === "long" ? "fill-long" : "fill-short"}" d="M${x},${y + dir * 4}L${x - 6},${y + dir * 16}L${x + 6},${y + dir * 16}Z" style="stroke:var(--surface);stroke-width:2;paint-order:stroke"/>`;
    } else {
      body += `<circle cx="${x}" cy="${y}" r="6" style="fill:var(--surface);stroke:var(--${m.side});stroke-width:2.5"/>`;
    }
  });
  el.innerHTML = `<svg ${SVGNS} width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${g}${body}<line class="cross vx hidden" y1="${pad.t}" y2="${h - pad.b}"/><line class="cross hy hidden" x1="${pad.l}" x2="${w - pad.r}"/><rect x="${pad.l}" y="0" width="${w - pad.l - pad.r}" height="${h}" fill="transparent"/></svg>`;
  const svg = el.querySelector("svg"), vx = svg.querySelector(".vx"), hy = svg.querySelector(".hy"), tip = tipEl(el);
  svg.addEventListener("mousemove", (ev) => {
    const r = svg.getBoundingClientRect();
    const mx = ev.clientX - r.left, my = ev.clientY - r.top;
    const i = clamp(Math.floor((mx - pad.l) / band), 0, bars.length - 1);
    const b = bars[i];
    vx.setAttribute("x1", X(i)); vx.setAttribute("x2", X(i)); vx.classList.remove("hidden");
    if (my < plotB) { hy.setAttribute("y1", my); hy.setAttribute("y2", my); hy.classList.remove("hidden"); } else hy.classList.add("hidden");
    tip.innerHTML = `<b>${esc(hhmm(b[0]))}</b>${tipRows([["Open", price(b[1])], ["High", price(b[2])], ["Low", price(b[3])], ["Close", `<span class="${tone(b[4] - b[1])}">${price(b[4])}</span>`], ["VWAP", price(b[6])], ["Volume", int(b[5])]])}`;
    tip.classList.add("on");
    placeTip(el, tip, X(i), my);
  });
  svg.addEventListener("mouseleave", () => { vx.classList.add("hidden"); hy.classList.add("hidden"); tip.classList.remove("on"); });
}

/* Calibration: predicted probability vs realised win rate per bucket. */
function calibrationChart(el, rows, side) {
  if (!rows || !rows.length) return emptyChart(el, "No out-of-sample predictions yet");
  const { w, h } = size(el);
  const pad = { l: 44, r: 12, t: 10, b: 28 };
  const hi = Math.min(1, Math.max(0.6, ...rows.map((r) => Math.max(r.p_mean, r.win_rate))) + 0.05);
  const X = lin(0, hi, pad.l, w - pad.r), Y = lin(0, hi, h - pad.b, pad.t);
  let g = "";
  ticks(0, hi, 4).ticks.forEach((v) => {
    g += `<line class="gridline" x1="${pad.l}" x2="${w - pad.r}" y1="${Y(v)}" y2="${Y(v)}"/><text x="${pad.l - 10}" y="${Y(v) + 3.5}" text-anchor="end">${pct(v)}</text>`;
    g += `<text x="${X(v)}" y="${h - 8}" text-anchor="middle">${pct(v)}</text>`;
  });
  g += `<line class="axisline" x1="${X(0)}" y1="${Y(0)}" x2="${X(hi)}" y2="${Y(hi)}"/>`;
  let body = `<polyline class="series" style="stroke:var(--${side})" points="${rows.map((r) => `${X(r.p_mean)},${Y(r.win_rate)}`).join(" ")}"/>`;
  rows.forEach((r, i) => { body += `<circle cx="${X(r.p_mean)}" cy="${Y(r.win_rate)}" r="5" style="fill:var(--${side});stroke:var(--surface);stroke-width:2"/><circle data-i="${i}" cx="${X(r.p_mean)}" cy="${Y(r.win_rate)}" r="12" fill="transparent"/>`; });
  el.innerHTML = `<svg ${SVGNS} width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">${g}${body}</svg>`;
  const tip = tipEl(el);
  $$("circle[data-i]", el).forEach((c) => {
    c.addEventListener("mouseenter", () => {
      const r = rows[Number(c.dataset.i)];
      tip.innerHTML = `<b>Predicted ${pct(r.p_mean, 1)}</b>${tipRows([["Won", pct(r.win_rate, 1)], ["Mean result", rmult(r.mean_r)], ["Samples", int(r.n)]])}`;
      tip.classList.add("on");
      placeTip(el, tip, X(r.p_mean), Y(r.win_rate));
    });
    c.addEventListener("mouseleave", () => tip.classList.remove("on"));
  });
}

/* Calendar heatmap: as many weeks as fit the card, newest on the right. */
function calendarChart(el, days) {
  const { w } = size(el);
  const n = clamp(Math.floor((w - 36) / 22), 10, 53);
  const byDay = Object.fromEntries(days.map((d) => [d.day, d]));
  const today = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  today.setHours(0, 0, 0, 0);
  const monday = new Date(today); monday.setDate(today.getDate() - ((today.getDay() + 6) % 7));
  const start = new Date(monday); start.setDate(monday.getDate() - 7 * (n - 1));
  const maxAbs = Math.max(1, ...days.map((d) => Math.abs(d.net)));
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  const cells = ["<span></span>"];
  let lastMonth = -1;
  for (let c = 0; c < n; c++) {
    const d = new Date(start); d.setDate(start.getDate() + 7 * c);
    const label = d.getMonth() !== lastMonth && c < n - 1 ? d.toLocaleDateString("en-IN", { month: "short" }) : "";
    if (label) lastMonth = d.getMonth();
    cells.push(`<span class="m">${label}</span>`);
  }
  ["Mon", "", "Wed", "", "Fri"].forEach((name, row) => {
    cells.push(`<span class="d">${name}</span>`);
    for (let c = 0; c < n; c++) {
      const d = new Date(start); d.setDate(start.getDate() + 7 * c + row);
      if (d > today) { cells.push('<span class="cal-cell future"></span>'); continue; }
      const k = iso(d), r = byDay[k];
      let cls = "";
      if (r) { const lvl = Math.min(3, 1 + Math.floor((Math.abs(r.net) / maxAbs) * 2.999)); cls = r.net > 0 ? `pos-${lvl}` : r.net < 0 ? `neg-${lvl}` : "flat"; }
      const title = r ? `${isoLabel(k)}: ${inr(r.net, { sign: true })}, ${plural(r.trades, "trade")} (${r.kind})` : `${isoLabel(k)}: no trades`;
      const clickable = r || S.replayDays.includes(k);
      cells.push(`<button class="cal-cell ${cls}" ${clickable ? `data-act="cal-day" data-date="${k}"` : 'tabindex="-1"'} title="${esc(title)}" aria-label="${esc(title)}"></button>`);
    }
  });
  el.innerHTML = `<div class="cal" style="grid-template-columns: 28px repeat(${n}, minmax(0, 1fr))">${cells.join("")}</div>`;
}

function meter(parts) {
  return `<div class="meter">${parts.filter((p) => p.w > 0.001).map((p) => `<span class="${p.cls || ""}" style="width:${clamp(p.w * 100, 0, 100)}%"></span>`).join("")}</div>`;
}
function table(head, rows, empty) {
  const th = head.map(([label, right, cls]) => `<th class="${right ? "r" : ""} ${cls || ""}">${label}</th>`).join("");
  const body = rows.length ? rows.join("") : `<tr class="empty-row"><td colspan="${head.length}">${empty}</td></tr>`;
  return `<div class="table-wrap"><table><thead><tr>${th}</tr></thead><tbody>${body}</tbody></table></div>`;
}
const sideChip = (s) => `<span class="chip ${esc(s)}">${s === "long" ? "Long" : "Short"}</span>`;
const emptyState = (title, body) => `<div class="empty-state"><b>${title}</b><p>${body}</p></div>`;
const stat = (label, value, ctx = "", extra = "") => `<div class="label">${label}</div><div class="value">${value}</div>${ctx ? `<div class="ctx">${ctx}</div>` : ""}${extra}`;
const dotSep = '<span class="sep">·</span>';



/* Daily P&L calendar: weeks run left to right, Monday to Friday top to bottom, like a contribution graph.
   Each cell is coloured by how big the day's profit or loss was, shows the amount when there is room,
   and opens a small card on hover. days: [{day:"YYYY-MM-DD", net, trades, gross?, costs?}] */
function calendar(days, o = {}) {
  if (!days.length) return `<div class="empty-state"><span class="ic">${icon("calendar")}</span><p>${o.empty || "No trading days yet."}</p></div>`;
  const byDay = Object.fromEntries(days.map((d) => [d.day, d]));
  const toDate = (d) => new Date(d + "T00:00:00Z");
  const iso = (dt) => dt.toISOString().slice(0, 10);
  const start = toDate(days[0].day);
  start.setUTCDate(start.getUTCDate() - ((start.getUTCDay() + 6) % 7));
  const end = toDate(days[days.length - 1].day);
  const weeks = [];
  for (let wk = new Date(start); wk <= end; wk.setUTCDate(wk.getUTCDate() + 7)) weeks.push(new Date(wk));
  const maxAbs = Math.max(1, ...days.map((d) => Math.abs(d.net || 0)));
  const level = (v) => {
    if (!v) return "";
    const q = Math.abs(v) / maxAbs;
    return (v > 0 ? "p" : "n") + (q > 0.66 ? 4 : q > 0.33 ? 3 : q > 0.12 ? 2 : 1);
  };
  const compact = weeks.length > 16;
  let months = `<span></span>`, last = "";
  weeks.forEach((wk) => {
    const m = wk.toLocaleDateString("en-IN", { month: "short", timeZone: "UTC" });
    months += `<span class="mo">${m !== last ? m : ""}</span>`;
    last = m;
  });
  let rows = `<div class="cal-row">${months}</div>`;
  ["Mon", "Tue", "Wed", "Thu", "Fri"].forEach((wd, i) => {
    let r = `<span class="wd">${wd}</span>`;
    weeks.forEach((wk) => {
      const dt = new Date(wk); dt.setUTCDate(wk.getUTCDate() + i);
      const key = iso(dt), d = byDay[key];
      if (dt < toDate(days[0].day) || dt > end) { r += `<span class="cal-cell out"></span>`; return; }
      if (!d) { r += `<span class="cal-cell none" title="${esc(isoLabel(key))}: no session"></span>`; return; }
      r += `<span class="cal-cell ${level(d.net)}" data-day="${key}">${d.trades ? esc(inrShort(d.net).replace(/^(?=₹)/, d.net > 0 ? "+" : "")) : "–"}</span>`;
    });
    rows += `<div class="cal-row">${r}</div>`;
  });
  const nets = days.map((d) => d.net || 0);
  const green = nets.filter((v) => v > 0).length, red = nets.filter((v) => v < 0).length;
  let streak = 0, best = 0;
  nets.forEach((v) => { streak = v > 0 ? streak + 1 : 0; best = Math.max(best, streak); });
  const legend = `<div class="cal-legend">Loss ${["n4", "n2", "0", "p2", "p4"].map((c) => `<i style="background:var(--heat-${c})"></i>`).join("")} Profit</div>`;
  calendar.data = byDay;
  return `<div class="cal ${compact ? "compact" : ""}" style="--weeks:${weeks.length}" data-cal>${rows}</div>
    <div class="cal-foot"><div class="cal-stats"><span>Green days <b class="pos">${green}</b></span><span>Red days <b class="neg">${red}</b></span>
    <span>Best <b>${inr(Math.max(...nets), { sign: true })}</b></span><span>Worst <b>${inr(Math.min(...nets), { sign: true })}</b></span>
    <span>Longest green run <b>${best}</b></span></div>${legend}</div>`;
}
function calendarTip(ev) {
  const cell = ev.target.closest && ev.target.closest(".cal-cell[data-day]");
  let tip = document.getElementById("cal-tip");
  if (!tip) { tip = document.createElement("div"); tip.id = "cal-tip"; tip.className = "tip ftip"; document.body.appendChild(tip); }
  if (!cell) { tip.classList.remove("on"); return; }
  const d = (calendar.data || {})[cell.dataset.day];
  if (!d) return;
  const rows = [["Net", `<span class="${tone(d.net)}">${inr(d.net, { sign: true })}</span>`], ["Trades", int(d.trades)]];
  if (isNum(d.gross)) rows.push(["Before charges", `<span class="${tone(d.gross)}">${inr(d.gross, { sign: true })}</span>`]);
  if (isNum(d.costs)) rows.push(["Charges", `<span class="neg">${inr(-d.costs, { sign: true })}</span>`]);
  if (isNum(d.end_equity)) rows.push(["Account after", inr(d.end_equity)]);
  const yr = d.day.slice(0, 4) !== new Date().getFullYear().toString() ? ` ${d.day.slice(0, 4)}` : "";   // older days show their year
  tip.innerHTML = `<b>${esc(isoLabel(d.day))}${yr}</b>${tipRows(rows)}`;
  const r = cell.getBoundingClientRect();
  tip.classList.add("on");
  const w = tip.offsetWidth, h = tip.offsetHeight;
  tip.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.left + r.width / 2 - w / 2))}px`;
  tip.style.top = `${r.top - h - 8 < 8 ? r.bottom + 8 : r.top - h - 8}px`;
}

/* Dropdown that replaces a native select. opts: [[value, label, short?]]; the button shows the short label when given. Picking an option fires
   onAction with data-act=act and data-val=value. */
function dropdown(id, opts, value, act, label = "") {
  const cur = opts.find((o) => String(o[0]) === String(value)) || opts[0] || ["", ""];
  const open = S.openDd === id;
  return `<div class="dd ${open ? "open" : ""}"><button type="button" data-act="dd" data-val="${esc(id)}" aria-haspopup="listbox" aria-expanded="${open}" ${label ? `aria-label="${esc(label)}"` : ""}><span>${esc(cur[2] || cur[1])}</span>${icon("caret", "xs")}</button>
    ${open ? `<div class="dd-menu" role="listbox">${opts.map(([v, l]) => `<button type="button" role="option" aria-selected="${String(v) === String(cur[0])}" data-act="${act}" data-val="${esc(v)}"><span>${esc(l)}</span>${String(v) === String(cur[0]) ? icon("check", "xs") : ""}</button>`).join("")}</div>` : ""}</div>`;
}
