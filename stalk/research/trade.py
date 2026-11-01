"""One trade's life, shared by research, the campaign, replay and live paper.

Research walks a trade through stored bars with `walk`; the engine calls `Trade.step` bar by bar.
Both use the same `step`, so research and execution cannot drift apart.

Rules: entry at the next bar's open plus slippage; the stop is checked before the target inside a
bar (conservative); a gap through the stop fills at the open; the stop moves to entry after +1R
(from the next bar); anything still open is closed at the close of the bar that ends at flatten time.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Optional, Sequence, Tuple

from stalk.config import Settings
from stalk.market.costs import round_trip_cost

Bar = Tuple[int, float, float, float, float, int]  # ts, o, h, l, c, end_minute


def stop_distance(atr: float, adr: float, s: Settings) -> float:
    """Rupees per share between entry and stop, fixed when the trade is planned."""
    if s.stop_mode == "range":
        return s.stop_range * adr if adr == adr and adr > 0 else 0.0
    return s.stop_atr * atr if atr == atr and atr > 0 else 0.0


def size(entry: float, dist: float, s: Settings, equity: float) -> int:
    """Shares so a stop-out loses at most risk_per_trade, capped by notional and buying power."""
    if entry <= 0 or dist <= 0:
        return 0
    cap = min(s.max_notional, max(0.0, equity) * s.leverage)
    return int(max(0, math.floor(min(s.risk_per_trade / dist, cap / entry))))


def cost_gate(qty: int, entry: float, dist: float, s: Settings) -> Tuple[bool, float, float]:
    """The rupee target must be at least cost_gate_mult × round-trip charges."""
    reward = qty * dist * s.target_r
    charges = round_trip_cost(qty, entry, entry)
    return reward >= s.cost_gate_mult * charges, reward, charges


@dataclass
class Trade:
    symbol: str
    side: str            # long | short
    strategy: str
    qty: int
    entry_ts: int
    entry: float
    dist: float          # stop distance in rupees per share
    stop: float
    target: float
    be_level: float
    be_active: bool = False
    best: float = 0.0
    worst: float = 0.0
    exit_ts: Optional[int] = None
    exit: Optional[float] = None
    reason: Optional[str] = None

    @property
    def sign(self) -> float:
        return 1.0 if self.side == "long" else -1.0

    @classmethod
    def open(cls, symbol: str, side: str, strategy: str, open_px: float, ts: int, dist: float, qty: int,
             s: Settings) -> "Trade":
        sign = 1.0 if side == "long" else -1.0
        entry = open_px * (1 + sign * s.slippage_bps / 10000.0)
        return cls(symbol, side, strategy, qty, ts, entry, dist, entry - sign * dist,
                   entry + sign * s.target_r * dist, entry + sign * s.breakeven_at_r * dist,
                   best=entry, worst=entry)

    def r_at(self, price: float) -> float:
        return self.sign * (price - self.entry) / self.dist if self.dist else 0.0

    def step(self, bar: Bar, s: Settings) -> bool:
        """Advance one bar. Returns True when the trade closed on this bar."""
        ts, o, h, l, c, end_min = bar
        long_ = self.side == "long"
        cur_stop = self.entry if self.be_active else self.stop
        px = None
        if long_:
            if l <= cur_stop:
                px, why = min(cur_stop, o), ("breakeven" if self.be_active else "stop")
            elif h >= self.target:
                px, why = max(self.target, o), "target"
        else:
            if h >= cur_stop:
                px, why = max(cur_stop, o), ("breakeven" if self.be_active else "stop")
            elif l <= self.target:
                px, why = min(self.target, o), "target"
        if px is None:
            self.best = max(self.best, h) if long_ else min(self.best, l)
            self.worst = min(self.worst, l) if long_ else max(self.worst, h)
            if (long_ and h >= self.be_level) or (not long_ and l <= self.be_level):
                self.be_active = True
            if end_min >= s.flatten_min:
                px, why = c, "flatten"
        if px is None:
            return False
        self.exit = px * (1 - self.sign * s.slippage_bps / 10000.0)
        self.exit_ts = ts
        self.reason = why
        return True

    def force_exit(self, ts: int, price: float, reason: str, s: Settings) -> None:
        self.exit = price * (1 - self.sign * s.slippage_bps / 10000.0)
        self.exit_ts = ts
        self.reason = reason

    def result(self) -> dict:
        gross = self.sign * (self.exit - self.entry) * self.qty
        costs = round_trip_cost(self.qty, self.entry, self.exit)
        net = gross - costs
        return {"gross": round(gross, 2), "costs": round(costs, 2), "net": round(net, 2),
                "r": round(net / (self.qty * self.dist), 3) if self.qty and self.dist else 0.0,
                "mfe_r": round(self.r_at(self.best), 2), "mae_r": round(self.r_at(self.worst), 2)}

    def to_dict(self) -> dict:
        return asdict(self)


def walk(bars: Sequence[Bar], symbol: str, side: str, strategy: str, dist: float, qty: int, s: Settings) -> Optional[Trade]:
    """Enter at bars[0]'s open with a stop `dist` rupees away and run the trade to its exit. bars must all be from one session."""
    if not bars or qty <= 0 or dist <= 0:
        return None
    t = Trade.open(symbol, side, strategy, bars[0][1], bars[0][0], dist, qty, s)
    for bar in bars:
        if t.step(bar, s):
            return t
    last = bars[-1]
    t.force_exit(last[0], last[4], "data_end", s)
    return t
