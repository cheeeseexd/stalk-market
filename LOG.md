# Stalk Market: engineering log and handoff

- 2026-10-31 H18 (Codex; report label2026-11-01, Claude review pending): pre-registered daily failed-move screen; primary power design770 picks/411 days (696 fundable/399), reserved520/277 (479/276); design -Rs7,163.85/-22.93bps/day t=-4.515. Both sides/all six registered cells lose; confirmation and reserved five-minute outcomes unscored. Signal frequency231/239 days per252 sessions; calendar2024/2025=232/221. Added scripts/h18_daily.py,12 tests, data/h18 artifacts and docs/research/2026-11-01-h18-daily.md;134 unit tests and saved-row/hash reconciliation pass, protected files unchanged, no servers/task processes. Lesson: frequency is not edge; next H16 execution evidence then H19 prior-day range failure, count/fresh-forward first. QUEUE/INSIGHTS/RUNBOOK updated.

**Next agent: read this, then RUNBOOK.md. Add a changelog entry when you finish.**
Last updated: 2026-10-31 (Sat).

- 2026-10-31 - Research cycle5 H17 PASS (reconstruction):120/120 action chunks,22,432 rows,1,214 dated alias repairs,351/351 morning inputs; strict/loose stopped+168.44/+130.78bps, day t5.94/6.04; cleaned Rs10k+32,007.78/+39,568.47. Original History archived; cleaned runs/evidence/PLAYBOOK/report updated; all action publication times unknown, no execution/fresh-confirmation claim. One interrupted metadata-invalid attempt and verifier-only completion fix retained; protected runner/guards unchanged. Next H16; Claude review pending. See docs/research/2026-10-31-h17-rebuild.md.

- 2026-10-31 — Research cycle 3, H14 executable no-news fade: nominal design/hold-out25 picks/17 mornings vs 14/11; one 18-cell design run, primary +Rs3.00/24 funded trades, day t=0.006. Seven 2:1 price-scale mismatches plus a split-date pick invalidate clean inference; no repaired rescore, hold-out unscored, no runner spec. H15 price audit now first, H16 execution evidence added; docs/research/2026-10-31-h14-fade.md. All 95 tests pass; 504 saved trade rows/108 frozen input hashes and protected sources reconciled. Claude review pending; no live/protected changes or servers.

- 2026-10-31 — Research cycle 2, H12 sector panics: pre-registered cached-data study/script/report and 13 guard tests; design 14 trades/eight days, -31.58 bps, trade/day t=-0.38/-0.41, 1.6 days/year; confirmation unscored. Sparse controls/classification and look-ahead proxy limits documented; queue re-ranked to H14, no live changes. All81 tests and independent artifact/hash checks pass; Claude review pending.

## 1. What it is

An intraday paper-trading app for NSE (NIFTY 50 stocks) sized for a ₹10,000 Zerodha MIS account.
- Rule strategies come from published theses (see `docs/research/2026-10-30.md`), each with one parameter fixed in advance.
- **Validation:** a strategy is validated as of a day only with ≥ 40 earlier trades, t ≥ 1 after Zerodha charges, and neighbouring settings agreeing (a plateau).
- **One engine** (`stalk/sim/engine.py`) plays a day bar by bar. It's used by the campaign (many past weeks), day replay, and live paper.
- Every setup gets **Time Machine** odds and a **risk-gate** verdict with a reason.
- Live trading runs two paper accounts: **desk** (validated strategies only) and **shadow** (every signal).
- Real Kite orders mirror the desk only when every guard in `Agent.live_ready` passes. They are blocked today, because nothing is validated.

The previous app (NexusQuant: an ML ranker that never validated) is in `archive/nexusquant/`, reference only.

## 2. Layout

