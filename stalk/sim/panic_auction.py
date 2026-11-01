"""Panic buy, auction-entry paper variant (2026-11-01 finding). Runs beside the frozen rule; does not change it.

The frozen runner (panic.py / panic_live.py) buys at the 09:20 bar open. On 535 of its real 10-year picks that entry made
-0.13% a trade after 0.40% costs, while the 09:15 auction open made +1.37%: the bounce happens in the first five minutes
(RUNBOOK "Panic buy with real 5-minute fills"). This variant takes the SAME picks (ranked on NSE's pre-open auction prices)
and fills them at the 09:15 opening auction price, 3% stop from that price, sold at the 15:10 bar open. Paper only.
Caveat recorded in every entry line: a real order must be placed in the pre-open session (by 09:08) from indicative prices;
the pre-open recorder (stalk/research/preopen_log.py) logs those at 09:01-09:08:30 so the ranking at 09:07 can be checked.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import List, Optional, Sequence

from stalk.config import BASE_DIR, IST
from stalk.market.costs import round_trip_cost
from stalk.sim import panic
from stalk.sim.panic_live import PanicPaper, _hm

AUCTION_HM = 915
LOG = os.path.join(BASE_DIR, "data", "panic_paper_auction.jsonl")
LOOSE_LOG = os.path.join(BASE_DIR, "data", "panic_paper_loose_auction.jsonl")


def walk_from_auction(day: str, c: panic.Candidate, bars: Sequence[Sequence[float]], hms: Sequence[int]) -> Optional[panic.PanicTrade]:
    """panic.walk with the entry at the 09:15 bar open (the auction price) instead of 09:20; everything else identical."""
    try:
        i0 = list(hms).index(AUCTION_HM)
    except ValueError:
        return None
    entry = float(bars[i0][1])
    qty = panic.shares(entry)
    if qty <= 0:
        return None
    stop = entry*(1-panic.STOP)
    exit_price, reason = None, "close"
    for b, hm in zip(bars[i0:], hms[i0:]):
        if hm >= panic.EXIT_HM:
            exit_price = float(b[1])
            break
        if b[3] <= stop:
            exit_price, reason = min(stop, float(b[1])), "stop"
            break
    if exit_price is None:
        exit_price = float(bars[-1][4])
    slip = panic.SLIP.get(c.tier, 0.0006)
    fill_in, fill_out = entry*(1+slip), exit_price*(1-slip)
    gross = (fill_out-fill_in)*qty
    costs = round_trip_cost(qty, fill_in, fill_out)
    return panic.PanicTrade(day, c.symbol, c.tier, round(c.gap, 4), qty, round(fill_in, 2), round(fill_out, 2), reason,
                            round(gross, 2), round(costs, 2), round(gross-costs, 2))


class PanicAuctionPaper(PanicPaper):
    """Same morning decision as the frozen rule; fills at the opening auction price."""

    entry_hm = AUCTION_HM
    entry_basis = ("09:15 auction-open paper reference; not a realised fill. Picks are selected after the open, "
                   "including the first-five-minute lock check. A real auction order would need a pre-open "
                   "decision by 09:08; compare the recorded 09:07 indicative ranking before assessing feasibility.")

    def __init__(self, store=None, kite=None, index_gap: float = panic.INDEX_GAP, name: str = "panic_auction"):
        super().__init__(store, kite, log_path=LOOSE_LOG if index_gap > panic.INDEX_GAP else LOG, index_gap=index_gap, name=name)
        self.strategy = "panic_buy_auction_loose" if index_gap > panic.INDEX_GAP else "panic_buy_auction"

    def settle(self, day: str, c: panic.Candidate, bars: List[List[float]]) -> Optional[panic.PanicTrade]:
        hms = [_hm(int(b[0])) for b in bars]
        if AUCTION_HM not in hms:
            return None
        i0 = hms.index(AUCTION_HM)
        entry = bars[i0][1]
        stopped = any(b[3] <= entry*(1-panic.STOP) for b, hm in zip(bars[i0:], hms[i0:]) if hm < panic.EXIT_HM)
        if not stopped and max(hms) < panic.EXIT_HM:
            return None
        t = walk_from_auction(day, c, bars, hms)
        if t and self.store is not None:
            exit_hm = next((hm for b, hm in zip(bars[i0:], hms[i0:]) if hm >= panic.EXIT_HM or b[3] <= entry*(1-panic.STOP)), hms[-1])
            ets = int(datetime.strptime(f"{day} {exit_hm:04d}", "%Y-%m-%d %H%M").replace(tzinfo=IST).timestamp())
            self.store.save_trade({"id": f"{self.name}:{day}:{c.symbol}", "session_id": f"{self.name}:{day}", "day": day, "strategy": self.strategy,
                                   "symbol": c.symbol, "side": "long", "qty": t.qty, "entry_ts": int(bars[i0][0]), "entry_price": t.entry,
                                   "exit_ts": ets, "exit_price": t.exit, "stop_initial": round(entry*(1-panic.STOP), 2),
                                   "gross": t.gross, "costs": t.costs, "net": t.net, "reason": "flatten" if t.reason == "close" else t.reason,
                                   "fee_version": "zerodha-2026", "broker": "paper-auction"})
        return t
