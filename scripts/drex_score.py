"""Ask Drex about every trade in a backtest account and score its answers against what happened.

  python scripts/drex_score.py                    # latest campaign longer than 20 days, every-signal account
  python scripts/drex_score.py --campaign c123 --account every_signal

Drex gets what the engine knew at entry (side, strategy, time, Time Machine odds), never the ticker or date.
Reports the Brier score (lower is better) for Drex, the Time Machine and the plain base rate.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

from stalk.config import BASE_DIR, IST, load_settings  # noqa: E402
from stalk.data.store import Store  # noqa: E402
from stalk.llm.drex import QUESTIONS, DrexClient, setup_state  # noqa: E402

load_dotenv(os.path.join(BASE_DIR, ".env"))


def brier(ps, ys):
    pairs = [(p, y) for p, y in zip(ps, ys) if p is not None]
    return (sum((p - y) ** 2 for p, y in pairs) / len(pairs), len(pairs)) if pairs else (None, 0)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign")
    ap.add_argument("--account", default="every_signal")
    a = ap.parse_args()
    st = Store()
    cid = a.campaign or next(c["id"] for c in st.campaigns(20) if c["summary"]["days"] > 20)
    trades = list(reversed(st.trades(account_prefix=f"{cid}:{a.account}", limit=5000)))
    s = load_settings()
    client = DrexClient(timeout_s=20)
    if not client.configured:
        sys.exit("DREX_API_KEY is not set in .env")
    sem = asyncio.Semaphore(4)

    async def one(t):
        clock = datetime.fromtimestamp(t["entry_ts"] - 300, IST).strftime("%H:%M")
        setup = {"side": t["side"], "strategy": t["strategy"], "clock": clock,
                 "tm": {"p_target": t["tm_target"], "p_stop": t["tm_stop"]}}
        async with sem:
            ans = await client.ask(setup_state(setup, s.target_r), QUESTIONS)
        return ans.noul("reaches_target") if ans.ok else None

    drex = await asyncio.gather(*(one(t) for t in trades))
    hit = [1 if t["reason"] == "target" else 0 for t in trades]
    base = sum(hit) / len(hit) if hit else 0
    b_drex, n_drex = brier(drex, hit)
    b_tm, n_tm = brier([t["tm_target"] for t in trades], hit)
    b_base, _ = brier([base] * len(hit), hit)
    print(f"Campaign {cid}, account {a.account}: {len(trades)} trades, {sum(hit)} hit target ({base:.0%})")
    print(f"Brier (lower is better): Drex {b_drex:.4f} on {n_drex} answers | Time Machine {b_tm:.4f} on {n_tm} | base rate {b_base:.4f}")
    took = [(p, t) for p, t in zip(drex, trades) if p is not None and p >= 0.5]
    print(f"If we had only taken trades Drex rated ≥ 50%: {len(took)} trades, net ₹{sum(t['net'] for _, t in took):,.2f} "
          f"(all trades: ₹{sum(t['net'] for t in trades):,.2f})")
    print(f"Drex calls {client.calls}, errors {client.errors}, last error {client.last_error}")


if __name__ == "__main__":
    asyncio.run(main())
