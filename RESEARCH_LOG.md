# Research log

Every piece of research, newest first: what it was, what we took from it, and what happened when we tested it.
Status: **adopted** (in the code), **tested, rejected**, **noted** (not tested yet), or **proposed**.
Detailed notes live in `docs/research/`; trading results live in RUNBOOK.md (experiments) and JOURNAL.md (daily sessions).

| Date | Source | What it said | What we did | Status |
|---|---|---|---|---|
| 03 Nov | Edge map, 28k NIFTY 50 stock-days (`scripts/edge_map.py`) | Simple direction signals make 1–7 bps vs a 17 bps hurdle | Explains every failure so far | adopted (diagnosis) |
| 03 Nov | Global cues, 6 years (`scripts/premarket_cues.py`) | US explains NIFTY's gap (0.52), not the day (−0.06); crude, gold, rupee nothing | No pre-market directional layer | tested, rejected |
| 03 Nov | Zarattini & Aziz "stocks in play" ORB idea (`scripts/in_play.py`) | Opening relative volume picks movers | H1: predicts size, not direction, on 287 NSE stocks | tested, rejected |
| 03 Nov | Post-earnings drift literature (Bernard & Thomas; Brandt et al. 2008) | Results surprises drift | H2/H3 intraday: passed on NIFTY 50, failed on 240 unseen stocks. H5 multi-day: gross drift, eaten by ₹10k delivery costs | tested, rejected |
| 03 Nov | Your method: multi-timeframe agreement (`scripts/alignment.py`) | Daily trend + first hour + NIFTY agreeing = confidence | H4: +3.7 bps | tested, rejected |
| 03 Nov | Overreaction / liquidity provision (found in H6, `scripts/panic_buy.py`) | Non-news overshoots on broad sell-off mornings recover | H7 passed untouched 2021–23 test (+100 bps, t 2.8); forward paper test running | adopted for forward test |
| 02 Nov | Sol, turns 4–6: options vs stocks, method, UI | Options don't fix a missing edge; filter tests on path P&L are noise; judge on the signal pool, freeze, beat the 95th percentile of random selectors; UI: hierarchy and density, not more styling | Choppy-mood filter (its structure axis) tested: −₹994 vs −₹400, rejected. Method changed. UI rebuilt around Today / History | adopted (method, UI), rejected (chop) |
| 02 Nov | SEBI F&O studies; NSE lot sizes (NIFTY 65 from Jan 2026) | ~91% of individual F&O traders lost money in FY25; one NIFTY lot is far too big for ₹10k | Stay on stock intraday; record the NIFTY chain for later | noted |
| 02 Nov | Conversation with GPT-6.1 Sol via Codex CLI (`docs/research/2026-11-02-sol.md`) | Real risk is ₹55, not ₹100 (no-leverage cap), so charges are about 18% of risk; filters may be one correlated factor; test the selector against random picks, frozen and forward | Its relative-volume idea was tested at once on 27k signals: no added value. Random-pick benchmark is next | noted / partly tested |
| 02 Nov | NSE corporate announcements API (free, timestamped) | 4,131 filings for 40 stocks; 2,361 routine | News layer: block trades against net-bad or net-good company news. Keywords −₹304, Drex-judged +₹152, vs +₹226 without | tested, rejected (data kept) |
| 02 Nov | Drex as a news judge (`scripts/label_news.py`) | 1,770 material filings: 351 good, 43 bad, 1,376 neutral | Clearly better than keywords (fewer false "bad" calls), but the layer still didn't beat the stack | adopted for labelling, layer rejected |
| 02 Nov | NSE corporate board meetings API (free, nseindia.com) | 1,149 board meetings for our 40 stocks since Nov 2023, with the date each was announced | Events layer: skip stocks with a meeting the day before, of or after (only if announced before that morning). −₹509 vs −₹666 baseline | adopted (layer) |
| 02 Nov | Market breadth (our own 40 stocks) | Share of stocks above VWAP on each bar | Breadth layer: −₹225 vs −₹666; first layer positive before charges | adopted (layer) |
| 02 Nov | Sector peers (sector map of the 40 stocks) | Average move of the stock's sector today | Sector layer: −₹512 vs −₹666 | adopted (layer) |
| 02 Nov | Factor-agreement ranking | Pick the clashing setup with the most factors agreeing | +₹154 vs −₹666, the best single layer | adopted (layer) |
| 02 Nov | India VIX (Yahoo ^INDIAVIX) | Stand aside after fear spikes | −₹685 vs −₹666, no help in this window | tested, rejected |
| 02 Nov | Drex factor checklist | 5 checks + quality score, veto only | −₹336 vs −₹666, but the worst day got worse; its "against the market" vetoes averaged −₹48 | tested, rejected by the rule (promising) |
| 02 Nov | PE / fundamentals | Yahoo gives only today's PE; no free point-in-time history | Can't be backtested honestly; usable later as a live watchlist filter only | noted |
| 01 Nov | Drex decision API (redesign) | The 31 Oct test gave Drex almost no information (side, strategy, time, our own odds) | Designed a factor checklist with veto power only: richer state (technicals, index, sector, VIX, PE, headlines), 5 yes/no checks and a score, measured in a day-by-day month (`docs/research/2026-11-01-drex.md`) | proposed |
| 01 Nov | [Dexter (virattt)](https://github.com/virattt/dexter) | Open-source financial-research agent; no trading; US-centred data | Could later fetch news and fundamentals for Drex's state | noted |
| 01 Nov | ["I Gave AI ₹10 Lakh to Trade" (Trading With Sidhant)](https://www.youtube.com/watch?v=M8QHS7l69eQ) | 5 rules: fixed small risk, stop never moved, no averaging, no leverage, 3 losses → 3 days off | RULES.md; run B on 632 unseen sessions halved the loss and drawdown | adopted |
| 01 Nov | Same video, daily-chart swing | Hold winners for days, stop beyond yesterday's high or low | `scripts/swing_test.py`, 5 years: −₹6,912, stops too tight for daily bars | tested, rejected |
| 01 Nov | [SEBI intraday study (FY23)](https://www.business-standard.com/markets/news/over-70-intra-day-traders-incur-losses-during-fy23-reveals-sebi-study-124072401110_1.html) | 71% of intraday traders lose; 80% of heavy traders; charges add 57% to losses | Matches our finding that charges are the whole loss; one trade a day | adopted (as a constraint) |
| 01 Nov | [Groww: 6-year Nifty VWAP backtest](https://www.youtube.com/watch?v=6f53ODFClJk) | VWAP alone isn't an edge; picking the best variant afterwards is curve-fitting | Kept the plateau rule and trials ledger | adopted (method) |
| 01 Nov | Indian blogs: Supertrend, previous-day breakout, CPR, EMA | 58–68% win rates claimed, no costs or method | Supertrend and previous-day breakout tested on 632 sessions: both lost | tested, rejected |
| 01 Nov | [vijaxx/algo-trading-system](https://github.com/vijaxx/algo-trading-system) | NSE backtester; results on synthetic data only | Confirmed our charge model | noted |
| 01 Nov | Yahoo hourly bars (2 years, free) | 724 sessions of history | Long unseen test; exposed stops too wide to ever hit | adopted (data) |
| 31 Oct | [quants.md](https://quants.md/) O15/W07 | Only positive intraday rules there are RSI mean reversion (US ETFs) | `rsi_reversion`: lost on NSE at every setting | tested, rejected |
| 31 Oct | [day-trader-skill (MIT)](https://github.com/Jakeschincariol/day-trader-skill) | Time Machine odds, PASS/BLOCK risk gate | Ported both; the Time Machine isn't calibrated yet | adopted |
| 31 Oct | [claude-code-quant-skills](https://github.com/shakeebshaan/claude-code-quant-skills), [tradermonty skills](https://github.com/tradermonty/claude-trading-skills) | Backtest review, strategy critique, post-mortem | `.claude/skills/` (3 skills) | adopted |
| 31 Oct | Drex decision API | Scored 77 backtest trades | Brier no better than the base rate; off by default | tested, rejected |
| 30 Oct | [@shivanshco](https://x.com/shivanshco/status/2115064439972249723) | Claude on Nifty options, range breakouts, RUNBOOK habit | Runbook + journal; ORB tested: loses on NIFTY 50 stocks | adopted (process), rejected (ORB) |
| 30 Oct | [@pranjalsoni_](https://x.com/pranjalsoni_/status/2116104700282175971) | Two reviewers, paper beside live, broker-side stop, static IP | Shadow account, safe exits, static-IP guard; 52-week momentum tested: unproven | adopted / tested |
| 30 Oct | [Ganesh S, ₹5,000 test](https://sganesh17.substack.com/p/i-gave-claude-5000-to-day-trade-and) | Gap-up shorts lost 23%; news gaps | Gap fade: positive on 57 days, flat on 632 unseen sessions (luck) | tested, rejected |
| 30 Oct | [skill-algotrader](https://github.com/javajack/skill-algotrader), [agents-trade](https://github.com/MrSpideyNihal/agents-trade) | Reconcile on startup, tick size, midday lull, ≤ 2 trades a day, close-only breaks | All in the risk gate | adopted |
| 30 Oct | [dev.to hidden holdout](https://dev.to/hawkbtcommander/i-let-claude-code-search-for-a-trading-strategy-but-hid-the-last-two-years-of-data-from-it-48ee) | Count trials, hold out data, predict first | Holdout and trials ledger; predictions written in RUNBOOK | adopted |
| 30 Oct | [@RohOnChain / QuantGPT](https://x.com/RohOnChain/status/2109650293365305427) | Promo; plateau and "no edge" verdicts are useful | Plateau rule | adopted (method) |
| 30 Oct | SEBI retail-algo rules (April 2026) | Static IP for API orders | Live guard | adopted |

## Open questions to research next
- Free NSE sources for results calendars, corporate announcements and FII/DII flows (for the news and sentiment layer).
- Sector rotation: do some sectors trend intraday more than others in our data?
- India VIX as a regime filter.
