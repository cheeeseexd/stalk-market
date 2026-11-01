# Playbook: how we trade (₹10,000 account)

Short and fixed. Changing a rule needs a written test in RUNBOOK.md first. Benchmarks use the H17 action-excluded ten-year reconstruction (`scripts/h17_rebuild.py`; Claude review pending).

Both strict (NIFTY below -0.5%) and looser (below -0.3%) versions are on paper from Monday 2026-11-02, with separate forward journals and reviews.

## The strategy: panic buy
- **Only on panic mornings:** NIFTY opens more than 0.5% below yesterday's close. About 22 mornings a year. **On every other day we don't trade.**
- **What to buy:** the 2 stocks (outside the NIFTY 50, at least ₹10 crore traded yesterday, price ₹50 or more) that opened furthest down, by more than 2%.
- **Skip:** a stock with results within 3 days, or one stuck at its lower limit in the first 5 minutes. (Skipping other bad news hasn't been tested; the runner logs news so it can be.)
- **When:** decide at 09:20 (after the first 5-minute candle), buy at once. **⚠ 2026-11-01 finding: buying at 09:20 forfeits the edge** (535 real picks: +1.37% a trade at the 09:15 auction open, −0.13% at 09:20; the bounce happens in the first 5 minutes). Proposed change, pending the owner's yes: pick from the pre-open indicative prices at ~09:07 and buy in the pre-open session so the fill is the auction price. Since 2024 the gap is wider still (194 picks: +1.96% auction vs −0.08% at 09:20). An auction-entry paper variant runs beside the frozen rule from 2 Nov (`stalk/sim/panic_auction.py`). Paper runner aligned on 2026-11-01 (owner's yes): it now ranks every liquid EQ stock (≥ ₹10 crore traded the previous session, price ≥ ₹50, not NIFTY 50), the same universe as the 10-year evidence.
- **Size:** ₹5,000 each, whole shares, no leverage. Never more than 2 positions.

