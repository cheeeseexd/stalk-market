"""Time Machine: odds for a planned trade, from 10,000 replays of the rest of the session.

Method ported from the `trader-timemachine` skill, github.com/Jakeschincariol/day-trader-skill
(MIT License, Copyright (c) 2026 Jake Schincariol):
every remaining 5-minute candle is drawn from this stock's own candles at the same time of day over
the previous sessions (59 by default), with a fixed seed so the answer is repeatable. Each path is
run through the same stop-first rules as a real trade (no breakeven move, so it is the raw barrier race).

Only sessions before the trade's day are used.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

LOOKBACK = 59
PATHS = 10000
SEED = 19


def odds(f: pd.DataFrame, day: str, entry_minute: int, side: str, entry: float, dist: float,
         target_r: float, flatten_min: int, lookback: int = LOOKBACK, paths: int = PATHS,
         seed: int = SEED) -> Optional[Dict[str, float]]:
    """P(target first), P(stop first), P(still open at flatten), mean gross R. None without enough history."""
    if dist <= 0 or entry <= 0:
        return None
    past_days = sorted(d for d in f["day"].unique() if d < day)[-lookback:]
    if len(past_days) < 20:
        return None
    hist = f[f["day"].isin(past_days)]
    step = int(f["end_minute"].iat[0] - f["minute"].iat[0]) if len(f) else 5
    slots = [m for m in range(entry_minute, flatten_min, step)]
    if not slots:
        return None
    # Per slot: candle shape relative to the previous close (gap) and to its own open.
    pc = hist.groupby("day")["c"].shift(1)
    shape = pd.DataFrame({"day": hist["day"], "minute": hist["minute"],
                          "g": hist["o"] / pc, "h": hist["h"] / hist["o"],
                          "l": hist["l"] / hist["o"], "c": hist["c"] / hist["o"]})
    piv = {k: shape.pivot_table(index="day", columns="minute", values=k).reindex(columns=slots) for k in "ghlc"}
    mats = {k: v.to_numpy(dtype=float) for k, v in piv.items()}
    nd = mats["c"].shape[0]
    rng = np.random.default_rng(seed)
    pick = rng.integers(0, nd, size=(paths, len(slots)))

    sign = 1.0 if side == "long" else -1.0
    stop, target = entry - sign * dist, entry + sign * target_r * dist
    price = np.full(paths, entry)
    state = np.zeros(paths, dtype=int)   # 0 open, 1 target, -1 stop
    for j in range(len(slots)):
        g = mats["g"][pick[:, j], j]
        hr, lr, cr = mats["h"][pick[:, j], j], mats["l"][pick[:, j], j], mats["c"][pick[:, j], j]
        bad = ~np.isfinite(g) | ~np.isfinite(cr)
        g = np.where(bad, 1.0, g)
        hr, lr, cr = (np.where(bad, 1.0, x) for x in (hr, lr, cr))
        o = np.full(paths, entry) if j == 0 else price * g
        h, l, c = o * hr, o * lr, o * cr
        live = state == 0
        if side == "long":
            s_hit = live & (l <= stop)
            t_hit = live & ~s_hit & (h >= target)
        else:
            s_hit = live & (h >= stop)
            t_hit = live & ~s_hit & (l <= target)
        state = np.where(s_hit, -1, np.where(t_hit, 1, state))
        price = np.where(state == 0, c, price)
    final_r = np.where(state == 1, target_r, np.where(state == -1, -1.0, sign * (price - entry) / dist))
    return {"p_target": round(float((state == 1).mean()), 3), "p_stop": round(float((state == -1).mean()), 3),
            "p_open": round(float((state == 0).mean()), 3), "exp_r": round(float(final_r.mean()), 3),
            "sessions": len(past_days), "paths": paths}


def analogs(events: pd.DataFrame, symbol: str, day: str, lookback_days: int = LOOKBACK) -> Dict[str, int]:
    """How the same rule did on this stock in recent past sessions where it fired."""
    if events is None or events.empty:
        return {"n": 0, "target": 0, "stop": 0, "other": 0}
    e = events[(events["symbol"] == symbol) & (events["day"] < day)]
    days = sorted(e["day"].unique())[-lookback_days:]
    e = e[e["day"].isin(days)]
    return {"n": int(len(e)), "target": int((e["reason"] == "target").sum()),
            "stop": int((e["reason"] == "stop").sum()),
            "other": int((~e["reason"].isin(["target", "stop"])).sum())}
