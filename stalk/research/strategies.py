"""Rule strategies. Each one turns a feature frame into signals at bar closes: +1 long, -1 short, 0 none.

Every rule comes from a thesis someone published, and each has one main parameter with a small grid
around a value fixed in advance. The grid exists for the plateau check, not for picking the best value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Strategy:
    key: str
    name: str
    thesis: str
    source: str
    param: str
    default: float
    grid: List[float] = field(default_factory=list)
    fn: Callable[[pd.DataFrame, float], np.ndarray] = None  # type: ignore[assignment]

    def signals(self, f: pd.DataFrame, value: float = None) -> np.ndarray:
        return self.fn(f, self.default if value is None else value)


def _arr(s: pd.Series) -> np.ndarray:
    return s.to_numpy(dtype=float)


def orb_breakout(f: pd.DataFrame, window: float) -> np.ndarray:
    """First 5-minute close beyond the opening range, on the VWAP side, with volume, indices agreeing."""
    w = int(window)
    hi, lo = _arr(f[f"or_hi_{w}"]), _arr(f[f"or_lo_{w}"])
    c, cp, vwap, rvol = _arr(f["c"]), _arr(f["c_prev"]), _arr(f["vwap"]), _arr(f["rvol"])
    calm = ~f["idx_div"].to_numpy(dtype=bool)
    with np.errstate(invalid="ignore"):
        long_ = (c > hi) & (cp <= hi) & (c > vwap) & (rvol >= 1.5) & calm
        short = (c < lo) & (cp >= lo) & (c < vwap) & (rvol >= 1.5) & calm
    return long_.astype(int) - short.astype(int)


def momentum_52w(f: pd.DataFrame, near: float) -> np.ndarray:
    """Long only: within `near` of the 52-week high, heavy volume, above VWAP, short EMA above long EMA."""
    c, hi52, vwap, rvol = _arr(f["c"]), _arr(f["hi52"]), _arr(f["vwap"]), _arr(f["rvol"])
    with np.errstate(invalid="ignore"):
        long_ = (c >= hi52 * (1 - near)) & (rvol >= 3.0) & (c > vwap) & (_arr(f["ema9"]) > _arr(f["ema21"]))
    return long_.astype(int)


def vwap_pullback(f: pd.DataFrame, trend: float) -> np.ndarray:
    """On a trend day, a bar that tags VWAP and closes back in the trend direction."""
    c, o, h, l, vwap, rday = (_arr(f[k]) for k in ("c", "o", "h", "l", "vwap", "rday"))
    with np.errstate(invalid="ignore"):
        long_ = (rday >= trend) & (l <= vwap * 1.001) & (c > vwap) & (c > o)
        short = (rday <= -trend) & (h >= vwap * 0.999) & (c < vwap) & (c < o)
    return long_.astype(int) - short.astype(int)


def gap_fade(f: pd.DataFrame, k: float) -> np.ndarray:
    """Short a large gap-up that has not filled, on a red bar. The ₹5,000 experiment's strategy."""
    c, o, pc, gs = _arr(f["c"]), _arr(f["o"]), _arr(f["prev_close"]), _arr(f["gap_sigma"])
    with np.errstate(invalid="ignore"):
        short = (gs >= k) & (c > pc) & (c < o)
    return -short.astype(int)


def rsi_reversion(f: pd.DataFrame, level: float) -> np.ndarray:
    """Fade stretched moves: RSI(14) on 5-minute bars at or past `level` (or 100 - level), on the far side of VWAP."""
    c, vwap, rsi = _arr(f["c"]), _arr(f["vwap"]), _arr(f["rsi"])
    with np.errstate(invalid="ignore"):
        long_ = (rsi <= level) & (c < vwap)
        short = (rsi >= 100 - level) & (c > vwap)
    return long_.astype(int) - short.astype(int)


def supertrend_line(f: pd.DataFrame, mult: float) -> np.ndarray:
    """Supertrend direction (+1 up, -1 down) from ATR bands, computed bar by bar so each value uses only the past."""
    h, l, c, atr = _arr(f["h"]), _arr(f["l"]), _arr(f["c"]), _arr(f["atr"])
    mid = (h + l) / 2
    up, dn = mid - mult * atr, mid + mult * atr
    trend = np.zeros(len(c), dtype=int)
    fu, fd, t = np.nan, np.nan, 1
    for i in range(len(c)):
        if not np.isfinite(atr[i]):
            continue
        pc = c[i - 1] if i else c[i]
        fu = up[i] if not np.isfinite(fu) or pc < fu else max(up[i], fu)
        fd = dn[i] if not np.isfinite(fd) or pc > fd else min(dn[i], fd)
        if t == 1 and c[i] < fu:
            t = -1
        elif t == -1 and c[i] > fd:
            t = 1
        trend[i] = t
    return trend