## Exits (no greed, no hope)
- **Stop:** 3% under the buy price, placed with the order. It never moves down.
- **No early profit-taking:** the edge is the whole day's recovery. Sell everything at 15:10, profit or loss.
- **Never** average down, add to a loser, or hold past 15:10 (Zerodha's auto square-off fee).

## Limits
- Planned stop loss: ₹300 for two full positions before charges and stop slippage; this is not a hard daily loss cap.
- Expect bad runs: cleaned strict history had a ₹1,895 worst drawdown and 7 losing active mornings in a row; loose had ₹2,133 and 12. Daily proxies do not guarantee these limits.
- **Pause and review** if the account is down ₹3,000 from its high, or 15 panic mornings lose in a row.
- 2 trades a day at most. No other strategy uses real money until it passes its own test.

## Desk rules: kept and removed (evidence from 2021–2025 trades, 2026-11-01)
| Rule | Status | Why |
|---|---|---|
| Account kill switch at ₹5,000 (50%) | **Kept** (`Settings.equity_floor`) | Every losing strategy would have hit it within 6–32 months; it caps damage, it doesn't fix a strategy |
| At most 2 trades a day | **Kept** | Fewer trades only shrink totals; each trade still has to clear ~₹5 of charges |
| Skip the 2nd trade if the 1st is already in profit | **Kept, provisional** | H31 ₹3.4 → ₹5.4 a trade, H30's loss halved; confirming on 2015–2020 (H31 check s1) |
| Fade only big spikes (≥ 5%), prefer larger caps | **Provisional** | In-sample leads; checks s2/s3 on 2015–2020 |
| Pause after 3 losing days / sessions | **Removed** (`lockout_after_losses = 0`) | Losing streaks don't predict the next day; it cut H31's profit from ₹4,641 to ₹2,713 |
| Skip the 2nd trade if the 1st is losing | **Removed** | Made H31 and H30 worse |
| RSI / 15-min / daily-trend filters | **Removed** | Didn't separate winners (RSI is already 70–80+ after a spike) |

## What to expect
- **Strict, cleaned:** +₹32,008 over ten full years on fixed ₹10,000 allocations (₹3,201/year before tax), 420 funded trades on 216 mornings. Average funded morning +₹148; 61% green; 63.6% of active weeks positive. Losing calendar years: 2019 −₹827, 2023 −₹7.
- **Loose, cleaned:** +₹39,568 total (₹3,957/year), 667 funded trades on 346 mornings; 59.4% active weeks positive. Worst drawdown ₹2,133; longest losing run 12 active mornings.
- Both keep the 3% stop, charges, 10bps slippage each side and 8bps early-exit proxy. Splits, bonuses, rights, schemes, dividends and other price-effect ex-dates are excluded **before ranking**, using dated historical symbols. Most calendar weeks still have no trade.
- The strict reconstruction passes (day t=5.94, 9/11 calendar years positive, above all 2,000 random controls). This reused history supports a paper execution check, not a profit forecast or real-money approval. Publication times are unknown; the exchange ex-date advance-announcement assumption is explicit. Fixed ₹5,000 slots are not always self-financing after losses.
- The frozen paper runner is unchanged and its H15 action guard is incomplete. Check the full action calendar before ranking forward observations and log displaced picks; H16 must establish quote/MIS/circuit evidence. [Full H17 report](docs/research/2026-10-31-h17-rebuild.md); Claude review pending.

## Tax (general information, not tax advice; check with a CA)
- Intraday profit is **speculative business income**, taxed at your slab rate. Account totals above are before tax.
- Intraday losses offset only speculative profits, and carry forward 4 years. File ITR-3; keep Zerodha's tax P&L statement.
- STT and charges are already in every number above.

## Every trade gets journaled
- Before: why it qualified (NIFTY gap, the stock's gap, no results).
- After: entry vs the official open (slippage), exit reason, net. After every 5 panic mornings: compare with the 10-year averages.

## Entry discipline from the REDINGTON drill (2026-11-01)
- A recent fall and a rising first candle are not enough to buy. A rebound long must satisfy the existing panic rule, including the market trigger, stock gap, eligibility and ranking. Missing evidence means no qualified trade, not permission to guess.
- Log the exact unmet condition before the future price path is shown. Keep the original idea and skipped-trade outcome for review; a skipped winner is possible and must be reported too.
- Drex disagreement is context, not a veto or proof of an edge. A chosen target is not evidence that a stock is likely to move far enough to cover costs.
- The REDINGTON example illustrates discipline; one synthetic loss does not establish a new profitable filter. Frozen production rules remain unchanged. The new two-stock practice comparison tests only rejection of unqualified rebound longs; any baseline shorts remain explicitly unproven practice trades.
- Owner priority clarified: minimize losses, accepting fewer trades and missed winners. Apply the same evidence standard to long and short ideas. A small five-session rise is not a reason to short; rejecting one stock does not qualify another.
- For new model practice, record observations, thesis, missing evidence and invalidation separately. Do not manufacture a trade to fill two slots. Compare net result AND participation/missed winners; a flat account alone does not establish predictive skill.

## Exploratory scenario authorization (2026-11-01)
- Owner explicitly permits arbitrary stocks and non-gap Wednesday ideas as hypothetical practice, including actual Drex stop/target proposals. This does not change the panic runner's qualification rules or enable any real order.
- Separate “qualified strategy trade” from “exploratory what-if.” The latter may simulate an unproven idea to learn how its plan behaves; state missing evidence and model uncertainty rather than silently converting it into a recommendation.
- The Tuesday skips reflected limited packet information and our restrictive design, not the popularity of AXISBANK/IRFC. Full stock-selection freedom changes the range of ideas we can explore; it does not establish an edge.
- Preserve weak ratings alongside outcomes. Wednesday's top-ranked Drex plans scored only about 2.4/5, both stopped, net −₹69.04. Stops limited this draw's loss; they did not fix its directional premise. No hindsight change to stops or targets and no new filter from two trades.
- Keep model proposals, assumed price scenarios and actual recorded paths visibly distinct. Numeric Drex answers are not free-text rationales or generated price forecasts.

## The consensus desk (adopted 2026-11-01, paper only)
Every trading morning, beside panic buy: after 09:30 the 6 biggest gap-downs and 6 biggest gap-ups among liquid stocks go, anonymously, to
**Claude Opus and GPT-6 Astra, who decide blind and separately**. Only stocks **both** pick on the same side are paper-traded, at most 2,
**Drex ranks them but never vetoes**. Entry at the first 5-minute bar after both have decided (about 09:40-09:50; 09:35 only in past-day drills), 3% stop, out at 15:10, realistic costs, ₹5,000 each. Runs by itself in the app
(`_consensus_loop` → `scripts/consensus_desk.py`, needs the Kite login); journal `data/consensus_paper.jsonl`, card on Sessions.
Evidence so far: 5 blind drills, agreement +₹112 on 4 trades vs Claude alone −₹14 and Astra alone +₹73 — a safety filter, not a proven edge.
Watch: agreed shorts are often small caps (0.40% costs, short availability not modelled).
