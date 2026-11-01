"""Turn rule signals into net-of-charges trades on stored bars, and decide which strategies have an edge.

A strategy is validated as of a day when, on trades from earlier days only:
  - there are at least MIN_TRADES of them,
  - the mean net R is positive with t ≥ MIN_T,
  - every neighbouring parameter value on its grid also has a positive mean (a plateau, not a lucky peak).
Days from HOLDOUT_START on are excluded unless the holdout has been opened on purpose.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from stalk.config import HOLDOUT_START, Settings, hhmm_to_min
from stalk.research.strategies import STRATEGIES, entry_mask, first_per_day
from stalk.research.trade import cost_gate, size, stop_distance, walk

MIN_TRADES = 40
MIN_T = 1.0


def stats(r: np.ndarray, net: Optional[np.ndarray] = None) -> Dict[str, float]:
    r = np.asarray(r, dtype=float)
    n = len(r)
    if n == 0:
        return {"n": 0, "mean_r": 0.0, "t": 0.0, "win": 0.0, "pf": 0.0, "net": 0.0}
    sd = float(r.std(ddof=1)) if n > 1 else 0.0
    wins, losses = r[r > 0].sum(), -r[r < 0].sum()
    return {"n": n, "mean_r": round(float(r.mean()), 4),
            "t": round(float(r.mean() / (sd / math.sqrt(n))), 2) if sd > 0 else 0.0,
            "win": round(float((r > 0).mean()), 3),
            "pf": round(float(wins / losses), 2) if losses > 0 else 0.0,
            "net": round(float(np.sum(net)), 2) if net is not None else 0.0}


def strategy_events(feats: Dict[str, pd.DataFrame], key: str, value: float, s: Settings) -> pd.DataFrame:
    """Every first-of-day signal for one strategy and parameter, traded with the account's sizing and charges."""
    strat = STRATEGIES[key]
    es, ee = hhmm_to_min(s.entry_start), hhmm_to_min(s.entry_end)
    ls, le = hhmm_to_min(s.lunch_start), hhmm_to_min(s.lunch_end)
    rows: List[dict] = []
    for sym, f in feats.items():
        sig = strat.signals(f, value) * entry_mask(f, es, ee, ls, le)
        sig = first_per_day(f, sig)
        idx = np.flatnonzero(sig)
        if len(idx) == 0:
            continue
        ts, o, h, l, c = (f[k].to_numpy() for k in ("ts", "o", "h", "l", "c"))
        endm, day, atr, adr = f["end_minute"].to_numpy(), f["day"].to_numpy(), f["atr"].to_numpy(), f["adr"].to_numpy()
        n = len(f)
        busy_until = -1          # one position per stock, as the engine allows: skip signals while a trade is open
        for i in idx:
            j = i + 1
            if j >= n or day[j] != day[i] or int(ts[j]) <= busy_until:
                continue
            k = j
            bars = []
            while k < n and day[k] == day[i]:
                bars.append((int(ts[k]), float(o[k]), float(h[k]), float(l[k]), float(c[k]), int(endm[k])))
                if endm[k] >= s.flatten_min:
                    break
                k += 1
            side = "long" if sig[i] > 0 else "short"
            dist = stop_distance(float(atr[i]), float(adr[i]), s)
            qty = size(float(o[j]), dist, s, s.capital)
            if qty < 1 or not cost_gate(qty, float(o[j]), dist, s)[0]:
                continue
            t = walk(bars, sym, side, key, dist, qty, s)
            if t is None:
                continue
            res = t.result()
            busy_until = int(t.exit_ts)
            rows.append({"day": day[i], "ts": int(ts[i]), "symbol": sym, "side": side, "strategy": key,
                         "value": value, "qty": qty, "entry": t.entry, "exit": t.exit, "reason": t.reason, **res})
    cols = ["day", "ts", "symbol", "side", "strategy", "value", "qty", "entry", "exit", "reason",
            "gross", "costs", "net", "r", "mfe_r", "mae_r"]
    return pd.DataFrame(rows, columns=cols).sort_values("ts").reset_index(drop=True) if rows else pd.DataFrame(columns=cols)


def all_events(feats: Dict[str, pd.DataFrame], s: Settings, keys: Optional[List[str]] = None) -> Dict[str, Dict[float, pd.DataFrame]]:
    keys = keys or list(STRATEGIES)
    return {k: {v: strategy_events(feats, k, v, s) for v in STRATEGIES[k].grid} for k in keys}


def validate_as_of(events: Dict[float, pd.DataFrame], key: str, as_of: str, open_holdout: bool = False) -> Dict[str, object]:
    """Validation verdict for one strategy using only trades on days before as_of."""
    strat = STRATEGIES[key]
    cutoff = as_of if open_holdout else min(as_of, HOLDOUT_START)
    grid = {}
    for v, ev in events.items():
        e = ev[ev["day"] < cutoff] if len(ev) else ev
        grid[v] = stats(e["r"].to_numpy() if len(e) else np.array([]), e["net"].to_numpy() if len(e) else None)
    main = grid[strat.default]
    plateau = all(g["mean_r"] > 0 for g in grid.values() if g["n"] >= 10) and main["n"] > 0
    reasons = []
    if main["n"] < MIN_TRADES:
        reasons.append(f"{main['n']} trades, needs {MIN_TRADES}")
    if main["t"] < MIN_T:
        reasons.append(f"t {main['t']:.2f}, needs {MIN_T:.0f}")
    if not plateau:
        reasons.append("neighbouring settings disagree")
    return {"key": key, "as_of": as_of, "validated": not reasons, "reasons": reasons, "plateau": plateau,
            "main": main, "grid": {str(k): g for k, g in grid.items()}}
