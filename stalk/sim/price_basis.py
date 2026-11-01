"""Signal-time price checks. Read cached auction open/reference only, never today's returns."""
from functools import lru_cache
from datetime import datetime
import json
import math
from pathlib import Path

import pandas as pd

from stalk.config import BASE_DIR

TOLERANCE = .01


@lru_cache(maxsize=1)
def known_actions():
    """Compact, source-attributed inventory built by scripts/h15_audit.py.

    Record dates are conservative ex-date proxies; only already published
    notices may block a historical decision. Absence does not certify no action.
    """
    path = Path(BASE_DIR) / "data" / "price_basis_actions.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def action_known(day, symbol):
    cutoff = datetime.strptime(day + ' 09:15:00', '%Y-%m-%d %H:%M:%S')
    for row in known_actions():
        if row['symbol'] != symbol or row['day'] != day:
            continue
        try:
            published = datetime.strptime(row['published'], '%d-%b-%Y %H:%M:%S')
        except (ValueError, TypeError):
            continue
        if published < cutoff:
            return True
    return False


def differs(value, reference):
    """Strictly greater than 1%; invalid prices fail closed."""
    return (not all(math.isfinite(x) and x > 0 for x in (value, reference))
            or abs(value / reference - 1) > TOLERANCE + 1e-12)


@lru_cache(maxsize=128)
def official_day(day):
    root = Path(BASE_DIR) / "data"
    for path in (root / "bhav" / f"{day}.pkl", root / "h9" / "bhav" / f"{day}.pkl"):
        if path.exists():
            frame = pd.read_pickle(path)
            if frame.empty:
                continue
            # Deliberately discard same-day close/extremes/turnover before the decision path.
            return frame[["open", "prev_close"]].to_dict("index")
    return {}


def resolve(day, symbol, yahoo_open, yahoo_prev=None, official=None):
    """Official gap if present; explicitly marked Yahoo fallback if absent.

    PREVCLOSE in raw bhavcopy is NOT guaranteed adjusted on action dates. An
    inconsistent Yahoo/reference pair is rejected, never repaired using returns.
    Missing yesterday's Yahoo candle does not prevent use of an official gap.
    """
    prices = official_day(day) if official is None else official
    row = prices.get(symbol)
    if row is None:
        pc, op, source = yahoo_prev, yahoo_open, "yahoo_fallback"
        problems = []
        if pc is None or differs(pc, pc) or differs(op, op):
            problems.append("missing or invalid Yahoo reference")
    else:
        pc, op, source = float(row["prev_close"]), float(row["open"]), "nse_bhavcopy"
        problems = []
        if differs(yahoo_open, op):
            problems.append("Yahoo open versus official OPEN >1% or invalid")
        if differs(pc, pc):
            problems.append("invalid official PREVCLOSE")
        if yahoo_prev is not None and differs(yahoo_prev, pc):
            problems.append("Yahoo previous last close versus official PREVCLOSE >1% or invalid")
    if action_known(day, symbol):
        problems.append("previously announced split/bonus record date; adjusted reference unverified")
    return {"prev_close": pc, "open": op, "source": source,
            "mismatch": bool(problems), "details": problems}
