# Independent Tuesday practice

Owner priority: minimize losses, accept fewer trades and missed winners. Paper only.

Two solo drills are complete. Two comparison packets remain sealed, with the current assistant's decisions already committed. Claude has not been called because the owner reported its usage unavailable. No synthetic Claude decisions are substituted. Drex successfully reviewed both solo plans using only their visible packets.

The earlier Monday drills 2/3 were a mechanical baseline versus a checklist, not new independent Claude/Astra decisions. The original Monday drill 1 was the saved model comparison. Preserve all results and their different execution/cost limitations.

## Fair comparison

1. Same masked-date Tuesday packet to each model: previous prices, prior movement statistics, completed 09:15–09:30 candles, costs and explicit missing evidence. Each submits buy/short/skip with observations, rationale, missing evidence and invalidation.
2. Commit each plan before revealing its peer or any future candles. The current assistant is labelled honestly; the Claude runner stores the actual CLI response and any model identifiers it reports.
3. Drex reviews anonymous plans A/B with only that packet. Its opinion cannot modify the locked positions. It is an evidence critique, not a calibrated probability of profit.
4. The agreement account trades only when both choose the same non-skip direction and both declare sufficient evidence. Otherwise it skips.
5. Score all three accounts once on the same prices and cost model. Report net, costs, participation, skipped hypothetical losses and missed winners. Both hypothetical directions are disclosed; neither is credited as a prediction.

## Execution and limitations

AXISBANK and IRFC were uniformly drawn from the preregistered bank and railway pools. Four dates were drawn without replacement from 79 common eligible Tuesdays, using design history 2024-01-01 through 2025-07-31. Same-day paths preserve the pair's correlation. This is reused historical practice, not unseen validation or a forecast for Tuesday 3 November. That displayed date is only a synthetic clock.

Entry 09:35; fixed 1% stop; exit 15:10; whole shares; maximum ₹5,000 per stock and planned stop plus tier cost budget at most ₹50. Gaps can exceed that budget. Cost budgets are total round trip: 0.15% large/Next50, 0.25% mid, 0.40% small. Additional 0.5%-worse entry resets sizing and stops. These risk settings are drill assumptions, not optimized rules or changes to the frozen panic strategy.

The visible packets deliberately disclose missing market/news eligibility and lack of verified directional evidence. These drills can assess discipline under limited information; they cannot establish who forecasts better or test a fully informed trading desk. Future forward comparisons need contemporaneous market, event, execution and eligibility evidence supplied identically to both models, registered before outcomes.

## Resume when Claude is available

From `D:\Trading Agent`, with the existing app running:

```powershell
python scripts/tuesday_practice.py claude tuesday_compare_1
python scripts/tuesday_practice.py review tuesday_compare_1
python scripts/tuesday_practice.py score tuesday_compare_1
python scripts/tuesday_practice.py publish tuesday_compare_1

python scripts/tuesday_practice.py claude tuesday_compare_2
python scripts/tuesday_practice.py review tuesday_compare_2
python scripts/tuesday_practice.py score tuesday_compare_2
python scripts/tuesday_practice.py publish tuesday_compare_2
```

Stop at any failed command. Inspect the error and saved status; do not bypass an attempt/score guard, substitute a model, reroll data or hand-edit a committed plan. The `claude` command uses the installed official CLI in an isolated temporary working directory, with tools and MCP disabled and session persistence off. It has not yet been exercised against an available Claude subscription; authentication/quota or structured-response errors remain possible. Do not use `--bare`, which would bypass subscription authentication. No credential file is opened by this workflow.

Preparation, commits and scores are one-time operations. Do not rerun `prepare` or score completed solo packets. `publish` only publishes saved results; pending comparisons create Pending Strategies entries, not fake zero-profit Sessions. Frozen scorer and registration copies, manifests, committed plans, reviews, result files and verification are in `data/synthetic/tuesday_20261101/`. Do not open reserved `sealed.json` files before both plans are locked.
