# Codex final test of panic buy

Requested report label: 3 November 2026. Executed on 31 October 2026, the environment date. Historical data window: November 2016 to October 2026. Pre-registration and results are appended in [RUNBOOK.md](../../RUNBOOK.md). This report concerns paper research only.

**Verdict: proceed to the Monday paper trial as-is.** H7d passes all three pre-registered numerical gates. Do not change the frozen rule to a winning grid neighbour. This supports measuring the existing paper runner; it does not establish executable profitability or authorize real orders. H8 does not qualify for confirmation. Both research scripts are separate from the application.

**The single biggest remaining risk is execution at 09:20:** extreme final opening gaps can be stale prints, circuit-bound stocks or instruments unavailable for MIS. Ranking after observing the open cannot guarantee buying at that open. Daily bhavcopy cannot measure the first-five-minute lock screen, executable spreads, or which lows occurred before the actual entry. The existing +1% entry stress helps, but is not a substitute for quotes and fills. Some of the largest historical winners opened about 20% down and recovered sharply.

H7d is a reconstruction on reused history, not another untouched confirmation. It has full file coverage: 351/351 grid mornings, including 218 base panic mornings, with no invalid eligible prices. Two base mornings (2 January 2018 and 15 June 2020) had no qualifying stock; 216 active mornings produced 429 ranked trades. The account bought whole shares in 422 of those trades and leaves the other allocation idle.

The universe is point-in-time EQ-series bhavcopy with previous-session value at least ₹10 crore and previous close at least ₹50, excluding the repo's fixed 40 names. This avoids choosing only today's surviving wider-list companies, but does not reconstruct historical NIFTY 50 membership or historical MIS availability. EQ also includes ETFs: JUNIORBEES is among large panic wins. Cached +/-3-calendar-day results sets lack announcement timestamps, so their availability on the historical morning cannot be established. Full coverage means files and set-valued calendars exist; it does not certify that an empty historical calendar response is complete. The unchanged executable runner instead uses its fixed wider list, tier slippage and a different results window. These differences limit transfer of the research result to that runner.

**Selection versus luck.** All results below use the registered daily stop proxy: 3% stop plus 20 bps overshoot, 30.6 bps costs, and an 8 bps allowance on nonstopped close exits. This produces **+164.24 bps per trade, day t=5.83**. The old +187.45 bps headline is reproduced by the separate no-stop, official-close calculation; it is not the account's stopped return.

Each of 2,000 seed-7004 random draws takes up to two stocks without replacement from each day's same eligible <-2% gap pool, keeping market days and selected trade counts fixed. Random history means at the 5th/50th/95th percentiles are **+22.21/+45.62/+68.39 bps**. The rule beats all 2,000 draws, above the empirical 95th percentile; finite Monte Carlo upper-tail p=(1+0)/2,001=0.00050. The observed rank is 100% of simulated draws, not a literal zero probability of luck. The all-pool trade-weighted mean is +59.18 bps; it differs from the random mean because days with large pools receive more weight. Any eligible gap-down had a positive pooled average here, but selecting the deepest gaps added substantial return. This is conditional on the observed history and pool, not a test against every strategy tried in the project.

**Neighbour grid.** All **36/36** cells satisfy net >0 and day t>=2, versus the required 24. Net means span +118.21 to +312.18 bps and day t spans 4.36 to 6.56. Positive full years are 9 or 10 out of 10. This is a broad positive region, with stronger historical means for deeper market selloffs, rather than a narrow peak at the base thresholds. Cells overlap heavily and are not 36 independent replications. The base row is -0.5% / -2% / 2 picks. No row changes the paper rule. Return comparisons use equal conceptual allocation within ₹10,000; only the base account simulation below enforces whole shares.