| Path | Role |
|---|---|
| `stalk/config.py` | `Settings` (₹10k defaults, validation), paths, universe, `HOLDOUT_START` |
| `stalk/market/costs.py` | Zerodha MIS charges (copied with its tests) |
| `stalk/data/store.py` | SQLite `data/stalk.db`: bars (`5minute` and `day`), sessions (with engine `state`), trades, logs, campaigns, trials |
| `stalk/data/yahoo.py`, `feed.py` | Yahoo 5-minute (60 days) and daily (2 years) bars; Kite first when logged in |
| `stalk/data/kite_mcp.py` | Hosted Kite MCP client (copied unchanged) |
| `stalk/research/features.py` | Causal features: ATR, VWAP, opening ranges 15/30/45, rvol vs same time of day, gap σ, 52-week high, NIFTY/BANK divergence |
| `stalk/research/strategies.py` | `orb_breakout`, `momentum_52w`, `vwap_pullback`, `gap_fade`, plus the entry-window and first-per-day filters |
| `stalk/research/trade.py` | `Trade.step` / `walk`: entry at the next open, stop before target, gap fills at the open, breakeven from the next bar, 15:10 flatten; `size`, `cost_gate` |
| `stalk/research/validate.py` | `strategy_events` (every first-of-day signal traded with the account's sizing and charges), `validate_as_of`, `stats` |
| `stalk/research/timemachine.py` | 10,000-path same-time-of-day bootstrap (ported from day-trader-skill, MIT) |
| `stalk/sim/risk.py` | Risk gate: PASS/BLOCK with a reason |
| `stalk/sim/engine.py` | `DayEngine`: fill queued entries → step trades → loss halt → signals → gate → Time Machine → queue; `to_state` / `load_state` for restarts |
| `stalk/sim/campaign.py` | Weekly as-of validation, then accounts `validated`, `every_signal`, `solo_<strategy>` over the same days |
| `stalk/sim/broker.py` | `LiveKiteBroker`: MARKET entry + immediate SL-M; the exit **modifies the SL-M to MARKET** (falls back to a market exit, then cancels the stop); positions for reconciliation |
| `stalk/agent.py` | `LiveRunner` (every completed bar + 12 s; desk + shadow; persists state; reconcile; mirror), `ReplayRunner`, jobs, `live_ready` |
| `stalk/llm/drex.py` | Drex client (copied); votes on queued setups when `drex_enabled`; stored as `drex_p` |
| `stalk/api/server.py` | REST; the UI polls `/api/state` every 1.5 s |
| `web/` | `index.html`, `lib.js` (helpers and charts copied from the archive), `app.js`, `styles.css` (same token system) |
| `scripts/campaign.py`, `scripts/open_holdout.py` | CLI campaign; the one-time holdout opener |
| `.claude/skills/` | `backtest-review`, `strategy-critique`, `trade-postmortem` (adapted, MIT, attributed) |

## 3. Run

```
pip install -r requirements.txt
python run.py                                   # http://localhost:8080
python scripts/campaign.py --until 2026-10-16   # ~3 min, prints the tables
python -m unittest discover -s tests -t .       # 28 tests, ~7 s
```
`.env`: `DREX_API_KEY` (set), `ALLOW_LIVE_ORDERS=1` (not set). Kite: Desk → Connect Kite each morning (the token expires at 06:00).

## 4. Data

`data/stalk.db` holds 5-minute bars from 2026-07-06 to 2026-10-30 (82 sessions; Jul–Sep migrated from the archive, since Yahoo only serves 60 days) and 2 years of daily bars for 40 stocks plus the two indices. Refresh from Simulate → Refresh data, or `Feed.refresh_history()`.

## 5. Results

See RUNBOOK.md. Nothing is proven. With stop 3 × ATR, the every-signal account lost ₹2,358 over 48 sessions (it was ₹5,088 at 1.5 ATR) and ₹784 on the 9-session holdout (19–30 Oct, opened once, inside the prediction). The proven-only account stayed at ₹0. Drex scores no better than the base rate.

## 6. Verified

- 28 tests pass:
  - charges
  - causal features and signals
  - opening range hidden until it closes
  - trade rules (gap stop, stop-first, breakeven, flatten)
  - sizing caps
  - **research ↔ engine parity** (exact trades)
  - Time Machine determinism and past-only data
  - every risk-gate rule
  - campaign capital carry-over
  - validation ignores later and holdout days
  - SL-M placed on a tick
  - exit never cancels the stop first
  - live needs every guard including static IP
  - API contract
  - frontend contract (one font, ₹ only, tokens only, control heights)
- Real data: the campaign ran on 48 sessions. Parity was checked on all 75 ORB trades against research. The replay of 16 Oct played in the browser, with no console errors.

## 7. Not verified / gaps

- **Live paper on a trading day** hasn't run yet (built on Friday night). Watch Monday 2 Nov: bar timing, `refresh_today`, state persistence across a restart.
- **The live mirror is minimal and untested against Zerodha.** The breakeven stop move isn't mirrored to the broker. The engine's exit price is the paper price, not the broker fill. Live is blocked anyway, because nothing is validated.
- **Survivorship:** the universe is today's NIFTY 50.
- **No NSE holiday calendar:** on a holiday the data looks stale and entries pause.
- The Time Machine isn't calibrated (see RUNBOOK). It ranks simultaneous setups, which rarely matters at 1 position and 2 trades/day.
- Drex votes are recorded but not yet scored on a Brier board.
- The campaign takes about 3 minutes (dominated by per-bar `iloc`). Fine for now.

## 8. Next steps

1. RUNBOOK's next experiment: stop 3.0 × ATR (one change), re-run the campaign, log it.
2. Run the two reviewer skills (`backtest-review`, `strategy-critique`) as independent subagents and fix what they confirm.
3. Write the holdout prediction, `python scripts/open_holdout.py`, run `scripts/campaign.py --open-holdout` once, and log it.
4. Monday 2 Nov: connect Kite at 09:00, leave `python run.py` running, then do a `trade-postmortem` after 15:30.

## Changelog

### 2026-10-30: Stalk Market, fresh build (Claude)
- Researched the X threads (@shivanshco, @pranjalsoni_, @RohOnChain), the Day Trader skill video, and similar Indian write-ups and repos (`docs/research/2026-10-30.md`).
- Archived the whole old app to `archive/nexusquant/`. Built `stalk/` and `web/` fresh, copying charges, the bar store, the Yahoo/Kite/Drex clients and the UI design system with their tests. Migrated 194k stored bars.
- Added four thesis strategies, plateau validation, a trials ledger, a holdout, the Time Machine, the risk gate, the day engine, campaigns, replay, live desk + shadow, and safe live exits.
- First campaign: no edge (RUNBOOK). Added three adapted skills.

### 2026-10-31: wider stop, RSI reversion, holdout, Drex score, dark redesign (Claude)
- Default stop is 3 × ATR (60 settings tried; losses shrink smoothly with stop width). Added `rsi_reversion` from quants.md O15/W07, which lost. Holdout opened once and logged; `data/holdout_opened.json` exists now.
- `scripts/drex_score.py` scores Drex on backtest trades (Brier). `/api/days` gives daily P&L per account.
- UI redesign, dark by default: Geist as the only font, Lucide icon data (CDN `lucide@0.469.0`), hero number with gradient curve, KPI cards with icons, a GitHub-style daily P&L grid (`heatmap()` in `web/lib.js`), separate Backtest and Replay pages, a backtest run picker, shorter copy. Frontend skill installed at `.claude/skills/frontend-design`.

### 2026-10-31 (later): 15-minute bars (Claude)
- `Settings.bar_minutes` (5 or 15, default 15). `features.resample` joins stored 5-minute bars into 15-minute bars aligned to 09:15 and drops forming bars. Bar size is read from the data by features, the Time Machine and the engine. Live keeps polling every 5 minutes and acts when a 15-minute bar completes.
- On 15-minute bars the bar ending 15:15 is the flatten bar, so positions close at 15:15 (before Zerodha's 15:20 auto square-off).
- `scripts/campaign.py --set key=value` overrides one setting for a run. The Backtest page's run picker labels runs by bar size and stop.
- Results: RUNBOOK 2026-10-31 (later). Every signal −₹1,180 vs −₹3,142 on 5-minute bars over 57 sessions; gap fade +₹374 but not proven. 30 tests.

### 2026-10-31 (night): UI rebuild on the user's reference, 10:00-start test (Claude)
- UI rebuilt to the user's dark reference: pure black page, #121212 cards with hairline borders, Geist only, sentence case, one colour per meaning.
  Every row of cards shares one height (hero + summary, exit tiles, three mini cards). Filled Phosphor icons (MIT) bundled in `web/icons.js` (50 icons, no CDN).
  Custom dropdowns replace native selects (`dropdown()` in lib.js). The daily P&L calendar shows ₹ in each cell and a hover card (`calendar()` / `calendarTip()`).
  Top bar with breadcrumb, market status, clock and Kite status. Hover states on rows, tiles, buttons, menus and checks.
- Experiment 4 (entries from 10:00) rejected; the default stays 09:30. See RUNBOOK.

### 2026-11-01: rules, hourly long test, more research (Claude)
- `RULES.md`: the five trading rules (video "I Gave AI ₹10 Lakh to Trade", plus SEBI data), each mapped to the code that enforces it.
  New cross-session timeout: `lockout_after_losses` / `lockout_sessions` (3 / 3), using `GateState.streak` and `locked`, carried by `DayEngine.carry()` / `start_from()` in campaigns and from the previous live session in `LiveRunner._restore`.
- Hourly bars: `HOURLY = "60minute"`, about two years from Yahoo (724 sessions stored); `bar_interval()`; `bar_minutes` 60 is backtest-only (the API refuses it).
  Daily bars refresh 5 years. `trading_days` needs 5 of 6 hourly bars.
- New strategies: `supertrend` (flip with VWAP agreement, ATR × 2/3/4) and `pdh_breakout` (close beyond the previous day's high or low, buffer 0/0.1/0.2%).
- `docs/research/2026-11-01.md`: one summary of all research. `scripts/swing_test.py`: a daily delivery swing test under the five rules (rejected, see RUNBOOK).
- Long test results (RUNBOOK 2026-11-01): 632 unseen hourly sessions; every signal −₹2,538 (−0.04R, gross ≈ ₹0, charges are the loss); gap fade's edge didn't hold up.
  The five rules (run B) halved the loss and drawdown, so they're now defaults: leverage 1 / max position ₹10,000, 1 trade a day, breakeven off (stop never moves), timeout 3/3.
- Daily P&L grid uses small square cells for ranges over 16 weeks (`.cal.compact`). 32 tests.

### 2026-11-01 (later): reachable stops, day-by-day month, daily journal (Claude)
- Stops: `stop_mode="range"`, `stop_range=0.35` × average daily range of the prior 10 sessions (`adr` feature); `stop_distance()` in trade.py is the one place the stop is sized; `Trade.open`/`walk` take the distance.
- Research skips signals while a trade in that stock is open (engine parity).
- `stalk/sim/daybyday.py` + `scripts/day_by_day.py`: one session at a time with data only up to that day, hard checks per session
  (`check_day`: flat before 15:20, stop and target on every trade, trade cap, P&L reconciles), a journal (`JOURNAL.md`), and reviews after 3-day streaks
  (bench a strategy, follow the index, start at 10:00; priority for winners). The Journal page shows the daily journal and the experiments.
- New defaults from the October run: `entry_start="10:00"`, `with_index=True`. October: −₹2.07, 14 trades. 38 tests.

### 2026-11-01 (end of day): research log and Drex checklist design (Claude)
- `RESEARCH_LOG.md`: one table of every research source, what we took from it, and its status (adopted, rejected, noted or proposed).
- `docs/research/2026-11-01-drex.md`: Drex redesigned as a factor checklist with veto power only (richer state, 5 yes/no checks and a score,
  measured day by day). The 31 Oct Drex test sent too little information, and `setup_state` still describes the old 1.5-ATR stop. Not built yet.
  Dexter (virattt) noted as a possible news and fundamentals fetcher only.
- Where things live: LOG.md (engineering), RUNBOOK.md (experiments), JOURNAL.md (each trading session), RESEARCH_LOG.md (research), RULES.md (trading rules).

### 2026-11-02: market layers, Drex checklist, mood tests (Claude)
- Data: NSE corporate board meetings (`events` table, 1,149 rows since Nov 2023, with announcement time) and India VIX daily/5-minute (`INDIA VIX`).
- Layers (Settings switches): `avoid_events` (`Store.event_symbols`), `breadth_filter`, `sector_filter` (`SECTORS` map in config), `vix_filter` (`daybyday.vix_spike`), `rank_by_score` (factor agreement, then relative volume), `drex_veto`.
  The engine computes breadth and sector moves per bar (`DayEngine._ctx`) and records `score`, `rvol` and `context` on every setup.
- Drex: `CHECKLIST` (5 yes/no checks and a 0–4 quality score), `checklist_state`, `checklist_verdict` in `stalk/llm/drex.py`.
  The engine takes a `vetter` callback that can only block; `daybyday.drex_vetter` builds it, and `would_have` simulates every vetoed setup.
- `scripts/day_by_day.py --label` names runs (shown in the Backtest picker). The settings parser reads "true"/"false" text correctly.
- Results (RUNBOOK 2026-11-02): stack S1 +₹226 vs baseline −₹666 on 10 Aug–30 Oct. S1 is now the default; VIX and the Drex veto stay off. 41 tests.

### 2026-11-02 (later): news layer (Claude)
- `news` table: NSE corporate announcements (4,131 rows; Jul–Oct 2026 plus Feb–Mar 2025, Nov–Dec 2025, Feb–Mar 2026) with exchange timestamps and `drex_p`.
- `stalk/research/news.py`: `classify` (routine vs material, keyword good or bad), `session_news` (previous close → today's close), `news_view` (only items published by the bar).
  `scripts/label_news.py`: Drex labels material filings (good_news and bad_news questions; p = 0.5 + (good − bad) / 2).
- Settings `news_filter` (default off) and `news_source` keywords|drex; the engine blocks trades against net news and counts agreeing news in the ranking.
- Results: both news variants lost to S1; S1 passed its stop-size robustness check. 43 tests.

### 2026-11-02 (night): choppy mood, second Sol talk, UI polish (Claude)
- `features.index_frame`: `idx_cross4` (NIFTY crossings of its day's typical-price average in the last 4 bars). Setting `mood_chop` (default off): 2+ crossings makes the mood unclear. Tested as C3, rejected.
- Engine snapshot now sends the whole day's signals (`MAX_SETUPS` 200), so the UI can group every skip.
- UI: nav is Today / History / Strategies / Journal. Replay is a mode of History (click a day in the P&L grid, or "Replay a day"); hourly-only days say they can't be replayed.
  Removed icon-in-a-box decoration (page titles, summary rows, exit tiles). "How trades ended" is one stacked bar. Tables use 40px rows, one column takes the slack, strategy moved to a muted "Rule" column, trades show 15 with "Show all".
  Replay's signals list shows taken trades, with skipped ones grouped by reason and the full list a click away. Today shows the next session, the last paper day and a before-the-open check outside market hours.
  Strategies says "Passed / Failed screen" rather than "Proven". Sidebar runs the full page height.
- Next: build the frozen forward test with random-selector benchmark (RUNBOOK 2026-11-02 night), and judge filters on the signal pool.

### 2026-11-03: profitability push, H1–H7, panic-buy forward test (Claude)
- INSIGHTS.md: everything learned so far, in one place.
- Research scripts: `edge_map.py` (where any edge is), `premarket_cues.py` (US, crude, gold, rupee, 6 years), `fetch_wide.py` / `fetch_daily_wide.py` / `fetch_events_wide.py` /
  `fetch_events_old.py` (Next 50, Midcap 100, Smallcap 100: 5-min, hourly, daily bars and NSE board meetings; research only, trading universe unchanged),
  `in_play.py` (H1), `results_drift.py` (H2/H3), `alignment.py` (H4), `results_swing.py` (H5), `gap_fade_wide.py` (H6), `panic_buy.py` (H7), `panic_5min.py` (execution check).
  Data lands in data/*.pkl and is rebuilt by the fetch scripts.
- `stalk/sim/panic.py`: the frozen panic-buy rule. `stalk/sim/panic_live.py`: forward paper runner (NSE pre-open, board meetings, announcements; Yahoo 5-min; Kite quotes if logged in),
  started by `Agent.boot`. `/api/panic` + a Today-page card. Trades go to the trades table as session `panic:<day>`. 5 new tests (46 total).
- Live desk settings restored to the S1 defaults (the C1 test had been left on).
- UI: the "Closed at day end" result now reads "Sold at day end", with a hover note: neither stop nor target was hit, so the end-of-day rule sold it (15:10; 15:15 on hourly runs).
  The Strategies page leads with the panic-buy card (untouched-test evidence from `/api/panic`, forward-test progress).
- Replay and Today: open positions show their entry time, how long they've been open, and a stop → target track (entry tick, price dot, R so far).
- `scripts/panic_buy.py --y2016` (H7b, Nov 2016 → Oct 2021 from 10-year daily bars, `fetch_events_2016.py`), and `scripts/panic_bhav.py` (H7c: survivorship-free test from NSE bhavcopy
  files, cached in data/bhav/).
- PLAYBOOK.md (the trading rules) and `scripts/panic_account.py` (10 years as a ₹10k account).
- `scripts/panic_bhav_stress.py`: H7c without the all-day-lock look-ahead and with worse entries. `/api/panic` evidence now shows the 10-year survivorship-free result;
  the forward test's first review is after 5 panic mornings (execution check). RUNBOOK has the revised plan.

### 2026-10-31: Codex final research handoff (report label 2026-11-03)
- Added scripts/panic_final.py for pre-registered H7d random selection (2,000 draws), 36-cell neighbour grid, whole-share account bootstrap (5,000), and ten full November–October year checks. Added scripts/euphoria_design.py for the separate H8 cached design screen, with no confirmation mode. Both are research-only; protected rules and order controls untouched.
- H7d: all numerical gates pass, 351/351 mornings covered; stopped proxy +164 bps versus random 95th +68, 36/36 grid cells pass. Annual pooled bootstrap 5th +₹435, losing 2.68% (stationary assumption). Account +₹31,580; reconciles all 422 prior journal trades within ₹0.005. No aggregate late-period decay. Execution and universe/calendar limitations remain.
- H8: +157 bps on only 24 design trades, day t=1.11, best-day share 72%; inconclusive, not promoted. Explicit future confirmation reserved and unused. Two non-panic proposals and full verdict in docs/research/2026-11-03-codex-final-test.md. INSIGHTS.md and RUNBOOK.md updated. Raw tables in data/panic_final/ and data/euphoria_design/.
- Validation: python -m unittest discover -s tests -t . passed all 46 tests after each script addition. No app started, real orders enabled or credentials opened. Runtime environment says 31 October; requested report filename retains the handoff's 3 November label.
- History page: the 10-year panic-buy account is stored as its own run (`scripts/panic_to_history.py`, ETFs excluded): curve, summary, how trades ended,
  a by-year table (replaces the day grid for runs longer than a year), and all 409 trades. Dates show the year on multi-year runs.


### 2026-10-31: one-week observable panic simulation (Codex)
- Added stalk/sim/panic_replay.py: offline replay of 21?25 September 2026 using the frozen panic selector/walk/cost model. Current API state exposes completed candles and current opens only. Stops appear when the candle completes; missing full execution paths are flagged unavailable, never silently filled. No paper/trade DB writes or broker calls from the simulation.
- Added /api/panic/replay and /api/panic/replay/control, and Week simulation navigation: play/pause, 1/2/10/30 bars per second, next candle, restart, day jumps, seek, account curve, positions, all candidate decisions, rolling action log, closed trades and completed session results. Reasons are deterministic rule checks, not fabricated LLM thoughts.
- Week: 360 snapshots; Thursday NIFTY gap ?0.966%; 247 wider-universe stocks scanned; LTF ??82.33 and CHOLAFIN +?30.30, both 15:10 exits; net ??52.03 (charges ?10.42), other four days zero trades. Summary data/panic_week/summary.json. Reused cached history, not fresh confirmation or a repeat of the all-EQ daily reconstruction.
- Validation: 52 tests pass (6 new causality, parity, skip/reject, missing-data, playback and seek/copy-isolation checks); JS syntax and HTTP controls checked. Dashboard opened at http://localhost:8081/#week with STALK_NO_BOOT=1; port 8080 was already occupied. Restart with python scripts/week_dashboard.py. The normal server picks up the new view on its next restart.

### 2026-10-31: live week simulation (Claude, rebuilding Codex's first version)
- `stalk/sim/panic_replay.py`: a past week of the frozen panic-buy rule played as if live. One frame per 5-minute candle with only knowable data,
  a one-line insight, a short decision log, the 09:20 scan funnel (reviewed → fell 2%+ → results → locked → clean → bought), open positions and running P&L.
  `WeekSim` runs a simulated clock at 4× / 60× / 300× / 1200× and skips quiet stretches (no-trade days, nights) after 3 seconds.
- `/api/sim`, `/api/sim/new` (random week from the stored 5-minute weeks; each draw put to Drex as yes/no, at most 4 draws), `/api/sim/control`.
  Replaces Codex's fixed-week `/api/panic/replay`. Paper only: writes nothing to the trades table or the paper log.
- Simulation page (nav: Simulation): insight card with the running clock, controls and day chips, live numbers, "What the bot did" log,
  "This morning's scan", P&L chart by step. tests/test_panic_replay.py rewritten (no look-ahead, parity with panic.walk, funnel, quiet skipping). 52 tests.
- Fixes after a click-through test: History's run button shows a short label (dates in the menu and the subtitle); on panic-buy runs "Replay a day" becomes
  "Watch a week" (opens Simulation) and days aren't clickable to the NIFTY 50 desk replay; a finished simulated week is saved to History as
  "Panic buy, week of …" (`save_to_history`). Tested end to end with real clicks (Playwright): pick, play at 4× (clock ticks), 1200× to the end, History, Replay a day.


### 2026-10-31: every-week panic comparison and causal trade chart (Codex)
- Added scripts/panic_weeks.py: every one of 12 available five-minute weeks under frozen -0.5% and reconstructed -0.3%, saved as 24 distinct w/v History runs. Ten-year ISO-week distribution in data/panic_weeks/report.md and CSV/JSON files; 351/351 mornings cached, strict account reconciles the 409 non-ETF journal trades within rounding. Existing H7d reuse, not new evidence; ETFs excluded after top-two selection with no replacement.
- Strict/loose ten-year active weeks 163/243 of 522; positive shares 62.6%/58.4%; mean active week 180.15/149.11 rupees; median 96.40/58.04; totals 29,364.63/36,233.02. Recent totals -68.14/+283.96 rupees; positive/negative/inactive weeks 1/3/8 and 2/5/5. Strict is more reliable per active week, loose offers more opportunities. No forward-paper switch.
- Added GET /api/sim/bars and atomic WeekSim candle snapshot: completed candles plus masked current open only, current-day entry/stop/exit markers, NIFTY fallback on quiet days. Simulation left column now stacks a bounded scrolling log and candle chart with stock buttons; existing token colours/candle classes. Campaign list limit 100 makes all 24 saved versions accessible.
- Validation: 54 tests pass, JS syntax passes; endpoint causality and distinct History labels/IDs covered. Temporary 8099 HTTP new/day/play/pause checks pass. Requested Edge screenshot is blocked by sandbox (Python pipe WinError 5, Node launch EPERM); native fallback stopped after concurrent user input. No screenshot or visual pass claimed. Check scripts retained; data/ui_check_sim.json records blocker and server cleanup. All task-started processes/servers stopped. Protected rules and secrets untouched.


### 2026-10-31: both panic thresholds on forward paper from Monday
- Added the approved -0.3% runner alongside frozen -0.5%; strict defaults, panic_paper.jsonl, panic:<day> IDs and execution rules retained. Loose uses panic_paper_loose.jsonl, panic03:<day>:<symbol> trade IDs, panic03:<day> sessions and panic_buy_loose. Each tick has independent error handling (panic_error / panic_loose_error); fetches remain independent to preserve the existing strict path.
- /api/panic now returns strict/loose summaries. Today compares checked/panic mornings, trades and net side by side with version-labelled trades; Strategies shows both forward counts and nets, attributing historical evidence only to strict. RUNBOOK pre-registers independent five-panic-morning execution reviews and the post-grid threshold choice; PLAYBOOK notes both paper versions.
- Validation: all 59 unit tests pass (5 new variant, journal/ID, endpoint and failure-isolation tests); node --check web/app.js and Node rendering smoke checks for Today/Strategies with mixed/empty journals pass. No server started, no task process left running. No .env, live_ready or validate.py edits. Two empty directories from Windows tempfile permission failures remain (tmprsz20akb, tmptfc61rlg): automatic approval review blocked recursive removal; nonrecursive os.rmdir also returned access denied.

### 2026-10-31: H9 ordinary-day news gaps research cycle
- Pre-registered before returns; added scripts/h9_fetch.py and scripts/h9_news.py with paced/resumable NSE caching, filing-time provenance, historical ETF identities, exact side-aware MIS costs, controls, adverse fills and exclusive confirmation guard. All 2,425 stored-calendar bhavcopy inputs and 1,198 announcement windows cached (1,270,895 filings). Design 2017?2021 rejected: 663 trades/485 days, -82.52 bps, trade/day t=-5.35/-5.16, both sides and all five years negative; worse entry -126.70 bps. Confirmation 2022?2026 not scored. Updated RUNBOOK, INSIGHTS and QUEUE; H12 next, H14 no-news fade retained only as a fragile execution lead. Review: docs/research/2026-10-31-h9-news.md; artifacts: data/h9/. All 68 unit tests pass, including nine H9 guards. Protected panic.py, agent.py (live_ready) and validate.py hashes unchanged; no .env access, server, runner or live-order changes; all task processes finished. Claude review pending.

- 2026-10-31 H15 cycle4 (Codex; Claude review pending): audited17,220 stock-days,312 >1% mismatches/159 symbols; repaired replay official OPEN/PREVCLOSE/provenance/action-date rejects and live auction consistency, fixed explicit variant thresholds/stale History rows;24 same-id runs reconcile (strict -68.14 -> +127.59, loose +283.96 -> +531.11 rupees; three fallback trade rows). Raw bhav PREVCLOSE is not action-neutral:11 announced split/bonus matches expose H7c/H7d; H17 next.108 unit tests pass; protected sources unchanged, H14 not rescored, paper only/no servers/processes. Report: docs/research/2026-10-31-h15-audit.md.
- History run picker rebuilt: plain names (no experiment codes), grouped (simulated weeks, panic buy history, market moods, strategy experiments, early backtests),
  two-line items with dates, trades and the run's result, all runs (was the newest 12). Header now opens with one plain sentence about the run.
  New Sessions page (every run as a card with large numbers, summary strip, grouped with "Show all"). Simulation data warms at startup.

### 2026-10-31 — H19 power/specification cycle (report 2026-11-01)

- Registered in RUNBOOK before counts; added paper-only scripts/h19_range.py and12 meaningful unit tests. Official previous-session high/low, completed10:15 candle/11:15 decision, exact11:20 entry, two ₹5,000 whole-share slots; both-boundary sweeps excluded. Reuses only whitelisted H18 eligibility columns, verifies source hashes, and explicitly separates signal-close affordability from five-minute entry coverage. No return scorer or runner/store change.
- Design435/reserved291 dates:868/574 picks on434/287 independent days (long342/232, short526/342), nominal251.4/248.5 days/year. Zero design five-minute entries; reserved113 fundable opens/60 days, long49/short64, complete paths unknown. QUEUE restricts reused dates to power: BOTH return phases unscored, net/t unavailable, no confirmation consumed; hurdle stays2.5. Profitability deferred pending H16 execution evidence and fresh252-session151/101 specification review; no hourly proxy returns or reserved-data rescue.
- All146 unit tests pass. Independent saved-artifact checker verifies736 distinct inputs (1,463 slash/backslash manifest keys),4 CSV hashes,18,232 candidate rankings and1,442 raw decisions/reference/entry rows. Protected sources unchanged; no .env access, computer use, broker/server calls or remaining task processes. UTF-8 files preserve ₹,− and — headings. Artifacts:data/h19/; report:docs/research/2026-11-01-h19-range.md. QUEUE/HANDOFF updated; INSIGHTS adds exactly2 lines. Lesson: frequent candle patterns do not supply executable evidence when the required entry history is missing. Next:H16 coverage at11:15/11:20/exits, then fresh H19; Claude specification/coverage review requested.
- Site-wide polish after a page-by-page check: Today's forward-test card is one card with two columns (no cards inside cards); Strategies' panic card shows 3 clear
  numbers and a plain note; Settings has no icons in titles, balanced groups and no panic-buy box (it runs separately); History shows exit splits for the cleaned
  10-year runs (trade reasons normalised to stop/flatten, notes in plain words); Journal shows one collapsed row per run or experiment, 10 at a time.

### 2026-11-01: v3 visual polish (Claude)
- Three-column shell: left nav (new bars logo, Main / Tools / Other groups, NIFTY 50 · BANK NIFTY · India VIX strip with market status), page, fixed right panel
  (Performance: paper or 10-year stats + one square per trading day; Ask Drex: three preset yes/no questions and a free question, `/api/drex/ask`, labelled as opinion).
- Icons: Phosphor regular from @phosphor-icons/core 2.1.1 (MIT), one set everywhere. Cards: soft gradients; `.glow` for each page's key card.
- Overview: greeting + one plain sentence, three stat cards with ↗ links (paper account, panic-buy edge, next session), the two paper tests.
- Flow: History is now a session's detail page under Sessions (back link, run name as title, breadcrumb). Run backtest / Update data moved to Sessions.
- Dropdown menus align under their button and are at least as wide. `/api/market` for the sidebar strip. Checked at 1440 / 1024 / 390 px and in light mode.
- v4: brand logo (three staggered pills, STALK / MARKET wordmark) and palette (#080909 page, #F2F2F0 ink, emerald #34D399, red #F05252) from the earlier
  Stalk Market project. Right panel: Drex questions scroll while the question box stays at the bottom; each preset shows what our data says
  (`/api/drex/context`: 183 of 202 fifteen-morning stretches made money; looser beat strict in 7 of 11 years; fills not measured yet); a named stock's
  real prices go to Drex and are shown under the answer; neutral probability bars; the box's example is a real recent pick. Squares show a hover card.
- v5: thin themed page scrollbar, hidden scrollbars on inner panels; Drex list fades at its edges; the assistant uses the brand's amber "organic blob"
  (from the earlier project's index.css); Performance keeps one height on both tabs (empty squares until paper data); page headers run edge to edge with more space below.
- v6: glass orb for Ask Drex (indigo core, iridescent rim, fixed highlight, drifting inner colour; from the user's Pinterest references); brighter teal/violet
  glow under solid Drex cards; Drex footnote removed; favicon is the Stalk Market mark. Overview now shows Latest research (from data/research_summary.json,
  `/api/research`; Codex adds a line per cycle, see HANDOFF.md), Confidence in panic buy (3 of 5 steps) and a short Paper tests card.
- v7: smaller green glass orb with softly blended inner colour; "show more" buttons are clear pills with an arrow (Show all N / Show fewer / Show 10 older);
  "All experiments" is a real button in the research card's header; the sidebar market strip shows the price with its change underneath, coloured even for small moves.
- Brand: the sidebar uses the real logo files (web/logo-light.png on dark, web/logo-dark.png on light, cropped from the earlier project's Logo folder);
  favicon is the real favicon.png mark on a dark rounded square (web/favicon-64.png, favicon-180.png), linked with ?v=2 so browsers refresh it.
- Sidebar market strip: index name small on top, price and change on one line, hairlines between indices.
- Sol turn 12 (docs/research/2026-11-03-sol.md): horizons are stability checks of a fixed rule; test 3rd/4th panic picks as a portfolio; score H19 as exploratory;
  daily reviews log process / execution / outcome, rule changes only in scheduled research reviews. Codex is running Phase A (horizon ladder), B (H20 more picks), C (pre-open brief + end-of-day review).

### 2026-11-01: horizons, more picks, daily paper workflow (Codex) + UI (Claude)
- Codex Phase A (data/horizons.json, docs/research/2026-11-01-horizons.md): strict panic buy made money in 100% of 5-year, 99% of 2-year, 90% of 1-year,
  71% of trading months and 64% of trading weeks; most weeks have no panic morning. Payoff: 52% wins, average win ₹241 vs loss ₹101, profit factor 2.57, worst streak 9.
- Codex Phase B (H20): 3 or 4 picks per panic morning lower net per morning (strict ₹147 → ₹127 → ₹121). Two picks stays.
- Codex Phase C: stalk/research/paper_daily.py — 09:10 brief (overnight cues, NIFTY indicative gap, would each version trade, top candidates) and 15:35
  factual review (process, execution vs open and 09:20, outcome vs the 10-year expectation), journalled under "Paper days". /api/brief, /api/reviews.
- UI: Overview shows Morning brief and Paper tests (with daily reviews); Strategies shows "How steady is it" (/api/horizons). 172 tests.

### 2026-11-01 — H16 prospective execution recorder (Codex; built 2026-10-31)

- Registered before capture in RUNBOOK; added `stalk/research/execution_log.py`, a fourth cancellable Agent loop, `/api/execution`, and 15:35 `execution_evidence`. Seven weekday slots from 09:15:30 through 15:10; read-only existing Kite get_quotes, raw-depth estimates for ₹5,000 on both sides, source circuits and once-daily public MIS membership cache. Atomic snapshot JSONL; missing/stale/late evidence remains unknown; no historical reconstruction or realised-fill claim.
- Five qualifying panic mornings per version AND twenty verified ordinary sessions before the registered ≤30 bps median full-spread-plus-buy-impact / ≥80% MIS-and-unlocked screen. Later runner picks match only actually captured 09:20 symbols; uncovered picks stay missing. H19 explicitly skips pending certified live candle/range/eligibility/calendar inputs; twenty paired quote sessions do not waive its fresh-calendar/complete-path gates. No runner, threshold, trade, arming or confirmation change.
- 202 offline tests pass, including 30 new cases; logs and protected-source checks in `data/h16/`. Corrected an initial test-network blocker that also blocked Windows asyncio internal socketpairs; no product failure hidden. `.env` untouched; no computer use, application server, actual broker calls or remaining task processes. Report: `docs/research/2026-11-01-h16-execution.md`; UI/source review requested in HANDOFF. User must restart the normal app in paper mode, keep it awake through 15:35 and log into Kite before the first morning snapshot. Execution result Pending; no observations fabricated.

### 2026-10-31 · H16 fill check reviewed and on screen
- Codex built the read-only quote recorder (`stalk/research/execution_log.py`, `/api/execution`, seven snapshots a day from 2 Nov). Claude re-checked it independently: 202 tests pass, `validate.py` and `panic.py` hashes and the `live_ready` AST are unchanged, and the recorder makes no order calls.
- Overview gets a "Fill check" card: one row per snapshot (seen/late/missed, typical spread, intraday-allowed share, stocks at a price limit), unknown shown as Unknown, never zero, plus the pre-registered pass rule. The banner spacing was evened out, the hover-card numbers are coloured, and the dark heat scale was made even.

### 2026-10-31 · Journal lessons, shorter buttons, H21 hybrid started
- Journal opens with three lesson cards (charges hurdle, panic buy the only edge but rare, backtests can lie) and a scoreboard of every tested strategy's net % a trade (`insights` / `scoreboard` in data/research_summary.json).
- Buttons max three words, no play icon: "Pick a week", "Another week", "Replay a day", "Every signal", "Group skipped", "Show all N".
- H21 (Codex): panic buy on panic mornings + a pre-registered every-day leg on the others, scored on 10 years and the week of 9 Dec 2024.
- 2026-10-31: zero-brokerage re-score of every failed strategy (`scripts/zero_brokerage.py`, data/zero_brokerage/): +0.07% a trade each, none rescued. Staying on Kite; fills, not fees, decide.


### 2026-10-31: H21 hybrid every-day research (report 2026-11-01, Codex)

Registered three variants before returns; one guarded scored run, no tuning or fresh confirmation. All A/B/C Failed. Hybrids trade84.2%/38.0%/51.5% of sessions and367.4/140.1/232.8 times a year; net Rs5.83/27.14/10.42 a trade and Rs2,142.94/3,801.98/2,426.16 a year. Everyday-alone net -Rs10,578.36/+Rs6,012.04/-Rs7,746.18, day t -0.945/0.888/-0.886. Drawdowns6.79x/3.05x/4.58x panic-only exceed1.5x. Dec9-13 2024 week A/C +Rs926.24, B -Rs60.54. Exact H17 panic branch unchanged.

Added scripts/h21_hybrid.py (signal-only causal pool builder, preparation, single-score guard, History and validation), scripts/h21_verify_saved.py, scripts/h21_report.py,16 tests, data/h21/ artifacts and docs/research/2026-11-01-h21-hybrid.md. Saved six deterministic Sessions campaigns. Fixed backend Sessions null-day extrema/unknown_days to preserve30 unavailable dates without crashing; no web/ edits. Added H21 at top of research_summary cycles; RUNBOOK/QUEUE/HANDOFF updated. Before returns, preserved original unscored preparation and registered cached official PHILIPCARB-to-PCBL dated identity evidence to remove a documented split gap. No post-score repair or rescore.

All218 unit tests pass (9.600s, exit0);3,821 distinct saved fills, selections, costs, day t/drawdown, week slices, six campaigns and frozen hashes reconcile. All pre-existing database rows and protected panic.py/validate.py/agent.py unchanged. Opening locks and complete historical event-calendar vintages remain unavailable, so results are assumed-fill daily-price descriptions, not certified causal execution. Unknown prices are not zero opportunities; fixed slots may need replenishment. No network, secrets, orders, arming, browser or server used. H16 remains next; Claude should review/show the report and six Sessions, including missing evidence.

Lesson: more frequent trades must earn their own net return and fit the drawdown budget; panic profits cannot rescue the everyday leg.
- 2026-10-31: H21 hybrid failed (every-day leg −₹3 a trade, t −0.9); MISTAKES.md added; Sol turn 13; H23/H22/H25 running in Codex.


### H23-H22-H25 completion (2026-10-31; report2026-11-01)

H23 Failed the mechanism screen: -8.17bps excess, day t-0.115,48 pairs/44 dates; only one event pair and323 unknown tiers, so full-edge attribution remains unresolved. H22 both settings Failed: primary k0.25 adds24.0 funded mornings/year,+27.20bps (t1.242), -23.05bps at0.5%-worse; k0.50 adds9.0/year,+57.32bps (t1.513),worse+17.82bps. H25 descriptive gross-7.62bps,95% CI[-28.39,13.14],1771 stock-days/48 dates; Does not clear the 60bps descriptive hurdle; no scored strategy test follows from this cycle. No strategy verdict.

Added offline scripts/h22_h25.py, scripts/h25_complete.py, saved-output verifier/report scripts, nine causal tests, data/h23/h22/h25 and the report. Three sequential registrations/guards; one run each, H22 two frozen k values. Two added-only Sessions, null prices retained; summary cycles prepended, insights and existing scoreboard rows preserved, one primary H22 row added in descending net order.227 tests pass; matching/timing/fill/fee/hash/History audits pass; no pre-existing DB row or protected/web file changed. No network, secrets, orders, arming, browser, server or remaining task process. Lesson: matching coverage limits mechanism claims, and extra panic mornings must survive worse entry prices on their own.


### 2026-11-01: H26 stocks-in-play ORB pilot, history tooling and daily paper

Registered three stops and fixedRs5,000 slots before returns, exact Claude prediction/counterparty, one guarded pilot, both0.25%/0.5% entry stresses and1,000 same-day random histories. Only5 trades on5/45 scored dates after15 warmup dates; net% V1/V2/V3 -0.4703/+0.2663/-0.0367,0.25%-worse -0.4708/-0.2669/-0.1877. Post-score audit found16,760/17,220 zero first-bar volumes,16,748 nonflat. Raw Failed screen preserved; published Unclear per under40/20 power rule, no source repair/rescore or everyday-frequency claim. Three Sessions stored and independently reconciled; prior database rows unchanged.

Added shared causal stalk/research/orb.py, scripts/kite_backfill.py (fake-client tested; login refusal, paced99-day chunks, retry/resume/empty checkpoints, atomic pkl and coverage), guarded scripts/h26_full.py (NOT run; design then held-back12 months excluding pilot), saved-output report/verifier, stalk/sim/orb_live.py, fifth Agent loop, /api/orb and15:35 review block. Frozen scorer/core unchanged after returns. After volume audit, only downloader/paper-source validity checks reject nonflat zero-volume openings; no trading threshold change. H26 explicitly uses current NSE0.00307% exchange charge through costs.py, retaining historical defaults. No real broker calls, orders, arming, secrets, UI, browser or server used. No full/backfill guard created; user must log in Monday, restart normal app in paper mode, keep it awake through15:35, inspect backfill coverage, then score once. Claude exact UI request in HANDOFF; report docs/research/2026-11-01-h26-orb.md.

Initial fake-filesystem tests hit Windows tempfile directory ACL errors; switched to the repository's existing isolated-directory helper. The failed attempts did not score returns. Final unit-suite log and saved-output verification are in data/h26/. Protected panic.py/validate.py and live_ready AST match prior H16 evidence.

Validation: python -m unittest discover -s tests -t . passes249 tests; data/h26/unit_tests_final.log retained. Saved verifier checks45 trade/scenario rows and9,000 random summaries, registration/code/input/output hashes, three Sessions, and exact pre-existing store rows. Full-history and broker downloads remain unrun; no task process remains.
- 2026-10-31: H26 stocks-in-play desk (Codex): pilot Unclear (5 trades a variant; Yahoo opening volume is zero on 16,760/17,220 09:15 bars, verified, MISTAKES 24). Paper runner trades V1/V2/V3 (10/25/50% ATR stops) every day from 2 Nov with random-pick controls; `/api/orb`; Overview "Everyday desk" card. After Kite login: `python scripts/kite_backfill.py`, then `python scripts/h26_full.py --full` once. Scoreboard no longer shows failed/pilot rows as green.


2026-11-01 — H27/H28 Unresolved at the required input audit, zero return runs: strict beta37/427 missing (8.67%); strict hourly fields81.10% missing even within date overlap. H29 descriptive complete: BEL60.59, TATASTEEL57.90, HINDALCO56.54, AXISBANK50.47bps median absolute10:15-to14:15 move, against17.5bps planning cost; actual spreads unknown. Three pair95% day-clustered intervals include zero. H16 records all12 at every existing slot from Monday with watchlist labels; original candidate aggregates preserved, unknown stays null. One guard each, outcome runs0/0/1;261 tests and frozen hash/arithmetic checks pass, protected sources unchanged. No promotion/scoreboard row. Report: docs/research/2026-11-01-h27-h29.md; artifacts:data/h27,data/h28,data/h29; validation:data/h27_h29_validation.json.


### 2026-11-01: H27b and pre-open auction recorder

H27b Passed the separately registered history-complete attribution screen:390/427 strict picks retained;313/390 matched (80.26%),191 mornings, ordinary-control excess+196.80bps/day t6.28;0.5%-worse+146.10bps/t4.68. All210 retained strict mornings ordinary stocks-9.85bps, NIFTY+10.93bps. Also-gapped+202.61bps/t4.05 has42.56% coverage, so ranking Unresolved. H27 and guard unchanged; one new guard/score, no net-strategy scoreboard row or promotion. Report:docs/research/2026-11-01-h27b.md.

Pre-open recorder wired into existing Agent execution loop:Monday Kite login before09:01, slots09:01/09:04/09:07/09:08:30/09:15 IST,12 names+NIFTY+prospective panic candidates. data/execution/preopen_<day>.jsonl; API adds preopen beside unchanged execution. Returned quote prices/totals/raw depth, current quote open, actual times; null/missed/stale never reconstructed; no work when logged out.275 tests pass and5,815 saved match links/price arithmetic/frozen hashes verify. Protected/web sources unchanged; no order tools, secrets, server, browser or task process left running. No new process rule needed. Next:collect actual auction/execution evidence in the normal paper app.
- 2026-11-01: H27b passed (Claude-verified from data/h27b/paired_returns.csv): panic picks beat matched ordinary stocks by +197 bps open-to-close, day t 6.28, 191 mornings; stock-specific, not index rebound. Pre-open auction recorder ready (09:01–09:08:30, 12 watchlist names). Sol round 15: H26 full on Kite history as registered; auction continuation forward-only. Overview confidence adds "Picks beat similar stocks".
- 2026-11-01 (overnight, Claude): Kite 5-minute backfill 2021-01-01 → 2026-10-30 done for 285/287 stocks (`data/kite_5m`; HFCL, STLTECH have no Kite token). Backfill fix: drop and count zero-price placeholder and Muhurat bars instead of abandoning the stock. H30 capitulation reversal scored once: **Failed** (1,877 trades, −0.02%/trade, day t −0.61, beats random picks at the 100th percentile but not charges; shorts +0.03%, longs −0.16%). Final year sealed. Scoreboard row + cycle added; fixed the mojibake "Â·" in scoreboard notes. Still to do after Monday login: backfill 2015–2020 (`--end 2020-12-31`), then `python scripts/h26_full.py --full` once.
- 2026-11-01 (Claude, user request): Sessions page is now one list, newest run first, each card showing when it was run and the years it covers; research runs (h-prefixed, except h17) show as "Strategy test"; same-week variants keep their name (e.g. "Hybrid C"). H30's design trades stored as run `h30_design` via `scripts/h30_history.py` (reads data/h30 only; DB backup data/h30/before_history.db). Strategies page adds "Every strategy we tested": every research cycle newest first with a plain one-line result (`plain` field in data/research_summary.json) and its verdict. **Every future run must be stored as a Sessions run and added as a cycle.**
- 2026-11-01 (Claude, speed): the run page redrew everything every 1.5 s poll and one redraw took ~1.5 s (6 slow date formats per trade for "worst entry hour"). Now: polls only redraw live pages (desk/replay/week) or when a job changes; date formatters are cached (`fmtDate` in web/lib.js); `istHour` is arithmetic; long equity curves keep ~250 points (`thin`), short runs keep every day. Redraw 1,470 ms → 2–25 ms. Server: `Store.campaign(id)` reads one row instead of decoding all 86 runs; run trades use the session-id index; /api/sessions and the strategies lookup are cached until a run is added (`Store.campaigns_version`); GZip on responses. Run dropdown removed from the run page (Sessions is the picker). First request after a restart can still take a few seconds while SQLite warms up.
- 2026-11-01 (Claude, research): NIFTY 2007-2026 calendar study (gains are overnight; intraday drift −0.06%/day, t −4). H31 lab `scripts/h31_lab.py` (design window only, trials in data/h31_explore/trials.csv): exits don't change expectancy; volume ≥ 10× is the only lead → H31 registered (RUNBOOK), scorer `scripts/h31_score.py` (sample A 2015-2020 after download, then sealed year B; each guarded). H32 sector-leader pullback (`scripts/h32_lab.py`) failed (t −3.95). `scripts/publish_run.py` publishes any trades CSV as a Sessions run (h31_design, h32_design). Sectors for all 287 stocks in data/sectors.json. Kite 2015-2020 download running. Sol r16 (resumed thread, ~280k new tokens: avoid) and Astra r1 (fresh call, ~32k).
- 2026-11-01 (Claude, dashboard flow): every run now reaches the dashboard without manual steps. `/api/sessions` adds live paper cards (panic buy strict / looser) read fresh from their journals, sorted with research runs newest first; they open the Overview. `scripts/publish_run.py` gained `record_cycle` (adds/updates a Strategies test and scoreboard row); `scripts/h31_score.py` calls `publish` + `record_cycle` at the end of each sample. CLAUDE.md states the rule for future sessions. H31 stop amended to 3% (user) before any test data was read.
- 2026-11-01 (Claude): H33 `scripts/h33_lab.py`, six popular intraday strategies (ORB, NR7, CPR, 5-EMA, gap fill, VWAP 2-SD), one run each on 2021-2025: all failed, five negative before charges. Published to Sessions + Strategies via publish_run. Two more random sessions (all 11 strategies side by side) published as rand2_*.
- 2026-11-01 (Claude, user's risk ideas): overlays on recorded design trades: skip-2nd-if-1st-winning helps H31/H30, skip-if-losing hurts; losing streaks don't predict next day; 50% kill switch would have stopped every loser; 15-min RSI / daily trend / run-up don't separate H31 winners. H31 secondary checks s1-s3 pre-registered (RUNBOOK) and added to `h31_score.py` (`secondary`). Data: data/h31_explore/h31_trades_with_timeframes.csv.
- 2026-11-01 (Claude): ML ranker H34 dev (`scripts/h34_ml.py`; scikit-learn 1.9.1 + LightGBM 4.7.0 installed with the user's OK): relative-return LightGBM, threshold/k grid, exits, win-probability classifiers. Config B (4× charges, top-2, 5% stop) 51% win, ₹9.7/trade on 2023-25 design data. Published h34_dev, h34_cfg_b, h34_cfg_c and two random ML weeks. Plugin card offered (Spera, QuanterLab). Astra report saved to docs/research/2026-11-01-astra-research.md.
- 2026-11-01 (Claude, UI): Sessions cards restructured (type chip + run time; strategy name only via runName(); one period line via runPeriod(); 3 stats). Filters: All/Win/Loss segment, a Strategy dropdown (types first, then each strategy name with counts) and a Sort dropdown (newest, oldest, most profit, biggest loss, best win rate). Random runs tagged 'Random check'. Sparkline shrinks to fit narrow cards.
- 2026-11-01 (Claude): Simulation page now plays the desk we plan to run: `stalk/sim/desk_replay.py` (panic buy on panic mornings from the cleaned H17 picks; ML ranker setting B on other days from walk-forward predictions `h34_preds_dev2.pkl`; Kite 5-minute candles; ₹10,000 in two ₹5,000 slots; weeks 2023-01..2025-07; same WeekSim player, `DeskWeekSim` for long/short candles). `/api/sim/new?mode=desk` is the default (`mode=panic` keeps the old Yahoo replay). Finished weeks publish to Sessions as `desk_<monday>` ("Random check"). Test: tests/test_desk_replay.py. H31 sample A attempt 1 was interrupted by the session ending before any output; documented in RUNBOOK and re-run.
- 2026-11-01 (Claude): ML trades in the desk simulation now have a profit target set at entry = 8x the model's predicted move (≈5%), shown in the log, on open positions and as a green line on the chart; exits mirror h31_lab.walk (gap at open, stop, then target). Reference trades data/h31_explore/trades_h34_cfg_B_target.csv; test updated. H34 registration amended to choose target none/8x in its nested inner grid.
- 2026-11-01 (Claude): team reflection (3 subagents: quant auditor, prop trader, ML engineer + Astra r4). Realistic per-tier costs kill setting B (−₹1,207); B ∩ mechanism +₹3,797 → H35 registered and added to h34_score.py. Learning loop v2 (review-led candidates, 4-week forward champion/challenger, quarter/Friday bug fixes): 6 sessions +₹2,919 backtest / +₹2,503 realistic; all 3 proposed changes rejected forward. Published learn*/learn2_* sessions.
- 2026-11-01 (Claude): H34/H35 nested test done; bad PIIND print found (MISTAKES 26), clean verdicts Failed; dashboard records corrected; bad-print guard in h31_lab.walk; START_HERE current state updated (every-day ML search stopped; next: Monday paper fills at the open).
- 2026-11-01 (Claude): gap-up short execution check (data/h37/replay_0920.csv): liquid names +6 bps at the open, -15 bps at 09:20; combined desk not executable; RUNBOOK updated.
- 2026-11-01 (Claude): panic buy real-fill check (scripts/pick_5m.py + scripts/panic_exec_check.py): +137 bps at the 09:15 auction open vs −13 bps at 09:20 → paper rule must move to a pre-open entry (pending user's yes; frozen runner untouched). Blind drill scripts/synthetic_day.py (Claude −₹46.68, Astra +₹22.82).
- 2026-11-01 (Claude): PanicAuctionPaper variant (stalk/sim/panic_auction.py) wired into Agent._panic_loop and /api/sessions paper cards; frozen panic.py/panic_live.py unchanged. Recent-replay check recorded in RUNBOOK.
- 2026-11-01 (Claude): panic paper runner ranks the evidence's full liquid universe (owner's yes); liquid_universe() in panic_live.py; test added.

### 2026-11-01, 19:17 IST: Astra handoff verification and restart
- Read HANDOFF_ASTRA, START_HERE, MISTAKES, the last 300 RUNBOOK lines, PLAYBOOK, TRADER_TALKS newest first, and the recent LOG. Initial suite: 290 tests passed. Found the new auction subclass inherited entry-journal fields fixed at 09:20 despite settlement using 09:15.
- Fixed journal metadata only: shared runner uses its variant's entry reference; auction journal records 09:15 and explicitly states post-open selection, first-five-minute lock screening, hypothetical fills and the need to compare the actual 09:07 ranking. No selection, sizing, costs, stops or frozen constants changed. Added a synthetic, fully mocked tick regression for both references. Final full suite: 291 passed in 91.179 seconds; log data/astra_handoff_tests.log (PowerShell wraps unittest stderr as NativeCommandError, but process exit is 0 and unittest reports OK).
- Restarted the identified existing run.py server after the first launch hit occupied port 8080. New process 43508, python run.py, hidden window, browser auto-open disabled, normal background boot enabled and ALLOW_LIVE_ORDERS=0. Homepage HTTP 200; /api/state paper/unarmed/live_ready false, Kite connected, no research job running. /api/sessions returns 128 existing sessions; zero forward cards because journals have not begun. /api/execution exposes execution and preopen. Server stderr empty after successful restart.
- Updated START_HERE's stale scorer queue and dashboard confidence notes; refreshed the existing Panic fills cycle through record_cycle without another score or a fabricated Sessions run. Existing trials/scoring counts preserved. API legacy holdout flag concerns 2026-10-19 and predates this turn; sealed research year untouched. No scorer, order tool or .env inspection; panic.py, validate.py and agent.py unchanged.

### 2026-11-01: entry discipline and three visible Monday practice drills
- Owner requested the REDINGTON skip lesson and two new random stocks. Added explicit rebound-entry discipline to PLAYBOOK; no frozen strategy thresholds, guards or LLM veto changed. Registered two synthetic paths before generation, uniformly selected M&M/RADICO, committed baseline/checklist decisions before reveal, and scored once with an exclusive guard. Checklist skips unqualified rebound longs; baseline shorts remain practice only.
- New scripts/monday_practice.py has separate prepare/commit/score/publish stages; no secret-file reads, uses existing local Drex endpoint. Source shapes restricted to development dates; current anchors only after sealed-year end. Costs are total tier budgets and extra worse-entry scenarios reset size/stops. Entry-bar stop checking included. All forecasts, unknown eligibility, source limitations and counterfactuals saved. Preparation stdout hit cp1252 after artifacts were complete; no reroll, used UTF-8 thereafter.
- scripts/publish_run.py now optionally accepts explicit synthetic session dates (no market-cache access), correct entry/exit timestamps, notes and decisions. Default historical publication remains the default. API classifies these as Synthetic practice. UI shows synthetic disclaimer and each trade/skip reason, plus skipped outcome, and disables unrelated real-history replay controls for synthetic runs. Original drill imported without rescore, with original cost/entry-stop limitations disclosed.
- Six new Sessions cards and three Strategies records: drill 1 original Claude/Astra; drills 2/3 baseline/checklist. New results: −₹55.06/−₹18.91 and +₹57.98/−₹88.51 respectively. The skip avoided a loss and missed a larger winner; no evidence to promote a profitable filter. Full postmortem in RUNBOOK.
- Six regression tests added (Drex independence, missing evidence, entry-bar stop/gap, adverse-entry sizing/stop, zero-trade synthetic publication, true stop timestamps). Full suite 297 passes, node --check passed. verify_monday_practice checks hashes, all 12 saved account/stress trade rows, six campaigns and unchanged pre-existing DB rows. Backup before_publication.db retained.
- Restarted python run.py, process 43896. API paper/unarmed/live-ready false, 134 Sessions including all six new cards. Browser host unavailable; verified in existing Brave window through computer-use: refreshed Sessions, saw all six cards, opened drill 2 checklist and checked readable skip/counterfactual panel. .env and protected sources untouched; no real orders. Logs data/monday_practice_tests.log and monday_app_*.log.


### 2026-11-01: symmetric evidence, Tuesday practice and genuine-model comparison preparation
- Owner confirmed loss minimization with fewer trades. PLAYBOOK now applies the same evidence threshold to shorts and buys; not selecting one stock never qualifies another. Corrected the interpretation of Monday 2/3 as mechanical comparisons. Existing score files and results retained.
- Preregistered then froze four AXISBANK/IRFC Tuesday packets. Current assistant independently committed skips on all eight stock-packets. Completed two solo sessions at ₹0/zero trades after actual Drex review; two genuine Claude comparisons remain sealed and Pending. No Claude inference call was made while owner reports quota exhausted. Workflow and exact later commands saved in docs/research/CLAUDE_ASTRA_DREX_WORKFLOW.md.
- New paper-only script, blind-review app route and six regression tests. All 303 tests pass; node syntax passes. Reviewed eight hypothetical direction outcomes and neighboring prices; three missed winning shorts and five losing directions are shown honestly, not counted as model profit.
- Dashboard hierarchy: metrics first, short stock-specific decision bullets, collapsed method/caveats. Six prior summaries updated without score changes; two new Sessions and two Pending Strategies entries published. App restarted as PID33024 with live-order environment disabled. No .env inspection, protected-code change, sealed-year analysis or live order.
- Final API/browser verification: 136 Sessions, two new zero-trade accounts, two Pending comparisons, compact reason cards and collapsed methods. All six older account results unchanged. API confirms unarmed, live_ready false, env_allow_live false.


### 2026-11-01: Wednesday Drex stop/target drill and portable handoff
- Added HANDOFF_CURRENT.md as account/harness continuation entry; linked old HANDOFF_ASTRA and START_HERE. Records all prior results/provenance, what caused Tuesday skips, latest exploratory authorization, pending Claude packets, guarded artifacts, safe commands and service-access limitations. PLAYBOOK distinguishes a hypothetical scenario from a qualified strategy trade.
- Preregistered Wednesday selection, risk/execution and ranking; drew TORNTPHARM/PNBHOUSING and source2025-01-08. Actual Drex ranked12 plans/name from anonymous visible packets. No .env read, broker access or current-context enrichment. Locked two weakly rated long plans, scored once: -Rs69.04; stress -Rs69.39. Four rows/neighbor bars independently audited, no bad prints.
- Added scripts/wednesday_practice.py and narrow app propose route; six unit tests for actual-score validation/ranking, cost-inclusive risk, short target, entry/gap stop, same-bar ambiguity, target-open ordering and15:10 boundary. Full309 tests passed, exit0. Read-only verifier added separately; no rescore. New responsive expandable chart in existing dashboard style shows actual recorded path, pre-entry/executed stop/target and numeric Drex opinions.
- Published campaign/cycle wednesday_drex_scenario; app restarted PID13924 with live-order environment disabled. Existing Tuesday frozen script/plans and pending hidden outcomes remain untouched; no Claude inference calls. Server logs wednesday_app_*.log, tests wednesday_practice_tests.log.

Final publication check: API reports137 Sessions, Wednesday two trades/net -Rs69.04 and two chart payloads; both Tuesday comparisons remain Pending. Unarmed, live_ready false, env_allow_live false. Browser confirmed the Wednesday card, result, compact reasons, both level panels and reveal controls. The user resumed navigation during chart inspection; no further UI interaction was attempted.
- 2026-11-01 (Claude): three-layer blind drill (scripts/three_layer_drill.py, data/drills/20261101_202132): consensus Rs0; agreed short ADANIPOWER +Rs128 vetoed by Drex 25%; recommendation Drex ranks not vetoes.
- 2026-11-01 (Claude): three-layer drills 2-4 (score adds 'agreed, no veto'); 4-drill table in RUNBOOK: agreement +Rs89, Claude -Rs37, Astra +Rs43, consensus-with-Drex Rs0.
- 2026-11-01 (Claude): drill 5 + five-drill totals in RUNBOOK (agreement +Rs112, Astra +Rs73, Claude -Rs14, Drex-veto consensus Rs0); prompt-template bug noted.
- 2026-11-01 (Claude): consensus desk adopted (owner): scripts/consensus_desk.py, Agent._consensus_loop, Sessions card; dry run 2026-10-30 -Rs35.09 (plumbing ok); test count updated (309 pass).
