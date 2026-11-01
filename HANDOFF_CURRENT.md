# Current handoff — read this first in any account or harness

Updated 2026-11-01. Workspace: `D:\Trading Agent`, Windows PowerShell, Python 3.12. App: http://localhost:8080. This file is the portable continuation point; older `HANDOFF_ASTRA.md` is historical and its initial test/restart task is already complete. Chat history is not required to continue.

## Owner intent and the correction that matters

- Paper trading only. Owner prefers minimizing losses, accepts fewer trades and missed winners, but also wants exploratory simulations rather than endless abstention.
- Owner wants genuine independent Claude/current-assistant comparisons, followed by Drex review. Do not label scripted baseline decisions as Claude or pretend to have called an unavailable model. “Astra” is the conversation label for the current assistant; preserve actual provider/model metadata where available rather than inferring a model identity from that label.
- All runs, including losses, skips and pending comparisons, belong on the dashboard. Use concise decision bullets, results first, detailed methods collapsed. Speak plainly in rupees.
- The Tuesday AXISBANK/IRFC skips were caused by missing shared evidence and an overly restrictive verified-setup requirement in our packet design. Popularity was not the reason. That exercise tested abstention under limited information, not a fully informed trading desk. Do not imply that those stocks can never offer an opportunity.
- Latest explicit authorization: choose any stocks for a Wednesday exploratory what-if, with no large-gap restriction, ask actual Drex to suggest stop/target plans, simulate and visualize them. This is permission for hypothetical practice beyond panic eligibility, not permission to change or arm a live strategy. Exploratory ideas may be tested without pretending their edge is verified.

## Read next and hard boundaries

Read `START_HERE.md`, `MISTAKES.md`, newest `RUNBOOK.md` entries, `PLAYBOOK.md`, newest `docs/research/TRADER_TALKS.md`, then the workflow guide linked below.

Never place/modify/arm real orders, invoke Kite order tools, expose credentials, or open/print `.env`. The existing app loads its configured environment during normal boot; use its narrow local Drex routes rather than inspecting keys. No secret values are in this handoff. A different machine/account will need its own authorized service access; these documents do not transfer authentication.

Do not change `stalk/sim/panic.py`, `stalk/research/validate.py`, `stalk/agent.py::live_ready`, or the sealed 2025-08-01..2026-07-31 research year. Do not rerun guarded H31/H34/H35/H36/H37 or completed practice scorers. Existing research failures stay failed. Preregister new experiments before outcomes, include tier costs and worse-fill sensitivity, audit trades and neighboring bars, publish all results. No production Drex veto is added by these drills. Older documents proposing one are historical, superseded guidance.

## What actually happened

| Exercise | Actual provenance | Result after its disclosed costs |
|---|---|---|
| Monday 1, REDINGTON/POONAWALLA | Saved independent Claude vs Astra decisions | Claude −₹46.68; Astra +₹22.82 by skipping REDINGTON |
| Monday 2, M&M/RADICO | Mechanical fade baseline vs rebound-long checklist | Baseline −₹55.06; checklist −₹18.91 |
| Monday 3, M&M/RADICO | Same mechanical comparison, second hidden path | Baseline +₹57.98; checklist −₹88.51 |
| Tuesday solo 1/2, AXISBANK/IRFC | Current assistant manually committed decisions; actual Drex critique | Both skipped both stocks: ₹0, zero participation. Three profitable hypothetical shorts missed across the four stock-days |
| Tuesday comparison 1/2 | Current-assistant decisions committed; actual Claude absent | Pending. Both outcome files remain sealed; no score, no Claude inference attempt |
| Wednesday, TORNTPHARM/PNBHOUSING | Actual Drex ranked 12 direction/stop/target candidates per stock | Two longs stopped: −₹23.68 and −₹45.36, total −₹69.04; worse entry −₹69.39 |

Original Monday 1 used different costs and omitted entry-bar stops. Later drills use total tier friction budgets and entry-bar checks. These are small, reused-history exercises with differing designs, not an apples-to-apples model leaderboard. Do not add them up to claim a strategy edge.

M&M lesson: a tiny five-session rise did not justify shorting. Rejecting RADICO did not qualify M&M. RADICO's skip avoided one loss and missed a larger winner. Apply evidence discipline symmetrically to both sides, while clearly separating an exploratory what-if from a qualified production setup.

## Latest Wednesday drill — completed once

Two names were uniformly drawn from 279 cached wide-meta names excluding the six already practiced. One common Wednesday was uniformly drawn from 72 eligible dates; after reveal it was **2025-01-08**. No outcome redraw. Synthetic display date **2026-11-04** is a clock label, not a forecast.

Drex saw anonymous A/B, prior20 OHLC/movement summaries, the first four completed candles, aggregate first20-minute volume and its prior20 mean, cost/risk assumptions and explicit missing market/news/events. No ticker, source date, hidden path, current-day context or credentials were sent. Volume audit: 0 missing/zero among 3,008 input bars. Universe/tier metadata is current and survivorship-biased; this is practice on reused design history, not fresh validation.

