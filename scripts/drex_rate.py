"""Ask Drex for a quick yes/no opinion on each strategy we have tested, with its real numbers as context.

    python scripts/drex_rate.py        # writes data/drex_ratings.json; Drex's answer is an opinion, never evidence
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT/'.env')          # the API key, as run.py loads it (never printed)
from stalk.config import IST  # noqa: E402
from stalk.llm.drex import DrexClient  # noqa: E402

COMMON = ("Rs10,000 Zerodha MIS paper account, NSE cash equity intraday, Rs5,000 a trade, ~0.106% round-trip charges. "
          "NIFTY's intraday drift since 2013 is -0.06% a day (gains come overnight).")
STRATEGIES = {
    "Panic buy (strict)": "On mornings NIFTY opens 0.5%+ down, buy the 2 biggest no-news gap-down small/midcaps at 09:20, 3% stop, exit 15:10. "
                          "10 years: +1.68% a trade after charges, 427 trades on 216 mornings, 52% won, 9 of 11 years positive; picks beat matched "
                          "ordinary stocks by +1.97%/day (t 6.3). Only ~22 mornings a year. Real fills not yet measured.",
    "H31 volume-climax fade (short)": "Short stocks up 3%+ by 09:30 on 10x normal volume whose bar closes in its lower half; 3% stop; exit 15:10; max 2/day. "
                          "2021-2025 design data (found by slicing, not yet confirmed): +0.08% a trade after charges, 1,378 trades (~300/yr), 52% won, "
                          "+0.15% better than similar stocks; fails 0.25%-worse fills.",
    "Opening-range breakout": "First 5-min close beyond the 09:15-09:30 range; stop at the other side; exit 15:10. 2021-2025: -0.18% a trade, -0.07% before charges, 39% won.",
    "CPR breakout (Pivot Boss)": "On narrow central-pivot-range days, trade a break of yesterday's high/low. 2021-2025: -0.26% a trade, -0.16% before charges, 36% won.",
    "5-EMA (Power of Stocks)": "Alert candle off the 5-EMA on 5-min, trade its break with a 3x target. 2021-2025: -0.20% a trade, -0.10% before charges, 25% won.",
    "Gap fill": "Fade 1-3% opening gaps back toward the prior close. 2021-2025: -0.09% a trade, +0.02% before charges, 52% won.",
    "VWAP 2-SD reversion": "After 10:30 fade a close 2 standard deviations from VWAP, target VWAP, 1% stop. 2021-2025: -0.26% a trade, 47% won.",
    "Sector leader pullback (long)": "Buy the sector leader after its first VWAP dip. 2021-2025: -0.20% a trade, -0.10% before charges, t -3.95.",
}
QUESTIONS = {
    "profitable_12m": {"type": "noul", "instructions": "Run exactly as described on paper for the next 12 months, this strategy makes money after charges.",
                       "criteria": {"true": "Net profit after charges over 12 months", "false": "Flat or a loss after charges"}},
    "edge_real": {"type": "noul", "instructions": "This strategy has a real, lasting edge in Indian markets rather than a lucky backtest.",
                  "criteria": {"true": "A real edge that should persist", "false": "Luck, noise or an edge too small to survive costs"}},
}


async def main():
    client = DrexClient(timeout_s=40)
    if not client.configured:
        raise SystemExit("Drex isn't set up (no API key in .env)")
    out = {}
    for name, facts in STRATEGIES.items():
        state = {"task": "Judge yes/no statements about one Indian intraday paper-trading strategy using the facts given.",
                 "account": COMMON, "strategy": name, "facts": facts}
        a = await client.ask(state, QUESTIONS)
        out[name] = {k: a.noul(k) for k in QUESTIONS} if a.ok else {"error": a.error}
        print(name, out[name], flush=True)
    (ROOT/'data/drex_ratings.json').write_text(json.dumps(dict(at=datetime.now(IST).isoformat(timespec='seconds'),
        model=client.model, note='Opinion only, not evidence', ratings=out), indent=1), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
