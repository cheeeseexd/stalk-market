# Stalk Market

Intraday NSE paper-trading app for a ₹10,000 Zerodha MIS account: rule strategies taken from published theses,
validated on stored history, played bar by bar through one engine for research, campaigns, replay and live paper.
Claude is the researcher and reviewer; no order waits on an LLM. Drex only votes.

**Read `START_HERE.md` first** (current state, next steps, every workflow), **then `MISTAKES.md` and `LOG.md`** (also `PLAYBOOK.md` for the trading rules, `INSIGHTS.md` for everything learned so far, `RULES.md`, `RESEARCH_LOG.md`, and `JOURNAL.md` for the daily sessions) (architecture, how to run, results, gaps, next steps, changelog), then `RUNBOOK.md` (the trading journal).
Append to both when you finish.

- Run: `python run.py` (http://localhost:8080) · Campaign: `python scripts/campaign.py` · Tests: `python -m unittest discover -s tests -t .`
- Keep features causal. Research and the engine share `stalk/research/trade.py`; `ParityTests` must keep passing.
- Never weaken the validation rule (`stalk/research/validate.py`), the holdout, or the live guards in `stalk/agent.py::live_ready`.
- Change one setting per experiment and log it in RUNBOOK.md with the trials count. Don't open the holdout without a written prediction.
- UI: one font (`var(--font)`), colours only as tokens in `web/styles.css`, money only in ₹ (`en-IN`), 36px controls / 24px chips.
  Layout: left nav · page · fixed right panel (performance + Ask Drex). One icon set: Phosphor regular (`web/icons.js`), in the nav, buttons and card corners.
  One `.glow` card per page for what matters most; a plain sentence before numbers. Render server state only.
- **Every run goes on the dashboard, newest first.** A scorer ends by calling `scripts/publish_run.py` (`publish` → Sessions,
  `record_cycle` → Strategies, with a plain one-line result); `scripts/h31_score.py` shows the pattern. Live paper tests
  appear on Sessions by themselves (read from their journals by `/api/sessions`). Restart the server after Python changes.
- Skills in `.claude/skills/`: `backtest-review` and `strategy-critique` (run as two independent reviewers before any real order),
  `trade-postmortem` (after each session).
- `archive/nexusquant/` is the old app, kept for reference only. Don't import from it.