| NIFTY gap below | Stock gap below | Picks | Trades | Net bps | Day t | Positive years / 10 |
|---|---|---:|---:|---:|---:|---:|
| -0.3% | -1.5% | 1 | 350 | +157.1 | 5.22 | 9 |
| -0.3% | -1.5% | 2 | 698 | +127.5 | 6.06 | 10 |
| -0.3% | -1.5% | 3 | 1044 | +118.2 | 6.56 | 10 |
| -0.3% | -2.0% | 1 | 348 | +158.4 | 5.24 | 9 |
| -0.3% | -2.0% | 2 | 686 | +129.4 | 6.03 | 10 |
| -0.3% | -2.0% | 3 | 1013 | +121.5 | 6.47 | 9 |
| -0.3% | -3.0% | 1 | 321 | +172.0 | 5.36 | 9 |
| -0.3% | -3.0% | 2 | 595 | +144.4 | 6.01 | 10 |
| -0.3% | -3.0% | 3 | 823 | +139.2 | 6.28 | 9 |
| -0.5% | -1.5% | 1 | 217 | +193.8 | 5.19 | 9 |
| -0.5% | -1.5% | 2 | 434 | +162.4 | 5.84 | 9 |
| -0.5% | -1.5% | 3 | 650 | +152.1 | 6.27 | 9 |
| -0.5% | -2.0% | 1 | 216 | +196.3 | 5.25 | 9 |
| -0.5% | -2.0% | 2 | 429 | +164.2 | 5.83 | 9 |
| -0.5% | -2.0% | 3 | 639 | +155.3 | 6.24 | 9 |
| -0.5% | -3.0% | 1 | 206 | +205.3 | 5.29 | 9 |
| -0.5% | -3.0% | 2 | 397 | +177.6 | 5.88 | 9 |
| -0.5% | -3.0% | 3 | 566 | +169.4 | 6.10 | 9 |
| -0.7% | -1.5% | 1 | 149 | +212.8 | 4.61 | 9 |
| -0.7% | -1.5% | 2 | 298 | +193.8 | 5.29 | 9 |
| -0.7% | -1.5% | 3 | 447 | +178.6 | 5.57 | 9 |
| -0.7% | -2.0% | 1 | 149 | +212.8 | 4.61 | 9 |
| -0.7% | -2.0% | 2 | 298 | +193.8 | 5.29 | 9 |
| -0.7% | -2.0% | 3 | 447 | +178.6 | 5.57 | 9 |
| -0.7% | -3.0% | 1 | 146 | +216.4 | 4.60 | 9 |
| -0.7% | -3.0% | 2 | 287 | +202.1 | 5.25 | 9 |
| -0.7% | -3.0% | 3 | 414 | +188.2 | 5.35 | 9 |
| -1.0% | -1.5% | 1 | 76 | +312.2 | 4.36 | 9 |
| -1.0% | -1.5% | 2 | 152 | +282.6 | 4.78 | 9 |
| -1.0% | -1.5% | 3 | 228 | +258.5 | 5.02 | 9 |
| -1.0% | -2.0% | 1 | 76 | +312.2 | 4.36 | 9 |
| -1.0% | -2.0% | 2 | 152 | +282.6 | 4.78 | 9 |
| -1.0% | -2.0% | 3 | 228 | +258.5 | 5.02 | 9 |
| -1.0% | -3.0% | 1 | 76 | +312.2 | 4.36 | 9 |
| -1.0% | -3.0% | 2 | 151 | +285.0 | 4.79 | 9 |
| -1.0% | -3.0% | 3 | 222 | +263.2 | 4.94 | 9 |

**₹10,000 account and annual bootstrap.** The account uses ₹5,000 per ranked stock, whole shares, no leverage or compounding, 10 bps entry and exit slippage, 3% stop below slipped entry, another 20 bps adverse stop exit, and an 8 bps allowance versus the official close. Charges use the existing cost model. Stops are not guaranteed and losses can exceed the playbook's ₹300 daily figure. Results are before tax; changing fee schedules, funding constraints following drawdowns and circuit exits are not reconstructed.

Total account net is **+₹31,579.74**, or +₹3,158 a year. All 422 trades reconcile to the earlier account journal; maximum per-trade difference is less than ₹0.005 due to its rounding. The account model is distinct from the fixed-cost return proxy used for selection and the grid.

