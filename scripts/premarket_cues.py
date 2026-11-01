"""Do overnight global markets tell us how NIFTY will trade today? Five years of daily bars, free from Yahoo.

  python scripts/premarket_cues.py

For each Indian session D, the cue is the last move that finished *before* 09:15 IST on D:
  S&P 500 (^GSPC), Nasdaq (^IXIC), Brent crude (BZ=F), gold (GC=F), dollar-rupee (INR=X), US 10-year yield (^TNX), India VIX.
Each uses its last daily bar dated before D, so nothing from D itself can leak in (US sessions end 01:30–02:30 IST).

Targets: NIFTY's gap at the open (previous close → open) and NIFTY's open → close move on D.
The gap is already priced in at 09:15, so only the open → close part could be traded.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, INDEX_SYMBOL, IST  # noqa: E402
from stalk.data.store import Store  # noqa: E402

CUES = {"sp500": "^GSPC", "nasdaq": "^IXIC", "brent": "BZ=F", "gold": "GC=F", "usdinr": "INR=X", "us10y": "^TNX", "vix_in": "^INDIAVIX"}
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}"


def daily(sym: str) -> pd.Series:
    """Close by the exchange's own calendar date."""
    r = httpx.get(URL.format(sym), params={"interval": "1d", "range": "6y"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    res = r.json()["chart"]["result"][0]
    gmt = res["meta"].get("gmtoffset", 0)
    ts, cl = res["timestamp"], res["indicators"]["quote"][0]["close"]
    days = [datetime.fromtimestamp(t + gmt, timezone.utc).strftime("%Y-%m-%d") for t in ts]
    s = pd.Series(cl, index=days, dtype=float).dropna()
    return s[~s.index.duplicated(keep="last")]


def nifty_daily(cache: str) -> pd.DataFrame:
    """NIFTY 50 daily open and close for six years (the store keeps two)."""
    if os.path.exists(cache) and "--fresh" not in sys.argv:
        return pd.read_pickle(cache)
    r = httpx.get(URL.format("^NSEI"), params={"interval": "1d", "range": "6y"}, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    res = r.json()["chart"]["result"][0]
    days = [datetime.fromtimestamp(t, IST).strftime("%Y-%m-%d") for t in res["timestamp"]]
    q = res["indicators"]["quote"][0]
    n = pd.DataFrame({"o": q["open"], "c": q["close"]}, index=days).dropna()
    n = n[~n.index.duplicated(keep="last")]
    n.to_pickle(cache)
    return n


def main() -> None:
    cache = os.path.join(BASE_DIR, "data", "cues.pkl")
    if os.path.exists(cache) and "--fresh" not in sys.argv:
        cues = pd.read_pickle(cache)
    else:
        cues = {k: daily(v) for k, v in CUES.items()}
        pd.to_pickle(cues, cache)
    n = nifty_daily(os.path.join(BASE_DIR, "data", "nifty_6y.pkl"))
    n["gap"] = n["o"] / n["c"].shift(1) - 1
    n["oc"] = n["c"] / n["o"] - 1
    n["prev_oc"] = n["oc"].shift(1)
    n = n.dropna()

    feats = pd.DataFrame(index=n.index)
    for k, s in cues.items():
        ret = s.pct_change() if k != "us10y" else s.diff()       # yield: change in points
        # last value dated strictly before the Indian day
        pos = np.searchsorted(ret.index.values, n.index.values, side="left") - 1
        feats[k] = [ret.iloc[p] if p >= 0 else np.nan for p in pos]
    df = n.join(feats).dropna()
    print(f"{len(df)} NIFTY sessions, {df.index.min()} → {df.index.max()}\n")

    print("Correlation with NIFTY's gap at the open (priced in by 09:15) and with open → close (tradable):")
    print(f"{'cue':<8}{'gap':>8}{'open→close':>12}")
    for k in CUES:
        print(f"{k:<8}{df[k].corr(df['gap']):>8.2f}{df[k].corr(df['oc']):>12.2f}")
    print(f"{'gap':<8}{'':>8}{df['gap'].corr(df['oc']):>12.2f}   (does the gap itself continue or fade?)")

    # The tradable question: trade NIFTY open → close in the cue's direction
    print("\nTrade NIFTY open → close in the direction the cue points (gross, before the ~17 bps hurdle):")
    for k in ("sp500", "nasdaq", "brent", "gold", "usdinr"):
        sign = np.sign(df[k]) * (-1 if k in ("brent", "usdinr") else 1)   # oil up and rupee weaker are bad for India
        x = sign * df["oc"]
        big = df[k].abs() > df[k].abs().quantile(0.75)
        xb = x[big]
        print(f"  {k:<7} all days {x.mean() * 1e4:+5.1f} bps (hit {(x > 0).mean():.0%}, t {x.mean() / (x.std() / np.sqrt(len(x))):+.2f})"
              f"   biggest 25% of cue moves {xb.mean() * 1e4:+5.1f} bps (hit {(xb > 0).mean():.0%}, n {len(xb)})")
    # Gap and the day: when the US was strongly up/down and NIFTY gapped the same way, did the day follow or fade?
    for lab, m in (("US up >1% and NIFTY gapped up", (df["sp500"] > 0.01) & (df["gap"] > 0)),
                   ("US down >1% and NIFTY gapped down", (df["sp500"] < -0.01) & (df["gap"] < 0))):
        print(f"\n{lab}: {m.sum()} days, NIFTY open → close {df.loc[m, 'oc'].mean() * 1e4:+.1f} bps on average, "
              f"up on {(df.loc[m, 'oc'] > 0).mean():.0%} of them")
    df.to_pickle(os.path.join(BASE_DIR, "data", "cue_days.pkl"))


if __name__ == "__main__":
    main()
