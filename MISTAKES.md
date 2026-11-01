# Mistakes we have already made. Do not make them again.

Every model working on Stalk Market (Claude, Codex/Sol, any other) reads this before proposing or testing a strategy.
Each line is something we did, what it cost, and the rule that now applies. Breaking a rule here needs the user's written yes.
Last updated 2026-10-31. Append; never delete.

## A. Choosing what to test

1. **Testing direction signals on large caps.** Breakouts, VWAP pullbacks, RSI, Supertrend, EMA crosses, 52-week momentum, timeframe agreement on NIFTY 50 stocks all earned 0.01–0.07% a trade before charges against a ~0.17% hurdle. Thousands of trades, all negative.
   **Rule:** no new chart-pattern or indicator rule on large caps. Large caps are priced by institutions and HFT to within a few bps. A new idea must explain *who is on the other side and why they lose*.
2. **Starting from a pattern instead of a reason.** Every failure began with "this setup looks good". The one success (panic buy) began with a reason: forced, uninformed sellers overshoot in thin stocks.
   **Rule:** every hypothesis names the counterparty (forced seller, index fund, margin call, panicking retail, F&O ban, lock-in expiry…) and why that flow is not informed. No counterparty, no test.
3. **Expected edge smaller than the hurdle.** H18 (11:15 failed moves) traded ~230 days a year and lost 0.23% a trade; US-cue trading after the open lost 0.05%.
   **Rule:** before coding, estimate the typical gross move per trade from a published source or our data. If it isn't at least 2× the hurdle (≈0.35% for mid/smallcaps), drop it.
4. **Confusing "trades often" with "has an edge".** H18 and H19 were chosen for frequency. Frequency is worthless without an edge.
   **Rule:** edge first, then look for more days of the *same* edge (lower thresholds, more stocks, related forced flows), never a new rule just to fill empty days.
5. **Filters that remove trades to rescue a dead rule.** Choppy-day filter, news veto, Drex veto, VIX filter, mood filters: each made things worse or did nothing.
   **Rule:** a filter is only tested on a rule that is already positive before the filter.
6. **Delivery holds for intraday-sized edges.** H5 had a real drift but ₹15 DP fee + 0.1% STT each way ate it at ₹10k.
   **Rule:** at ₹10k, multi-day holds need a gross edge ≥ 0.6%.

## B. Testing

7. **In-sample success treated as evidence.** Results-day gaps: +0.65% on 40 NIFTY 50 stocks, then −0.36% on 240 unseen stocks (t −2.9).
   **Rule:** register the rule, the pass mark and the trials count in RUNBOOK.md before any returns. Confirm once on data it never touched.
8. **Tuning after looking.** Every "slightly better setting" found after seeing returns has faded.
   **Rule:** one scored run per registered design (started.json guard). If it fails, it stays failed; write the lesson.
9. **Judging filters on one path of trades.** The 3-loss timeout reshuffles all later days, so ₹200 differences between stacks were noise.
   **Rule:** judge on every signal independently, by % after costs and day-level t, not on account P&L paths.
10. **Measuring in R instead of %.** Tight stops made R look good while charges ate the % move.
    **Rule:** report net % per trade after charges and slippage, and ₹ on the ₹10k account.
11. **Survivorship.** Using today's index lists inflated panic buy from +1.9% to +2.2%.
    **Rule:** use NSE bhavcopy with delisted names for anything on daily data; say so when a test can't.
12. **Too few independent days.** H12 (14 trades), H14 (25), H8 (12 mornings) produced numbers that meant nothing.
    **Rule:** count independent days before scoring. Under 40 trades or 20 days: power check only, no verdict.

## C. Data

13. **Splits and bonuses creating fake gaps.** H14's TDPOWERSYS 2:1 mismatch and 11 split dates manufactured "gap-downs".
    **Rule:** every gap must use action-adjusted previous close (`stalk/sim/price_basis.py`), and any >10% gap is checked against the corporate-action list.
14. **ETFs in the stock list.** 13 of 422 panic trades were ETFs.
    **Rule:** filter to EQ series stocks; exclude ETFs, InvITs, REITs.
