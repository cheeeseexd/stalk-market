/* Stalk Market dashboard. Renders server state only: every price, odds figure and rupee comes from /api. */
"use strict";

const S = {
  view: "desk", st: null, strategies: null, campaign: null, campTrades: null, journal: null, days: null, liveDays: null,
  runs: null, cid: null, account: "every_signal", jtab: "daily", tab: { desk: "setups", replay: "setups" }, deskAcct: "shadow",
  sym: {}, bars: {}, settingsDirty: false, lastJob: null, openDd: null, rday: null, allSignals: {}, allTrades: false,
};

const ACCOUNT_LABEL = {
  validated: "Proven strategies only", every_signal: "Every signal", solo_orb_breakout: "Opening-range breakout",
  solo_momentum_52w: "52-week-high momentum", solo_vwap_pullback: "VWAP pullback", solo_gap_fade: "Gap-up fade",
  daily: "Day by day, with rules", panic_buy: "Panic buy", solo_rsi_reversion: "RSI reversion", solo_supertrend: "Supertrend flip", solo_pdh_breakout: "Previous-day high/low breakout",
};
const STRAT_NAME = { orb_breakout: "Opening-range breakout", momentum_52w: "52-week-high momentum", vwap_pullback: "VWAP pullback", gap_fade: "Gap-up fade", rsi_reversion: "RSI reversion", supertrend: "Supertrend flip", pdh_breakout: "Previous-day breakout", panic_buy: "Panic buy" };
const EXITS = [
  ["target", "Target hit", "target", "green"], ["stop", "Stopped out", "stop", "red"], ["breakeven", "Breakeven", "breakeven", "amber"],
  ["flatten", "Sold at day end", "close", "blue"], ["loss_halt", "Loss limit", "halt", "pink"],
];
const REASON = Object.fromEntries(EXITS.map(([k, l, , c]) => [k, [l, c]]));
const PHASE = { open: ["Market open", "pos"], pre_open: ["Opens at 09:15", "warn"], closed: ["Market closed", ""], weekend: ["Weekend", ""] };
const VIEW = { desk: ["dashboard", "Overview"], simulate: ["backtest", "Session"], replay: ["replay", "Replay"], strategies: ["strategies", "Strategies"], journal: ["journal", "Journal"], settings: ["settings", "Settings"] };
VIEW.week = ["replay", "Simulation"];
VIEW.sessions = ["list", "Sessions"];
const NAV_OF = { replay: "sessions", simulate: "sessions" };  // a session's detail and its replay sit under Sessions  // replay is a mode of History, not a page of its own
// Why a signal was skipped, grouped by the engine's verdict code
const SKIP = {
  loss_streak: "Done for the day after losses", max_positions: "A position was already open", trade_cap: "Day's trade limit reached",
  against_index: "Against NIFTY's direction", mood: "Market mood unclear or opposite", confidence: "Too few factors agreeing",
  spacing: "Too soon after the last trade", news: "News against it", event: "Results or board meeting", breadth: "Breadth against it",
  sector: "Sector moving the other way", ranked: "Another setup ranked higher", lunch: "Midday lull", window: "Outside entry hours",
  cooldown: "Cooldown on this stock", costs: "Target too small for charges", size: "Stop too wide for the risk", holding: "Already holding this stock",
  timeout: "Timeout after a losing streak", loss_halt: "Daily loss limit reached", drex: "Drex voted against", not_validated: "Rule hasn't passed the test",
  killed: "Kill switch on", stale: "Market data stale",
};

// Runs longer than a year show the year too
const shortDate = (d) => (d ? fmtDate(istDay(d), { timeZone: "Asia/Kolkata", day: "numeric", month: "short", ...(S.longRun ? { year: "2-digit" } : {}) }) : "—");
const empty = (ic, title, body, actions = "") => `<div class="empty-state"><span class="ic">${icon(ic)}</span><b>${title}</b>${body ? `<p>${body}</p>` : ""}${actions ? `<div class="actions">${actions}</div>` : ""}</div>`;
const pctOf = (x, base) => (base ? x / base : 0);
const delta = (x, base) => `<span class="delta ${tone(x)}">${icon(x >= 0 ? "up" : "down", "xs")}${fixed(Math.abs(pctOf(x, base)) * 100, 1)}%</span>`;
function seg(key, opts, cur, act) {
  return `<div class="seg" role="tablist">${opts.map(([k, l, n]) => `<button role="tab" aria-selected="${String(cur) === String(k)}" data-act="${act}" data-key="${key}" data-val="${k}">${l}${isNum(n) ? ` <span class="count">${n}</span>` : ""}</button>`).join("")}</div>`;
}
const head = (view, sub, actions = "") => `<header class="page-head"><div class="title"><h1>${VIEW[view][1]}</h1>${sub ? `<div class="sub">${sub}</div>` : ""}</div><div class="actions">${actions}</div></header>`;
// label left, figure right; icon and colour arguments are kept for call sites but no longer drawn
const fact = (ic, color, label, value, small = "") => `<div class="fact"><div class="k">${label}${small ? `<small>${small}</small>` : ""}</div><div class="v">${value}</div></div>`;
const bars = (vals, max) => `<div class="bars">${vals.map((v) => `<i class="${v > 0 ? "pos" : v < 0 ? "neg" : ""}" style="height:${Math.max(6, (Math.abs(v) / (max || 1)) * 100)}%"></i>`).join("")}</div>`;

// ------------------------------------------------------------------ data
async function poll() {
  try {
    S.st = await api("/api/state");
    const job = S.st.job;
    if (S.lastJob && S.lastJob.running && !job.running) {
      toast(job.error ? job.error : job.kind === "campaign" ? "Backtest finished" : "Data updated");
      S.campaign = null; S.cid = null; S.runs = null; S.strategies = null; S.journal = null;
      loadView();
    }
    // Only live pages need a full redraw on every poll; the rest redraw when a job starts or ends
    const live = ["desk", "replay", "week"].includes(S.view);
    const jobKey = `${job.running}|${job.kind}|${job.message}`;
    if (live || jobKey !== S.jobKey || !S.drawnOnce) { S.jobKey = jobKey; S.drawnOnce = true; render(); }
    else { renderChrome(); renderSide(); }
    S.lastJob = job;
  } catch (e) { /* server restarting */ }
}

async function loadView() {
  try {
    if (S.view === "week") await pollSim(false);
    if (S.view === "sessions") S.sessions = await api("/api/sessions");
    if (S.view === "simulate") {
      if (S.campaign === null) { try { S.campaign = await api(`/api/campaigns/${S.cid || "latest"}`); } catch (e) { S.campaign = false; } S.campTrades = null; }
      if (S.campaign && !S.campaign.summary.accounts[S.account]) {
        const names = Object.keys(S.campaign.summary.accounts);
        S.account = names.includes("every_signal") ? "every_signal" : names[0];
      }
      if (S.campaign && !S.campTrades) S.campTrades = await api(`/api/campaigns/${S.campaign.id}/trades?account=${S.account}`);
    }
    if (S.view === "replay" && !S.days) S.days = await api("/api/replay/days");
    if (S.view === "desk" && !S.liveDays) S.liveDays = await api("/api/days?account=live:desk");
    if (S.view === "desk") S.panic = await api("/api/panic");
    if (S.view === "strategies" && !S.strategies) S.strategies = await api("/api/strategies");
    if (S.view === "strategies") S.panic = await api("/api/panic");
    if (S.view === "journal" && !S.journal) S.journal = await api("/api/journal");
  } catch (e) { toast(e.message); }
  render();
}

