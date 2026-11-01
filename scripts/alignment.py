"""H4: do the daily trend, NIFTY's first hour and the stock's first hour agreeing predict the rest of the day? (RUNBOOK 2026-11-03)

  python scripts/alignment.py            # NIFTY 50 stocks (design)
  python scripts/alignment.py --confirm  # the 240 wider stocks

Daily trend uses only completed days: yesterday's close above its 20-day average, and that average above its value 5 days earlier.
Entry 10:15 (after the first hourly bar), exit 15:15.
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

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402

CHARGES = 0.00106
SLIP = {"nifty50": 0.0003, "next50": 0.0004, "midcap": 0.0006, "smallcap": 0.0010}


def days_of(d: pd.DataFrame) -> pd.DataFrame:
    t = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert(IST)
    d = d.assign(day=t.dt.strftime("%Y-%m-%d"), hm=t.dt.hour * 100 + t.dt.minute)
    g = d.groupby("day")
    out = pd.DataFrame({"f": g["hm"].first(), "l": g["hm"].last(), "open": g["o"].first(), "c1015": g["c"].first(), "close": g["c"].last()})
    out = out[(out["f"] == 915) & (out["l"] == 1415)].copy()
    out["h1"] = out["c1015"] / out["open"] - 1
    out["rest"] = out["close"] / out["c1015"] - 1
    ma = out["close"].rolling(20).mean()
    up = (out["close"] > ma) & (ma > ma.shift(5))
    down = (out["close"] < ma) & (ma < ma.shift(5))
    out["trend"] = (up.astype(int) - down.astype(int)).shift(1)        # yesterday's state: known before today's open
    return out.dropna().reset_index()


def main() -> None:
    confirm = "--confirm" in sys.argv
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")["index"]
    if confirm:
        bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_60m.pkl"))
        frames = {s: bars[s] for s in bars if s in meta.index and s not in DEFAULT_UNIVERSE}
        idx = bars["NIFTY 50"]
    else:
        con = sqlite3.connect(os.path.join(BASE_DIR, "data", "stalk.db"))
        q = "SELECT ts, o, h, l, c FROM bars WHERE symbol=? AND interval='60minute' ORDER BY ts"
        frames = {s: pd.read_sql(q, con, params=(s,)) for s in DEFAULT_UNIVERSE}
        idx = pd.read_sql(q, con, params=("NIFTY 50",))
    n = days_of(idx).set_index("day")
    df = pd.concat([days_of(d).assign(symbol=s) for s, d in frames.items() if len(d)], ignore_index=True)
    df["n_h1"] = df["day"].map(n["h1"])
    df = df.dropna()
    df["tier"] = df["symbol"].map(meta).fillna("nifty50")
    df["cost"] = CHARGES + 2 * df["tier"].map(SLIP)
    s_stock, s_idx = np.sign(df["h1"]), np.sign(df["n_h1"])
    rules = {
        "stock first hour only": s_stock,
        "stock + NIFTY first hour agree": s_stock.where(s_stock == s_idx, 0),
        "stock first hour + daily trend agree": s_stock.where(s_stock == df["trend"], 0),
        "all three agree (H4)": s_stock.where((s_stock == s_idx) & (s_stock == df["trend"]), 0),
        "all three agree, first hour > 0.5%": s_stock.where((s_stock == s_idx) & (s_stock == df["trend"]) & (df["h1"].abs() > 0.005), 0),
    }
    rows = []
    for name, side in rules.items():
        m = side != 0
        x = side[m] * df.loc[m, "rest"]
        net = x - df.loc[m, "cost"]
        per_day = net.groupby(df.loc[m, "day"]).mean()
        t = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day)))
        rows.append({"rule": name, "trades": int(m.sum()), "days": len(per_day), "gross_bps": round(x.mean() * 1e4, 1),
                     "net_bps": round(net.mean() * 1e4, 1), "hit": round(float((x > 0).mean()), 3), "t_net_by_day": round(float(t), 2)})
    print(f"{'Confirm, 240 wider stocks' if confirm else 'NIFTY 50 (design)'}: {len(df):,} stock-days, {df['day'].min()} → {df['day'].max()}\n")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
