# What every run so far has taught us

Compiled 3 Nov 2026 from RUNBOOK.md, JOURNAL.md, RESEARCH_LOG.md and the GPT-6.1 Sol conversations (docs/research/).
Goal: a strategy that genuinely passes and makes money on a ₹10,000 Zerodha account. Updated as tests finish.

H27b (2026-11-01): the separately registered history-complete population retains390/427 strict picks. Ordinary-control excess+196.80bps, day t6.28,313/390 matched (80.26%) on191 mornings: Passed attribution screen. Across all210 retained strict mornings, ordinary stocks-9.85bps and NIFTY+10.93bps, supporting stock-specific recovery rather than mostly market rebound. Also-gapped comparison is coverage-limited, so ranking remains Unresolved. Reused gross evidence, no execution/promotion claim. Pre-open quote/depth collection is ready for Monday09:01 with Kite login; missing auction fields remain null. Report: docs/research/2026-11-01-h27b.md.

## The short version

1. **Every strategy failed for the same reason: no edge before costs, not bad tuning.** On NIFTY 50 stocks, simple direction signals earn
   1–7 bps (0.01–0.07%) a trade. One round trip costs about 17 bps (₹10.60 charges on ₹10,000 plus slippage). Zero minus 17 is a steady loss.
2. **The overnight world (US stocks, crude, gold, the rupee) is priced into the 09:15 open.** It tells you the gap, not the day.
3. **Lining up timeframes, opening volume, news filters and market mood didn't change the odds** on 2 years of data.
4. **One strategy has strong evidence: "panic buy"** (10 years, every liquid NSE stock including delisted ones: +1.87% a trade after costs, 58% won, t 7; still +0.86% if fills are 1% worse): on mornings NIFTY opens more than 0.5% down, buy the two
   hardest-hit midcaps or smallcaps that have no results news, and sell by 15:10. It's now in a forward paper test. It isn't a proven money-maker yet.
5. **The biggest risk to that candidate is execution**: the price you can actually buy at on a panic open. That's measured from the next session.

## Why the strategies failed: the economics

| Item | Number | What it means |
|---|---|---|
| Charges per round trip, ₹10,000 position | ₹10.60 (0.106%) | STT, exchange, GST, stamp, ₹3 brokerage a side. The same % at any size up to about ₹65k. |
| Slippage assumed | 3 bps a side (large caps) to 10 (smallcaps) | Not yet measured; Kite quotes will measure it |
| **Hurdle a trade must clear** | **≈ 17 bps** (large caps), 25–31 bps (mid/small) | |
| Typical rest-of-day move of a NIFTY 50 stock | 76 bps | So a signal needs about 61% right to break even at equal win and loss sizes |
| What our signals managed | 50–52% right, 1–7 bps edge | First-hour momentum, gaps, yesterday's reversal, following NIFTY, all three lined up |
| Real risk per trade with no leverage | ≈ ₹55 (0.55% stop on ₹10k), not ₹100 | Charges are about 18% of the risk on every trade |

From the 2½-year hourly test (650 unseen sessions, 7 strategies): **before charges every rule was almost exactly zero.**
Charges of ₹2,449 were the whole loss of ₹2,538.

## What didn't work, and the evidence

