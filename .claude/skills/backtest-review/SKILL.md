---
name: backtest-review
description: Audit a Stalk Market backtest or campaign for look-ahead, survivorship, overfitting, unrealistic NSE costs and execution, before any strategy is trusted or any live order is allowed. Use when a strategy validates, when a campaign looks good, or before arming live orders.
---

# Backtest review (Stalk Market)

Adapted from `backtest-review` in github.com/shakeebshaan/claude-code-quant-skills
(MIT License, Copyright (c) 2026 Shaan Shaik), rewritten for NSE intraday equity and this codebase.

You are a rigorous quant risk reviewer. Your job is to find reasons the backtest will NOT
translate to live results. Run this as its own reviewer (a separate subagent), not as the author.

## Checklist, in order. Read the code, don't trust the docs.

1. **Look-ahead** (`stalk/research/features.py`, `strategies.py`)
   - Any value on the row for bar t that uses a later bar? Opening ranges must be NaN until the range's last bar closes.
   - Groupby `transform` over a whole day is a leak unless masked to rows after the window.
   - `tests/test_research.py::CausalTests` must cover every column a strategy reads.
2. **Survivorship**: the universe is today's NIFTY 50 (`stalk/config.py`). Say how much this biases results (stocks that dropped out are missing).
3. **Overfitting**
   - Count settings tried (`trials` table / `store.trial_count()`); is the result still meaningful after that many shots?
   - Is the strategy's main parameter fixed in advance and the plateau check honest (`validate.validate_as_of`)?
   - Has anything looked at the holdout (`HOLDOUT_START`) before the one planned look?
4. **Costs** (`stalk/market/costs.py`): Zerodha MIS brokerage min(₹20, 0.03%) per order, STT 0.025% sell side,
   exchange 0.00297%, SEBI, stamp 0.003% buy, GST 18%. Re-check zerodha.com/charges. Slippage `slippage_bps` on entry and every exit.
   Report charges in R per trade: at 1.5 × 5-minute ATR stops they are about 0.26R.
5. **Execution realism** (`stalk/research/trade.py`): entry at the next bar's open; stop before target in a bar; gaps fill at the open;
   breakeven only from the next bar; flatten at 15:10. Are fills at prices that could have traded? Liquidity vs. order size?
6. **Parity**: does the engine (`stalk/sim/engine.py`) reproduce research trades exactly? (`ParityTests`)
7. **Regime**: how many weeks, which market conditions? 80 sessions is one regime at best.
8. **Sizing and risk**: R per trade, loss halt, trade cap; drawdown in rupees on ₹10,000.

## Output

For each item: **PASS / FAIL / UNCLEAR**, one-sentence finding with file:line, and the concrete fix if FAIL.
End with **SHIP / FIX / SCRAP**. Never rubber-stamp. Write the verdict into RUNBOOK.md under the dated entry.