function setView(v) {
  S.view = v;
  S.openDd = null;
  $$(".nav button").forEach((b) => { if (b.dataset.view === (NAV_OF[v] || v)) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current"); });
  $$("main > section").forEach((s) => s.classList.toggle("hidden", s.id !== `view-${v}`));
  try { history.replaceState(null, "", `#${v}`); } catch (e) {}
  loadView();
}

// ------------------------------------------------------------ side panels
// The left strip (indices, market status) and the right panel (performance, Drex) stay the same on every page.
const DREX_Q = [["edge", "Will panic buy make money over its next 15 panic mornings?"], ["fills", "Will real orders fill near the opening price?"],
  ["loose", "Will the looser version beat the strict one?"]];
async function loadSide() {
  try {
    const [m, p] = await Promise.all([api("/api/market"), api("/api/panic")]);
    S.market = m; S.panic = p;
    if (!S.hist10) { try { S.hist10 = await api("/api/campaigns/h17_panic_10y_strict"); } catch (e) { S.hist10 = false; } }
    if (!S.drexCtx) { try { S.drexCtx = await api("/api/drex/context"); } catch (e) { S.drexCtx = null; } }
    try { S.research = await api("/api/research"); } catch (e) { /* keep the last copy */ }
    try { const [b, r] = await Promise.all([api("/api/brief"), api("/api/reviews")]); S.brief = b.brief; S.reviews = r.reviews; } catch (e) { /* keep the last copy */ }
    try { S.execution = (await api("/api/execution")).execution; } catch (e) { /* keep the last copy */ }
    try { S.orb = await api("/api/orb"); } catch (e) { /* keep the last copy */ }
    if (!S.horizons) { try { S.horizons = await api("/api/horizons"); } catch (e) { S.horizons = null; } }
  } catch (e) { /* server restarting */ }
  renderSide();
  if (S.view === "desk") render();
}
function paperStats() {
  const p = S.panic || {};
  const trades = [...((p.strict && p.strict.trades) || []), ...((p.loose && p.loose.trades) || [])];
  const byDay = {};
  trades.forEach((t) => { const d = (byDay[t.day] = byDay[t.day] || { day: t.day, net: 0, trades: 0 }); d.net += t.net; d.trades += 1; });
  const won = trades.filter((t) => t.net > 0).length;
  return { trades: trades.length, won, lost: trades.length - won, net: trades.reduce((a, t) => a + t.net, 0), days: Object.values(byDay).sort((a, b) => (a.day < b.day ? -1 : 1)) };
}
function histStats() {
  const c = S.hist10;
  if (!c) return null;
  const a = c.summary.accounts.panic_buy || Object.values(c.summary.accounts)[0];
  const won = a.days.reduce((s, d) => s + (d.wins || 0), 0), lost = a.days.reduce((s, d) => s + (d.losses || 0), 0);
  return { trades: a.trades, won: won || Math.round(a.stats.win * a.trades), lost: lost || a.trades - Math.round(a.stats.win * a.trades), net: a.net, days: a.days };
}
// One square per trading day, oldest to newest, coloured by that day's P&L (the long calendar doesn't fit the panel)
function dayDots(days) {
  const max = Math.max(1, ...days.map((d) => Math.abs(d.net || 0)));
  const lvl = (v) => (!v ? "" : (v > 0 ? "p" : "n") + (Math.abs(v) / max > 0.5 ? 4 : Math.abs(v) / max > 0.2 ? 3 : 2));
  calendar.data = { ...(calendar.data || {}), ...Object.fromEntries(days.map((d) => [d.day, d])) };
  return `<div class="dots">${days.map((d) => `<i class="cal-cell ${lvl(d.net)}" data-day="${esc(d.day)}"></i>`).join("")}</div>
    <div class="dots-key"><span>${days.length ? esc(shortDate(days[0].day)) : ""}</span><span class="cal-legend">Loss ${["n4", "n2", "0", "p2", "p4"].map((c) => `<i style="background:var(--heat-${c})"></i>`).join("")} Profit</span><span>${days.length ? esc(shortDate(days[days.length - 1].day)) : ""}</span></div>`;
}
function renderSide() {
  if (!S.st) return;
  const st = S.st, [phase, cls] = PHASE[st.phase] || [st.phase, ""];
  const idx = (S.market && S.market.indices) || [];
  setHTML($("#market"), idx.map((i) => `<div class="mrow"><span>${esc(i.name)}</span><div><b>${price(i.close)}</b><em class="${tone(i.change, 1e-6)}">${fixed(i.change * 100, 2, true)}%</em></div></div>`).join("") +
    `<div class="mfoot"><span><i class="dot ${cls}"></i>${phase}</span><span>${idx[0] ? `Close of ${esc(shortDate(idx[0].day))}` : ""}</span></div>`);
  const paper = paperStats(), hist = histStats();
  const mode = S.railMode || (paper.trades ? "paper" : "history");
  const d = mode === "paper" ? paper : hist || paper;
  const wr = d.trades ? pct(d.won / d.trades) : "—";
  const days = (d.days || []).slice(-90);
  const perf = `<div class="rail-sec"><div class="rail-head"><h2>Performance</h2>${seg("railMode", [["paper", "Paper"], ["history", "10 years"]], mode, "rail-mode")}</div>
    <div class="perf"><div><b>${wr}</b><span>Win rate</span></div><div><b>${int(d.trades)}</b><span>Trades</span></div>
      <div><b class="pos">${int(d.won)}</b><span>Wins</span></div><div><b class="neg">${int(d.lost)}</b><span>Losses</span></div>
      <div><b class="${tone(d.net)}">${inrShort(d.net)}</b><span>Net P&L</span></div></div>
    ${days.length ? dayDots(days) : `<div class="dots empty">${"<i class=\"cal-cell\"></i>".repeat(90)}</div>
      <div class="dots-key"><span>${mode === "paper" ? "No paper trades yet" : "Loading"}</span><span class="cal-legend">Loss ${["n4", "n2", "0", "p2", "p4"].map((c) => `<i style="background:var(--heat-${c})"></i>`).join("")} Profit</span><span></span></div>`}
    <p class="rail-note">${mode === "paper" ? "Both panic-buy versions on paper, after charges. Each square will be a panic morning." : `Strict panic buy over 10 years, splits removed. Squares: the latest ${days.length} panic mornings.`}</p></div>`;
  const ctx = S.drexCtx || {}, base = ctx.history || {};
  const odds = (p) => `<span class="odds">${meter([{ w: p }])}<b>Drex ${pct(p)} yes</b></span>`;
  const qs = DREX_Q.map(([k, label]) => {
    const r = (S.drex || {})[k], busy = (S.drexBusy || {})[k], h = base[k];
    return `<button data-act="drex-ask" data-val="${k}" ${busy ? "disabled" : ""} title="${r ? "Ask again" : "Ask Drex"}"><span>${esc(label)}</span>${icon(busy ? "loader" : "open", "sm")}
      ${h && h.text ? `<span class="hist">Our data: ${esc(h.text)}${isNum(h.p) ? ` (${pct(h.p)})` : ""}</span>` : ""}${r ? odds(r.p) : ""}</button>`;
  }).join("");
  const mine = (S.drexCustom || []).map((r) => `<button disabled><span>${esc(r.question)}</span><span></span>
      ${(r.used || []).map((u) => `<span class="used">Used: ${esc(u)}</span>`).join("")}${odds(r.p)}</button>`).join("");
  const drex = `<div class="drex"><div class="drex-top"><div class="orb"></div><h2>Ask Drex</h2><p>Yes or no odds, with our real numbers as context.</p></div>
    <div class="drex-q">${mine}${qs}</div>
    <form class="drex-form" data-drex-form><input type="text" name="q" maxlength="300" placeholder="${esc(ctx.placeholder || "Ask a yes or no question…")}" aria-label="Ask Drex a yes or no question">
      <button class="btn primary" type="submit" aria-label="Ask" ${(S.drexBusy || {}).custom ? "disabled" : ""}>${icon((S.drexBusy || {}).custom ? "loader" : "open", "sm")}</button></form></div>`;
  const keep = document.activeElement && document.activeElement.name === "q" ? document.activeElement.value : null;
  setHTML($("#rail"), perf + drex);
  if (keep !== null) { const inp = $("#rail input[name=q]"); if (inp) { inp.value = keep; inp.focus(); } }
}
async function askDrex(key, question) {
  S.drexBusy = { ...(S.drexBusy || {}), [key]: true }; renderSide();
  try {
    const r = await api("/api/drex/ask", { method: "POST", body: key === "custom" ? { question } : { preset: key } });
    if (key === "custom") S.drexCustom = [r, ...(S.drexCustom || [])].slice(0, 3);
    else S.drex = { ...(S.drex || {}), [key]: r };
  } catch (e) { toast(e.message); }
  S.drexBusy = { ...(S.drexBusy || {}), [key]: false }; renderSide();
}
// Overview: the next trading morning, from the calendar only (exchange holidays aren't known here)
function nextSession(st) {
  const now = new Date(st.now.replace(" ", "T") + "+05:30");
  const d = new Date(now);
  const mins = Number(st.now.slice(11, 13)) * 60 + Number(st.now.slice(14, 16));
  if (st.phase === "open" || st.phase === "pre_open") return { label: "Today", time: st.phase === "open" ? "Open now" : "09:15" };
  do { d.setDate(d.getDate() + 1); } while ([0, 6].includes(d.getDay()));
  if (st.phase === "closed" && mins < 9 * 60) d.setDate(now.getDate());
  return { label: d.toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Kolkata" }), time: "09:15" };
}
function greeting(st) {
  const h = Number(st.now.slice(11, 13));
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
function overviewSentence(st) {
  const p = S.panic || {}, today = st.now.slice(0, 10);
  const last = p.strict && p.strict.last;
  if (st.phase === "open" && last && last.day === today) {
    if (last.kind === "skip") return `NIFTY opened <b>${isNum(last.nifty_gap) ? fixed(last.nifty_gap * 100, 2, true) + "%" : "flat"}</b>: not a panic morning, so the bot is <b>sitting out today</b>.`;
    return `It's a <b>panic morning</b>. The bot is holding its picks until the stop or <b>15:10</b>.`;
  }
  if (st.phase === "open") return `The market is open. The bot checks NIFTY's opening at <b>09:25</b>.`;
  if (st.phase === "pre_open") return `Markets open at <b>09:15</b>. Both paper tests check the opening at <b>09:25</b>.`;
  return `Markets are closed. Both paper tests start by themselves at <b>09:25</b> on the next trading day.`;
}

// ----------------------------------------------------------- daily paper
// The 09:10 brief and the 15:35 reviews (stalk/research/paper_daily.py). Facts only: they never change the frozen rules. Null means unknown.
const CUE_NAME = { sp500: "S&P 500", nasdaq: "Nasdaq", brent: "Brent", gold: "Gold", usdinr: "Dollar–rupee" };
function briefCard(b, ns) {
  if (!b) return `<div class="card span-6"><div class="card-head"><h2>Morning brief</h2><span class="hint">09:10 on trading days</span></div>
    <p class="muted">The first brief arrives at 09:10 on ${esc(ns.label)}: overnight markets, NIFTY's expected open, and whether either panic-buy version would trade.</p></div>`;
  const cues = Object.entries(b.globals || {}).filter(([, v]) => v && isNum(v.change));
  const gap = b.nifty && b.nifty.indicative_gap;
  const q = (k) => { const v = (b.would_qualify || {})[k]; return v === true ? `<span class="chip pos">Trades</span>` : v === false ? `<span class="chip">Sits out</span>` : `<span class="chip">Unknown</span>`; };
  const elig = (b.candidates || []).filter((c) => c.preliminary_eligible).slice(0, 3);
  return `<div class="card span-6"><div class="card-head"><div><h2>Morning brief</h2><p class="card-sub">${esc(isoLabel(b.day))} · ${b.status === "observed" ? "seen at " + esc(b.observed_at.slice(11, 16)) : esc(b.status)}</p></div></div>
    <div class="brief-top"><div><span>NIFTY expected open</span><b class="${tone(gap, 1e-6)}">${isNum(gap) ? fixed(gap * 100, 2, true) + "%" : "—"}</b></div>
      <div class="brief-q"><span>Strict ${q("strict")}</span><span>Looser ${q("loose")}</span></div></div>
    ${cues.length ? `<div class="brief-cues">${cues.map(([k, v]) => `<span>${esc(CUE_NAME[k] || k)} <em class="${tone(v.change, 1e-6)}">${fixed(v.change * 100, 2, true)}%</em></span>`).join("")}</div>` : ""}
    ${elig.length ? `<div class="brief-cands"><span class="card-sub">Biggest clean falls so far</span>${elig.map((c) => `<div><b>${esc(c.symbol)}</b><span class="num neg">${fixed(c.gap * 100, 1)}%</span></div>`).join("")}</div>` : ""}</div>`;
}
// H26: the everyday long/short desk (stocks in play), three stop sizes side by side against a random-pick control. Paper only; null is unknown.
const ORB_STOP = { v1: "Tight stop", v2: "Medium stop", v3: "Wide stop" };
function orbCard(o, ns) {
  const vs = (o && o.versions) || {}, bm = (o && o.benchmark) || {};
  const started = Object.values(vs).some((v) => v && isNum(v.days) && v.days > 0);
  const head = `<div class="card-head"><div><h2>Everyday desk</h2><p class="card-sub">The 2 busiest stocks each morning, long or short with their first move</p></div><span class="hint">Paper only</span></div>`;
  if (!started) return `<div class="card">${head}<p class="muted">Starts at 09:20 on ${esc(ns.label)} and trades every day. Three stop sizes run side by side, each next to a random pick of stocks and directions, so we can see whether the choice beats chance.</p>
    <p class="card-foot">Needs Kite: Yahoo's opening volume is missing on 97% of days, so stocks in play can only be found from Kite's data.</p></div>`;
  const money = (v) => (isNum(v) ? `<span class="${tone(v)}">${inr(v, { sign: true, whole: true })}</span>` : `<span class="muted">Unknown</span>`);
  const rows = ["v1", "v2", "v3"].map((k) => { const v = vs[k] || {}, b = bm[k] || {};
    return `<tr><td class="sym">${ORB_STOP[k]}</td><td class="r num">${isNum(v.days) ? int(v.days) : "—"}</td><td class="r num">${isNum(v.trades) ? int(v.trades) : "—"}</td>
      <td class="r num">${isNum(v.wins) && isNum(v.losses) ? `${int(v.wins)} / ${int(v.losses)}` : "—"}</td><td class="r num">${money(v.net)}</td><td class="r num grow">${money(b.net)}</td></tr>`; });
  const last = (vs.v2 && vs.v2.last_day_trades) || [];
  const lastHtml = last.length ? `<div class="orb-last"><span class="card-sub">Last day · medium stop</span>${last.map((t) => `<div><b>${esc(t.symbol)}</b><span class="chip ${t.side === "long" ? "pos" : "neg"}">${t.side === "long" ? "Long" : "Short"}</span><span class="num ${tone(t.net)}">${inr(t.net, { sign: true, whole: true })}</span></div>`).join("")}</div>` : "";
  return `<div class="card">${head}${table([["Version"], ["Days", 1], ["Trades", 1], ["Won / lost", 1], ["Net", 1], ["Random picks", 1, "grow"]], rows, "")}${lastHtml}
    <p class="card-foot">Real money only after a version beats its random picks over at least 20 trading days.</p></div>`;
}
// H16: what the order book looked like at seven fixed times. Displayed-book estimates, not real fills; null stays unknown.
const SLOT_NAME = { "09:15:30": "Open", "09:20:00": "Panic-buy entry", "09:25:00": "After entry", "11:15:00": "Late morning", "11:20:00": "Late morning +5", "15:05:00": "Before exit", "15:10:00": "Exit" };
function fillsCard(x, ns) {
  const gate = `<p class="card-foot">Passes when the typical spread plus price impact is 0.30% a side or less, and at least 80% of picks can be bought intraday and aren't stuck at a price limit. Judged after 5 panic mornings and 20 ordinary days.</p>`;
  const headHtml = (sub) => `<div class="card-head"><div><h2>Fill check</h2><p class="card-sub">${sub}</p></div><span class="hint">Measured, not traded</span></div>`;
  if (!x) return `<div class="card">${headHtml("Would real orders fill near the opening price?")}
    <p class="muted">The first check runs at 09:15 on ${esc(ns.label)}. Seven times a day it reads the order book for the day's candidates: the gap between buy and sell prices, and what a ₹5,000 order would have paid.</p>${gate}</div>`;
  const status = (r) => r.status === "observed" ? `<span class="chip pos">Seen</span>` : r.status === "late" ? `<span class="chip pending">Late</span>` : `<span class="chip neg">Missed</span>`;
  const known = (v, n, f) => (isNum(v) ? `${f(v)}${n !== undefined ? ` <small class="muted">of ${int(n)}</small>` : ""}` : `<span class="muted">Unknown</span>`);
  const rows = (x.snapshots || []).map((r) => `<tr><td class="sym">${esc(r.slot.slice(0, 5))} <small class="muted">${esc(SLOT_NAME[r.slot] || "")}</small></td><td>${status(r)}</td>
    <td class="r num">${isNum(r.symbols_seen) ? int(r.symbols_seen) : "—"}</td>
    <td class="r num">${known(r.median_spread_bps, r.spread_known_count, (v) => fixed(v / 100, 2) + "%")}</td>
    <td class="r num">${known(r.share_mis_eligible, r.mis_known_count, (v) => pct(v))}</td>
    <td class="r num grow">${r.any_locked_or_at_circuit === true ? `<span class="neg">${esc((r.flagged_symbols || []).join(", "))}</span>` : r.any_locked_or_at_circuit === false ? "None" : `<span class="muted">Unknown</span>`}</td></tr>`);
  return `<div class="card">${headHtml(esc(isoLabel(x.day)))}
    ${table([["Time"], ["Status"], ["Stocks", 1], ["Typical spread", 1], ["Intraday allowed", 1], ["At price limit", 1, "grow"]], rows, "No snapshots yet today.")}${gate}</div>`;
}
function reviewsCard(reviews, p) {
  const versions = p ? [["strict", "Strict · NIFTY down 0.5%+", p.strict], ["loose", "Looser · NIFTY down 0.3%+", p.loose]] : [];
  const totals = versions.map(([, label, v]) => `<div><span>${label}</span><b class="${tone(v.net)}">${inr(v.net, { sign: true, whole: true })}</b><small>${plural(v.fired.length, "panic morning")} · ${plural(v.trades.length, "trade")}</small></div>`).join("");
  const rows = (reviews || []).slice(0, 5).map((r) => {
    const cell = (k) => { const o = (r.runners || {})[k]; if (!o) return `<span class="muted">—</span>`; const n = o.outcome && o.outcome.net;
      return isNum(n) ? `<span class="num ${tone(n)}">${inr(n, { sign: true, whole: true })}</span>` : `<span class="muted">${o.process && o.process.picks && o.process.picks.length ? "Incomplete" : "No trade"}</span>`; };
    return `<div class="rev-row"><span>${esc(isoLabel(r.day))}</span>${cell("strict")}${cell("loose")}</div>`;
  }).join("");
  return `<div class="card span-6"><div class="card-head"><h2>Paper tests</h2><span class="hint">Daily review at 15:35</span></div>
    <div class="papers">${totals}</div>
    ${rows ? `<div class="rev"><div class="rev-row head"><span>Day</span><span>Strict</span><span>Looser</span></div>${rows}</div>` : `<p class="muted rev-empty">Each trading day gets a short factual review here after the close.</p>`}</div>`;
}
// Strategies: how steady the frozen rule was across rolling windows (describes stability; not new evidence)
function steadyCard(h) {
  const vs = (h && h.versions) || {};
  const key = S.steadyVer && vs[S.steadyVer] ? S.steadyVer : "strict";
  const v = vs[key];
  if (!v) return "";
  const f = v.full_period || {}, hz = v.horizons || {};
  const order = [["5y", "5 years"], ["2y", "2 years"], ["1y", "1 year"], ["1m", "1 month"], ["1w", "1 week"]];
  return `<div class="card"><div class="card-head"><div><h2>How steady is it</h2><p class="card-sub">Rolling windows over 10 years, same rule throughout</p></div>
      ${seg("steadyVer", [["strict", "Strict"], ["loose", "Looser"]], key, "steady-ver")}</div>
    <div class="lead-row kpis">
      <div><span>Win rate</span><b>${pct(f.win_rate)}</b><small>${int(f.trades)} trades</small></div>
      <div><span>Average win vs loss</span><b>${inr(f.average_win, { whole: true })} / ${inr(f.average_loss, { whole: true })}</b><small>Wins are ${fixed(f.average_win / f.average_loss, 1)}× the losses</small></div>
      <div><span>Profit factor</span><b>${fixed(f.profit_factor, 2)}</b><small>₹ won for each ₹1 lost</small></div>
      <div><span>Worst losing streak</span><b>${int(f.longest_losing_streak)}</b><small>trades in a row</small></div></div>
    ${table([["Window"], ["Windows with trades", 1], ["Made money", 1], ["Typical result", 1, "grow"]], order.filter(([k]) => hz[k]).map(([k, label]) => { const x = hz[k];
      return `<tr><td class="sym">${label}</td><td class="r num">${int(x.windows_with_trades)} of ${int(x.windows)}</td><td class="r num">${pct(x.share_positive_with_trades)}</td><td class="r num grow ${tone(x.median_net)}">${inr(x.median_net, { sign: true, whole: true })}</td></tr>`; }), "")}
    <p class="card-foot">Longer windows almost always made money; single weeks are mostly quiet (no panic morning) and only about 6 in 10 trading weeks were positive.</p></div>`;
}

// ---------------------------------------------------------------- chrome
function renderChrome() {
  const st = S.st;
  const [phase, cls] = PHASE[st.phase] || [st.phase, ""];
  const here = S.view === "simulate" && S.crumbTitle ? esc(S.crumbTitle) : VIEW[S.view][1];
  const crumb = NAV_OF[S.view] ? `${VIEW[NAV_OF[S.view]][1]} <span>/</span> <b>${here}</b>` : `<b>${here}</b>`;
  setHTML($("#topbar"), `<div class="crumbs">Stalk Market <span>/</span> ${crumb}</div>
    <div class="status"><span><i class="dot ${cls}"></i>${phase}</span><span>${icon("clock", "xs")}${esc(st.now.slice(11, 16))} IST</span>
    <span>${icon("plug", "xs")}${st.kite.logged_in ? "Kite connected" : "Kite not connected"}</span>
    <button class="icon-btn" data-act="theme" aria-label="${currentTheme() === "dark" ? "Light mode" : "Dark mode"}" title="${currentTheme() === "dark" ? "Light mode" : "Dark mode"}">${icon(currentTheme() === "dark" ? "sun" : "moon", "sm")}</button></div>`);
  const banners = [];
  if (st.job.running) banners.push(`<div class="banner">${icon("loader", "sm")}<b>${st.job.kind === "campaign" ? "Running backtest" : "Updating data"}</b><span class="grow">${esc(st.job.message)}</span><div class="progress" style="width:160px"><span style="width:${(st.job.frac * 100).toFixed(0)}%"></span></div></div>`);
  if (st.killed) banners.push(`<div class="banner neg">${icon("kill", "sm")}<b>Kill switch is on.</b><span class="grow">No new entries until the server restarts.</span></div>`);
  if (st.live.reconcile) banners.push(`<div class="banner warn">${icon("alert", "sm")}<b>Positions don't match Zerodha.</b><span class="grow">${esc(st.live.reconcile)}</span></div>`);
  setHTML($("#banners"), banners.join(""));
}

// ---------------------------------------------------------- shared parts
function heroCard(label, value, base, meta, control, chartId) {
  return `<div class="card hero span-8"><div class="card-head"><h2>${label}</h2><div class="actions">${control || ""}</div></div>
    <div class="figure"><span class="big ${tone(value)}">${inr(value, { sign: true })}</span>${delta(value, base)}</div>
    ${meta ? `<div class="meta">${meta}</div>` : ""}<div class="chart" data-chart id="${chartId}"></div></div>`;
}
// One stacked bar of how trades ended, then a line per exit type that happened
function exitSplit(trades) {
  const used = EXITS.map(([k, l, , c]) => { const t = trades.filter((x) => x.reason === k); return { k, l, c, n: t.length, net: t.reduce((a, x) => a + x.net, 0) }; }).filter((e) => e.n);
  return `<div class="split">${used.map((e) => `<span class="m-${e.c}" style="flex:${e.n}" title="${esc(e.l)}: ${e.n}"></span>`).join("")}</div>
    <div class="split-rows">${used.map((e) => `<div><i class="key m-${e.c}"></i><span class="k">${esc(e.l)}</span><span class="n num">${int(e.n)}</span><span class="v num ${tone(e.net)}">${inr(e.net, { sign: true, whole: true })}</span></div>`).join("")}</div>`;
}
// "Sold at day end": neither the stop nor the target was reached, so the end-of-day rule sold it (15:10, or 15:15 on hourly runs). Closed, not still open.
// How long a position has been open, from its entry clock to the session clock (both "HH:MM")
const held = (since, clock) => {
  const m = (x) => (x && /^\d\d:\d\d$/.test(x) ? Number(x.slice(0, 2)) * 60 + Number(x.slice(3)) : null);
  const a = m(since), b = m(clock);
  if (a === null || b === null || b < a) return "—";
  const d = b - a;
  return d >= 60 ? `${Math.floor(d / 60)}h ${d % 60}m` : `${d}m`;
};
// Where the price sits between the stop (left) and the target (right), with the stop and target prices at the ends
const progress = (p) => {
  const span = p.target - p.stop;
  const at = span ? clamp((p.price - p.stop) / span, 0, 1) : 0.5;
  const from = span ? clamp((p.entry - p.stop) / span, 0, 1) : 0.5;
  return `<div class="track"><span class="num muted">${price(p.stop)}</span><div class="rail"><i class="entry" style="left:${(from * 100).toFixed(1)}%"></i>
    <i class="now ${p.pnl >= 0 ? "pos" : "neg"}" style="left:${(at * 100).toFixed(1)}%"></i></div><span class="num muted">${price(p.target)}</span><span class="num">${rmult(p.r, 1)}</span></div>`;
};
const DAY_END_TIP = "Neither the stop nor the target was reached, so the end-of-day rule sold it (15:10, or 15:15 on hourly runs). The trade is fully closed.";
const result = (t) => {
  const [label, c] = REASON[t.reason] || [t.reason, "ink"];
  return `<span class="result" ${t.reason === "flatten" ? `title="${DAY_END_TIP}"` : ""}><i class="key m-${c}"></i>${esc(label)}</span>`;
};
function setupRow(c) {
  const tm = c.tm;
  return `<tr><td class="num muted">${esc(c.clock)}</td><td class="sym">${esc(c.symbol)}</td><td>${sideChip(c.side)}</td>
    <td class="r num">${price(c.entry)}</td><td class="r num muted">${price(c.stop)}</td><td class="r num muted">${price(c.target)}</td>
    <td class="r num">${tm ? pct(tm.p_target) : "—"}</td><td class="grow muted">${esc(STRAT_NAME[c.strategy] || c.strategy)}</td>
    <td>${c.status === "queued" ? `<span class="chip pos">Taken</span>` : `<span class="muted">${esc(SKIP[c.verdict.code] || c.verdict.reason)}</span>`}</td></tr>`;
}
function tradeRow(t, withDay) {
  return `<tr>${withDay ? `<td class="num muted">${esc(shortDate(t.day))}</td>` : ""}<td class="sym">${esc(t.symbol)}</td><td>${sideChip(t.side)}</td>
    <td class="r num muted">${hhmm(t.entry_ts)}</td><td class="r num">${price(t.entry_price)}</td><td class="r num">${price(t.exit_price)}</td>
    <td>${result(t)}</td><td class="grow muted">${esc(STRAT_NAME[t.strategy] || t.strategy)}</td>
    <td class="r num muted">${inr(t.costs)}</td><td class="r num ${tone(t.net)}">${inr(t.net, { sign: true })}</td></tr>`;
}
const TRADE_HEAD = [["Stock"], ["Side"], ["Time", 1], ["Entry", 1], ["Exit", 1], ["Result"], ["Rule", 0, "grow"], ["Charges", 1], ["Net", 1]];
const SETUP_HEAD = [["Time"], ["Stock"], ["Side"], ["Entry", 1], ["Stop", 1], ["Target", 1], ["Odds", 1], ["Rule", 0, "grow"], ["Decision"]];
// Signals: the ones taken in full, the skipped ones grouped by reason (all of them one click away)
function signalsBody(setups, kind) {
  const taken = setups.filter((c) => c.status === "queued");
  const skipped = setups.filter((c) => c.status !== "queued");
  if (S.allSignals[kind]) return table(SETUP_HEAD, setups.map(setupRow), "No signals yet.") +
    `<div class="table-foot"><button class="btn more up" data-act="signals" data-val="${kind}">Group skipped${icon("caret", "xs")}</button></div>`;
  const groups = {};
  skipped.forEach((c) => { const k = SKIP[c.verdict.code] || c.verdict.reason; (groups[k] = groups[k] || []).push(c); });
  const rows = Object.entries(groups).sort((a, b) => b[1].length - a[1].length).map(([k, list]) => {
    const syms = [...new Set(list.map((c) => c.symbol))];
    const from = list[list.length - 1].clock, to = list[0].clock;
    return `<tr><td class="sym">${esc(k)}</td><td class="r num">${int(list.length)}</td><td class="grow muted">${esc(syms.slice(0, 4).join(", "))}${syms.length > 4 ? ` and ${syms.length - 4} more` : ""}</td>
      <td class="r num muted">${esc(from === to ? from : `${from}–${to}`)}</td></tr>`;
  });
  return table(SETUP_HEAD, taken.map(setupRow), setups.length ? "Nothing taken." : "No signals yet.") +
    (skipped.length ? `<div class="sub-head">Skipped ${int(skipped.length)}</div>${table([["Reason"], ["Signals", 1], ["Stocks", 0, "grow"], ["Between", 1]], rows, "")}
      <div class="table-foot"><button class="btn more" data-act="signals" data-val="${kind}">Every signal${icon("caret", "xs")}</button></div>` : "");
}

// ----------------------------------------------------- one trading day
function accountView(acc, other, kind, title) {
  const shown = S.deskAcct === "shadow" && other ? other : acc;
  const sm = shown.summary, s = S.st.settings;
  const used = Math.max(0, -Math.min(0, shown.day_pnl));
  const control = other ? seg("deskAcct", [["shadow", "Paper, rules on"], ["desk", "Proven only"]], S.deskAcct, "acct") : "";
  const pos = shown.positions[0];
  const top = `<div class="grid">${heroCard(title, shown.day_pnl, s.capital, `Charges ${inr(sm.costs)}, account ${inr(shown.equity)}`, control, `curve-${kind}`)}
    <div class="card span-4"><div class="card-head"><h2>Today</h2></div><div class="facts">
      ${fact("trades", "blue", "Trades", `${sm.trades} / ${s.max_trades_per_day}`, `${sm.wins} won, ${sm.losses} lost`)}
      ${fact("shield", used / s.loss_halt > 0.66 ? "red" : "green", "Loss limit used", inr(used, { whole: true }), `of ${inr(s.loss_halt, { whole: true })}`)}
      ${fact("briefcase", "violet", "Open position", pos ? esc(pos.symbol) : "None", pos ? `${inr(pos.pnl, { sign: true })}, open for ${held(pos.since, shown.clock)}, ${rmult(pos.r, 1)}` : "")}
      ${(() => { const m = shown.mood || {}; const col = m.label === "bullish" ? "green" : m.label === "bearish" ? "red" : "amber";
        return fact(m.label === "bearish" ? "trend-down" : "trend-up", col, "Market mood", `<span class="c-${col === "amber" ? "ink" : col}">${m.label ? m.label[0].toUpperCase() + m.label.slice(1) : "—"}</span>`,
          isNum(m.index) ? `NIFTY ${fixed(m.index * 100, 2, true)}%, ${pct(m.breadth)} above VWAP` : "Waiting for the first bar"); })()}
      ${fact("receipt", "pink", "Charges", inr(sm.costs))}</div></div></div>`;
  const tab = S.tab[kind];
  const tabs = [["setups", "Signals", shown.setups.length], ["positions", "Positions", shown.positions.length], ["closed", "Closed", shown.closed.length], ["log", "Activity"]];
  let body = "";
  if (tab === "setups") body = signalsBody(shown.setups, kind);
  if (tab === "positions") body = table([["Stock"], ["Side"], ["In", 1], ["Open for", 1], ["Qty", 1], ["Entry", 1], ["Now", 1], ["Stop → target", 0, "grow"], ["P&L", 1]],
    shown.positions.map((p) => `<tr><td class="sym">${esc(p.symbol)}</td><td>${sideChip(p.side)}</td><td class="r num muted">${esc(p.since)}</td><td class="r num">${held(p.since, shown.clock)}</td>
      <td class="r num">${int(p.qty)}</td><td class="r num">${price(p.entry)}</td><td class="r num">${price(p.price)}</td>
      <td class="grow">${progress(p)}</td><td class="r num ${tone(p.pnl)}">${inr(p.pnl, { sign: true })}</td></tr>`), "No open positions.");
  if (tab === "closed") body = table(TRADE_HEAD, shown.closed.map((t) => tradeRow(t, false)), "No closed trades yet.");
  if (tab === "log") body = `<ul class="feed">${shown.logs.map((l) => `<li><span class="t">${hhmm(l.ts)}</span><span class="x">${esc(l.text)}</span></li>`).join("") || `<li><span></span><span class="x">Nothing yet.</span></li>`}</ul>`;
  return top + chartCard(shown, kind) + `<div class="card flush"><div class="card-head">${seg(kind, tabs, tab, "tab")}</div>${body}</div>`;
}
function chartCard(acc, kind) {
  const syms = [...new Set([...acc.positions.map((p) => p.symbol), ...acc.closed.map((t) => t.symbol), ...acc.setups.filter((c) => c.status === "queued").map((c) => c.symbol)])];
  if (!syms.length) return "";
  const sym = S.sym[kind] && syms.includes(S.sym[kind]) ? S.sym[kind] : syms[0];
  S.sym[kind] = sym;
  return `<div class="card"><div class="card-head"><h2>${esc(sym)}</h2>
    <div class="actions">${syms.length > 1 ? dropdown(`sym-${kind}`, syms.map((x) => [x, x]), sym, `sym-${kind}`, "Stock") : ""}</div></div>
    <div class="chart" data-chart id="candles-${kind}" style="height:300px"></div></div>`;
}
function drawCurve(kind, acc) {
  const el = $(`#curve-${kind}`);
  if (!el) return;
  const pts = acc.curve.map((p) => [p[0], p[1]]);
  chart(el, JSON.stringify(pts.slice(-1).concat([pts.length])), () => lineChart(el, [{ pts, split: true }], {
    zero: true, empty: "The curve starts with the first bar.", fmtY: (v) => inrShort(v), fmtX: (t) => hhmm(t),
    tip: (p) => `<b>${hhmm(p[0])}</b>${tipRows([["P&L", `<span class="${tone(p[1])}">${inr(p[1], { sign: true })}</span>`]])}`,
  }));
}
async function drawCandles(kind, acc, day, upto) {
  const el = $(`#candles-${kind}`);
  const sym = S.sym[kind];
  if (!el || !sym) return;
  const key = `${sym}|${day}|${upto || ""}`;
  if (!(S.bars[kind] && S.bars[kind].key === key)) {
    try { S.bars[kind] = { key, data: await api(`/api/bars?symbol=${encodeURIComponent(sym)}&day=${day}${upto ? `&upto=${upto}` : ""}`) }; }
    catch (e) { return emptyChart(el, esc(e.message)); }
  }
  const data = S.bars[kind].data;
  const trades = acc.closed.filter((t) => t.symbol === sym);
  const pos = acc.positions.find((p) => p.symbol === sym);
  const markers = trades.flatMap((t) => [{ kind: "entry", ts: t.entry_ts, price: t.entry_price, side: t.side }, { kind: "exit", ts: t.exit_ts, price: t.exit_price, side: t.side }]);
  const levels = pos ? [{ y: pos.stop, kind: "stop" }, { y: pos.entry, kind: "entry" }, { y: pos.target, kind: "target" }] : [];
  chart(el, JSON.stringify([data.bars.length, markers.length, levels]), () => candleChart(el, data.bars, { markers, levels }));
}

// ------------------------------------------------------------------ desk
function renderDesk() {
  const st = S.st;
  const live = st.live.accounts || {};
  const acc = live.desk, other = live.shadow;
  const kite = st.kite.logged_in ? `<button class="btn" data-act="kite-check">${icon("refresh", "sm")}Check Kite</button>`
    : `<button class="btn" data-act="kite-login">${icon("plug", "sm")}Connect Kite</button>`;
  const h = head("desk", "Paper trading on today's market", kite);
  const p = S.panic;
  const versions = p ? [["Strict \u22120.5%", p.strict], ["Looser \u22120.3%", p.loose]] : [];
  const panicCard = p ? `<div class="card flush"><div class="card-head"><h2>Panic-buy forward test</h2><span class="hint">Paper only. Two independent versions</span></div>
    <div class="versions">${versions.map(([label, v]) => `<div class="version">
      <div class="version-head"><h3>${label}</h3><span class="hint">${v.fired.length ? `${plural(v.fired.length, "panic morning")} so far` : "Waiting for its first panic morning"}</span></div>
      <div class="version-stats">
        <div><span>Mornings checked</span><b>${int(v.checked)}</b><small>Every weekday</small></div>
        <div><span>Panic mornings</span><b>${int(v.fired.length)}</b><small>Review at ${int(v.review_after)}</small></div>
        <div><span>Trades</span><b>${int(v.trades.length)}</b><small>${v.trades.filter((t) => t.net > 0).length} won</small></div>
        <div><span>Net</span><b class="${tone(v.net)}">${inr(v.net, { sign: true })}</b><small>After charges</small></div></div>
      ${v.error ? `<p class="version-error">Last check failed: ${esc(v.error)}</p>` : ""}</div>`).join("")}</div>
    ${versions.some(([, v]) => v.trades.length) ? table([["Version"], ["Day"], ["Stock"], ["Gap", 1], ["Entry", 1], ["Exit", 1], ["Result", 0, "grow"], ["Net", 1]], versions.flatMap(([label, v]) => v.trades.slice().reverse().map((t) =>
      `<tr><td>${label}</td><td class="num muted">${esc(shortDate(t.day))}</td><td class="sym">${esc(t.symbol)}</td><td class="r num neg">${fixed(t.gap * 100, 1)}%</td><td class="r num">${price(t.entry)}</td><td class="r num">${price(t.exit)}</td>
       <td class="grow muted">${t.reason === "stop" ? "Stopped out" : "Sold at 15:10"}</td><td class="r num ${tone(t.net)}">${inr(t.net, { sign: true })}</td></tr>`)), "") : ""}</div>` : "";
  const daysCard = panicCard + ((S.liveDays || []).length ? `<div class="card"><div class="card-head"><h2>Paper trading days</h2></div>${calendar(S.liveDays)}</div>` : "");
  if (!acc) {
    // Outside a session: when the next one starts, the last result, and whether everything is ready for it
    const s = st.settings, d = st.data || {}, days = S.liveDays || [];
    const last = days[days.length - 1];
    const lead = st.phase === "open" ? "Waiting for the first bar" : st.phase === "pre_open" ? "Opens at 09:15 today" : "Next trading day, 09:15";
    const next = `<div class="card span-8"><div class="card-head"><h2>Next session</h2></div>
      <div class="lead">${lead}</div>
      <p class="lead-sub">Paper trading starts by itself and closes every position by ${esc(s.flatten_at)}. No real orders.</p>
      <div class="lead-row">${last ? `<div><span>Last paper day</span><b class="${tone(last.net)}">${inr(last.net, { sign: true })}</b><small>${esc(isoLabel(last.day))}, ${plural(last.trades, "trade")}</small></div>` : `<div><span>Last paper day</span><b>None yet</b><small>The first one is the next session</small></div>`}
        <div><span>Paper days so far</span><b>${int(days.length)}</b><small>${days.filter((x) => x.net > 0).length} green, ${days.filter((x) => x.net < 0).length} red</small></div></div>
      <div class="lead-actions"><button class="btn" data-act="replay-pick">Replay a day</button><button class="btn ghost" data-act="go" data-val="simulate">See history</button></div></div>`;
    const ready = `<div class="card span-4"><div class="card-head"><h2>Before the open</h2></div><div class="facts">
      ${fact("", "", "Market data", d.last_bar_ts ? esc(dateTime(d.last_bar_ts)) : "None", d.stale ? "Update before 09:15" : "Latest bar stored")}
      ${fact("", "", "Kite", st.kite.logged_in ? "Connected" : "Not connected", "Login expires every day at 06:00")}
      ${fact("", "", "Trades a day", `Up to ${int(s.max_trades_per_day)}`, `Stops at a ${inr(s.loss_halt, { whole: true })} loss`)}
      ${fact("", "", "Market mood check", s.mood_gate ? "On" : "Off", s.mood_gate ? "Trades only with NIFTY and breadth" : "Index direction and breadth filters only")}</div></div>`;
    const ps = paperStats(), ns = nextSession(st), ev = p && p.strict && p.strict.evidence;
    const go = (view, label) => `<button class="go" data-act="go" data-val="${view}" aria-label="${label}">${icon("open")}</button>`;
    const stats = `<div class="grid">
      <div class="card span-4 stat">${go("sessions", "Open sessions")}<span>Paper account</span><b>${inr(s.capital + ps.net, { whole: true })}</b>
        <small><em class="${tone(ps.net)}">${inr(ps.net, { sign: true, whole: true })}</em> from ${plural(ps.trades, "paper trade")}</small></div>
      <div class="card span-4 stat glow">${go("strategies", "Open strategies")}<span>Best strategy · Panic buy</span><b class="pos">${ev ? `+${fixed(ev.net_pct, 2)}%` : "—"}</b>
        <small>a trade after charges · ${ev ? esc(ev.years_positive) : ""} years made money</small></div>
      <div class="card span-4 stat">${go("settings", "Open settings")}<span>Next session</span><b>${esc(ns.label)}</b>
        <small>${esc(ns.time)} · ${st.kite.logged_in ? "Kite connected" : "Kite not connected"} · data to ${d.last_bar_ts ? esc(shortDate(new Date(d.last_bar_ts * 1000).toISOString().slice(0, 10))) : "—"}</small></div></div>`;
    const R = S.research || {}, strat = R.strategy || { confidence: [] }, cycles = R.cycles || [];
    const done = strat.confidence.filter((c) => c.done).length, total = strat.confidence.length || 1;
    const conf = `<div class="card span-5 conf"><div class="card-head"><h2>Confidence in ${esc(strat.name || "the strategy")}</h2></div>
      <b class="big">${done} of ${strat.confidence.length}</b>${meter([{ w: done / total }])}
      <ul class="steps">${strat.confidence.map((c) => `<li class="${c.done ? "done" : ""}">${icon(c.done ? "ok" : "circle")}<span>${esc(c.step)}${!c.done && c.note ? `<small>${esc(c.note)}</small>` : ""}</span></li>`).join("")}</ul></div>`;
    const works = [...new Set(cycles.filter((c) => c.verdict === "Passed").map((c) => c.name.split(",")[0]))];   // one strategy can pass more than one test
    const research = `<div class="card span-7"><div class="card-head"><div><h2>Latest research</h2><p class="card-sub">${plural(cycles.length, "test")} run · ${works.length ? `${esc(works.join(", "))} works` : "nothing passed yet"}</p></div>
      <button class="btn" data-act="go" data-val="journal">All experiments${icon("open", "xs")}</button></div>
      <div class="cycles">${cycles.slice(0, 5).map((c) => `<div><b>${esc(c.name)}</b><span class="chip ${c.verdict === "Passed" ? "pos" : c.verdict === "Failed" ? "neg" : esc(c.verdict.toLowerCase())}">${esc(c.verdict)}</span><small>${esc(c.result)}</small></div>`).join("")}</div></div>`;
    const pv = (label, v) => `<div><span>${label}</span><b class="${tone(v.net)}">${inr(v.net, { sign: true, whole: true })}</b><small>${plural(v.fired.length, "panic morning")} · ${plural(v.trades.length, "trade")}</small></div>`;
    const papers = `<div class="grid">${briefCard(S.brief, ns)}${reviewsCard(S.reviews, p)}</div>` + fillsCard(S.execution, ns);
    const desk = orbCard(S.orb, ns);
    setHTML($("#view-desk"), head("desk", "", kite) + `<p class="hello">${greeting(st)}. ${overviewSentence(st)}</p><div style="height:var(--s6)"></div>` +
      stats + desk + `<div class="grid">${research}${conf}</div>` + papers + ((S.liveDays || []).length ? `<div class="card"><div class="card-head"><h2>Paper trading days</h2></div>${calendar(S.liveDays)}</div>` : ""));
    return;
  }
  setHTML($("#view-desk"), h + accountView(acc, other, "desk", "Today's P&L") + daysCard);
  const shown = S.deskAcct === "shadow" && other ? other : acc;
  drawCurve("desk", shown);
  drawCandles("desk", shown, acc.day);
}

// --------------------------------------------------------- run picker
// Plain names for every stored run, grouped, with each run's result, so picking one doesn't mean decoding experiment codes.
const EXPERIMENT = {
  "L0 baseline": "Baseline rules", "L1 events": "Skip results days", "L2 breadth": "Market breadth filter", "L3 sector": "Sector filter",
  "L4 VIX": "Volatility (VIX) filter", "L5 score rank": "Rank by agreement", "L6 Drex veto": "Drex veto", "S1 stack": "Best stack",
  "S1 stop 0.30": "Best stack, tighter stop", "S1 stop 0.40": "Best stack, wider stop", "S2 stack + Drex": "Best stack + Drex",
  "N1 news keywords": "News filter, keywords", "N2 news Drex": "News filter, Drex", "C1 confident 3/day": "Mood first, up to 3 a day",
  "C2 confident 2/day": "Mood first, up to 2 a day", "C3 mood + chop": "Mood + choppy-day filter", "daily run": "Day by day",
  "every_signal run": "Every signal", "smoke drex": "Drex smoke test",
};
const MOOD_VARIANT = { baseline: "baseline rules", S1: "best stack", N1: "news filter (keywords)", N2: "news filter (Drex)", C1: "mood first", C3: "mood + choppy filter" };
function runInfo(r) {
  const label = r.label || "";
  const accts = r.accounts || {};
  const main = ["panic_buy", "daily", "every_signal", "validated"].find((k) => accts[k]) || Object.keys(accts)[0];
  const a = accts[main] || { net: 0, trades: 0 };
  const week = label.match(/week of (.+)$/);
  const mood = label.match(/^Mood (\w+)-(\d{4})-(\w+) (\w+)$/);
  let title = EXPERIMENT[label] || label || "Unnamed run", tag = "", group = "Strategy experiments";
  if (week) {
    // "Hybrid C, week of 9 Dec 2024" keeps its variant name, so weeks of the same dates stay tellable apart
    const pre = label.split(/,?\s*week of/i)[0].trim();
    title = `Week of ${week[1]}`; group = "Simulated weeks";
    tag = pre && !/^panic buy$/i.test(pre) ? pre : /−0\.3%|-0\.3%/.test(label) ? "Looser" : "Strict";
  }
  else if (/^Panic buy.*10 years/.test(label) || r.id.startsWith("p")) {
    title = "10 years of panic buy"; group = "Panic buy history";
    tag = /original/.test(label) ? "Original data" : /−0\.3%|-0\.3%/.test(label) ? "Looser, cleaned" : "Strict, cleaned";
  }
  else if (mood) { const t = mood[3]; title = `${t[0].toUpperCase() + t.slice(1)} month, ${mood[1]} ${mood[2]}`; tag = MOOD_VARIANT[mood[4]] || mood[4]; group = "Market moods"; }
  else if (r.id.startsWith("c")) group = "Early backtests";
  return { id: r.id, title, tag, group, net: a.net, trades: a.trades, start: r.first_day, dates: `${shortDate(r.first_day)} – ${shortDate(r.last_day)}` };
}
// One plain sentence about the run: what the bot did and how it ended
function runStory(sm, acc, panicRun, weekRun) {
  const money = `<b class="${tone(acc.net)}">${inr(acc.net, { sign: true, whole: Math.abs(acc.net) >= 1000 })}</b>`;
  const traded = acc.days.filter((d) => d.trades).length;
  if (weekRun) return traded ? `Traded on <b>${traded} of ${plural(acc.days.length, "day")}</b> and finished ${money}.`
    : `No panic morning this week, so <b>no trades</b>. Sitting out is part of the plan.`;
  if (panicRun) return `Over <b>${Math.round((new Date(sm.last_day) - new Date(sm.first_day)) / 3.156e10)} years</b>, traded on <b>${plural(traded, "panic morning")}</b> and made ${money}.`;
  return `Over <b>${plural(acc.days.length || sm.days, "session")}</b>, took <b>${plural(acc.trades, "trade")}</b> and finished ${money}.`;
}

// -------------------------------------------------------------- backtest
function renderSimulate() {
  const running = S.st.job.running;
  const c = S.campaign;
  const isPanic = c && c.summary && c.summary.kind === "panic_buy";
  // Runs are picked on the Sessions page (newest first); the page itself only offers replay
  const actions = c && c.summary && c.summary.kind === "synthetic" ? "" : (isPanic ? `<button class="btn" data-act="go" data-val="week">${icon("replay", "sm")}Watch a week</button>`
      : `<button class="btn" data-act="replay-pick">${icon("replay", "sm")}Replay a day</button>`);
  if (c === null) return setHTML($("#view-simulate"), head("simulate", "", actions) + `<div class="card" style="min-height:280px">${empty("loader", "Loading", "")}</div>`);
  if (!c) return setHTML($("#view-simulate"), head("simulate", "", actions) + `<div class="card" style="min-height:280px">${empty("backtest", "No backtest yet", "Run one to replay past sessions as a ₹10,000 paper account.")}</div>`);
  const sm = c.summary, acc = sm.accounts[S.account], st = acc.stats, trades = S.campTrades || [];
  S.longRun = sm.first_day.slice(0, 4) !== sm.last_day.slice(0, 4);
  const panicRun = sm.kind === "panic_buy";
  const names = Object.keys(sm.accounts);
  const weekRun = panicRun && /week of/.test(sm.label || "");
  const info = runInfo({ ...c, label: sm.label, accounts: sm.accounts });
  const meta = panicRun ? `${shortDate(sm.first_day)} – ${shortDate(sm.last_day)} · ${esc(sm.note || "")}`
    : `${shortDate(sm.first_day)} – ${shortDate(sm.last_day)} · ${c.settings.bar_minutes || 5}-minute bars${sm.kind === "day_by_day" ? ", played one day at a time" : ""}`;
  const sub = `<span class="lede">${runStory(sm, acc, panicRun, weekRun)}</span><span class="meta">${esc(info.title)}${info.tag ? ` (${esc(info.tag)})` : ""} · ${meta}</span>`;
  const top = `<div class="grid">
    ${heroCard("Net P&L", acc.net, sm.capital, acc.trades ? `Before charges ${inr(acc.gross, { sign: true })}, charges ${inr(acc.costs)}` : acc.require_validated ? "No strategy was proven, so this account never traded." : "No trades.",
      names.length > 1 ? dropdown("account", names.map((n) => [n, ACCOUNT_LABEL[n] || n]), S.account, "account", "Account") : "", "camp-curve")}
    <div class="card span-4"><div class="card-head"><h2>Summary</h2></div><div class="facts">
      ${fact("percent", "blue", "Win rate", acc.trades ? pct(st.win) : "—", plural(acc.trades, "trade"))}
      ${fact("calendar", "green", "Green days", `${acc.green_days} / ${acc.days.length}`, `${acc.red_days} red`)}
      ${fact("trend-down", "red", "Worst drawdown", inr(acc.max_dd, { whole: true }), `${fixed(pctOf(acc.max_dd, sm.capital) * 100, 1)}% of capital`)}
      ${fact("coins", "violet", "Average trade", acc.trades ? inr(acc.net / acc.trades, { sign: true }) : "—")}
      ${fact("receipt", "pink", "Charges", inr(acc.costs, { whole: true }), acc.trades ? `${inr(acc.costs / acc.trades)} a trade` : "")}</div></div></div>`;
  const exits = `<div class="card span-4"><div class="card-head"><h2>How trades ended</h2></div>${trades.length ? exitSplit(trades) : empty("list", "No trades", "")}</div>`;
  const hourOf = (t) => istHour(t.entry_ts);
  const hours = [9, 10, 11, 12, 13, 14].map((h) => trades.filter((t) => hourOf(t) === h).reduce((a, t) => a + t.net, 0));
  const worstHour = hours.indexOf(Math.min(...hours));
  const sideNet = ["long", "short"].map((sd) => trades.filter((t) => t.side === sd).reduce((a, t) => a + t.net, 0));
  const sideN = ["long", "short"].map((sd) => trades.filter((t) => t.side === sd).length);
  const miniCard = (title, value, foot, chartHtml) => `<div class="card span-4"><div class="card-head"><h2>${title}</h2></div><div class="mini"><div><div class="v">${value}</div><div class="f">${foot}</div></div>${chartHtml}</div></div>`;
  const minis = trades.length && panicRun ? `<div class="grid">${exits.replace("span-4", "span-12")}</div>` : trades.length ? `<div class="grid">${exits}
    ${miniCard("Worst entry hour", `${9 + worstHour}:00`, `${inr(hours[worstHour], { sign: true, whole: true })} from trades entered then<br>Bars: 09:00 to 14:00`, bars(hours, Math.max(1, ...hours.map(Math.abs))))}
    ${miniCard("Shorts vs longs", `<span class="${tone(sideNet[1])}">${inr(sideNet[1], { sign: true, whole: true })}</span>`, `Shorts, ${sideN[1]} trades<br>Longs ${inr(sideNet[0], { sign: true, whole: true })}, ${sideN[0]} trades`, bars([sideNet[1], sideNet[0]], Math.max(1, ...sideNet.map(Math.abs))))}</div>` : "";
  // Ten years don't fit a day grid: show one row a year instead
  const years = {};
  acc.days.forEach((d) => { const y = (years[d.day.slice(0, 4)] = years[d.day.slice(0, 4)] || { days: 0, green: 0, trades: 0, net: 0 }); y.days += 1; y.green += d.net > 0 ? 1 : 0; y.trades += d.trades; y.net += d.net; });
  const cal = S.longRun ? `<div class="card flush"><div class="card-head"><h2>By year</h2><span class="hint">Before tax</span></div>
    ${table([["Year"], [panicRun ? "Panic mornings" : "Sessions", 1], ["Green", 1], ["Trades", 1], ["Net", 1], ["Return on ₹10,000", 1, "grow"]], Object.entries(years).map(([y, v]) =>
      `<tr><td class="sym">${y}</td><td class="r num">${int(v.days)}</td><td class="r num">${int(v.green)}</td><td class="r num">${int(v.trades)}</td><td class="r num ${tone(v.net)}">${inr(v.net, { sign: true, whole: true })}</td><td class="r num grow ${tone(v.net)}">${fixed((v.net / sm.capital) * 100, 1, true)}%</td></tr>`), "")}</div>`
    : `<div class="card"><div class="card-head"><h2>Daily P&L</h2><span class="hint">${panicRun ? "Hover a day for details" : "Click a day to replay it"}</span></div>${calendar(acc.days)}</div>`;
  const maxNet = Math.max(1, ...names.map((n) => Math.abs(sm.accounts[n].net)));
  const rows = names.map((n) => {
    const a = sm.accounts[n];
    return `<tr data-act="account" data-val="${n}" class="${n === S.account ? "sel" : ""}"><td class="sym">${esc(ACCOUNT_LABEL[n] || n)}</td>
      <td class="r num ${tone(a.net)}"><span class="bar" style="width:${Math.round((Math.abs(a.net) / maxNet) * 60)}px;background:var(--${a.net >= 0 ? "pos" : "neg"}-mark)"></span>${inr(a.net, { sign: true })}</td>
      <td class="r num">${int(a.trades)}</td><td class="r num">${a.trades ? pct(a.stats.win) : "—"}</td>
      <td class="r num"><span class="pos">${a.green_days}</span> / <span class="neg">${a.red_days}</span></td><td class="r num">${inr(a.costs, { whole: true })}</td></tr>`;
  });
  const compare = names.length > 1 ? `<div class="card flush"><div class="card-head"><h2>Compare strategies</h2><span class="hint">Click a row to view it</span></div>
    ${table([["Account", 0, "grow"], ["Net", 1], ["Trades", 1], ["Win rate", 1], ["Green / red days", 1], ["Charges", 1]], rows, "")}</div>` : "";
  const shownTrades = S.allTrades ? trades : trades.slice(0, 15);
  const tradesCard = `<div class="card flush"><div class="card-head"><h2>Trades <span class="count">${int(trades.length)}</span></h2><span class="hint">Newest first</span></div>
    ${S.campTrades ? table([["Day"], ...TRADE_HEAD], shownTrades.map((t) => tradeRow(t, true)), "This account took no trades.") : empty("loader", "Loading trades", "")}
    ${trades.length > shownTrades.length || S.allTrades ? `<div class="table-foot"><button class="btn more ${S.allTrades ? "up" : ""}" data-act="all-trades">${S.allTrades ? "Show fewer" : `Show all ${int(trades.length)}`}${icon("caret", "xs")}</button></div>` : ""}</div>`;
  S.crumbTitle = info.title;
  const header = `<header class="page-head"><div class="title"><button class="back" data-act="go" data-val="sessions">${icon("back")}All sessions</button>
    <h1>${esc(info.title)}${info.tag ? ` <em class="tag">${esc(info.tag)}</em>` : ""}</h1><div class="sub">${sub}</div></div><div class="actions">${actions}</div></header>`;
  const practice = sm.kind === "synthetic" ? `<div class="card practice-review"><div class="card-head"><div><h2>Why we traded or waited</h2><p class="card-sub">Decisions saved before the reveal. Practice only.</p></div><span class="chip">Synthetic</span></div>
    <div class="practice-decisions">${(sm.decisions || []).map((d) => {
      const points = d.points || (d.reason || "").split(/(?<=\.)\s+/).filter(Boolean).slice(0, 3);
      return `<section><div class="practice-choice"><h3>${esc(d.symbol)}</h3><span class="chip">${esc(d.action)}</span></div>
        <ul>${points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>
        ${isNum(d.counterfactual_net) && d.action === "Skip" ? `<p class="practice-outcome">If taken: <b class="${tone(d.counterfactual_net)}">${inr(d.counterfactual_net, { sign: true })}</b><small>Known only after the reveal.</small></p>` : ""}
        ${d.outcome ? `<p class="practice-outcome">${esc(d.outcome)}</p>` : ""}</section>`;
    }).join("")}</div>
    <details class="practice-method"><summary>Method and caveats</summary><p>${esc(sm.note || "")}</p></details></div>` : "";
  const scenarios = (sm.practice_charts || []).map(practiceScenario).join("");
  setHTML($("#view-simulate"), header + top + practice + scenarios + minis + (sm.kind === "synthetic" ? "" : cal) + compare + tradesCard);
  const el = $("#camp-curve");
  // A day with an unknown result (a missing price bar) has no equity: hold the line flat, never plot it as zero
  let last = sm.capital;
  // Up to ~6 months every day is drawn; longer runs keep every few days (the shape is what matters there)
  const pts = thin([[0, 0], ...acc.curve.map((p, i) => [i + 1, (last = isNum(p[1]) ? p[1] : last) - sm.capital])]);
  const labels = ["Start", ...acc.curve.map((p) => p[0])];
  chart(el, `${c.id}|${acc.name}`, () => lineChart(el, [{ pts, split: true }], {
    zero: true, fmtY: (v) => inrShort(v), fmtX: (i) => (labels[i] && labels[i] !== "Start" ? shortDate(labels[i]) : ""),
    tip: (p) => `<b>${esc(labels[p[0]] === "Start" ? "Start" : isoLabel(labels[p[0]]))}</b>${tipRows([["Net so far", `<span class="${tone(p[1])}">${inr(p[1], { sign: true })}</span>`], ["Account", inr(sm.capital + p[1])]])}`,
  }));
}

function practiceScenario(c) {
  const minutes = (hm) => Math.floor(hm / 100) * 60 + hm % 100;
  const bars = c.path || [];
  if (!bars.length || !bars.every((b) => b.length === 5 && b.every(Number.isFinite))) return "";
  const lv = c.levels, lo = Math.min(lv.stop, lv.target, ...bars.map(b => b[3])), hi = Math.max(lv.stop, lv.target, ...bars.map(b => b[2]));
  const pad = Math.max((hi - lo) * .12, .01), bottom = lo - pad, top = hi + pad;
  const x = (m) => 58 + (m - 555) / (910 - 555) * 514, y = (p) => 220 - (p - bottom) / (top - bottom) * 192;
  const coords = [[555, bars[0][1]], ...bars.filter(b => b[0] < 1510).map(b => [minutes(b[0]) + 5, b[4]]), [910, bars[bars.length-1][1]]];
  const path = coords.map(([m,p],i) => `${i ? "L" : "M"}${x(m).toFixed(2)},${y(p).toFixed(2)}`).join(" ");
  const levels = [["Target",lv.target,"var(--pos)"],["Entry",lv.entry,"var(--ink-3)"],["Stop",lv.stop,"var(--neg)"]];
  const lines = levels.map(([label,p,color]) => `<line x1="58" x2="572" y1="${y(p)}" y2="${y(p)}" stroke="${color}" stroke-dasharray="5 4"/><text x="580" y="${y(p)+4}" fill="${color}">${label} ${p.toFixed(2)}</text>`).join("");
  const wicks = bars.filter(b => b[0] < 1510).map(b => `<line x1="${x(minutes(b[0])+2.5)}" x2="${x(minutes(b[0])+2.5)}" y1="${y(b[2])}" y2="${y(b[3])}" stroke="currentColor" opacity=".16"/>`).join("");
  const exit = isNum(c.exit_price) ? `<circle cx="${x(minutes(c.exit_hm))}" cy="${y(c.exit_price)}" r="4" fill="var(--ink)"/><text x="${Math.min(x(minutes(c.exit_hm))+7,510)}" y="${y(c.exit_price)-9}" fill="currentColor">Exit ${Math.floor(c.exit_hm/100)}:${String(c.exit_hm%100).padStart(2,"0")}</text>` : "";
  const timed = Object.entries(c.timed_opinions || {}).map(([hm,a]) => `${hm.slice(0,-2)}:${hm.slice(-2)}: ${(a.p*100).toFixed(0)}% (observed ${a.realized_above ? "above" : "at/below"})`).join(" · ");
  return `<div class="card practice-scenario"><div class="card-head"><div><h2>${esc(c.symbol)} · ${esc(c.side)} scenario</h2><p class="card-sub">Drex plan, then the recorded Wednesday path.</p></div></div>
    <div class="practice-levels">${levels.map(([label,p]) => `<div><span>${label}</span><b>${inr(p)}</b></div>`).join("")}</div>
    <p class="hint">Pre-entry reference ${inr(c.reference)} · planned stop ${inr(c.planned_stop)} · planned target ${inr(c.planned_target)}. Levels above re-anchor to the simulated fill.</p>
    <p class="hint">Drex target-first opinion: ${(c.target_first_p*100).toFixed(0)}%. Price above reference: ${esc(timed)}. Uncalibrated opinions.</p>
    <details class="practice-path"><summary>Show what happened after the decision</summary><div class="practice-plot"><svg viewBox="0 0 700 260" role="img" aria-label="${esc(c.symbol)} recorded price path with entry, stop, target and exit"><title>${esc(c.symbol)} historical path, not a Drex-generated forecast</title>${wicks}${lines}<path d="${path}" fill="none" stroke="currentColor" stroke-width="1.6"/><line x1="${x(575)}" x2="${x(575)}" y1="24" y2="222" stroke="currentColor" opacity=".3" stroke-dasharray="2 3"/>${exit}<text x="58" y="246" fill="currentColor">09:15</text><text x="${x(575)}" y="18" fill="currentColor">09:35 entry</text><text x="${x(720)}" y="246" fill="currentColor">12:00</text><text x="545" y="246" fill="currentColor">15:10</text></svg></div><p class="hint">Line: recorded candle closes; thin ranges: intrabar highs/lows. Exit dot marks the exit bar; exact intrabar time is unknown. ${c.ambiguous ? "Both levels touched in one candle: stop counted first." : ""} This path was hidden when Drex answered.</p></details></div>`;
}

// ---------------------------------------------------------------- replay
// ------------------------------------------------------------ simulation
// A random past week of the panic-buy rule, played as if live (stalk/sim/panic_replay.py). The server keeps the clock;
// the page polls it and runs the on-screen timer forward between polls.
async function pollSim(draw = true) {
  try { S.week = await api("/api/sim"); S.weekAt = performance.now(); } catch (e) { S.week = { ready: false }; }
  // A finished week is saved as a History run: drop the cached run list so History shows it
  if (S.week && S.week.saved && S.lastSaved !== S.week.saved) { S.lastSaved = S.week.saved; S.runs = null; S.campaign = null; S.cid = S.week.saved; toast("Week saved to History"); }
  if (draw && S.view === "week") render();
}
async function drawSimCandles(r) {
  const requested = S.simSymbolDay === `${r.week}|${r.day}` ? S.simSymbol || "" : "";
  try {
    const data = await api(`/api/sim/bars?symbol=${encodeURIComponent(requested)}`);
    if (S.view !== "week" || S.week.week !== data.week || S.week.idx !== data.idx) return;
    const el = $("#sim-candles"), choices = $("#sim-symbols"), note = $("#sim-chart-note");
    if (!el || !choices || !note) return;
    choices.innerHTML = data.picks.length ? data.picks.map((symbol) => `<button class="btn ${symbol === data.symbol ? "primary" : ""}" data-act="sim-symbol" data-val="${esc(symbol)}">${esc(symbol)}</button>`).join("") : `<span class="chip">${esc(data.symbol)}</span>`;
    note.textContent = data.note;
    chart(el, JSON.stringify(data), () => candleChart(el, data.bars, { levels: data.levels, markers: data.markers }));
  } catch (e) { const el = $("#sim-candles"); if (el) emptyChart(el, "Candles unavailable"); }
}
const SIM_KIND = { buy: "green", sell: "blue", stop: "red", skip: "ink", scan: "amber", check: "amber", hold: "ink", day: "blue" };
const simDayText = (d) => ({ pending: "Not yet", skip: "No trade", watch: "Panic morning", trade: d.net ? inr(d.net, { sign: true, whole: true }) : "Trading", done: inr(d.net, { sign: true, whole: true }) })[d.state] || "";
function simTick() {
  const r = S.week, el = $("#sim-clock");
  if (!el || !r || !r.ready || !r.playing || r.quiet || r.holding_screen) return;
  const t = Math.min(r.sim_ts + ((performance.now() - S.weekAt) / 1000) * r.speed, r.ts + 299);
  el.textContent = new Date(t * 1000).toLocaleTimeString("en-GB", { timeZone: "Asia/Kolkata", hour12: false });
}
function renderWeek() {
  const r = S.week;
  const newBtn = `<button class="btn" data-act="sim-new" ${S.simBusy ? "disabled" : ""}>${S.simBusy ? "Picking…" : "Another week"}</button>`;
  const desk = !r || !r.ready || r.mode === "desk";
  const h = head("week", desk ? "The desk we plan to run, on a random past week, played as if it were live: panic buy on panic mornings, the ML ranker on other days. Paper only, no orders."
    : "The panic-buy rule on a random past week, played as if it were live. Paper only, no orders.", r && r.ready ? newBtn : "");
  if (!r) return setHTML($("#view-week"), h + `<div class="card" style="min-height:240px">${empty("loader", "Loading", "")}</div>`);
  if (!r.ready) {
    return setHTML($("#view-week"), h + `<div class="card glow sim-intro">
      <div class="lead">Watch the bot trade a past week</div>
      <ul class="sim-points">
        <li>A week is drawn at random from Jan 2023 – Jul 2025 (Kite 5-minute candles). Drex answers yes or no to each draw, so the pick isn't ours.</li>
        <li>Panic mornings (NIFTY opens 0.5%+ down): panic buy, the 2 biggest clean falls at 09:20, 3% stop.</li>
        <li>Every other day: the ML ranker (setting B) trades up to 2 stocks at 09:35, long or short, only when it is confident; 5% stop.</li>
        <li>₹10,000 in two ₹5,000 slots; everything is sold by 15:10. The ML picks come from models trained only on earlier years.</li>
        <li>A clock runs at the speed you choose. Quiet stretches, like days with no trade, are skipped after a few seconds.</li>
      </ul>
      <div class="lead-actions"><button class="btn primary" data-act="sim-new" ${S.simBusy ? "disabled" : ""}>${S.simBusy ? "Picking…" : "Pick a week"}</button></div></div>`);
  }
  const draws = (r.pick && r.pick.draws) || [];
  const pickNote = draws.length ? `Random week · ${draws.map((d) => `${shortDate(d.week)}: Drex ${d.drex}`).join(" · ")}` : "Random week";
  const status = r.playing ? (r.quiet || r.holding_screen ? "Skipping ahead" : `Live · ${r.speed}×`) : r.finished ? "Week finished" : "Paused";
  // At the end, the insight becomes the week's summary
  const tradedDays = r.days.filter((d) => d.trades);
  const ins = r.finished ? {
    headline: r.trades.length ? `Week finished: ${plural(r.trades.length, "trade")}, net ${inr(r.total, { sign: true })} after charges.` : desk ? "Week finished: no confident picks, so no trades." : "Week finished: no panic mornings, so no trades.",
    detail: r.trades.length ? `Traded on ${tradedDays.map((d) => d.label).join(", ")}. ${r.won} won, ${r.lost} lost. Every position was sold by 15:10.`
      : desk ? "The model never cleared its confidence bar this week. Standing aside is part of the plan." : "NIFTY never opened 0.5% or more down this week. Doing nothing is the rule working: most weeks have no panic morning.",
  } : r.insight;
  if (r.finished) ins.detail += desk ? " The result is saved on the Sessions page." : " The result is saved on the History page.";
  const insight = `<div class="card sim-insight">
      <div class="sim-top"><span class="sim-day-label">${esc(r.label)}</span><span class="sim-time num" id="sim-clock">${esc(r.sim_clock)}</span>
        <span class="chip ${r.playing ? "pos" : ""}">${status}</span><span class="grow"></span><span class="hint">${esc(pickNote)}</span></div>
      <h2 class="sim-headline">${esc(ins.headline || "Ready. Press play to start Monday at 09:15.")}</h2>
      <p class="sim-detail">${esc(ins.detail || "")}</p></div>`;
  const controls = `<div class="card sim-controls">
      <button class="btn primary" data-act="sim-play">${r.playing ? "Pause" : r.finished ? "Play again" : r.idx ? "Resume" : "Play"}</button>
      ${seg("simspeed", r.speeds.map((v) => [v, `${v}×`]), r.speed, "sim-speed")}
      <div class="sim-days">${r.days.map((d) => `<button class="sim-dbtn ${d.day === r.day ? "on" : ""}" data-act="sim-day" data-val="${d.day}"><b>${esc(d.label)}</b>
        <small class="${d.state === "done" || d.state === "trade" ? tone(d.net) : ""}">${simDayText(d)}</small></button>`).join("")}</div></div>`;
  const openNames = r.positions.map((p) => p.symbol).join(", ");
  const stat = (label, value, sub, cls = "") => `<div class="card span-3 sim-stat"><span>${label}</span><b class="${cls}">${value}</b><small>${sub}</small></div>`;
  const numbers = `<div class="grid">
      ${stat("Week P&L, after charges", inr(r.total, { sign: true }), `${inr(r.realized, { sign: true })} closed, ${inr(r.unrealized, { sign: true })} open`, tone(r.total))}
      ${stat("Open positions", int(r.positions.length), openNames || "None")}
      ${stat("Trades closed", int(r.trades.length), `${r.won} won, ${r.lost} lost`)}
      ${stat("Charges paid", inr(r.charges), "Already in the P&L")}</div>`;
  const items = r.log.slice().reverse().slice(0, 14).map((e) => `<li><span class="t num">${esc(e.when)}</span><i class="key m-${SIM_KIND[e.kind] || "ink"}"></i><span class="x">${esc(e.text)}</span></li>`).join("");
  const log = `<div class="card flush"><div class="card-head"><h2>What the bot did</h2><span class="hint">Newest first</span></div>
      <ul class="sim-log">${items || `<li><span class="t"></span><i></i><span class="x muted">Nothing yet. Press play.</span></li>`}</ul></div>`;
  const f = r.funnel;
  const frow = (label, n) => `<div class="frow"><span>${label}</span><div class="fbar"><i style="width:${f && f.reviewed ? Math.max(1.5, (n / f.reviewed) * 100) : 0}%"></i></div><b class="num">${int(n)}</b></div>`;
  const deskList = desk && r.top.length ? `<div class="sim-top-list">${r.top.map((c) => `<div class="${c.picked ? "picked" : ""}"><b>${esc(c.symbol)}</b><span class="num ${tone(c.gap)}">${fixed(c.gap * 100, 1, true)}%</span><small>${esc(c.why)}</small></div>`).join("")}</div>` : "";
  const scanBody = desk ? (deskList || `<p class="muted">${r.panic_day ? "Panic buy picks at 09:20, after the first candle." : "The ML ranker scores every stock at 09:35."}</p>`)
    : f
    ? `<div class="funnel">${frow("Stocks reviewed", f.reviewed)}${frow("Opened 2%+ down", f.fell)}${frow("Results news, skipped", f.results)}${frow("Stuck at limit, skipped", f.locked)}${frow("Clean candidates", f.eligible)}${frow("Bought", f.picked)}</div>
       <div class="sim-top-list">${r.top.map((c) => `<div class="${c.picked ? "picked" : ""}"><b>${esc(c.symbol)}</b><span class="num neg">${fixed(c.gap * 100, 1)}%</span><small>${esc(c.why)}</small></div>`).join("")}</div>`
    : `<p class="muted">${r.qualifies ? "The scan runs at 09:20, after the first 5-minute candle." : `No scan: NIFTY opened ${fixed(r.nifty_gap * 100, 2, true)}%, not below −0.5%.`}</p>`;
  const pos = r.positions.length ? `<div class="sim-pos">${r.positions.map((p) => `<div><b>${esc(p.symbol)}</b><span class="muted">open ${held(p.since, r.clock)}</span><span class="num ${tone(p.pnl)}">${inr(p.pnl, { sign: true })}</span>
      <small>${int(p.qty)} ${p.side === "short" ? "shorted" : "bought"} at ${price(p.entry)} · now ${price(p.last)} · stop ${price(p.stop)}${p.target ? ` · target ${price(p.target)}` : ""}${p.strategy ? ` · ${p.strategy === "ml" ? "ML ranker" : "panic buy"}` : ""}</small></div>`).join("")}</div>` : "";
  const scan = `<div class="card span-5"><div class="card-head"><h2>${desk ? "Today's picks" : "This morning's scan"}</h2><span class="hint">${esc(r.label)}</span></div>${scanBody}${pos}</div>`;
  const curve = `<div class="card"><div class="card-head"><h2>P&L through the week</h2><span class="hint">Closed plus open trades, after charges</span></div><div class="chart" data-chart id="sim-curve" style="height:220px"></div></div>`;
  const tradeChart = `<div class="card"><div class="card-head"><h2>The trade on a chart</h2><div class="sim-symbols" id="sim-symbols"></div></div><p class="hint" id="sim-chart-note"></p><div class="chart sim-candles" data-chart id="sim-candles"></div><p class="hint">Entry and stop levels &middot; triangle: buy &middot; circle: sell &middot; current candle shows its open only</p></div>`;
  setHTML($("#view-week"), h + insight + controls + numbers + `<div class="grid"><div class="span-7 sim-left">${log}${tradeChart}</div>${scan}</div>` + curve);
  drawSimCandles(r);
  const el = $("#sim-curve");
  // Plotted by step, not clock time, so nights and quiet days don't squash the trading hours
  const when = (i) => (r.curve[i] ? new Date(r.curve[i][0] * 1000).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false }) : "");
  chart(el, `sim|${r.week}|${r.idx}`, () => lineChart(el, [{ pts: r.curve.map((p, i) => [i, p[1]]), split: true }], {
    zero: true, empty: "The line starts with the first trade.", fmtY: (v) => inrShort(v), fmtX: when,
    tip: (p, i) => `<b>${esc(when(i))}</b>${tipRows([["Week P&L", `<span class="${tone(p[1])}">${inr(p[1], { sign: true })}</span>`]])}`,
  }));
  simTick();
}

// --------------------------------------------------------------- sessions
// Every stored run as a card: what it was, how long it covered, and the numbers that matter, large. Click opens it in History.
const runSpan = (r) => {
  const days = (new Date(r.last_day) - new Date(r.first_day)) / 86400000;
  return days > 400 ? `${Math.round(days / 365)} years` : days > 45 ? `${Math.round(days / 30)} months` : plural(r.sessions, "session");
};
// "Jan 2021 – Jul 2025" for long runs, "9 – 13 Dec 2024" style dates for short ones
const spanDates = (a, b) => {
  const f = (d, o) => fmtDate(istDay(d), { timeZone: "Asia/Kolkata", ...o });
  if (!a || !b) return "—";
  return (new Date(b) - new Date(a)) / 86400000 > 45 ? `${f(a, { month: "short", year: "numeric" })} – ${f(b, { month: "short", year: "numeric" })}`
    : `${f(a, { day: "numeric", month: "short" })} – ${f(b, { day: "numeric", month: "short", year: "numeric" })}`;
};
const SESS_SORTS = [
  ["new", "Newest first", (a, b) => (b.created_at || 0) - (a.created_at || 0)],
  ["old", "Oldest first", (a, b) => (a.created_at || 0) - (b.created_at || 0)],
  ["profit", "Most profit", (a, b) => b.net - a.net],
  ["loss", "Biggest loss", (a, b) => a.net - b.net],
  ["win", "Best win rate", (a, b) => (isNum(b.win) ? b.win : -1) - (isNum(a.win) ? a.win : -1)],
];
// The strategy alone, without the dates or the account size that the card shows elsewhere
function runName(r) {
  const label = r.label || "";
  if (EXPERIMENT[label]) return { name: EXPERIMENT[label], tag: "" };
  const strictness = /−0\.3%|-0\.3%/.test(label) ? "Looser" : /^Panic buy/i.test(label) ? "Strict" : "";
  let s = label
    .replace(/^Random (day|week)( [\d-]+( to [\d-]+)?)? · /, "")
    .replace(/, ₹[\d,]+$/, "")
    .replace(/,? ?week of .+$/i, "")
    .replace(/, (\d{4}-\d{4}|[A-Z][a-z]{2}-[A-Z][a-z]{2} \d{4})$/, "")
    .replace(/ \(splits removed\)|,? 10 years|,? -?−?0\.3%/g, "")
    .replace(/^([^:]+): .*?(\([^)]*\))?$/, (m, a, b) => `${a}${b ? " " + b : ""}`)
    .trim();
  const mood = label.match(/^Mood (\w+)-(\d{4})-(\w+) (\w+)$/);
  if (mood) s = `Market mood: ${mood[3]} month`;
  if (!s) s = "Unnamed run";
  return { name: s[0].toUpperCase() + s.slice(1), tag: strictness && /panic/i.test(s) ? strictness : "" };
}
// What stretch of market the run covers: one day, one week, or a span of months / years
function runPeriod(r) {
  if (!r.first_day || !r.last_day) return "";
  const days = (new Date(r.last_day) - new Date(r.first_day)) / 86400000;
  const f = (d, o) => fmtDate(istDay(d), { timeZone: "Asia/Kolkata", ...o });
  if (days < 1) return `Day · ${f(r.first_day, { weekday: "short" })} ${f(r.first_day, { day: "numeric", month: "short", year: "numeric" })}`;
  if (days <= 7) return `Week · ${f(r.first_day, { day: "numeric", month: "short" })} – ${f(r.last_day, { day: "numeric", month: "short", year: "numeric" })}`;
  const span = days > 400 ? `${(days / 365.25).toFixed(1).replace(/\.0$/, "")} years` : days > 45 ? `${Math.round(days / 30.4)} months`
    : plural(r.sessions || Math.round(days * 5 / 7), "session");
  return `${span} · ${spanDates(r.first_day, r.last_day)}`;
}
const ranAt = (ts) => fmtDate(new Date(ts * 1000), { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
function spark(vals, cls) {
  if (!vals || vals.length < 2) return "";
  const lo = Math.min(...vals), hi = Math.max(...vals), w = 160, h = 44, span = hi - lo || 1;
  const pts = vals.map((v, i) => `${((i / (vals.length - 1)) * w).toFixed(1)},${(h - 2 - ((v - lo) / span) * (h - 4)).toFixed(1)}`).join(" ");
  return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" aria-hidden="true"><polyline points="${pts}"/></svg>`;
}
function renderSessions() {
  const all = S.sessions;
  const running = S.st && S.st.job.running;
  const h = head("sessions", "Every backtest and simulated week. Filter by result, sort, and open one to see its days and trades.",
    `<button class="btn" data-act="refresh" ${running ? "disabled" : ""}>${icon("download", "sm")}Update data</button>
     <button class="btn primary" data-act="campaign" ${running ? "disabled" : ""}>Run backtest</button>`);
  if (!all) return setHTML($("#view-sessions"), h + `<div class="card" style="min-height:240px">${empty("loader", "Loading", "")}</div>`);
  // One "Strategy" dropdown: the run types first, then every strategy name (with how many runs each has)
  const count = (xs, f) => xs.reduce((m, r) => m.set(f(r), (m.get(f(r)) || 0) + 1), new Map());
  const kinds = [...count(all, (r) => r.kind)], names = [...count(all, (r) => runName(r).name)].sort((a, b) => b[1] - a[1]);
  const picks = [["all", `All strategies · ${all.length}`, "All strategies"], ...kinds.map(([k, n]) => [`type:${k}`, `${k} · ${n}`, k]),
    ...names.map(([k, n]) => [`name:${k}`, `${k} · ${n}`, k])];
  const pick = picks.some(([k]) => k === S.sessPick) ? S.sessPick : "all";
  const ofKind = pick === "all" ? all : all.filter((r) => (pick.startsWith("type:") ? r.kind === pick.slice(5) : runName(r).name === pick.slice(5)));
  const res = ["profit", "loss"].includes(S.sessResult) ? S.sessResult : "all";
  const sort = SESS_SORTS.find(([k]) => k === S.sessSort) || SESS_SORTS[0];
  const shown = ofKind.filter((r) => res === "all" || (res === "profit" ? r.net > 0 : r.net < 0)).slice().sort(sort[2]);
  const filt = pick === "all" ? "all" : pick.slice(5);
  const filters = `<div class="sess-tools">${seg("sessResult", [["all", "All", ofKind.length], ["profit", "Win", ofKind.filter((r) => r.net > 0).length],
      ["loss", "Loss", ofKind.filter((r) => r.net < 0).length]], res, "sess-result")}
    <div class="sess-dds">${dropdown("sessPick", picks, pick, "sess-pick", "Strategy")}${dropdown("sessSort", SESS_SORTS.map(([k, l]) => [k, l]), sort[0], "sess-sort", "Sort")}</div></div>`;
  const tools = "";
  const card = (r) => {
    const ratio = isNum(r.won) && isNum(r.lost) ? `${int(r.won)} won · ${int(r.lost)} lost` : `${int(r.green)} green · ${int(r.red)} red days`;
    const n = runName(r);
    return `<button class="card span-6 sess" ${r.link ? `data-act="go" data-val="${esc(r.link)}"` : `data-act="open-run" data-val="${esc(r.id)}"`}>
      <div class="sess-top"><span class="chip">${esc(r.kind)}</span><span class="hint">Run ${esc(ranAt(r.created_at))}</span></div>
      <div class="sess-name"><h2>${esc(n.name)}${n.tag ? ` <em class="tag">${esc(n.tag)}</em>` : ""}</h2><span class="hint">${esc(runPeriod(r))}</span></div>
      <div class="sess-hero"><b class="${tone(r.net)}">${inr(r.net, { sign: true, whole: Math.abs(r.net) >= 1000 })}</b>${spark(r.spark, r.net >= 0 ? "pos" : "neg")}</div>
      <div class="sess-stats">
        <div><span>Win rate</span><b>${r.trades ? pct(r.win) : "—"}</b><small>${ratio}</small></div>
        <div><span>Trades</span><b>${int(r.trades)}</b><small>on ${plural(r.trading_days, "day")}</small></div>
        <div><span>Worst drawdown</span><b>${inr(r.max_dd, { whole: true })}</b><small>${fixed((r.max_dd / 10000) * 100, 1)}% of ₹10,000</small></div>
      </div></button>`;
  };
  // Summary of whatever is shown, then one group per kind with the newest 6 cards (all of them on request)
  const traded = shown.filter((r) => r.trades);
  const best = traded.slice().sort((a, b) => b.net - a.net)[0], worst = traded.slice().sort((a, b) => a.net - b.net)[0];
  const strip = shown.length ? `<div class="grid sess-summary">
      <div class="card span-3 sim-stat"><span>Runs</span><b>${int(shown.length)}</b><small>${int(traded.length)} with trades</small></div>
      <div class="card span-3 sim-stat"><span>Made money</span><b>${traded.length ? pct(traded.filter((r) => r.net > 0).length / traded.length) : "—"}</b><small>${traded.filter((r) => r.net > 0).length} of ${traded.length} runs with trades</small></div>
      <div class="card span-3 sim-stat"><span>Best run</span><b class="${best ? tone(best.net) : ""}">${best ? inr(best.net, { sign: true, whole: true }) : "—"}</b><small>${best ? esc(runName(best).name) : ""}</small></div>
      <div class="card span-3 sim-stat"><span>Worst run</span><b class="${worst ? tone(worst.net) : ""}">${worst ? inr(worst.net, { sign: true, whole: true }) : "—"}</b><small>${worst ? esc(runName(worst).name) : ""}</small></div></div>` : "";
  // One list in the order the runs were made, newest on top (the server sends them that way)
  const open = S.sessOpen && S.sessOpen[filt];
  const vis = open ? shown : shown.slice(0, 12);
  const groups = `<div class="sess-group"><div class="sess-ghead"><h2>${filt === "all" ? "Every run" : esc(filt)}</h2><span class="hint">${esc(sort[1])} · ${plural(shown.length, "run")}</span></div>
      <div class="grid">${vis.map(card).join("")}</div>
      ${shown.length > 12 ? `<div class="sess-more"><button class="btn more ${open ? "up" : ""}" data-act="sess-more" data-val="${esc(filt)}">${open ? "Show fewer" : `Show all ${shown.length}`}${icon("caret", "xs")}</button></div>` : ""}</div>`;
  setHTML($("#view-sessions"), h + `<div class="sess-filter">${filters}${tools}</div>` + strip +
    (shown.length ? groups : `<div class="card">${empty("list", "No sessions yet", "Run a backtest or pick a random week on the Simulation page.")}</div>`));
}

function renderReplay() {
  const r = S.st.replay;
  const days = (S.days && S.days.days) || [];
  const mondays = days.filter((d) => new Date(d + "T12:00:00+05:30").getUTCDay() === 1);
  S.rday = S.rday || (r ? r.day : mondays[mondays.length - 1] || days[days.length - 1]);
  const speeds = [[2, "2×"], [8, "8×"], [32, "32×"], [0, "Max"]];
  const controls = r && !r.preparing && !r.error;
  const transport = `<div class="card"><div class="transport">
    ${days.length ? dropdown("rday", days.slice().reverse().map((d) => [d, isoLabel(d)]), S.rday, "rday", "Day") : ""}
    <button class="btn primary" data-act="replay-load">${icon("restart", "sm")}Start from 09:15</button>
    ${controls ? `<button class="btn icon" data-act="replay-play" aria-label="${r.playing ? "Pause" : "Play"}">${icon(r.playing ? "pause" : "play", "sm")}</button>
      ${seg("speed", speeds, r.speed, "replay-speed")}
      <div class="scrub"><input type="range" min="0" max="${r.total}" value="${r.idx}" data-act="replay-seek" aria-label="Bar"><span class="num muted">${r.idx} / ${r.total}</span></div>` : ""}
  </div></div>`;
  const h = head("replay", `${S.rday ? esc(isoLabel(S.rday)) + ", " : ""}played bar by bar with today's rules`,
    `<button class="btn ghost" data-act="go" data-val="simulate">Back to history</button>`);
  if (!r) return setHTML($("#view-replay"), h + transport + `<div class="card" style="min-height:280px">${empty("replay", "Pick a day and press start", "The latest Monday is picked for you.")}</div>`);
  if (r.error) return setHTML($("#view-replay"), h + transport + `<div class="card" style="min-height:280px">${empty("alert", "Replay failed", esc(r.error))}</div>`);
  if (r.preparing) return setHTML($("#view-replay"), h + transport + `<div class="card" style="min-height:280px">${empty("loader", "Getting the day ready", "")}</div>`);
  const acc = r.accounts.desk, other = r.accounts.shadow;
  setHTML($("#view-replay"), h + transport + `<div style="height:var(--gap)"></div>` + accountView(acc, other, "replay", `P&L at ${acc.clock || "09:15"}`));
  const shown = S.deskAcct === "shadow" ? other : acc;
  drawCurve("replay", shown);
  const ts = shown.curve.length ? shown.curve[shown.curve.length - 1][0] : null;
  drawCandles("replay", shown, r.day, ts);
}

// ------------------------------------------------------------ strategies
function renderStrategies() {
  const d = S.strategies;
  if (!d) return setHTML($("#view-strategies"), head("strategies") + `<div class="card" style="min-height:280px">${empty("loader", "Loading", "")}</div>`);
  const nval = d.strategies.filter((s) => s.verdict && s.verdict.validated).length;
  const rows = d.strategies.map((s) => {
    const v = s.verdict, m = v ? v.main : null;
    const avg = m && m.n ? m.net / m.n : null;
    return `<tr><td class="sym">${esc(s.name)}<div class="sub" style="white-space:normal;max-width:52ch">${esc(s.thesis)}</div></td>
      <td class="r num">${m ? int(m.n) : "—"}</td><td class="r num">${m ? pct(m.win) : "—"}</td>
      <td class="r num ${tone(avg)}">${isNum(avg) ? inr(avg, { sign: true }) : "—"}</td><td class="r num ${tone(m && m.net)}">${m ? inr(m.net, { sign: true, whole: true }) : "—"}</td>
      <td class="r">${v ? `<span class="chip ${v.validated ? "pos" : ""}" title="${esc(v.reasons.join("; "))}">${v.validated ? "Passed screen" : "Failed screen"}</span>` : `<span class="chip">Untested</span>`}</td></tr>`;
  });
  // What the numbers can and can't claim: a past-data screen is a filter, not proof; only fresh paper days count as a test
  const tiles = `<div class="grid">
    <div class="card span-4"><div class="facts">${fact("ok", "green", "Passed the screen", `${nval} / ${d.strategies.length}`, "40+ past trades, positive after charges, t ≥ 1")}</div></div>
    <div class="card span-4"><div class="facts">${fact("flag", "violet", "Settings tried", int(d.trials), "Each try makes a lucky result more likely")}</div></div>
    <div class="card span-4"><div class="facts">${fact("eye", "amber", "Held-back days", S.st.holdout.opened ? "Used up" : "Sealed", S.st.holdout.opened ? "Only new paper days count as a test now" : `From ${shortDate(S.st.holdout.start)}`)}</div></div></div>`;
  // Historical strict evidence and separate forward paper counts for both thresholds
  const p = S.panic && S.panic.strict, loose = S.panic && S.panic.loose, e = p && p.evidence;
  const sign = (x) => `${x > 0 ? "+" : ""}${fixed(x, 2)}%`;
  const candidate = e ? `<div class="card glow"><div class="card-head"><h2>Panic buy</h2><span class="chip ${e.status === "Under review" ? "" : "pos"}">${esc(e.status)}</span></div>
    <p class="muted">On mornings NIFTY opens down (0.5%+ strict, 0.3%+ looser), buy the 2 midcaps or smallcaps that fell hardest with no results news.
      ₹5,000 each, 3% stop, sold by 15:10. Tested on 10 years of every liquid NSE stock, with split and bonus days removed.</p>
    <div class="lead-row kpis">
      <div><span>Strict, a trade after charges</span><b class="${tone(e.net_pct)}">${sign(e.net_pct)}</b><small>${int(e.trades)} trades on ${int(e.days)} mornings, ${pct(e.win)} won</small></div>
      <div><span>Looser, a trade after charges</span><b class="${tone(loose.evidence.net_pct)}">${sign(loose.evidence.net_pct)}</b><small>${int(loose.evidence.trades)} trades on ${int(loose.evidence.days)} mornings</small></div>
      <div><span>Years that made money</span><b>${esc(e.years_positive)}</b><small>Strict version</small></div>
      <div><span>Paper test so far</span><b>${int(p.fired.length + loose.fired.length)} mornings</b><small>Strict ${inr(p.net, { sign: true })}, looser ${inr(loose.net, { sign: true })}</small></div></div>
    <p class="card-foot">Still to prove: that real orders fill near the opening price. The paper test from Monday measures that.</p></div>` : "";
  // Every test from the research log, newest first: what it tried, what happened, and its verdict
  const cycles = (S.research && S.research.cycles) || [];
  const vcls = { Passed: "pos", Failed: "neg" };
  const tried = cycles.map((c) => `<tr><td class="sym">${esc(c.name)}<div class="sub">${esc(c.id)}</div></td>
      <td><div class="sub" style="white-space:normal;max-width:72ch">${esc(c.plain || c.result || c.summary || "")}</div></td>
      <td class="r"><span class="chip ${vcls[c.verdict] || ""}">${esc(c.verdict || "—")}</span></td></tr>`);
  const triedCard = cycles.length ? `<div class="card flush"><div class="card-head"><h2>Every strategy we tested</h2><span class="hint">Newest first · ${plural(cycles.length, "test")}</span></div>
      ${table([["Test", 0], ["What happened", 0, "grow"], ["Verdict", 1]], tried, "")}</div>` : "";
  setHTML($("#view-strategies"), head("strategies", "Every past signal of each rule, after charges. Past data screens a rule; it doesn't prove it.") + candidate + steadyCard(S.horizons) + tiles + triedCard +
    `<div class="card flush"><div class="card-head"><h2>Textbook rules</h2></div>${table([["Strategy", 0, "grow"], ["Trades", 1], ["Win rate", 1], ["Avg trade", 1], ["Total", 1], ["Status", 1]], rows, "")}</div>`);
}

// --------------------------------------------------------------- journal
function md(text) {
  const lines = text.split(/\r?\n/);
  let out = "", list = null, tbl = null, para = [];
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, (_, x) => `<b>${x}</b>`).replace(/`(.+?)`/g, (_, x) => `<code>${x}</code>`);
  const flushPara = () => { if (para.length) { out += `<p>${inline(para.join(" "))}</p>`; para = []; } };
  const flush = () => {
    if (list) { out += `<ul class="md-list">${list.join("")}</ul>`; list = null; }
    if (tbl) { const [h, ...rows] = tbl; out += `<div class="table-wrap"><table><thead><tr>${h.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; tbl = null; }
  };
  for (const ln of lines) {
    if (!ln.trim() || /^(#|\||\s*[-*] |---)/.test(ln)) flushPara();
    if (/^\|/.test(ln)) { if (!/^\|[\s:|-]+\|$/.test(ln)) (tbl = tbl || []).push(ln.replace(/^\||\|$/g, "").split("|").map((c) => c.trim())); continue; }
    if (/^\s*[-*] /.test(ln)) { (list = list || []).push(`<li>${inline(ln.replace(/^\s*[-*] /, ""))}</li>`); continue; }
    flush();
    if (/^### /.test(ln)) out += `<h3>${inline(ln.slice(4))}</h3>`;
    else if (/^## /.test(ln)) out += `<h2>${inline(ln.slice(3))}</h2>`;
    else if (/^(# |---)/.test(ln)) out += "";
    else if (ln.trim()) para.push(ln.trim());
  }
  flushPara(); flush();
  return out;
}
// The journals are long: one collapsed row per run or experiment (newest first, newest open), its days or details inside
function journalSections(text, newestFirst) {
  const parts = text.split(/\n(?=## )/).filter((x) => /^## /.test(x.trim()));
  return newestFirst ? parts : parts.reverse();
}
function journalRow(sec, i) {
  const lines = sec.trim().split(/\r?\n/);
  const headLine = lines[0].replace(/^## /, "");
  const m = headLine.match(/\(([^)]+)\):\s*(\d{4}-\d{2}-\d{2})\s*→\s*(\d{4}-\d{2}-\d{2})/);
  const title = m ? runInfo({ id: "d", label: m[1], first_day: m[2], last_day: m[3], accounts: {} }) : null;
  const name = title ? `${title.title}${title.tag ? ` · ${title.tag}` : ""}` : headLine;
  const when = m ? `${shortDate(m[2])} – ${shortDate(m[3])}` : "";
  const netLine = lines.find((l) => /^Net ₹/.test(l));
  const net = netLine ? Number((netLine.match(/Net ₹(-?[\d,]+\.?\d*)/) || [])[1]?.replace(/,/g, "")) : null;
  const facts = netLine ? netLine.replace(/^Net ₹-?[\d,.]+ on ₹[\d,]+ · /, "") : "";
  return `<details class="jrow" ${i === 0 ? "open" : ""}><summary><span class="jt"><b>${esc(name)}</b><small>${esc([when, facts].filter(Boolean).join(" · "))}</small></span>
    ${isNum(net) ? `<span class="jn num ${tone(net)}">${inr(net, { sign: true })}</span>` : ""}${icon("caret", "xs")}</summary>
    <div class="md">${md(lines.slice(1).join("\n"))}</div></details>`;
}
// Journal: the three lessons so far and how every tested strategy did, net a trade after charges (data/research_summary.json)
function lessonsBlock(R) {
  if (!R || !R.insights) return "";
  const cards = R.insights.map((c, i) => `<div class="card span-4 ${i === 1 ? "glow" : ""} lesson"><span class="card-sub">${esc(c.title)}</span><b class="big">${esc(c.big)}</b><p>${esc(c.text)}</p></div>`).join("");
  const sb = R.scoreboard || [], max = Math.max(...sb.map((x) => Math.abs(x.net_pct)), 0.01);
  const rows = sb.map((x) => { const w = (Math.abs(x.net_pct) / max) * 50;
    return `<div class="sb-row"><div class="sb-name"><b>${esc(x.name)}</b><small>${esc(x.note)}</small></div>
      <div class="sb-track"><span class="sb-bar ${x.net_pct >= 0 ? "pos" : "neg"}" style="${x.net_pct >= 0 ? "left:50%" : `left:${50 - w}%`};width:${w}%"></span></div>
      <span class="num ${tone(x.net_pct)}">${x.approx ? "≈ " : ""}${fixed(x.net_pct, 2, true)}%</span></div>`; }).join("");
  return `<div class="grid">${cards}</div><div class="card"><div class="card-head"><div><h2>What worked and what didn't</h2><p class="card-sub">Profit or loss a trade, after charges</p></div></div>
    <div class="sb">${rows}</div><div class="sb-axis"><span></span><span>Loses money</span><span>0</span><span>Makes money</span><span></span></div></div>`;
}
function renderJournal() {
  const d = S.journal;
  const text = d ? (S.jtab === "daily" ? d.daily : d.runbook) : "";
  const all = text ? journalSections(text, S.jtab === "daily") : [];
  const n = S.jShow || 10;
  const body = !d ? empty("loader", "Loading", "") : !all.length ? empty("journal", "No entries yet", "")
    : `${all.slice(0, n).map(journalRow).join("")}${all.length > n ? `<div class="table-foot"><button class="btn more" data-act="j-more">Show ${Math.min(10, all.length - n)} older${icon("caret", "xs")}</button></div>` : ""}`;
  setHTML($("#view-journal"), head("journal", S.jtab === "daily" ? `${plural(all.length, "run")}, each played one day at a time. Newest first; open one to see its days.` : `${plural(all.length, "experiment")}, newest first. Open one to read what was tested and what we learned.`,
    seg("jtab", [["daily", "Daily journal"], ["runbook", "Experiments"]], S.jtab, "tab")) + lessonsBlock(S.research) + `<div class="card flush">${body}</div>`);
}

// -------------------------------------------------------------- settings
const FIELDS = [
  ["Account", "wallet", "blue", [["capital", "Capital (₹)"], ["leverage", "Leverage (×)"], ["max_notional", "Max position (₹)"], ["tax_slab", "Tax slab (0–0.45)"]]],
  ["Hours", "clock", "amber", [["entry_start", "First entry"], ["entry_end", "Last entry"], ["lunch_start", "Pause from"], ["lunch_end", "Pause until"], ["flatten_at", "Close all at"]]],
  ["Risk", "shield", "green", [["risk_per_trade", "Risk per trade (₹)"], ["loss_halt", "Daily loss limit (₹)"], ["max_trades_per_day", "Trades per day"], ["max_positions", "Open positions"], ["max_consecutive_losses", "Stop for the day after losses"], ["lockout_after_losses", "Timeout after losses in a row"], ["lockout_sessions", "Timeout length (sessions)"], ["cooldown_min", "Wait after exit (min)"], ["cost_gate_mult", "Target at least × charges"]]],
  ["Trade shape", "ruler", "violet", [["bar_minutes", "Bar size (5 or 15 min)"], ["stop_atr", "Stop (× ATR)"], ["target_r", "Target (× stop)"], ["breakeven_at_r", "Breakeven after (R)"], ["slippage_bps", "Slippage (bps)"]]],
];
const GUARDS = { mode_live: "Live mode on", env_allow_live: "Allowed in .env", armed: "Armed", kite_logged_in: "Kite logged in", kite_can_trade: "Kite can place orders", strategy_validated: "A strategy is proven", static_ip_confirmed: "Static IP registered", positions_reconciled: "Positions match Zerodha", not_killed: "Kill switch off" };
function renderSettings() {
  const st = S.st;
  if (S.settingsDirty && $("#view-settings").innerHTML) return;
  const s = st.settings;
  const groups = `<div class="grid">${FIELDS.map(([title, ic, c, fields]) => `<div class="card ${title === "Risk" || title === "Trade shape" ? "span-12" : "span-6"}"><div class="card-head"><h2>${title}</h2></div><div class="form-grid">${fields.map(([k, l]) =>
    `<label class="field"><span>${l}</span><input type="${typeof s[k] === "number" ? "number" : "text"}" step="any" data-set="${k}" value="${esc(s[k])}"></label>`).join("")}</div></div>`).join("")}</div>`;
  const strat = `<div class="card"><div class="card-head"><h2>Desk strategies</h2><span class="hint">Panic buy runs on its own and isn't switched here</span></div><div class="form-grid">${Object.entries(STRAT_NAME).filter(([k]) => k !== "panic_buy").map(([k, l]) =>
    `<label class="check"><input type="checkbox" data-strat="${k}" ${s.strategies.includes(k) ? "checked" : ""}> ${l}</label>`).join("")}
    <label class="check"><input type="checkbox" data-set-bool="drex_enabled" ${s.drex_enabled ? "checked" : ""}> Ask Drex for a second opinion</label></div></div>`;
  const g = st.live_ready.checks;
  const live = `<div class="card"><div class="card-head"><h2>Real orders</h2>${seg("mode", [["paper", "Paper"], ["live", "Live"]], s.mode, "mode")}</div>
    <ul class="checks">${Object.entries(GUARDS).map(([k, l]) => `<li class="${g[k] ? "ok" : ""}">${icon(g[k] ? "ok" : "no", "sm")}${l}</li>`).join("")}</ul>
    <div style="display:flex;flex-wrap:wrap;gap:var(--s2);align-items:center;margin-top:var(--s4)">
      <label class="check"><input type="checkbox" data-set-bool="static_ip_confirmed" ${s.static_ip_confirmed ? "checked" : ""}> My IP is registered with Zerodha</label><span style="flex:1"></span>
      <button class="btn" data-act="arm">Arm</button><button class="btn" data-act="disarm">Disarm</button><button class="btn danger" data-act="kill">${icon("kill", "sm")}Kill switch</button></div></div>`;
  setHTML($("#view-settings"), head("settings", "Defaults are the ₹10,000 MIS account", `<button class="btn primary" data-act="save">${icon("check", "sm")}Save changes</button>`) + groups + strat + live);
}

// ---------------------------------------------------------------- render
function render() {
  if (!S.st) return;
  renderChrome();
  renderSide();
  ({ desk: renderDesk, simulate: renderSimulate, replay: renderReplay, week: renderWeek, sessions: renderSessions, strategies: renderStrategies, journal: renderJournal, settings: renderSettings })[S.view]();
}

async function onAction(el) {
  const a = el.dataset.act, v = el.dataset.val;
  if (a === "dd") { S.openDd = S.openDd === v ? null : v; return render(); }
  S.openDd = null;
  if (a === "theme") {
    const next = currentTheme() === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    try { localStorage.setItem("sm-theme", next); } catch (e) {}
    $$("[data-chart]").forEach((c) => { c._sig = null; });
    return render();
  }
  if (a === "go") return setView(v);
  if (a === "open-run") { S.cid = v; S.campaign = null; S.campTrades = null; S.runs = null; return setView("simulate"); }
  if (a === "sess-kind") { S.sessKind = v; return render(); }
  if (a === "sess-result") { S.sessResult = v; return render(); }
  if (a === "sess-pick") { S.sessPick = v; return render(); }
  if (a === "sess-sort") { S.sessSort = v; return render(); }
  if (a === "rail-mode") { S.railMode = v; return renderSide(); }
  if (a === "steady-ver") { S.steadyVer = v; return render(); }
  if (a === "drex-ask") return askDrex(v);
  if (a === "j-more") { S.jShow = (S.jShow || 10) + 10; return render(); }
  if (a === "sess-more") { S.sessOpen = { ...(S.sessOpen || {}), [v]: !(S.sessOpen || {})[v] }; return render(); }
  if (a === "sim-new") {
    S.simBusy = true; render();
    const res = await act(() => api("/api/sim/new", { method: "POST" }));
    S.simBusy = false;
    if (res) toast(`Picked the week of ${shortDate(res.week)}`);
    return pollSim();
  }
  if (a === "sim-symbol") {
    S.simSymbol = v; S.simSymbolDay = `${S.week.week}|${S.week.day}`;
    return drawSimCandles(S.week);
  }
  if (a.startsWith("sim-")) {
    const action = a === "sim-play" ? (S.week.playing ? "pause" : "play") : a.slice(4);
    await act(() => api("/api/sim/control", { method: "POST", body: { action, value: action === "speed" ? Number(v) : v ?? null } }));
    return pollSim();
  }
  if (a === "replay-pick") return setView("replay");
  if (a === "signals") { S.allSignals[v] = !S.allSignals[v]; return render(); }
  if (a === "all-trades") { S.allTrades = !S.allTrades; return render(); }
  if (a === "tab") { if (el.dataset.key === "jtab") { S.jtab = v; S.jShow = 10; } else S.tab[el.dataset.key] = v; return render(); }
  if (a === "acct") { S.deskAcct = v; return render(); }
  if (a === "account") { S.account = v; S.campTrades = null; return loadView(); }
  if (a === "run") { S.cid = v; S.campaign = null; S.campTrades = null; return loadView(); }
  if (a === "rday") { S.rday = v; return render(); }
  if (a.startsWith("sym-")) { const k = a.slice(4); S.sym[k] = v; S.bars[k] = null; return render(); }
  if (a === "campaign") {
    if (await ask("Run the backtest?", "Plays every stored session as fresh ₹10,000 paper accounts. Takes two to four minutes.", "Run backtest")) {
      await act(() => api("/api/campaigns", { method: "POST", body: {} }), "Backtest started");
    }
  } else if (a === "refresh") await act(() => api("/api/refresh", { method: "POST" }), "Updating data");
  else if (a === "replay-load") { await act(() => api("/api/replay", { method: "POST", body: { day: S.rday, speed: 8 } })); S.bars.replay = null; }
  else if (a === "replay-play") await act(() => api("/api/replay/control", { method: "POST", body: { action: S.st.replay.playing ? "pause" : "play" } }));
  else if (a === "replay-speed") await act(() => api("/api/replay/control", { method: "POST", body: { action: "speed", value: Number(v) } }));
  else if (a === "kite-login") { const r = await act(() => api("/api/kite/login", { method: "POST" })); if (r && r.url) window.open(r.url, "_blank", "noopener"); else if (r) toast("Kite is connected"); }
  else if (a === "kite-check") await act(() => api("/api/kite/check", { method: "POST" }), (r) => (r.logged_in ? "Kite connected" : "Not logged in yet"));
  else if (a === "save") {
    const patch = {};
    $$("[data-set]").forEach((i) => { patch[i.dataset.set] = i.type === "number" ? Number(i.value) : i.value; });
    $$("[data-set-bool]").forEach((i) => { patch[i.dataset.setBool] = i.checked; });
    patch.strategies = $$("[data-strat]").filter((i) => i.checked).map((i) => i.dataset.strat);
    const r = await act(() => api("/api/settings", { method: "POST", body: patch }), "Changes saved");
    if (r) { S.settingsDirty = false; $("#view-settings").innerHTML = ""; }
  } else if (a === "mode") await act(() => api("/api/settings", { method: "POST", body: { mode: v } }), `Switched to ${v}`);
  else if (a === "arm") {
    const typed = prompt("Type LIVE to allow real orders through Kite");
    if (typed) await act(() => api("/api/live/arm", { method: "POST", body: { confirm: typed } }), "Real orders armed");
  } else if (a === "disarm") await act(() => api("/api/live/disarm", { method: "POST" }), "Disarmed");
  else if (a === "kill") { if (await ask("Turn on the kill switch?", "Stops all new entries until the server restarts.", "Turn on", true)) await act(() => api("/api/kill", { method: "POST" }), "Kill switch on"); }
  poll();
}

// Replay needs the day's 5-minute bars; hourly-only days (older history) can't be played
async function openReplay(d) {
  try { if (!S.days) S.days = await api("/api/replay/days"); } catch (e) { return toast(e.message); }
  if (!S.days.days.includes(d)) return toast(`No 5-minute bars stored for ${isoLabel(d)}, so it can't be replayed`);
  S.rday = d; S.bars.replay = null;
  setView("replay");
  await act(() => api("/api/replay", { method: "POST", body: { day: d, speed: 0 } }));
  poll();
}

function bind() {
  document.addEventListener("click", (ev) => {
    const nav = ev.target.closest(".nav button");
    if (nav) return setView(nav.dataset.view);
    // a day in History's P&L grid opens that day's replay
    const day = ev.target.closest("#view-simulate .cal-cell[data-day]");
    if (day && !(S.campaign && S.campaign.summary.kind === "panic_buy")) return openReplay(day.dataset.day);
    const el = ev.target.closest("[data-act]");
    if (el && el.type !== "range") return onAction(el);
    if (S.openDd && !ev.target.closest(".dd")) { S.openDd = null; render(); }
  });
  document.addEventListener("keydown", (ev) => { if (ev.key === "Escape" && S.openDd) { S.openDd = null; render(); } });
  document.addEventListener("change", (ev) => {
    if (ev.target.dataset.act === "replay-seek") act(() => api("/api/replay/control", { method: "POST", body: { action: "seek", value: Number(ev.target.value) } }));
  });
  document.addEventListener("submit", (ev) => {
    const f = ev.target.closest("[data-drex-form]");
    if (!f) return;
    ev.preventDefault();
    const q = (f.q.value || "").trim();
    f.q.value = ""; f.q.blur();          // sent: the box empties so the next question starts fresh
    if (q) askDrex("custom", q);
  });
  document.addEventListener("mouseover", calendarTip);
  document.addEventListener("scroll", () => { const t = $("#cal-tip"); if (t) t.classList.remove("on"); }, true);
  document.addEventListener("input", (ev) => { if (ev.target.closest("#view-settings")) S.settingsDirty = true; });
  window.addEventListener("hashchange", () => { const v = location.hash.slice(1); if (v && v !== S.view && $(`#view-${v}`)) setView(v); });
  window.addEventListener("resize", () => { $$("[data-chart]").forEach((c) => { c._sig = null; }); render(); });
}

hydrateIcons();
bind();
setView((location.hash || "#desk").slice(1).replace(/[^a-z]/g, "") || "desk");
poll();
setInterval(poll, 1500);
loadSide();
setInterval(loadSide, 30000);
setInterval(() => { if (S.view === "week") pollSim(); }, 700);
setInterval(simTick, 200);
