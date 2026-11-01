"""Stocks in play: does unusual opening volume tell us which way a stock will keep moving? (RUNBOOK 2026-11-03, H1 and on)

  python scripts/in_play.py              # design half only (5 Aug → 16 Sep)
  python scripts/in_play.py --confirm    # the untouched half; only for a rule frozen in RUNBOOK.md first

One row per stock-day from 5-minute bars (wide universe, scripts/fetch_wide.py):
  r15    09:15 open → 09:30 (the first three 5-minute bars)
  rvol   09:15–09:30 volume ÷ the stock's median of that window over the previous 10 sessions
  entry  open of the 09:30 bar (the window has closed by then)
  exit   open of the 15:10 bar (flat by 15:10)
Costs per round trip: ₹10.60 charges on ₹10,000 (0.106%) plus slippage by tier, both sides.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, IST  # noqa: E402

CHARGES = 0.00106
SLIP = {"nifty50": 0.0003, "next50": 0.0004, "midcap": 0.0006, "smallcap": 0.0010}
SPLIT = "2026-09-17"   # first day of the untouched confirm half


def hurdle(tier: pd.Series) -> pd.Series:
    return CHARGES + 2 * tier.map(SLIP)


def build() -> pd.DataFrame:
    cache = os.path.join(BASE_DIR, "data", "inplay_days.pkl")
    if os.path.exists(cache) and "--fresh" not in sys.argv:
        return pd.read_pickle(cache)
    bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_5m.pkl"))
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")
    out = []
    for sym, d in bars.items():
        if sym == "NIFTY 50" or sym not in meta.index:
            continue
        d = d.copy()
        t = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert(IST)
        d["day"], d["hm"] = t.dt.strftime("%Y-%m-%d"), t.dt.hour * 100 + t.dt.minute
        for day, g in d.groupby("day"):
            g = g.set_index("hm")
            if not {915, 920, 925, 930, 1510}.issubset(g.index):
                continue
            first = g.loc[[915, 920, 925]]
            after = g[(g.index >= 930) & (g.index < 1510)]
            out.append({"day": day, "symbol": sym, "open": g.at[915, "o"], "c930": g.at[925, "c"], "v15": first["v"].sum(),
                        "entry": g.at[930, "o"], "exit": g.at[1510, "o"], "close": g["c"].iloc[-1],
                        "hi_after": after["h"].max(), "lo_after": after["l"].min(), "day_hi": g["h"].max(), "day_lo": g["l"].min(),
                        "value15": (first["v"] * first["c"]).sum()})
    df = pd.DataFrame(out).sort_values(["symbol", "day"])
    df["tier"] = df["symbol"].map(meta["index"])
    df["prev_close"] = df.groupby("symbol")["close"].shift(1)
    df["gap"] = df["open"] / df["prev_close"] - 1
    df["r15"] = df["c930"] / df["open"] - 1
    df["rvol"] = df["v15"] / df.groupby("symbol")["v15"].transform(lambda s: s.rolling(10, min_periods=5).median().shift(1))
    df["adr"] = ((df["day_hi"] - df["day_lo"]) / df["open"]).groupby(df["symbol"]).transform(lambda s: s.rolling(10, min_periods=5).mean().shift(1))
    df["move"] = df["exit"] / df["entry"] - 1
    df["cont"] = np.sign(df["r15"]) * df["move"]
    df = df.dropna(subset=["rvol", "adr", "gap"])
    df.to_pickle(cache)
    return df


def summarise(name: str, x: pd.Series, days: pd.Series, hurdles: pd.Series) -> dict:
    per_day = (x - hurdles).groupby(days).mean()
    t = per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day))) if len(per_day) > 2 and per_day.std() > 0 else 0.0
    return {"group": name, "n": len(x), "days": len(per_day), "gross_bps": round(x.mean() * 1e4, 1),
            "hurdle_bps": round(hurdles.mean() * 1e4, 1), "net_bps": round((x - hurdles).mean() * 1e4, 1),
            "hit": round(float((x > 0).mean()), 3), "t_net": round(float(t), 2)}


def h1(df: pd.DataFrame) -> None:
    df = df.copy()
    df["h"] = hurdle(df["tier"])
    df["rv_dec"] = df.groupby("day")["rvol"].rank(pct=True).mul(10).clip(upper=9.999).astype(int) + 1
    rows = []
    for dec in range(1, 11):
        m = df["rv_dec"] == dec
        rows.append(summarise(f"rvol decile {dec}", df.loc[m, "cont"], df.loc[m, "day"], df.loc[m, "h"]))
    print("Continuation of the opening 15 minutes, 09:30 → 15:10, by relative-volume decile (all tiers):")
    print(pd.DataFrame(rows).to_string(index=False))

    top = df["rv_dec"] == 10
    rows = []
    for tier in SLIP:
        m = top & (df["tier"] == tier)
        rows.append(summarise(f"top decile, {tier}", df.loc[m, "cont"], df.loc[m, "day"], df.loc[m, "h"]))
    print("\nTop relative-volume decile, by tier:")
    print(pd.DataFrame(rows).to_string(index=False))

    # Sol's control: is it volume, or just a big opening move? Compare high vs low rvol within the same |r15| bucket.
    df["size_bucket"] = df.groupby("day")["r15"].transform(lambda s: pd.qcut(s.abs(), 3, labels=False, duplicates="drop"))
    rows = []
    for b in (0, 1, 2):
        for lab, m in (("low rvol (<1)", df["rvol"] < 1), ("high rvol (>2)", df["rvol"] > 2)):
            mm = (df["size_bucket"] == b) & m
            rows.append(summarise(f"opening move tercile {b + 1}, {lab}", df.loc[mm, "cont"], df.loc[mm, "day"], df.loc[mm, "h"]))
    print("\nSame-size opening moves, low vs high relative volume (controls for the move's size):")
    print(pd.DataFrame(rows).to_string(index=False))
    # And the reverse bet, for honesty
    m = top
    print("\nTop decile, betting on reversal instead:", summarise("reversal", -df.loc[m, "cont"], df.loc[m, "day"], df.loc[m, "h"]))


def main() -> None:
    df = build()
    if "--confirm" in sys.argv:
        print("CONFIRM HALF: run only for a rule frozen in RUNBOOK.md")
        df = df[df["day"] >= SPLIT]
    else:
        df = df[df["day"] < SPLIT]
    print(f"{len(df):,} stock-days, {df['day'].nunique()} sessions, {df['day'].min()} → {df['day'].max()}, "
          f"{df['symbol'].nunique()} stocks\n")
    h1(df)


if __name__ == "__main__":
    main()
