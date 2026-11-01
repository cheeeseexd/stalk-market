**Verdict: the numbers are real and reproducible. A dependable trading edge is not established.** Claude’s arithmetic checks out; any claim that this will reliably make money is unsupported.

I rebuilt all 248,088 feature rows, retrained the three annual models, and replayed both trade files entirely in memory. Features matched exactly; predictions and trades matched to floating-point precision. No files were changed or holdout tests run.

| Recomputed result | A: 3% stop | B: 5% stop |
|---|---:|---:|
| Trades | 862 | 862 |
| Winners | 412 | 437 |
| Win rate | 47.795824% | 50.696056% |
| Net/trade | ₹9.226442 | ₹9.715722 |
| Total net | ₹7,953.19 | ₹8,374.95 |
| Green traded weeks | 68/133 | 72/133 = 54.1353% |

Both contain identical picks, entries and quantities, spanning **2023-01-02 through 2025-07-31**. B’s annual profits are **₹2,616.60, ₹4,735.39 and ₹1,022.96**.

The leakage audit has mixed results:

- **Real look-ahead in eligibility.** [h30_capitulation.py:43–57](</D:/Trading Agent/scripts/h30_capitulation.py:43>) defines `complete` using the current day’s eventual bar count and last timestamp, then requires `eligible = complete & coherent ...`. At 09:35 you cannot know that the day will contain ≥70 bars and reach 15:25. This contaminates candidate selection and cross-sectional features. Its profit impact remains unmeasured.
- **Another retrospective filter:** [h31_lab.py:114](</D:/Trading Agent/scripts/h31_lab.py:114>) requires the entire future price path to be finite or returns `None`. However, none of B’s 924 ranked candidates was dropped for this: all 62 omissions were shares exceeding the ₹5,000 budget.
- **Training chronology passes.** [h34_ml.py:98](</D:/Trading Agent/scripts/h34_ml.py:98>) excludes test-year rows. Retraining reproduced the cached predictions. Training starts January 2021.
- **No same-day closing-price or ATR leakage found.** Prior close and volume averages are shifted; range averages exclude today; 15:25 closes enter returns only through prior days. See [h30_capitulation.py:47](</D:/Trading Agent/scripts/h30_capitulation.py:47>) and [h34_ml.py:70](</D:/Trading Agent/scripts/h34_ml.py:70>). Labels contain future returns appropriately and are excluded from predictors.
- **The target is relative performance**, not absolute profit after costs ([h34_ml.py:149](</D:/Trading Agent/scripts/h34_ml.py:149>)). Outperforming a falling market can still lose money. “4× costs” therefore does not establish that much expected tradable edge.

**The win-rate improvement is mainly a stop-width trade-off.** B earns only **₹0.49 more per trade**. Average losing trade worsens from ₹83.94 to ₹93.12; trade volatility increases. A weekly bootstrap gives the incremental profit a roughly **−₹2.50 to +₹3.63/trade** 95% interval. There is no convincing improvement in expectancy.

B’s daily t-statistic is only **1.98 before selection adjustment**. Twelve threshold/ranking combinations are documented; roughly 32 total variants are asserted in the [runbook](</D:/Trading Agent/RUNBOOK.md:1254>), without a complete machine-readable trial ledger. These are development results, despite annual walk-forward training. I would budget for **50–100% erosion of ₹9.72/trade**, potentially worse. That is judgment, not a measured forecast; 50.7% wins is not reliably distinguishable from 50%.

Execution remains optimistic. Features become available at 09:35, but filling at that instant’s opening print assumes negligible latency. An additional **0.25% entry cost** approximately turns ₹8,375 into **−₹1,240**. Stops handle bar-opening gaps, but assume intrabar fills at the stop plus fixed slippage ([h31_lab.py:127](</D:/Trading Agent/scripts/h31_lab.py:127>)). Cash shorts face [MIS restrictions](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/intraday-orders-not-allowed-for-some-stocks) and [circuit-lock/auction risk](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/circuit-limit-hit-intraday-product-type). The 285-stock present-day universe adds survivorship bias. ₹5,000 slots actually deploy ₹4,461.57 on average.

**I would not fund this yet.** My subjective odds of profitability after realistic charges and corrections: **30–45% for 2015–2020, 35–50% for 2025-08..2026-07, 20–30% for both**. These are uncalibrated judgments. Historical testing must train chronologically, allowing warm-up—not use 2021-trained models backward.

After fixing leakage, the single most valuable change is **nested chronological validation: choose thresholds and stops inside training history, never on the reported test window.**