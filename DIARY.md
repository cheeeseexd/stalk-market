# My trading diary: building Stalk Market

This is my own account of the project so far: what I set out to do, what I tried, what failed, and where I am now.
The detailed records are in the other files (RUNBOOK, LOG, INSIGHTS, MISTAKES, PLAYBOOK). This is the story in my words.

Everything here is **paper trading**. No real money has been traded. All amounts are in rupees.

---

## Why I started

I have a ₹10,000 Zerodha account set up for intraday (MIS) trading. I wanted to find out if AI could help me trade it
profitably: not a lucky week, but something that keeps working. My rules from day one:

- Paper first. Nothing touches real money until it has proven itself.
- Judge everything by rupees after charges, not by win rate. A 70% win rate that loses money is useless.
- Be honest about luck. A good week isn't proof.
- Every run, good or bad, goes on the dashboard so I can see it.

Claude (Opus) does most of the building and research. GPT-6 Astra and Sol (through Codex) act as second opinions and
critics. Drex is a small yes/no scoring model that I use for ratings only. No order ever waits on an AI's answer.

---

## Before 30 October: the old app

I had an earlier version called NexusQuant. It got messy, so I archived it and started fresh. (It isn't in this repo.)

## 30 October: a fresh start

I rebuilt everything as **Stalk Market**: a FastAPI back end, a dark dashboard, and one engine that runs backtests,
replays and live paper trading in the same way, bar by bar.

I read a lot of X threads, YouTube videos and GitHub repos from Indian traders and copied their best-known strategies:
opening range breakout, VWAP pullback, gap fade, RSI reversion, Supertrend, previous-day-high breakout, 52-week momentum.

**First result: no edge.** All of them lost money.

## 31 October: tuning, and learning why everything loses

I tried wider stops, 15-minute bars, hourly bars, a 10:00 start, market mood filters, news filters and Drex vetoes.
Some versions lost less, but none made money.

That was when I understood the main problem:
- On NIFTY 50 stocks, simple signals are only right about 50–52% of the time and earn almost nothing before costs.
- One round trip costs about 0.17% (Zerodha charges plus slippage).
- So zero edge minus costs is a steady loss. Better tuning can't fix that.

I wrote the five trading rules (RULES.md), the mistakes list (MISTAKES.md) and the research log (RESEARCH_LOG.md) so I'd
stop repeating the same errors.

## 31 October to 1 November: finding panic buy

I stopped tuning and started looking for where an edge actually exists. One idea held up: **panic buy**.

> On mornings NIFTY opens more than 0.5% down, buy the 2 midcaps or smallcaps that opened furthest down
> (with no results news), with a 3% stop, and sell everything at 15:10.

- Over 10 years of every liquid NSE stock (including delisted ones), it made about **+1.68% a trade after costs**.
- Profitable in 9 of 11 years. It works on only about 22 mornings a year.
- The picks beat ordinary stocks on the same mornings, so the bounce is specific to those stocks, not just the market recovering.

This is the only strategy that has passed a proper test. It goes on paper from **Monday 2 November 2026**.

## 1 November: the big lesson, and many failures

**The edge is in the opening auction.** Looking at 535 real panic picks with 5-minute Kite candles:
- Buying at the 09:15 auction price: **+1.37% a trade**.
- Buying at 09:20, as the rule says: **−0.13% a trade**.

The bounce happens in the first five minutes. So I added an "auction entry" paper version beside the frozen rule. The
paper test now runs four versions: strict (−0.5%) and looser (−0.3%), each with a 09:20 entry and an auction entry.

I also tried hard to find something that trades every day. **All of these failed** after realistic costs (0.15% large /
0.25% mid / 0.40% small caps):

| Test | Idea | Result |
|---|---|---|
| H30 | Buy capitulation sell-offs | Beats random picks, not costs |
| H31 | Fade volume-climax spikes | ≈ ₹0 over 982 trades |
| H32 | Sector-leader long | Loses |
| H33 | Six popular setups (ORB, NR7, CPR, 5-EMA, gap fill, VWAP 2-SD) | All lose, most even before charges |
| H34 | Machine-learning ranker | Real signal (₹8 a trade) but ≈ ₹0 at realistic costs |
| H35 | ML only when a known mechanism agrees | ₹3.59 a trade, 1 of 4 years positive |
| H36/H37 | Short the biggest gap-ups | Works at the open price, loses with a slightly worse fill |

One bad Zerodha price print (PIIND, 11 Dec 2018) once made the ML ranker look like a ₹60,000 winner. I caught it,
removed it and added a guard. Lesson #26 in MISTAKES: **a pass that comes from one trade is a data error until proven otherwise.**

Two facts that shape everything:
- NIFTY's gains come overnight (+0.107% a night). During the day it drifts slightly down (−0.06% a day).
- Exits don't create edge; they only trade win rate for win size. Only picking the right stocks moves results.

## 1 November: AI desk drills

I ran blind drills on random past days. Claude and Astra each saw the same anonymous morning packet (Stock A, B, C…,
no names or dates) and picked trades separately. Drex scored them.

Across 5 drills:
- Trades **both** models agreed on: **+₹112** (4 trades, 2 won)
- Astra alone: +₹73
- Claude alone: −₹14
- With a Drex veto: ₹0. Drex gave every short 24–32% and blocked everything.

So I adopted the **consensus desk** on paper: each morning Opus and Astra pick blind, I only take the trades both agree on
(at most 2), and Drex ranks them but never vetoes. Five drills are a tiny sample. This is a safety filter, not a proven edge.

## Where I am now (1 November 2026)

- **Only proven edge:** panic buy, starting paper on 2 Nov in four versions.
- **New paper experiment:** the consensus desk (Opus + Astra agreement).
- **Everything else:** failed and recorded on the dashboard.
- **Next checkpoint:** about 30 paper trades before I even think about real money.
- **Open decision:** whether to switch panic buy's entry from 09:20 to the opening auction. The paper results will decide.

## What I've learned about doing this

1. Most "strategies" online have no edge after Indian charges. Test with realistic costs from the start.
2. Write the rule and the pass mark down before looking at new data. Never re-run a test until it passes.
3. Check the top trades before believing any result. Bad prices fake big wins.
4. The AI models disagree with each other a lot. When they agree, it's worth more than either one alone.
5. Fewer, better trades beat forcing a trade every day.
