"""Synthetic intraday bars for deterministic tests."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import numpy as np
import pandas as pd

from stalk.config import IST


def trading_days(n: int, start: str = "2026-06-01") -> List[datetime]:
    d = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_bars(days: int = 25, bar_minutes: int = 5, price: float = 1000.0, seed: int = 1,
              vol: float = 0.002, drift: float = 0.0, volume: float = 50000) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    px = price
    for d in trading_days(days):
        t = d.replace(hour=9, minute=15)
        px *= 1 + rng.normal(0, vol * 3)  # overnight gap
        for _ in range((375) // bar_minutes):
            o = px
            c = o * (1 + drift + rng.normal(0, vol))
            h = max(o, c) * (1 + abs(rng.normal(0, vol / 2)))
            l = min(o, c) * (1 - abs(rng.normal(0, vol / 2)))
            v = volume * (1 + abs(rng.normal(0, 0.5)))
            rows.append((int(t.timestamp()), o, h, l, c, v))
            px = c
            t += timedelta(minutes=bar_minutes)
    return pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"])


def make_daily(days: int = 300, price: float = 1000.0, seed: int = 5, end: str = "2026-06-01") -> pd.DataFrame:
    """Daily bars ending the day before `end`, keyed at 09:15 IST like stored daily bars."""
    rng = np.random.default_rng(seed)
    d = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST, hour=9, minute=15)
    rows, px, n = [], price, 0
    while n < days:
        d -= timedelta(days=1)
        if d.weekday() >= 5:
            continue
        o = px
        c = o * (1 + rng.normal(0, 0.01))
        rows.append((int(d.timestamp()), o, max(o, c) * 1.005, min(o, c) * 0.995, c, 1e6))
        px = c
        n += 1
    return pd.DataFrame(sorted(rows), columns=["ts", "o", "h", "l", "c", "v"])
