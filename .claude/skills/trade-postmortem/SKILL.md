---
name: trade-postmortem
description: After a campaign, replay or live paper session, classify each Stalk Market trade's outcome, check process and risk discipline, and write a dated RUNBOOK.md entry with one next experiment. Use at the end of every session or campaign.
---

# Trade post-mortem (Stalk Market)

Method adapted from `signal-postmortem` and `trade-performance-coach` in github.com/tradermonty/claude-trading-skills
(MIT License, Copyright (c) 2026 TraderMonty). Their scripts and FMP API are not used: the data is already in
`data/stalk.db` (tables `trades`, `sessions`, `campaigns`, `trials`).

This is a process review. It never recommends a specific stock to buy or sell.

## Steps

1. **Collect** the session's or campaign's trades:
   `python -c "from stalk.data.store import Store; ..."` or `GET /api/campaigns/latest` and `/api/campaigns/<id>/trades?account=...`.
2. **Classify each trade**
   - `TRUE_POSITIVE`: hit target, or exited positive
   - `FALSE_POSITIVE`: stopped out
   - `COST_LOSS`: gross positive but net negative after charges
   - `REGIME_MISMATCH`: lost while NIFTY moved hard against the side (check `idx_rday`)
   - `MISSED`: a blocked setup (risk gate) that would have won; count by block reason
3. **Process and risk discipline** (from the engine logs and settings):
   - was every rule followed (trade cap, loss halt, cooldown, midday lull)?
   - `size_creep`: risk per trade or notional rising after wins
   - `rule_drift`: settings changed mid-campaign without a RUNBOOK entry
4. **Numbers to report**: trades, net ₹, charges ₹ and charges in R, mean R with t, hit rate,
   Time Machine predicted vs realised, and the trials count so far.
5. **Write the RUNBOOK.md entry** (dated): what ran, the numbers, the classification counts, and
   **one** next experiment that names the metric it should move and the result that would reject it.
   Settings are changed only one at a time, and a change is kept only if out-of-sample t improves and the plateau holds.

Minimum sample: no conclusion from fewer than 20 trades; say "not enough trades" instead.
