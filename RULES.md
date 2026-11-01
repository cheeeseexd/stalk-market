# Trading rules

The rules the agent follows on every trade, in backtests, replay and live. Each one is enforced in code, so it can't be talked out of
a rule mid-session. Sources: the five rules from "I Gave AI ₹10 Lakh to Trade For 30 Days" (Trading With Sidhant, youtube.com/watch?v=M8QHS7l69eQ),
SEBI's 2024 intraday study, and our own campaigns (RUNBOOK.md).

## The five rules

1. **Decide the loss before the trade.**
   Every trade has an entry, a stop and a target before it's placed. A stop-out costs at most ₹100 (1% of ₹10,000), plus charges.
   The video uses 0.5%, but at ₹10,000 that makes charges a third or more of the risk, so we keep 1% and record why.
   *Code:* `research/trade.py::size`, `sim/risk.py` (size and charges checks).

2. **Never move the stop further away, and never sell a working trade early.**
   A trade ends only at its stop, its target, or the day's close (MIS positions must be flat before 15:20).
   No widening "to give it room", and no grabbing a small profit because it feels good.
   *Code:* `Trade.step`. The only stop move is the optional breakeven tighten (`breakeven_at_r`), which can be switched off.

3. **Never add to a loser.**
   One position per stock and no averaging down.
   *Code:* `risk.check` ("Already holding this stock", max positions).

4. **Don't size beyond the account.**
   The position value is capped (`max_notional`). The ₹10,000 account allows MIS leverage up to 5×.
   The no-leverage version (position ≤ ₹10,000) is tested in RUNBOOK and becomes the default if it doesn't make results worse.
   *Code:* `research/trade.py::size`.

5. **Stop after a losing streak.**
   Two losses in one session ends that day. Three losses in a row across sessions starts a timeout of three sessions with no new trades.
   Plus a ₹300 daily loss limit that closes everything.
   *Code:* `sim/risk.py` (`loss_streak`, `timeout`, `loss_halt`), `GateState.streak` / `locked`, carried across days.

## When to sell, when to hold, when to stay out

- **Sell** when the stop is hit, the target is hit, or it's 15:10 (15:15 on 15-minute and hourly bars). Nothing else.
- **Hold** everything in between, including a trade that is down but hasn't reached its stop.
- **Stay out** when:
  - the strategy isn't proven (40+ past trades, t ≥ 1 after charges, nearby settings agree);
  - it's the midday lull (11:30–13:00);
  - the session's trade cap is used up;
  - a timeout or the loss limit is active;
  - the data is stale;
  - the target is worth less than three times the charges.
- **Don't trade** after a big win just because it worked. The video's AI wrote "no clear setup after three wins, so we wait".
  Our rules only trade on a rule signal, never on mood.

## Why these rules

SEBI found that 71% of individual intraday traders lost money in FY23, 80% of those making more than 500 trades a year,
and that loss-makers paid an extra 57% of their losses in charges.
The rules don't create an edge. They cap the damage while we look for one, and they keep the agent from overtrading.
