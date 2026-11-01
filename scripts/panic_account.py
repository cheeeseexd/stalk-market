"""Ten years of panic mornings played as one ₹10,000 account, one day at a time (NSE bhavcopy, every liquid stock).

  python scripts/panic_account.py      # needs data/bhav from scripts/panic_bhav.py

Each panic morning: the 2 biggest eligible gap-downs (locked stocks kept), ₹5,000 each at the official open, 3% stop, out at the official close
(our 15:10 exit is about 8 bps worse, taken off). Zerodha MIS charges and 10 bps slippage a side. Tax at 30% on each year's net profit
(intraday is speculative business income, taxed at your slab). Writes data/panic_journal.csv, one row per trade.
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
from stalk.market.costs import round_trip_cost  # noqa: E402
from scripts.panic_bhav import CACHE, MIN_VALUE  # noqa: E402

SLIP, STOP, PER_TRADE, TAX = 0.0010, 0.03, 5000.0, 0.30


def main() -> None:
    nb = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d_10y.pkl"))["NIFTY 50"]
    nd = pd.to_datetime(nb["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    n = pd.DataFrame({"o": nb["o"].values, "c": nb["c"].values}, index=nd.values)
    n = n[~n.index.duplicated(keep="last")]
    n["gap"] = n["o"] / n["c"].shift(1) - 1
    n["prev_day"] = pd.Series(n.index, index=n.index).shift(1)
    n = n[(n.index >= "2016-11-01") & (n.index <= "2026-10-31")].dropna()
    rows = []
    for day in n.index[n["gap"] < -0.005]:
        a, b = os.path.join(CACHE, f"{day}.pkl"), os.path.join(CACHE, f"{n.at[day, 'prev_day']}.pkl")
        if not (os.path.exists(a) and os.path.exists(b)):
            continue
        today, prev = pd.read_pickle(a), pd.read_pickle(b)
        if today.empty or prev.empty:
            continue
        near = pd.read_pickle(os.path.join(CACHE, f"bm_{day}.pkl")) or set()
        u = today.join(prev[["value"]].rename(columns={"value": "prev_value"}), how="inner")
        u = u[(u["prev_value"] >= MIN_VALUE) & (u["prev_close"] >= 50) & ~u.index.isin(DEFAULT_UNIVERSE) & ~u.index.isin(near)]
        u["gap"] = u["open"] / u["prev_close"] - 1
        for sym, r in u[u["gap"] < -0.02].sort_values("gap").head(2).iterrows():
            entry = r["open"] * (1 + SLIP)
            qty = int(PER_TRADE // entry)
            if qty <= 0:
                continue
            stopped = r["low"] <= entry * (1 - STOP)
            exit_px = (entry * (1 - STOP) * 0.998 if stopped else r["close"] * (1 - 0.0008)) * (1 - SLIP)
            gross = (exit_px - entry) * qty
            costs = round_trip_cost(qty, entry, exit_px)
            rows.append({"day": day, "nifty_gap": round(n.at[day, "gap"], 4), "symbol": sym, "gap": round(r["gap"], 4), "qty": qty,
                         "entry": round(entry, 2), "exit": round(exit_px, 2), "result": "stopped" if stopped else "sold at 15:10",
                         "gross": round(gross, 2), "charges": round(costs, 2), "net": round(gross - costs, 2)})
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(BASE_DIR, "data", "panic_journal.csv"), index=False)
    days = t.groupby("day")["net"].sum()
    eq = 10000 + days.cumsum()
    dd = (eq.cummax() - eq).max()
    streak = (days < 0).astype(int).groupby((days >= 0).cumsum()).sum().max()
    yr = t.groupby(t["day"].str[:4]).agg(mornings=("day", "nunique"), trades=("net", "size"), net=("net", "sum"), charges=("charges", "sum"))
    yr["tax_30pct"] = (yr["net"].clip(lower=0) * TAX).round(0)
    yr["after_tax"] = (yr["net"] - yr["tax_30pct"]).round(0)
    yr["net"] = yr["net"].round(0); yr["charges"] = yr["charges"].round(0)
    print(yr.to_string())
    print(f"\n{len(t)} trades on {len(days)} panic mornings. Net ₹{t['net'].sum():,.0f} on ₹10,000 over 10 years "
          f"(₹{t['net'].sum() / 10:,.0f} a year), after 30% tax ₹{yr['after_tax'].sum():,.0f}.")
    print(f"Per panic morning: average ₹{days.mean():,.0f}, median ₹{days.median():,.0f}, best ₹{days.max():,.0f}, worst ₹{days.min():,.0f}; "
          f"green mornings {(days > 0).mean():.0%}. Stopped trades {(t['result'] == 'stopped').mean():.0%}.")
    print(f"Worst drawdown ₹{dd:,.0f}; longest run of losing mornings {int(streak)}.")
    for lo, hi in ((-0.009, -0.005), (-0.015, -0.009), (-1, -0.015)):
        m = (t["nifty_gap"] > lo) & (t["nifty_gap"] <= hi) if lo > -1 else t["nifty_gap"] <= hi
        print(f"  NIFTY gap {lo:.1%} to {hi:.1%}: {m.sum()} trades, average ₹{t.loc[m, 'net'].mean():,.1f}, won {(t.loc[m, 'net'] > 0).mean():.0%}")


if __name__ == "__main__":
    main()