The 5,000 seed-7005 pooled morning bootstraps include zero-trade panic mornings, and draw 21 or 22 mornings per synthetic year to preserve the observed 21.8/year frequency. Annual net 5th/50th/95th percentiles are **+₹435 / +₹3,066 / +₹6,119**; losing-year frequency is **2.68%**. The 5th percentile clears the registered -₹1,500 gate. These are synthetic stationary-year outcomes, not confidence bounds on the true expected return. IID resampling breaks crisis clustering and mixes strong and weak regimes. Observed losing years were 1/10 on the full November-October grouping; that is a separate descriptive measure, not contradicted by a different synthetic-year model.

Below are the fixed-rule descriptive walk-forward results, with each year's own mornings independently bootstrapped 5,000 times. Years end in October and begin the previous November; earlier reports used 11 partial calendar-year groups. Year-specific intervals are conditional on a small year's observed sample and are also optimistic about regime uncertainty.

| Year ending October | Panic mornings | Ranked trades | Net bps | Account net | Bootstrap 5th / 50th / 95th | Losing probability |
|---|---:|---:|---:|---:|---|---:|
| 2017 | 9 | 18 | +205.9 | ₹+1,781 | ₹+45 / ₹+1,739 / ₹+3,767 | 4.6% |
| 2018 | 17 | 32 | +132.4 | ₹+866 | ₹-1,341 / ₹+823 / ₹+3,195 | 27.0% |
| 2019 | 11 | 22 | +10.2 | ₹+128 | ₹-1,427 / ₹+67 / ₹+1,766 | 47.2% |
| 2020 | 34 | 66 | +181.6 | ₹+5,436 | ₹+1,195 / ₹+5,248 / ₹+10,110 | 1.4% |
| 2021 | 20 | 39 | +190.8 | ₹+3,511 | ₹+1,214 / ₹+3,462 / ₹+5,736 | 0.3% |
| 2022 | 49 | 98 | +157.6 | ₹+7,564 | ₹+4,085 / ₹+7,563 / ₹+11,431 | 0.0% |
| 2023 | 14 | 26 | -38.0 | ₹-446 | ₹-1,667 / ₹-505 / ₹+955 | 72.1% |
| 2024 | 15 | 30 | +260.0 | ₹+3,658 | ₹+1,591 / ₹+3,560 / ₹+5,981 | 0.1% |
| 2025 | 16 | 32 | +444.4 | ₹+6,410 | ₹+3,685 / ₹+6,337 / ₹+9,340 | 0.0% |
| 2026 | 33 | 66 | +96.8 | ₹+2,672 | ₹-415 / ₹+2,653 / ₹+6,173 | 8.0% |

The weak November 2022 to October 2023 year has a -₹1,667 bootstrap 5th percentile and 72.1% chance of a loss under its own empirical distribution. The pooled 2.68% loss estimate should not be treated as the probability of loss in every future regime.

The first seven years have +138.97 bps mean within-day return versus +221.96 bps in the last three; the difference is +82.99 bps with Welch t=1.30. Average annual account P&L is +₹2,692 versus +₹4,246. There is no aggregate decay signal, nor strong evidence of improvement. The final year alone (+96.8 bps) is weaker than the preceding two; do not conceal that behind the pooled result. These periods were already used in research and are not independent forward evidence.

For the paper trial, preserve the existing ranking time, sizing, stop and exit. Use the existing five-qualifying-morning review to inspect feed reliability, rejected/locked picks, entry differences versus official open and quote availability. Five mornings can debug execution, not validate expected profit; the existing 15-morning profit review remains a separate, noisy checkpoint. No short trial automatically authorizes real orders. This research did not launch the app or change any trading controls.

**Two non-panic ideas, ranked as research proposals.**

