# Stalk Market

An AI-assisted **paper-trading** research desk for a ₹10,000 Zerodha intraday (MIS) account on the NSE.
Claude, GPT-6 Astra and Drex research and review strategies; every idea is tested on years of history with realistic
Indian costs, and only what passes goes to paper trading. **No real orders are placed.**

- **Start with [DIARY.md](DIARY.md)**: the whole story so far, in my words.
- **Live read-only dashboard:** a saved copy of the app, hosted on Netlify (link in the repo's About box).

## Where things stand (1 Nov 2026)

| Strategy | Status |
|---|---|
| Panic buy: on mornings NIFTY opens ≤ −0.5%, buy the 2 biggest no-news gap-down small/midcaps, 3% stop, out at 15:10 | **Passed** (10 years, +1.68% a trade after costs). Paper from 2 Nov |
| Consensus desk: Opus and Astra pick blind; only trades both agree on, Drex ranks | Paper experiment (5 drills, +₹112) |
| H30–H37: capitulation, volume-climax fade, sector leader, 6 popular setups, ML ranker, gap-up shorts | Failed after realistic costs |

## What's in this repo

| Path | What |
|---|---|
| [DIARY.md](DIARY.md) | My diary of the project |
| [START_HERE.md](START_HERE.md), [HANDOFF_CURRENT.md](HANDOFF_CURRENT.md) | Handoff for any AI or person continuing the project |
| [PLAYBOOK.md](PLAYBOOK.md), [RULES.md](RULES.md) | The trading rules |
| [RUNBOOK.md](RUNBOOK.md) | Every experiment: registration, prediction, result |
| [INSIGHTS.md](INSIGHTS.md), [MISTAKES.md](MISTAKES.md) | What we learned and the mistakes not to repeat |
| [LOG.md](LOG.md), [JOURNAL.md](JOURNAL.md), [RESEARCH_LOG.md](RESEARCH_LOG.md) | Engineering log, daily session journal, research sources |
| `docs/research/` | Research reports and AI debates |
| `stalk/` | The app: engine, broker sim, panic-buy runner, API (FastAPI) |
| `web/` | The dashboard (plain HTML/CSS/JS) |
| `scripts/` | Research, scoring and paper-desk scripts |
| `tests/` | Unit tests |
| `snapshot/` | Saved API answers that power the read-only Netlify copy |
| `.claude/skills/` | Review skills: backtest-review, strategy-critique, trade-postmortem |

**Not included:** market data (Zerodha/Kite candles, NSE files), the database, API keys and `.env`. Tests that need the
stored data skip themselves.

## Run it yourself

```bash
pip install -r requirements.txt
python run.py            # http://localhost:8080
python -m unittest discover -s tests -t .
```

You need your own `.env` (Drex key, optional) and your own Kite login for live candles. Research scripts rebuild their data
with the fetch/backfill scripts (for example `scripts/kite_backfill.py`).

To refresh the read-only website copy: run the app, then `python scripts/snapshot_site.py`, commit `snapshot/`, and Netlify
rebuilds `dist/`.

## Safety

Paper only. Real orders are blocked by `stalk/agent.py::live_ready`. This is not investment advice.
