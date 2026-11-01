"""Where is there any intraday edge at all? A map of simple conditional drifts on two years of hourly bars.

  python scripts/edge_map.py

One row per stock-day (40 NIFTY 50 stocks, hourly bars, Nov 2023 → Oct 2026):
  gap   previous close → 09:15 open
  h1    09:15 open → 10:15 (the first hourly bar's close)
  rest  10:15 → 15:15 (last hourly bar's close; we must be flat by 15:15 anyway)
  prev  the previous day's close-to-close return
and the same moves for NIFTY, so each stock's move can be split into market and stock-specific parts.

Each question asks: if you traded `rest` in the direction a signal points, what is the average move, and does it beat
the 0.17% round-trip hurdle (₹10.60 charges on ₹10,000 plus 3 bps slippage a side)? t-stats average per day first,
so 40 stocks moving together on one day count once.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import DEFAULT_UNIVERSE, HOURLY, INDEX_SYMBOL, IST, SECTORS  # noqa: E402
from stalk.data.store import Store  # noqa: E402

HURDLE = 0.0017


def day_table(st: Store, symbol: str) -> pd.DataFrame:
    rows = st._rows("SELECT ts, o, h, l, c FROM bars WHERE symbol=? AND interval=? ORDER BY ts", (symbol, HOURLY))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame([dict(r) for r in rows])
    dt = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    df["day"] = dt.dt.strftime("%Y-%m-%d")
    df["hm"] = dt.dt.hour * 100 + dt.dt.minute
    out = []
    for day, g in df.groupby("day"):
        g = g.sort_values("ts")
        if g["hm"].iloc[0] != 915 or g["hm"].iloc[-1] != 1415 or len(g) < 6:
            continue
        out.append({"day": day, "open": g["o"].iloc[0], "c1015": g["c"].iloc[0], "close": g["c"].iloc[-1],
                    "hi1": g["h"].iloc[0], "lo1": g["l"].iloc[0], "hi": g["h"].max(), "lo": g["l"].min()})
    t = pd.DataFrame(out)
    if t.empty:
        return t
    t["prev_close"] = t["close"].shift(1)       # 15:15 price of the previous session (close enough to the close)
    t["gap"] = t["open"] / t["prev_close"] - 1
    t["h1"] = t["c1015"] / t["open"] - 1
    t["rest"] = t["close"] / t["c1015"] - 1
    t["oc"] = t["close"] / t["open"] - 1
    t["prev"] = t["prev_close"] / t["prev_close"].shift(1) - 1
    t["range1"] = (t["hi1"] - t["lo1"]) / t["open"]
    t["symbol"] = symbol
    return t.dropna()


def judge(name: str, df: pd.DataFrame, side: pd.Series, move: str = "rest") -> dict:
    """side is +1 / −1 / 0 per row; average signed move per trade, t across days."""
    m = side != 0
    x = (side[m] * df.loc[m, move])
    per_day = x.groupby(df.loc[m, "day"]).mean()
    n, nd = int(m.sum()), len(per_day)
    t = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(nd)) if nd > 2 and per_day.std() > 0 else 0.0
    hit = float((x > 0).mean()) if n else 0.0
    return {"question": name, "trades": n, "days": nd, "gross_bps": round(x.mean() * 1e4, 1),
            "net_bps": round((x.mean() - HURDLE) * 1e4, 1), "hit": round(hit, 3), "t_gross": round(float(t), 2)}


def main() -> None:
    st = Store()
    idx = day_table(st, INDEX_SYMBOL).set_index("day")
    frames = [day_table(st, s) for s in DEFAULT_UNIVERSE]
    df = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    for col in ("gap", "h1", "rest", "prev", "oc"):
        df["n_" + col] = df["day"].map(idx[col])
        df["x_" + col] = df[col] - df["n_" + col]        # stock-specific part (excess over NIFTY)
    df = df.dropna()
    df["sector"] = df["symbol"].map(SECTORS)
    print(f"{len(df):,} stock-days, {df['day'].nunique()} sessions, {df['day'].min()} → {df['day'].max()}\n")

    res = []
    sgn = np.sign
    # 1. continuation vs reversal of the first hour, raw and stock-specific
    res.append(judge("first hour continues (raw)", df, sgn(df["h1"])))
    res.append(judge("first hour, stock vs NIFTY, continues (excess move)", df, sgn(df["x_h1"]), "x_rest"))
    big = df["h1"].abs() > df.groupby("day")["h1"].transform(lambda s: s.abs().quantile(0.8))
    res.append(judge("biggest 20% first-hour movers continue", df, sgn(df["h1"]).where(big, 0)))
    # 2. the gap
    res.append(judge("gap continues (from 10:15)", df, sgn(df["gap"])))
    res.append(judge("gap continues, open to close", df, sgn(df["gap"]), "oc"))
    biggap = df["gap"].abs() > 0.01
    res.append(judge("gap > 1% fades, open to close", df, -sgn(df["gap"]).where(biggap, 0), "oc"))
    # 3. yesterday
    res.append(judge("yesterday's move reverses today (open to close)", df, -sgn(df["prev"]), "oc"))
    # 4. follow the index's first hour
    res.append(judge("trade with NIFTY's first hour (all stocks)", df, sgn(df["n_h1"])))
    strong = df["n_h1"].abs() > 0.004
    res.append(judge("NIFTY first hour > 0.4%: stocks follow", df, sgn(df["n_h1"]).where(strong, 0)))
    both = (sgn(df["h1"]) == sgn(df["n_h1"])) & strong
    res.append(judge("stock and NIFTY agree, NIFTY > 0.4%", df, sgn(df["h1"]).where(both, 0)))
    # 5. a quiet or wide first hour
    wide = df["range1"] > df.groupby("symbol")["range1"].transform(lambda s: s.rolling(20, min_periods=10).median().shift(1)) * 1.5
    res.append(judge("wide first hour (1.5× usual) continues", df, sgn(df["h1"]).where(wide, 0)))

    out = pd.DataFrame(res)
    pd.set_option("display.width", 200)
    print(out.to_string(index=False))

    # Index alone: NIFTY first hour → rest of day (the "intraday momentum" effect)
    i = idx.dropna()
    for thr in (0.0, 0.002, 0.004):
        m = i["h1"].abs() > thr
        x = np.sign(i.loc[m, "h1"]) * i.loc[m, "rest"]
        print(f"\nNIFTY itself, first hour > {thr:.1%}: {m.sum()} days, rest-of-day with it {x.mean() * 1e4:+.1f} bps, "
              f"hit {(x > 0).mean():.0%}, t {x.mean() / (x.std() / np.sqrt(len(x))):.2f}")
    # Typical size of the move we could catch
    print(f"\nAverage absolute rest-of-day move per stock: {df['rest'].abs().mean() * 1e4:.0f} bps; open to close: {df['oc'].abs().mean() * 1e4:.0f} bps; "
          f"hurdle {HURDLE * 1e4:.0f} bps")
    df.to_pickle(os.path.join(os.path.dirname(__file__), "..", "data", "edge_days.pkl"))


if __name__ == "__main__":
    main()
