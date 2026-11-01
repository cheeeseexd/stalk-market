"""H5: multi-day drift after a big positive results reaction, long only, delivery (RUNBOOK 2026-11-03). Research only: not intraday.

  python scripts/results_swing.py

Reaction day R: the results meeting day M or M+1, whichever moved more (close to close). If that move is above the threshold,
buy at R+1's open and sell at the close h trading days later. Measured as excess over the equal-weighted average of all stocks
over the same days (today's index lists flatter any long-only result, so raw returns would mislead).
"""
from __future__ import annotations

import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, IST  # noqa: E402

DELIVERY = 0.00404   # STT 0.1% a side, stamp, exchange, DP ₹15.34 + GST on ₹10,000
SLIP = {"nifty50": 0.0003, "next50": 0.0004, "midcap": 0.0006, "smallcap": 0.0010}


def main() -> None:
    bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d.pkl"))
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")["index"]
    con = sqlite3.connect(os.path.join(BASE_DIR, "data", "stalk.db"))
    ev = pd.concat([pd.read_sql("SELECT symbol, day, purpose, detail FROM events", con),
                    pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_events.pkl"))[["symbol", "day", "purpose", "detail"]]])
    ev = ev[(ev["purpose"].fillna("") + " " + ev["detail"].fillna("")).str.contains("result", case=False)].drop_duplicates(["symbol", "day"])

    o = {}, {}
    opens, closes = {}, {}
    for s, d in bars.items():
        if s == "NIFTY 50":
            continue
        day = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
        opens[s] = pd.Series(d["o"].values, index=day.values)
        closes[s] = pd.Series(d["c"].values, index=day.values)
    O = pd.DataFrame(opens).sort_index()
    C = pd.DataFrame(closes).sort_index()
    O = O[~O.index.duplicated()]; C = C[~C.index.duplicated()]
    ret1 = C / C.shift(1) - 1
    days = C.index.values

    def fwd(h: int) -> pd.DataFrame:
        """Return from the open of day t to the close of day t+h−1, for every stock and day."""
        return C.shift(-(h - 1)) / O - 1

    rows = []
    for th in (0.02, 0.03, 0.05):
        for h in (5, 10, 20):
            F = fwd(h)
            bench = F.mean(axis=1)
            trades = []
            for sym, m in ev[["symbol", "day"]].itertuples(index=False):
                if sym not in C.columns:
                    continue
                i = int(np.searchsorted(days, m))
                if i + 2 >= len(days):
                    continue
                cands = [k for k in (i, i + 1) if k < len(days) and not np.isnan(ret1.iat[k, C.columns.get_loc(sym)])]
                if not cands:
                    continue
                r = max(cands, key=lambda k: abs(ret1.iat[k, C.columns.get_loc(sym)]))
                move = ret1.iat[r, C.columns.get_loc(sym)]
                e = r + 1
                if move > th and e < len(days) and not np.isnan(F.iat[e, C.columns.get_loc(sym)]):
                    raw = F.iat[e, C.columns.get_loc(sym)]
                    cost = DELIVERY + 2 * SLIP.get(meta.get(sym, "nifty50"), 0.0006)
                    trades.append({"symbol": sym, "entry_day": days[e], "raw": raw, "excess": raw - bench.iat[e], "cost": cost})
            t = pd.DataFrame(trades).drop_duplicates(["symbol", "entry_day"])
            t["net_excess"] = t["excess"] - t["cost"]
            t["year"] = t["entry_day"].str[:4]
            x = t["net_excess"]
            by_year = t.groupby("year")["net_excess"].mean().mul(1e4).round(0).to_dict()
            rows.append({"move >": f"{th:.0%}", "hold": h, "trades": len(t), "raw_bps": round(t["raw"].mean() * 1e4),
                         "excess_bps": round(t["excess"].mean() * 1e4), "net_excess_bps": round(x.mean() * 1e4),
                         "hit": round(float((x > 0).mean()), 2), "t": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2),
                         "by_year_net": by_year})
            if th == 0.03 and h == 10:
                t.to_pickle(os.path.join(BASE_DIR, "data", "results_swing_trades.pkl"))
    pd.set_option("display.width", 220)
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
