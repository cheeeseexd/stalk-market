"""Execution check for the frozen panic-buy rule on the last 60 days of 5-minute bars (stalk/sim/panic.py).

  python scripts/panic_5min.py

Not evidence: these days overlap the period the idea came from. It checks that the executable version (rank at 09:20, enter at the
09:20 open, 3% stop on 5-minute lows, out at 15:10, whole shares, real charges) behaves like the research version.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402
from stalk.sim import panic  # noqa: E402


def main() -> None:
    bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_5m.pkl"))
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")["index"]
    ev = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_events.pkl"))
    ev = ev[(ev["purpose"].fillna("") + " " + ev["detail"].fillna("")).str.contains("result", case=False)]
    by_day = {}
    for s, d in bars.items():
        t = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert(IST)
        d = d.assign(day=t.dt.strftime("%Y-%m-%d").values, hm=(t.dt.hour * 100 + t.dt.minute).values)
        by_day[s] = {k: g for k, g in d.groupby("day")}
    days = sorted(by_day["NIFTY 50"])
    near = set()
    for sym, m in ev[["symbol", "day"]].drop_duplicates().itertuples(index=False):
        i = int(np.searchsorted(days, m))
        for k in (i - 1, i, i + 1):
            if 0 <= k < len(days):
                near.add((sym, days[k]))
    trades, fired = [], []
    for i in range(1, len(days)):
        day, prev = days[i], days[i - 1]
        n_today, n_prev = by_day["NIFTY 50"].get(day), by_day["NIFTY 50"].get(prev)
        if n_today is None or n_prev is None or not panic.market_qualifies(n_today["o"].iloc[0], n_prev["c"].iloc[-1]):
            continue
        cands = []
        for s in by_day:
            if s == "NIFTY 50" or s in DEFAULT_UNIVERSE or s not in meta.index:
                continue
            g, p = by_day[s].get(day), by_day[s].get(prev)
            if g is None or p is None or g["hm"].iloc[0] != 915:
                continue
            first = g.iloc[0]
            cands.append(panic.Candidate(s, float(p["c"].iloc[-1]), float(first["o"]), meta[s], (s, day) in near, first["h"] == first["l"]))
        picks = panic.pick(cands)
        fired.append((day, [c.symbol for c in picks]))
        for c in picks:
            g = by_day[c.symbol][day]
            t = panic.walk(day, c, g[["ts", "o", "h", "l", "c", "v"]].values.tolist(), g["hm"].tolist())
            if t:
                trades.append(panic.as_dict(t))
    t = pd.DataFrame(trades)
    print(f"{days[0]} → {days[-1]}: {len(fired)} NIFTY gap-down mornings: {fired}\n")
    if t.empty:
        print("No trades.")
        return
    pd.set_option("display.width", 200)
    print(t.to_string(index=False))
    print(f"\n{len(t)} trades, net ₹{t['net'].sum():,.2f} on ₹10,000 (charges ₹{t['costs'].sum():,.2f}), "
          f"{(t['net'] > 0).mean():.0%} won, stops {int((t['reason'] == 'stop').sum())}")


if __name__ == "__main__":
    main()