def supertrend(f: pd.DataFrame, mult: float) -> np.ndarray:
    """Trade the bar where Supertrend flips, in the new direction, if price agrees with VWAP."""
    tr = supertrend_line(f, mult)
    prev = np.r_[0, tr[:-1]]
    same_day = np.r_[False, f["day"].to_numpy()[1:] == f["day"].to_numpy()[:-1]]
    c, vwap = _arr(f["c"]), _arr(f["vwap"])
    with np.errstate(invalid="ignore"):
        long_ = (tr == 1) & (prev == -1) & same_day & (c > vwap)
        short = (tr == -1) & (prev == 1) & same_day & (c < vwap)
    return long_.astype(int) - short.astype(int)


def pdh_breakout(f: pd.DataFrame, buffer: float) -> np.ndarray:
    """First close beyond yesterday's high (long) or low (short), by at least `buffer`."""
    c, cp, pdh, pdl = _arr(f["c"]), _arr(f["c_prev"]), _arr(f["pdh"]), _arr(f["pdl"])
    with np.errstate(invalid="ignore"):
        long_ = (c > pdh * (1 + buffer)) & (cp <= pdh * (1 + buffer))
        short = (c < pdl * (1 - buffer)) & (cp >= pdl * (1 - buffer))
    return long_.astype(int) - short.astype(int)


STRATEGIES: Dict[str, Strategy] = {s.key: s for s in [
    Strategy("orb_breakout", "Opening-range breakout",
             "A close beyond the first 30 minutes' range, with volume and the indices agreeing, keeps going.",
             "@shivanshco (range breakouts), agents-trade (close-only, index divergence)",
             "window", 30, [15, 30, 45], orb_breakout),
    Strategy("momentum_52w", "52-week-high momentum",
             "Stocks near their yearly high on heavy volume and above VWAP keep rising intraday.",
             "@pranjalsoni_ scanner, 52-Week High Momentum (QuantGPT library)",
             "near", 0.02, [0.01, 0.02, 0.03], momentum_52w),
    Strategy("vwap_pullback", "VWAP pullback",
             "On a trend day, a dip to VWAP that closes back in the trend is a low-risk continuation entry.",
             "skill-algotrader (VWAP Pullback), VWAP breakout backtests on NIFTY",
             "trend", 0.005, [0.003, 0.005, 0.008], vwap_pullback),
    Strategy("gap_fade", "Gap-up fade",
             "Large opening gap-ups overshoot and fade. Kept to test the claim: it lost 23% live in 5 days.",
             "Ganesh S, 'I gave Claude ₹5000 to day trade'",
             "k", 1.5, [1.0, 1.5, 2.0], gap_fade),
    Strategy("rsi_reversion", "RSI reversion",
             "Intraday moves that stretch RSI to an extreme away from VWAP snap back. Breakouts lost in our first run.",
             "quants.md O15 Intraday Bracket Reversion 5m and W07 Intraday RSI 15m (the only positive intraday entries)",
             "level", 25, [20, 25, 30], rsi_reversion),
    Strategy("supertrend", "Supertrend flip",
             "When Supertrend flips direction on an intraday bar, the new trend carries on for the day.",
             "Indian YouTube and blog staple (VWAP + Supertrend setups); ATR multiplier 3 is the common default",
             "mult", 3.0, [2.0, 3.0, 4.0], supertrend),
    Strategy("pdh_breakout", "Previous-day high/low breakout",
             "A close beyond yesterday's high or low marks a range expansion that keeps going.",
             "Common Indian intraday setup; also the stop placement in the 'I gave AI ₹10 lakh' video's rules",
             "buffer", 0.001, [0.0, 0.001, 0.002], pdh_breakout),
]}


def entry_mask(f: pd.DataFrame, entry_start: int, entry_end: int, lunch_start: int, lunch_end: int) -> np.ndarray:
    """Bars whose close is inside the entry window and outside the midday lull."""
    end = f["end_minute"].to_numpy()
    ok = (end >= entry_start) & (end <= entry_end) & ~((end > lunch_start) & (end <= lunch_end))
    return ok & f["warm"].to_numpy(dtype=bool)


def first_per_day(f: pd.DataFrame, sig: np.ndarray) -> np.ndarray:
    """Keep only the first signal of each side per day."""
    out = np.zeros_like(sig)
    seen = set()
    days = f["day"].to_numpy()
    for i in np.flatnonzero(sig):
        key = (days[i], sig[i])
        if key not in seen:
            seen.add(key)
            out[i] = sig[i]
    return out
