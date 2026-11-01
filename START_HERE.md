# Start here: Stalk Market handoff

**Switching accounts or harnesses:** start with [HANDOFF_CURRENT.md](HANDOFF_CURRENT.md). It records user intent, actual model provenance, every recent drill, frozen artifacts and the exact unfinished work.

For any new Claude (or other agent) picking this project up. Read this first, then `MISTAKES.md`, then the newest
entries at the bottom of `RUNBOOK.md` and `LOG.md`. Update the **Current state** section below whenever it changes.

## What this is
A paper-trading research app for a ₹10,000 Zerodha MIS account (NSE cash equity, intraday). Claude researches and
reviews; no order ever waits on an AI. Nothing trades real money; real orders are blocked by guards in
`stalk/agent.py::live_ready` (never weaken them). The user now prioritizes minimizing losses, accepting fewer trades and missed winners, honesty about luck,
plain short messages, rupees only, and every run visible on the dashboard.

## Newest first (2026-11-01, night, Claude)
- **Pre-mortem fixes** (RUNBOOK "Monday pre-mortem"): the consensus desk now enters at the first 5-minute bar *after* both models decide
  (`entry_hm` in its journal; it used to assume 09:35, a fill we could never get); the panic runners retry a late NIFTY 09:15 bar
  until 09:40 and skip a ticker whose bars fail instead of stalling. 311 tests pass. Three open gaps are listed there (check Monday's bid/ask).
- **Public copy:** GitHub `cheeeseexd/stalk-market` (code, docs, `DIARY.md` = the owner's own story) and a read-only dashboard at
  https://stalk-market-paper-desk.netlify.app (`scripts/snapshot_site.py` refreshes `snapshot/`; deploy `netlify deploy --prod --dir dist`).
  `.gitignore` is an allow-list: new top-level files stay private until added to it.
- **Nothing has traded yet, not even on paper.** Every positive number is a backtest or a drill. The first evidence is Monday 2 Nov.

**Your first job (in order):** before 09:00 the owner restarts `python run.py` and logs into Kite from the app → the app runs 4 panic
versions + the consensus desk by itself → after 15:35 read `data/panic_paper*.jsonl` and `data/consensus_paper.jsonl`, compare them
(entry vs official open, slippage, net, empty bid/ask?), write a `trade-postmortem` entry in RUNBOOK.md, publish, update this section.
On non-panic days panic writes a "skip"; that is correct, not a bug. Don't invent new strategies until ~30 paper trades exist.

### Continuing on another machine (from the GitHub copy)
The repo has **no `data/`** (Kite candles, NSE bhavcopy, `stalk.db`, journals) and **no `.env`**. To run: `pip install -r requirements.txt`,
create your own `.env` (Drex key, optional), `python run.py`, log into Kite from the app. The live paper runners fetch what they need
(NSE pre-open, previous-day bhavcopy, Yahoo/Kite candles), so they work on a fresh machine. Research scripts and data-dependent tests
need the data rebuilt (`scripts/kite_backfill.py`, `scripts/panic_bhav.py` downloads bhavcopies); those tests skip without it.
Guards for completed one-time scorers live in `data/`, so on a fresh machine **re-read RUNBOOK before running any scorer: never re-score
a test RUNBOOK already records as done**. Codex (Astra) and the Claude CLI must be logged in on that machine for the consensus desk;
without them it writes a "skip".

## Current state (2026-11-01, Astra handoff)
**Latest update: portable handoff and Wednesday Drex scenario completed.** Read HANDOFF_CURRENT.md for full continuity. Owner allowed any stocks and non-gap exploratory plans; Tuesday skips came from limited evidence plus restrictive packet design, not stock popularity. Wednesday draw TORNTPHARM/PNBHOUSING: actual Drex ranked 12 stop/target candidates each, highest plans were weak (~2.4/5), both longs stopped. Net -Rs69.04 (gross -Rs49.95, costs Rs19.09); worse-entry -Rs69.39. Saved result and expandable path/level charts on dashboard; no strategy promotion. Two existing Tuesday Claude comparisons remain Pending and sealed, current-assistant plans already committed. All 309 tests pass, app restarted PID13924 with live orders disabled. Frozen rules and earlier scored artifacts unchanged.

| Strategy | Status | Numbers |
|---|---|---|
| **Panic buy** (NIFTY opens ≤ −0.5%: buy the 2 biggest no-news gap-down small/midcaps, 3% stop, out 15:10) | **Passed**, on paper from Mon 2 Nov | +1.68% a trade over 10 years; ~22 mornings a year |
| **H31 volume-climax fade** (short stocks up ≥ 3% at 09:30 on ≥ 10× volume, bar closes in its lower half, 3% stop, out 15:10, max 2/day) | **Failed (2015-2020)** | ≈ ₹0 after charges over 982 trades |
| **H34 ML ranker** (LightGBM ranks stocks vs the day's average at 09:35; top 2 when confident; 5% stop) | **Failed (2017-2020 fresh test)** | Signal replicated (₹8.18 a trade vs ₹7.95 dev) but fails 0.25%-worse fills; ≈ ₹0 at realistic costs |
| **H35 mechanism-gated ML desk** (ML picks kept only if they fade a 5%+ non-small-cap spike or buy a real 1.5%+ gap-down; realistic costs) | **Failed (2017-2020)** | 225 trades, ₹3.59 a trade at realistic costs, 1 of 4 years positive |
| **H36 euphoria short / H37 every-morning gap-up short** (short the 2 biggest no-news gap-ups at the open) | Failed gates, but real | 10 years: +0.42% a trade over 4,594 trades (t 5.6) at harsh costs; turns negative with a 0.5%-worse entry. Edge lives in the opening-auction price |
| H30 capitulation, H32 sector leader, H33 six popular setups (ORB, NR7, CPR, 5-EMA, gap fill, VWAP 2-SD) | Failed | All lose; most even before charges |

Facts that shape everything: NIFTY's gains are overnight (+0.107%/night, t 10); intraday drift is −0.06%/day (t −4).
Exits don't create edge (they trade win rate for win size); only stock selection moves results. **Realistic costs** (0.15% large / 0.25% mid / 0.40% small-cap round trip) turn ML setting B from +₹6,793 to −₹1,207: always score with them. The ML ranker only adds value where a known mechanism agrees (team reflection 2026-11-01, RUNBOOK).

**Paper setup:** four versions run together: strict/looser × frozen 09:20/auction-reference entry. All rank the expanded liquid EQ universe authorized by the owner, plus the stored list. Auction entries are hypothetical 09:15 bar-open references selected after the open, including the first-five-minute lock check; they do not demonstrate an executable pre-open decision or realised fill. Their entry journals now identify the correct 09:15 reference and this limitation. Frozen rule constants remain unchanged.

**Handoff verification (2026-11-01, 19:17 IST):** all 291 unit tests pass (`data/astra_handoff_tests.log`). App restarted with `python run.py` at http://localhost:8080, process 43508; API confirms paper mode, unarmed, live-ready false, and live-order environment disabled. Kite connected at this check; log in again Monday before 09:00. Dashboard and execution/pre-open endpoints respond. No forward paper journals exist yet, so their Sessions cards appear after observations. The API's existing opened holdout flag refers to the legacy 2026-10-19 window (already documented in LOG.md), not the sealed 2025-08..2026-07 research year; neither flag nor protected code was changed.

**Consensus desk (adopted 2026-11-01, paper):** each morning Opus + Astra pick blind, only agreed trades (≤ 2) are paper-traded, Drex ranks only — `scripts/consensus_desk.py`, runs from `Agent._consensus_loop`; journal `data/consensus_paper.jsonl`. Review it beside the panic journals.

**Next steps, in order (HANDOFF_ASTRA.md supersedes the old scorer queue):**
1. Monday 2 Nov onward: log into Kite from the app before 09:00 and keep the laptop awake through 15:35. Collect all four paper journals and H16 pre-open/quote observations.
2. After each panic morning: compare entries, slippage and net across the four journals; compare the actual 09:07 indicative ranking with the final auction ranking; write a trade postmortem in RUNBOOK.md. Missing observations remain unknown. About 30 paper trades are the next checkpoint; assumed auction prices alone cannot establish actual fill reliability.
3. H31 A and H34/H35/H36/H37 are completed failures: never rerun their guarded scorers. The every-day ML search is stopped. The sealed year 2025-08-01..2026-07-31 stays unopened and single-use. The earlier instructions here to run H31 B or repeat H34 are superseded.
4. Optional new research must be pre-registered on unused observations. The next gap-up-short evidence should come from pre-open recordings, not another historical backtest. Switching the frozen 09:20 rule to a pre-open decision remains an open owner decision.

## How to run things
- App: `python run.py` → http://localhost:8080 (or preview "stalk" in `.claude/launch.json`). **Restart after Python changes.**
- Tests: `python -m unittest discover -s tests -t .` (must pass).
- Kite: the user logs in from the app's Overview ("Connect Kite"); the login lasts until 06:00 next day and drops if Kite
  MCP is logged in elsewhere. Check with `python -c "import asyncio;from stalk.data.kite_mcp import KiteMCP;print(asyncio.run(KiteMCP().check()))"`.
- Data download (resumable, read-only): `python scripts/kite_backfill.py --start YYYY-MM-DD --end YYYY-MM-DD`.
- Codex second opinions: fresh calls only (resuming old threads re-sends ~1.5M tokens).
  `codex exec -m gpt-6-astra -s read-only --skip-git-repo-check -C <scratch dir> --json -o reply.txt - < task.txt`.
  Track usage from the `"usage"` blocks in the events file.
- Drex (quick yes/no opinions, never evidence): `python scripts/drex_rate.py` → `data/drex_ratings.json`.

## Research workflow (never skip a step)
1. Explore only on the design window 2021-01-01..2025-07-31 (`scripts/h31_lab.py`, `h32_lab.py`, `h33_lab.py`; arrays cached in
   `data/h31_explore/B_*.npy`, rebuild with `--build`). Every variant is logged with a trial number.
2. Freeze the rule and write a registration in `RUNBOOK.md` (rule, counterparty, pass gates, prediction) BEFORE opening fresh data.
3. Score once on fresh data (2015-2020 replication, then the sealed year 2025-08..2026-07) with a guard file.
4. Publish: every scorer ends with `scripts/publish_run.py` → `publish()` (Sessions page) and `record_cycle()` (Strategies page,
   with a plain one-line `plain` result and a scoreboard row). Never leave a run off the dashboard.
5. Log: append to `RUNBOOK.md` (results, trials), `LOG.md` (engineering), `docs/research/TRADER_TALKS.md` (lessons), and
   update **Current state** here.

## Where things are
| Path | What |
|---|---|
| `data/kite_5m/*.pkl` | Kite 5-minute bars, 287 stocks (2021→ complete; 2015-2020 downloading) + `coverage.json`, `checkpoint.json` |
| `data/h17/*_cleaned_stop3_account.csv` | Panic buy's 10-year trades |
| `data/h31_explore/` | Lab arrays, trial logs (`trials*.csv`), trade files per strategy |
| `data/research_summary.json` | Feeds the Strategies page and the Journal scoreboard |
| `data/stalk.db` | SQLite: runs (`campaigns`), sessions, trades (Sessions page) |
| `data/sectors.json` | Sector for every stock |
| `stalk/api/server.py` | API (`/api/sessions` also shows live paper tests from their journals) |
| `web/app.js`, `web/lib.js`, `web/styles.css` | Dashboard (design frozen; tokens only; one font; ₹ only) |
| `PLAYBOOK.md` | Trading rules, incl. "Desk rules: kept and removed" |

## Changing the dashboard
Simulation page = `stalk/sim/desk_replay.py` (the desk we plan to run: panic buy on panic mornings, ML ranker B otherwise; random week 2023-01..2025-07; finished weeks publish to Sessions). When the desk's rules change, change `desk_replay.py` and `tests/test_desk_replay.py` together.
Pages: Overview (`renderDesk`), Sessions (`renderSessions`), Simulation/run page (`renderSimulate`), Strategies
(`renderStrategies`, table "Every strategy we tested" from `research_summary.cycles`), Journal. Keep: one `.glow` card per
page, colours only as tokens in `web/styles.css`, 36px controls / 24px chips, Phosphor icons. Polls redraw only live pages;
long charts are thinned with `thin()`. After edits, check in the browser preview and restart the server for Python changes.

## Never
Place or arm real orders; call Kite order tools; open or print `.env`; weaken `validate.py`, the holdout or `live_ready`;
edit frozen `stalk/sim/panic.py` without a passed test and the user's yes; re-run a guarded scorer; tune on fresh data.
