"""Panic buy (RUNBOOK H7): on a morning NIFTY opens more than 0.5% down, buy the two stocks from the wider universe that opened
furthest down (more than 2%) with no results meeting around today, and sell by 15:10.

Frozen for the forward paper test (RUNBOOK 2026-11-03). The executable version:
  09:15  the open prints. A gap is the 09:15 open against the previous close.
  09:20  after the first 5-minute bar: rank the eligible gap-downs, skip any locked at the lower circuit (first bar high == low),
         buy the two biggest at the 09:20 bar's open, ₹5,000 each in whole shares.
  stop   3% under the entry, checked on every 5-minute low.
  15:10  sell whatever is still open at the 15:10 bar's open.
Research (daily bars, Nov 2021 → Oct 2023) is the evidence; this module is how it's traded, shared by the 5-minute check and the paper runner.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, List, Optional, Sequence

from stalk.market.costs import round_trip_cost

INDEX_GAP = -0.005        # NIFTY must open at least this far below its previous close
STOCK_GAP = -0.02         # a candidate must open at least this far below its previous close
PICKS = 2
PER_TRADE = 5000.0        # ₹10,000 account, no leverage, two positions
STOP = 0.03
ENTRY_HM, EXIT_HM = 920, 1510
SLIP = {"nifty50": 0.0003, "next50": 0.0004, "midcap": 0.0006, "smallcap": 0.0010}   # per side, assumed until quotes are measured


@dataclass
class Candidate:
    symbol: str
    prev_close: float
    open: float
    tier: str = "midcap"
    results_near: bool = False       # a results board meeting from the previous session to the next
    locked: bool = False             # first 5-minute bar had high == low (stuck at the lower circuit)

    @property
    def gap(self) -> float:
        return self.open / self.prev_close - 1


def market_qualifies(nifty_open: float, nifty_prev_close: float, threshold: float = INDEX_GAP) -> bool:
    return nifty_open / nifty_prev_close - 1 < threshold


def pick(cands: Iterable[Candidate], k: int = PICKS) -> List[Candidate]:
    """The k biggest eligible gap-downs, biggest first."""
    ok = [c for c in cands if c.gap < STOCK_GAP and not c.results_near and not c.locked and c.prev_close > 0]
    return sorted(ok, key=lambda c: c.gap)[:k]


def shares(price: float, budget: float = PER_TRADE) -> int:
    return int(budget // price) if price > 0 else 0


@dataclass
class PanicTrade:
    day: str
    symbol: str
    tier: str
    gap: float
    qty: int
    entry: float
    exit: float
    reason: str              # "stop" or "close"
    gross: float
    costs: float
    net: float


def walk(day: str, c: Candidate, bars: Sequence[Sequence[float]], hms: Sequence[int]) -> Optional[PanicTrade]:
    """Play one pick through its 5-minute bars ([ts, o, h, l, c, v] with matching HHMM list)."""
    try:
        i0 = list(hms).index(ENTRY_HM)
    except ValueError:
        return None
    entry = float(bars[i0][1])
    qty = shares(entry)
    if qty <= 0:
        return None
    stop = entry * (1 - STOP)
    exit_price, reason = None, "close"
    for b, hm in zip(bars[i0:], hms[i0:]):
        if hm >= EXIT_HM:
            exit_price = float(b[1])
            break
        if b[3] <= stop:
            exit_price, reason = min(stop, float(b[1])), "stop"     # a bar that opens below the stop fills at its open
            break
    if exit_price is None:
        exit_price = float(bars[-1][4])
    slip = SLIP.get(c.tier, 0.0006)
    fill_in, fill_out = entry * (1 + slip), exit_price * (1 - slip)
    gross = (fill_out - fill_in) * qty
    costs = round_trip_cost(qty, fill_in, fill_out)
    return PanicTrade(day, c.symbol, c.tier, round(c.gap, 4), qty, round(fill_in, 2), round(fill_out, 2), reason,
                      round(gross, 2), round(costs, 2), round(gross - costs, 2))


def as_dict(t: PanicTrade) -> Dict:
    return asdict(t)
