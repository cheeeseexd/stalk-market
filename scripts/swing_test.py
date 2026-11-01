"""Daily-chart swing test under the five rules (RULES.md), on stored daily bars (about 5 years, NIFTY 50 stocks).

This is a research script, not part of the intraday engine: positions are held for days as delivery (CNC), with no leverage.
Rules, fixed in advance:
  entry   close above the prior 20-day high while above the 200-day average; buy at the next day's open
  stop    just below the signal day's low, never moved; filled at the open if the market gaps through it
  target  3R; exit at the close after 20 sessions if neither stop nor target is hit
  risk    ₹100 a trade on ₹10,000, at most 2 positions open, total position value at most ₹10,000 (no leverage)
  streak  3 losses in a row → no new entries for 3 sessions
Charges: Zerodha delivery (no brokerage, STT 0.1% on buy and sell, exchange 0.00297%, SEBI, stamp 0.015% on buy,
GST 18% on exchange + SEBI, DP charge ₹15.93 per sell).

  python scripts/swing_test.py [--target 3] [--lookback 20]
"""
from __future__ import annotations

import argparse
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from stalk.config import Settings  # noqa: E402
from stalk.data.store import Store  # noqa: E402
from stalk.research.features import session_columns  # noqa: E402
from stalk.research.validate import stats  # noqa: E402


def delivery_cost(qty: int, buy: float, sell: float) -> float:
    bv, sv = qty * buy, qty * sell
    stt = 0.001 * (bv + sv)
    exch = 0.0000297 * (bv + sv)
    sebi = 0.000001 * (bv + sv)
    stamp = 0.00015 * bv
    gst = 0.18 * (exch + sebi)
    return stt + exch + sebi + stamp + gst + 15.93


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=3.0)
    ap.add_argument("--lookback", type=int, default=20)
    ap.add_argument("--hold", type=int, default=20)
    a = ap.parse_args()
    s, st = Settings(), Store()
    frames = {}
    for sym in s.universe:
        d = st.load_bars(sym, "day")
        if len(d) < 260:
            continue
        d = session_columns(d)[["day", "o", "h", "l", "c"]].reset_index(drop=True)
        d["hi_n"] = d["h"].rolling(a.lookback).max().shift(1)
        d["sma200"] = d["c"].rolling(200).mean()
        d["sig"] = (d["c"] > d["hi_n"]) & (d["c"] > d["sma200"])
        frames[sym] = d
    days = sorted(set().union(*[set(f["day"]) for f in frames.values()]))
    idx = {sym: {dd: i for i, dd in enumerate(f["day"])} for sym, f in frames.items()}

    capital, risk, cap_value, max_pos = 10000.0, 100.0, 10000.0, 2
    equity = capital
    open_pos, trades = [], []
    streak, locked = 0, 0
    for day in days:
        # manage open positions on today's bar
        for p in list(open_pos):
            i = idx[p["sym"]].get(day)
            if i is None:
                continue
            o, h, l, c = (frames[p["sym"]].loc[i, k] for k in ("o", "h", "l", "c"))
            p["held"] += 1
            exit_px = None
            if l <= p["stop"]:
                exit_px, why = min(o, p["stop"]), "stop"
            elif h >= p["target"]:
                exit_px, why = max(o, p["target"]), "target"
            elif p["held"] >= a.hold:
                exit_px, why = c, "time"
            if exit_px is not None:
                gross = (exit_px - p["entry"]) * p["qty"]
                cost = delivery_cost(p["qty"], p["entry"], exit_px)
                net = gross - cost
                equity += net
                trades.append({"sym": p["sym"], "in": p["day"], "out": day, "net": net, "gross": gross, "cost": cost,
                               "r": net / risk, "why": why, "held": p["held"]})
                open_pos.remove(p)
                streak = streak + 1 if net < 0 else 0
                if streak >= 3:
                    locked, streak = 3, 0
        # entries queued from yesterday's signals fill at today's open
        if locked > 0:
            locked -= 1
        else:
            for sym, f in frames.items():
                i = idx[sym].get(day)
                if i is None or i == 0 or len(open_pos) >= max_pos or any(p["sym"] == sym for p in open_pos):
                    continue
                y = f.loc[i - 1]
                if not y["sig"]:
                    continue
                entry = f.loc[i, "o"]
                stop = y["l"] * 0.998
                dist = entry - stop
                if dist <= 0:
                    continue
                used = sum(p["qty"] * p["entry"] for p in open_pos)
                qty = int(math.floor(min(risk / dist, (min(cap_value, equity) - used) / entry)))
                if qty < 1:
                    continue
                open_pos.append({"sym": sym, "day": day, "entry": entry, "stop": stop, "target": entry + a.target * dist,
                                 "qty": qty, "held": 0})
    t = pd.DataFrame(trades)
    if t.empty:
        print("no trades")
        return
    t["year"] = t["out"].str[:4]
    x = stats(t["r"].to_numpy(), t["net"].to_numpy())
    print(f"Swing test, daily bars {days[0]} → {days[-1]}, ₹10,000, target {a.target}R, {a.lookback}-day breakout")
    print(f"Trades {x['n']}, win {x['win']:.0%}, mean {x['mean_r']:+.3f}R, t {x['t']:+.2f}, net ₹{t['net'].sum():,.0f} "
          f"(gross ₹{t['gross'].sum():,.0f}, charges ₹{t['cost'].sum():,.0f}), end equity ₹{equity:,.0f}")
    print(f"Exits: {t['why'].value_counts().to_dict()}, average hold {t['held'].mean():.1f} sessions")
    eq = capital + t.sort_values("out")["net"].cumsum()
    print(f"Worst drawdown ₹{(eq.cummax() - eq).max():,.0f}")
    print("By year:")
    for y, g in t.groupby("year"):
        gs = stats(g["r"].to_numpy(), g["net"].to_numpy())
        print(f"  {y}: {gs['n']:>3} trades, net ₹{g['net'].sum():>8,.0f}, mean {gs['mean_r']:+.3f}R, win {gs['win']:.0%}")


if __name__ == "__main__":
    main()