15. **Short intraday history.** Yahoo keeps 60 days of 5-minute bars and ~2 years of hourly. Intraday rules judged on 3 months are anecdotes.
    **Rule:** intraday rules need ≥ 2 years of bars (Kite historical when logged in, or hourly as a proxy). Otherwise: daily-bar proxy, labelled as such.
16. **Assuming fills at the open.** No-news fade: +0.27% at the open price, −0.28% if fills are 0.5% worse. Fills matter 10× more than brokerage.
    **Rule:** every result is reported at the assumed price *and* 0.5% worse. If only the first is positive, it's "execution-dependent", not a pass.

## D. Process and honesty

17. **Exit reasons as free text.** Codex wrote "official-close minus 8bps proxy" into the database.
    **Rule:** exit reason is exactly `stop`, `target` or `flatten`.
18. **Changing a frozen rule mid-test.** Not done yet. Keep it that way.
    **Rule:** `stalk/sim/panic.py`, `validate.py`, the holdout and `live_ready` change only with a passed test and the user's yes.
19. **Asking an LLM to pick trades.** Drex as a veto made things worse; no LLM beat chance at picking direction.
    **Rule:** LLMs research, review and explain. Rules decide orders.
20. **Blaming costs or the broker.** Zero brokerage adds ~0.07% a trade and rescues nothing.
    **Rule:** the problem is always the strategy until a rule is positive before costs.

## E. Added after H21 and Sol turn 13 (2026-10-31)

21. **Treating the winner's story as fact.** We say panic buy works because forced sellers overshoot. That was never tested against stressed stocks of the same beta, liquidity and volatility on the same mornings; if those bounce equally, it's paid market risk, not special liquidity provision.
    **Rule:** a mechanism is a hypothesis until a matched-control test supports it. Extensions must follow the tested mechanism, not the story.
22. **Filling empty days with a weaker copy of the winner.** H21 bought the 2 biggest gap-downs every day: 84% of days traded, everyday leg −₹3 a trade (t −0.9), drawdown 6.8× panic-only. A +₹926 week in Dec 2024 was luck inside a losing 10 years.
    **Rule:** a single week proves nothing. Extensions are scored on the added days alone; the original's profits can't carry them.

23. **Assuming hourly history includes a closing candle.** H25 registered a15:15 candle without inventorying actual cache times; the cache stops at14:15, so every requested path was missing and no return could be measured.
    **Rule:** inventory actual timestamp coverage before registering entry/exit sources. Missing closing bars remain unknown unless an explicit official-close proxy is registered before any returns; never call an earlier bar the market close. Preserve failed attempts and complete only frozen selections after a metadata-only source correction.

24. **Trusting Yahoo's opening volume.** Yahoo's 5-minute data has zero volume on 16,760 of 17,220 opening (09:15) candles (verified by Claude 2026-10-31). Every earlier result that used opening volume or relative volume from Yahoo (H1 "heavy opening volume", the in-play scan, H26 pilot) rests on broken data.
    **Rule:** volume-based intraday rules use Kite (or NSE) volume only. Before any test, count zero/missing values in every input column and stop if more than 1% are missing.


25. **Treating event-day coverage as lookback or frozen-name coverage.** H17 had complete eligible event-day prices, yet H27 lacks prior beta for37/427 strict picks; H28 lacks81.10% of required hourly fields even inside the cache window. An available-only subset would silently change the question.
    **Rule:** inventory the full prior-session support and exact frozen-symbol execution paths before returns, including warmup and date-overlap denominators. Required fields over1% missing stop the cycle; never trim the frozen sample or substitute names to evade that stop.

## 26. A pass that comes from one trade is a data error until proven otherwise (2026-11-01)
H34's one-time test showed "Passed" with ₹60,514, of which ₹44,837 was a single trade: Kite's 09:35 bar for PIIND on 2018-12-11 opened at ₹82.60 for a ~₹830 stock. **Before reading any verdict, list the top 5 trades and any trade over ±15%; check them against the neighbouring bars.** `h31_lab.walk` now refuses an entry more than 20% away from the previous bar (knowable at entry). Keep the as-scored summary, publish the clean one, and say both.