The script supplied 12 candidates: long/short × three volatility-scaled stop distances × two target multiples. It chose the actual Drex highest quality score, then target-first opinion, then candidate ID. Thus “Drex plan” means its ranking of a disclosed grid, not an unconstrained price forecast or free-text explanation. Its API returned numeric opinions, so do not invent a rationale and attribute it to Drex.

| Stock | Side / shares | Entry | Stop | Target | Exit bar | Net |
|---|---|---:|---:|---:|---|---:|
| TORNTPHARM | Long / 1 | ₹3,385.10 | ₹3,366.50 | ₹3,413.00 | 10:20 stop | −₹23.68 |
| PNBHOUSING | Long / 4 | ₹875.45 | ₹867.61 | ₹887.21 | 10:25 stop | −₹45.36 |

Drex quality was weak: 2.3545/5 and 2.4318/5; target-first opinions 0.1689/0.2372. Both stocks later remained at/below the pre-entry reference at 10:30, 12:00 and 15:10, despite Drex assigning above-reference probabilities over 0.6. Six correlated observations are not a calibration sample. Both trades lost before costs; gross −₹49.95, costs ₹19.09. No ambiguous dual-touch bar or stop gap occurred in this draw. Stops are intrabar events; the recorded exit time identifies the five-minute bar, not the exact tick.

Entry09:35, flat15:10, maximum ₹5,000 per stock and planned stop plus total costs ≤₹50; gaps can exceed that. Tier costs: 0.15% large/Next50, 0.25% mid, 0.40% small. Entry worse by0.5% re-anchors both levels and resizes. Unresolved stop/target double touch counts stop first. No rule changes after reveal. Two FALSE_POSITIVE stopped trades; not enough trades to judge an edge. Highest-ranked among weak plans is not a strong recommendation.

## Files and safe commands

- `scripts/wednesday_practice.py`: **frozen**, completed prepare/propose/score. Never repeat those stages. `publish` only republishes saved results.
- `data/synthetic/wednesday_20261101/`: draw, mapping, visible, sealed (now revealed), manifest, registration, frozen scorer, actual Drex A/B input/questions/answers, plans/commit, started guard, result and verification.
- `python scripts/verify_wednesday_practice.py`: read-only audit; safe to run. Does not rescore.
- `scripts/tuesday_practice.py`: **frozen**; do not edit because pending packets verify its hash.
- `data/synthetic/tuesday_20261101/tuesday_compare_1` and `.../tuesday_compare_2`: do not open `sealed.json` before actual Claude has independently committed. Current assistant has already committed; do not replace its plans.
- `docs/research/CLAUDE_ASTRA_DREX_WORKFLOW.md`: exact Claude/resume commands. CLI execution is prepared but not yet tested against available subscription access. Owner reported Claude quota unavailable; no paid alternate provider or impersonation was used. No automatic retry or scheduled job exists.
- `scripts/monday_practice.py` and its saved scorer: frozen completed experiment. `scripts/verify_monday_practice.py` is its read-only audit, but older counts may predate newer publications.
- `scripts/publish_run.py`: `publish(..., kind='synthetic', session_days=[...], decisions=[...])` plus `record_cycle`. Pending comparisons use Pending Strategies records, not fake zero-profit Sessions.
- Wednesday campaign ID: `wednesday_drex_scenario`. Its summary contains `practice_charts`: actual recorded paths, planned/reference versus executed levels and Drex timed opinions. `web/app.js` draws expandable charts; no generated price line is misrepresented as Drex output.

App was restarted after Python changes as PID13924 with live-order environment disabled. Verify the current listener identity before restarting; PID can change. Unit suite: **309 passed**, exit0, 95.352 seconds; `data/wednesday_practice_tests.log`. JS syntax check passed. Run `python -m unittest discover -s tests -t .` after relevant changes; don't repeatedly score experiments to test code. Server logs: `data/wednesday_app_stdout.log`, `data/wednesday_app_stderr.log`.

## What remains, and what not to infer

1. When Claude is available, complete the two existing Tuesday comparisons using the exact guide. No need to prepare a replacement dataset. Both real plans must precede anonymous Drex review and the single score; the agreement account trades only when direction matches and both claim sufficient evidence.
2. Forward paper execution recording from Monday2Nov remains the useful strategy evidence: four strict/looser ×09:20/auction-reference variants and the09:07 pre-open ranking. User logs into Kite before09:00 and keeps the laptop awake through15:35. Auction-reference journals are hypothetical post-open selections, not proof of executable pre-open fills.
3. Future exploratory sessions may use richer shared market/news/event context and arbitrary stocks under explicit preregistration. Never require every exploratory what-if to satisfy the panic production rule, and never promote an exploratory plan because one draw won.

To switch harnesses, keep this workspace and point the new agent to this file. Do not transfer secrets into a chat, export, or handoff. Durable conclusions and artifacts are in the repository, not only in conversation memory.

Final publication verification: 137 Sessions, Wednesday2 trades/net -Rs69.04, two chart payloads and both Tuesday comparisons Pending. API unarmed/live_ready false/env_allow_live false. Browser confirmed published session, levels and reveal controls. Expanded plot visual inspection was interrupted by user navigation; no further desktop interaction was attempted.