1. Broad-up-morning extreme gap-up exhaustion (H8): short the two largest non-results gaps above 3% when NIFTY opens at least 0.5% up. The possible edge is unusually concentrated opening buying pressure rather than ordinary large-cap direction prediction. H6's generic gap-up fades lost, so the market-wide and extreme-rank condition needs independent evidence. Thin names, upper circuits, ETF opening prints and MIS short availability are serious obstacles. The design result below is insufficient.
2. Timestamped non-results hard-catalyst continuation: restrict to unexpected, definitive cash-flow events such as a cash tender/buyback announcement or binding regulatory decision, with a pre-defined taxonomy and entry after public dissemination. A large discrete repricing could clear a 17-25 bps hurdle, unlike the 1-7 bps technical signals. Scheduled earnings continuation, generic news vetoes, opening volume alone and delivery drift have already failed; this idea needs a new event-specific dataset and has no measured edge here. A first study would need contemporaneous documents, timestamps, dissemination lags, executable quotes and controls, not retrospective good-news labels.

Neither is promised to make money or provide daily activity. In the absence of evidence, ordinary days remain no-trade days for this candidate.

**H8 design only.** Pre-registered before looking at its outcomes. The fixed sample uses the original seed-7 set of 200 non-panic control days, restricted to November 2016 to October 2021 and NIFTY gap >=+0.5%. All 13 sampled market mornings have cached coverage. The rule shorts the two biggest >3% non-results stock gaps with a 3% stop. Daily short proxy uses 30.6 bps costs, an 8 bps early-exit allowance and 20 bps adverse stop overshoot. The whole-share account charges the correct sell-entry/buy-exit sides and assumes 10 bps slippage each side.

| Stock gap above | Trades | Active mornings | Net bps | Day t | Account net | Best day share |
|---|---:|---:|---:|---:|---:|---:|
| 2% | 26 | 13 | +151.2 | 1.16 | ₹+1,877 | 69.6% |
| 3% | 24 | 12 | +157.5 | 1.11 | ₹+1,799 | 72.4% |
| 4% | 15 | 8 | +246.2 | 1.34 | ₹+1,835 | 74.1% |

The main cell is +188.1 bps gross and +157.5 bps net, but has only 24 trades / 12 mornings, day t=1.11 and best-day share 72.4%. It fails the >=40 trades / >=20 mornings, t>=2.5 and <=25% concentration criteria. Positive neighbours cannot rescue it. **Inconclusive sparse design, no confirmation justified.** The main-cell largest winner is the ETF KOTAKNIFTY on 17 March 2020: official open ₹123.96 versus previous close ₹96.58, closing ₹95.29, yielding +22.7% proxy net and +₹1,130 account P&L. Its opening print and shortability make the optimistic design particularly unconvincing. No post-hoc instrument exclusions were used to improve the result.

Untouched confirmation is explicitly reserved for the **first 60 consecutive NIFTY >=+0.5% opening-gap sessions from 4 November 2026 onward**, with contemporaneous EQ universe, publicly known results schedules, 09:20 quotes and historical-at-that-time MIS availability. This future set avoids calling already-used history untouched. Confirmation is not collected or scored in these scripts. It would require >=40 executable trades, >=30 bps mean net, day t>=2.5, positive 2%/4% neighbours, <=25% best-day concentration and median measured slippage <=15 bps per side. Missing evidence prevents a pass. Today's design does not earn promotion to that set.

Reproduce H7d with `python scripts/panic_final.py --fetch` (omit `--fetch` for cached-only replay), and H8 with `python scripts/euphoria_design.py`. Exact pre-registration is in RUNBOOK.md. Artifacts: [H7d summary](../../data/panic_final/summary.json), [grid](../../data/panic_final/grid.csv), [annual table](../../data/panic_final/years.csv), [coverage](../../data/panic_final/coverage.csv), [account trades](../../data/panic_final/account.csv), [H8 design](../../data/euphoria_design/summary.json). NSE bhavcopy and board-meeting retrieval reuse `scripts/panic_bhav.py`; the analysis does not independently validate the source's historical calendar completeness.

Validation: `python -m unittest discover -s tests -t .` passed all 46 tests after each code addition. Account parity against the existing rounded journal also passed. Frozen panic rule, validation rule, engine holdout and live-order controls were not modified; no credential files were opened and no application runner was started.
