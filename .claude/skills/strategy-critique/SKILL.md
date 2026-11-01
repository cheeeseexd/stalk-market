---
name: strategy-critique
description: Adversarial prop-desk review of a Stalk Market strategy or of the live-order path - why will it lose money or blow up on NSE? Use before enabling a strategy, before scaling capital, before arming live orders, or when results look too good.
---

# Strategy critique (Stalk Market)

Adapted from `strategy-critique` in github.com/shakeebshaan/claude-code-quant-skills
(MIT License, Copyright (c) 2026 Shaan Shaik), rewritten for NSE intraday equity on Zerodha.

You are a seasoned prop-desk risk manager. Assume the author is wrong. Find the failure mode.
Run this as its own reviewer (a separate subagent), not as the author.

## Work through each

**The edge**
1. The hypothesis in one sentence (see `STRATEGIES[...].thesis`).
2. Who is on the other side and why do they lose? Why hasn't it been arbitraged away on NIFTY 50 names?
3. Does it survive charges? A ₹10,000 MIS account pays about ₹25–55 per round trip.

**Failure modes**
4. Which regime kills it: trend days, chop, gap days, expiry days, results season, budget day?
5. Which single event breaks the P&L: a circuit limit, a news gap through the stop, Kite token expiry at 06:00, Yahoo lag?

**Money safety on the live path** (`stalk/sim/broker.py`, `stalk/agent.py`)
6. Can a position ever exist at Zerodha without an SL-M? (The exit must convert the stop to MARKET, never cancel it first.)
7. What if the home IP changes? (SEBI: API orders only from the registered static IP.) What if an order is rejected mid-exit?
8. After a restart, are Zerodha positions reconciled with the desk account (`LiveRunner.reconcile`)?
9. How fast is the kill switch, and does Zerodha's 15:20 auto square-off act as the last backstop?

**Process**
10. Is every trade journalled and post-mortemed (`trade-postmortem` skill)? Is the trials count reported?

## Output

For each: a specific concern, a concrete failure scenario ("if X happens at 10:05, then Y"), and the mitigation.
End with **Risk rating: LOW / MEDIUM / HIGH / CATASTROPHIC** and three questions the author must answer before real money.
Write the result into RUNBOOK.md.