| Idea | Test | Result |
|---|---|---|
| Textbook setups: opening-range breakout, VWAP pullback, RSI reversion, Supertrend, previous-day breakout, 52-week momentum, gap fade | 5-min, 15-min and hourly; 48 sessions to 2½ years | All negative after charges; about zero before. Gap fade's early +0.2R was luck (−0.01R on 85 unseen trades). |
| Wider stops, 15-minute bars | Same | Cut losses by cutting charges in R; didn't create an edge |
| Market filters (breadth, sector, results days, factor ranking) | 57 sessions, day by day | Best stack +₹226. But the 3-loss timeout reshuffles every later day, so differences of a few hundred rupees are mostly path noise. |
| Market mood first, ≤ 3 confident trades a day | Same + 3 mood months | −₹400 on the main window; better in the mood months. Inconclusive. |
| Choppy-day filter (Sol's idea) | Same | −₹994. It removed winners. |
| News filter (NSE announcements, keywords or Drex) | Same | Worse than without. Drex judged news far better than keywords, but blocking trades on it didn't help. |
| Drex as a veto | Same | Made the stack worse |
| India VIX spike filter | Same | No help |
| **US, crude, gold, dollar-rupee before the open** | 6 years daily | Correlation with NIFTY's gap: S&P 0.52, Nasdaq 0.48. With the rest of the day: −0.06. Trading the US cue after the open loses 4.8 bps (t −2.6). Crude, gold and the rupee: nothing. |
| **Daily trend + NIFTY first hour + stock first hour all agreeing** | 28,562 stock-days | +3.7 bps. Agreement doesn't raise the odds. |
| **Opening relative volume ("stocks in play")** | 287 stocks, 5-min | Predicts how much a stock moves, not which way. Big-volume opens slightly reverse. |
| **Results-day gap continuation** | NIFTY 50: +65 bps net, t 3.1. Then 240 unseen stocks. | **Failed on unseen stocks: −36 bps net, t −2.9, 1,141 trades.** The clearest lesson of all: a clean in-sample result is a lead, never evidence. |
| Holding results winners 10 days (delivery) | 280 stocks, daily | The drift exists before costs (+73 to +112 bps over 20 days), but delivery costs at ₹10k (~0.45%, a third of it the ₹15 DP fee) eat it |
| Daily swing breakouts | 5 years daily | −₹6,912 |

## What has worked

- **Risk rules** (no leverage, 1 trade a day, stop never moves, 3 losses means 3 sessions off): halved the losses and the drawdown on 2½ years. They don't make money; they buy time.
- **Flat before 15:20:** every one of 8,273 simulated trades closed the same day. No auto square-off fee.
- **The validation gate:** the "proven only" account traded almost nothing for weeks, which was correct while nothing had an edge.
- **Panic buy (H7), the one candidate.** Frozen before looking, tested once on Nov 2021 → Oct 2023 data nobody had examined:
  112 trades, **+100 bps net per trade, 59% won, t 2.8 by trade and 2.3 by day**, positive in both years. It holds at 1.5% and 3% gap cut-offs,
  with 2–4% stops, and with a 15:10 exit. The 2023–26 period agrees (+129 bps). It survives 15 bps a side of extra slippage; at 30 bps a side it fades out.
  It fires only about 2–3 mornings a month, so it's an occasional strategy, not a daily income.
  Why it might be real: forced and panic selling at the open on a broad sell-off overshoots in thinly traded stocks, then recovers. Stocks with real news (results) don't recover; they keep falling.
  Why it might not be: survivorship (today's index lists), only 61 independent days, 2026 has been weak, and opening prices on panic mornings may not be fillable.

**Update (same day, after your question about waiting):** instead of waiting months, two more past periods were tested at once.
Nov 2016 → Oct 2021 with today's stock lists: +222 bps (inflated by survivorship). Then the decisive test, with no survivorship: NSE bhavcopy files, every stock with ≥ ₹10 crore traded,
Nov 2016 → Oct 2026: **216 panic mornings, 429 trades, +187 bps net a trade with locked stocks kept, 58% won, t 7.05; 9 of 11 years positive.**
Ordinary gap-downs on other mornings: +34 bps, not significant, so the panic condition is what matters. Entry 1% worse than the open: still +86 bps (t 3.3).
Sol: stronger evidence, but not yet proven executable. So the forward test is now an execution check (5 panic mornings), then a small real trial you place yourself.

Excluding ETFs that slipped into the historical stock list (13 of 422 trades), the ₹10k account made about ₹2,900 a year before tax instead of ₹3,100.

## How not to fool ourselves (process lessons)

1. **Write the rule and the pass mark before looking.** Every test above was pre-registered in RUNBOOK.md.
2. **Confirm on data the idea never touched**: different stocks or different years, used once.
3. **Raise the bar with every attempt.** The first rule sent to confirmation needed t ≥ 1, the second 1.5, the third 2.0.
4. **Judge filters on every signal, not on one path of trades.** The losing-streak timeout makes path P&L noisy.
5. **Measure in % after costs, not in R.** A tight stop makes R look good while charges eat the % move.
6. **Check the data before believing an extreme result.** The biggest moves are where bad prices hide.
7. **"Nothing works" is an allowed answer.** A search that can't say no will eventually find a fluke.

## Your questions, answered with the data

- **How many trades a day?** Fewer is better while there's no edge, because each trade costs about 17 bps. The desk stays at 1. Panic buy takes 2, and only on qualifying mornings.
- **What type of trades?** Not breakouts, pullbacks or momentum on large caps: they're priced to within a few bps. The only candidate is buying overshoots in mid and smallcaps on broad sell-off mornings.
- **Which sectors?** No sector rule survived. The sector-agreement filter helped a little in one window. Panic buy's edge sits in smallcaps (+152 bps) and midcaps (+68).
- **What to check before the market opens?** US close, crude, gold and the rupee tell you roughly where NIFTY will open, which the pre-open auction shows directly anyway.
  The useful pre-market facts: is NIFTY opening more than 0.5% down; which stocks are opening down hard; did they have results; are they locked at the lower circuit.
- **Does the US market drive India?** It drives the opening gap strongly (correlation 0.52) and nothing after it. If anything, big US-up days fade slightly after the open.
- **Crude and gold?** No measurable effect on NIFTY's direction after the open in 6 years.
- **1-minute, 5-minute and daily charts lined up?** Tested as daily trend + first hour + NIFTY agreeing: +3.7 bps, below costs. It feels like more confidence but doesn't add any.
- **Large, mid and smallcaps at the open?** Scanned 287 stocks. Opening volume doesn't tell direction; the panic-morning overshoot is the one thing that showed up.

## What happens next

- **Panic-buy forward paper test** (runs by itself in `python run.py`, weekdays from 09:25; status on the Today page; log in data/panic_paper.jsonl).
  Review after 15 panic mornings (about 30 trades, likely 5–7 months). It continues only if net is positive after charges, the average trade is at least +30 bps,
  and measured slippage (Kite quotes) has a median of 15 bps a side or less. If it loses, the claim is dropped, not tuned back.
- **The desk** keeps paper trading the S1 stack (1 trade a day) on NIFTY 50.
- **Still to build:** the random-pick benchmark for the desk, and a bid/ask recorder that works without Kite.
- **Real money only after** a forward test passes and the two reviewer skills (backtest-review, strategy-critique) sign off.

**Codex final checks (2026-10-31; report labelled 2026-11-03):** H7d passed: stopped/15:10 daily proxy +164 bps, day t=5.83, versus random-pick 95th percentile +68 bps (2,000 draws); 36/36 grid cells pass net>0 and day t>=2.
₹10k annual iid bootstrap (5,000): 5th/median/95th +₹435/+₹3,066/+₹6,119, 2.68% losing years; this pools regimes (weak-year conditional 5th -₹1,667), not a loss guarantee. Last 3 years are not weaker in aggregate than the first 7.
Verdict: Monday paper trial as-is; biggest risk remains executable 09:20 fills in extreme/circuit-bound names. Daily all-EQ research differs from the frozen paper universe; EQ can include ETFs, and historical MIS/calendar availability is not established.
H8 euphoria gap-up short, design only: 24 trades/12 mornings, +157 bps net but day t=1.11 and 72% best-day concentration (including an ETF outlier): inconclusive, no confirmation justified. Future confirmation remains reserved and untouched; see docs/research/2026-11-03-codex-final-test.md.

H9 (2026-10-31): fresh non-results material-news gap continuation failed 2017–2021 design: 663 trades, -82.5 bps net, trade/day t=-5.35/-5.16; longs -92.5 and shorts -60.7 bps, 0/5 positive years.
It offered 70/74/93/115/133 trading days a year (97 average), but entry 0.5% worse lost -126.7 bps; 2022–2026 confirmation was not scored.
Lesson: a timestamped material-announcement flag does not establish continuation. No-news fade was +30.9 bps but -26.4 bps with worse entry; executable fills need evidence. Next: H12 sector panics.

H12 (2026-10-31, cycle 2): sector panics failed 2017–2021 design: 14 trades/eight mornings, -31.58 bps net, trade/day t=-0.38/-0.41; 1/4/2/1/0 days per year (1.6 average), zero panic-day overlap. Confirmation unscored.
Only three matched pairs (treated -289 vs calm +283 bps); no reliable condition comparison. Current/last-available NSE industry labels cover 70.94% of liquid stock-days; retrospective event/lock filters and daily fills limit interpretation.
Lesson: count independent opportunities before treating a sector condition as a route to more trading days. Next: H14, executable no-news fade on design-only intraday data; confirmation bar stays t>=2.5.

H14 (2026-10-31, cycle 3): nominal design 25 picks/17 mornings, hold-out14/11 (below 40-trade minimum); funded primary 24/16, +Rs3.00, day t=0.006. Hold-out returns unscored; no edge established.
Seven TDPOWERSYS intraday/daily 2:1 mismatches and one documented split-date gap contaminate selection; no repaired rescore or promotion. 09:25/09:30 P&L -Rs11/+Rs162 is diagnostic only.
Lesson: a price-basis mismatch can manufacture repeated gap opportunities before execution is even tested. Next: H15 coherent price/corporate-action audit, then H11 diversification.

H15: 312/17,220 stock-days have >1% price-component disagreement; official-gap replay changes strict/loose 12-week net from -Rs68.14/+Rs283.96 to +Rs127.59/+Rs531.11; three permitted Yahoo-fallback trade rows remain unverified.
H15 lesson: official does not mean action-adjusted; 11 documented split/bonus record-date selections expose H7c/H7d rankings; revalidate the ten-year baseline (H17) before diversification; H14 hold-out remains unscored.

H17 (cycle5): action-excluded strict/loose reconstruction survives: stopped+168.44/+130.78bps, day t5.94/6.04; strict9/11 positive years, above all2,000 matched random controls. Cleaned stopped Rs10k totals+Rs32,008/+Rs39,568; reused history, not fresh confirmation.
Lesson: dated ticker identities matter as much as ex-dates; NSE backfilled HEGAM into HEG's old split history. All action publication times unknown; daily execution remains unproven.
Keep paper execution measurement with cleaned benchmarks/full action-calendar review; frozen runners unchanged. Next:H16 quote/MIS/circuit evidence, then H11; Claude review pending.

H18 daily failed moves: 696 design trades/399 days lose Rs7,164 (-22.93bps, day t=-4.515); both sides, both stops, no-reversal and market-direction variants lose. Confirmation stays unscored; 134 tests pass.
Fundable signals occur on 231/239 days per252 sessions (design/reserved power); full-calendar2024/2025 counts232/221. Frequency is adequate, profitability is not; reused current-constituent history and modeled fills remain limitations.
Lesson: enough daily opportunities do not establish an edge; waiting for a reversal did not clear costs. Next:H16 execution evidence, then H19 prior-day range sweep/reclaim power and fresh prospective validation; Claude review pending.

H19 power only: design868 picks/434 days (L342/S526), reserved574/287 (L232/S342); nominal251/249 signal days/year, affordability proxies251/246. Zero design five-minute entries; reserved113 fundable opens/60 days, complete paths unknown; design/confirm net and day t unavailable, no returns scored;146 tests pass.
Lesson: frequent candle patterns do not supply executable evidence when the required entry history is missing. Next:H16 quote/MIS/circuit coverage at11:15/11:20/exits, then registered fresh H19 151/101-session test; Claude specification review requested, confirmation hurdle stays2.5.

H16: prospective seven-slot quote/depth/MIS/circuit recorder built; execution result Pending until five panic mornings and twenty ordinary sessions. Unknown coverage cannot count as executable or MIS/unlocked evidence.
Lesson: an earlier candle open cannot establish that a later-chosen stock was executable at the decision time. Next: accumulate H16; H19 skips until certified live inputs exist, with return gates still closed.


H23-H22-H25 completion: H23 excess -8.17bps (t-0.115) in only48/427 matched picks; mechanism unsupported, whole-edge attribution unresolved. H22 added days fail t or frequency/execution.
H25 gross -7.62bps is descriptive only; Does not clear the 60bps descriptive hurdle; no scored strategy test follows from this cycle. Lesson: matching coverage limits mechanism claims, and extra panic mornings must survive worse entry prices on their own.


H26 stocks-in-play ORB: Unclear, power/data quality only. Only5 trades/5 days;16,760/17,220 cached opening volumes zero. V1/V2/V3 net% -0.4703/+0.2663/-0.0367;0.25%-worse -0.4708/-0.2669/-0.1877. No RV repair, rerun or promotion.
Lesson: opening-volume coverage comes before an RV strategy's frequency claim; a positive five-trade sample that fails adverse fills establishes no daily edge. Kite history and separately labelled daily paper are next.


2026-11-01 — H27/H28 Unresolved at the required input audit, zero return runs: strict beta37/427 missing (8.67%); strict hourly fields81.10% missing even within date overlap. H29 descriptive complete: BEL60.59, TATASTEEL57.90, HINDALCO56.54, AXISBANK50.47bps median absolute10:15-to14:15 move, against17.5bps planning cost; actual spreads unknown. Three pair95% day-clustered intervals include zero. H16 records all12 at every existing slot from Monday with watchlist labels; original candidate aggregates preserved, unknown stays null. One guard each, outcome runs0/0/1;261 tests and frozen hash/arithmetic checks pass, protected sources unchanged. No promotion/scoreboard row. Report: docs/research/2026-11-01-h27-h29.md; artifacts:data/h27,data/h28,data/h29; validation:data/h27_h29_validation.json.

Lesson: large absolute moves are observation capacity, not forecastable net edge; incomplete required histories cannot be rescued with available-only returns.
