"""Risk gate: every candidate trade gets PASS or BLOCK with a plain reason.

Checked in order, so the reason shown is the first rule that fails. Modelled on the `trader-risk` skill
(github.com/Jakeschincariol/day-trader-skill, MIT License, Copyright (c) 2026 Jake Schincariol), with the
hard rules collected from agents-trade and skill-algotrader.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from stalk.config import Settings, hhmm_to_min
from stalk.research.trade import cost_gate


def _hhmm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


@dataclass
class GateState:
    halted: bool = False
    killed: bool = False
    stale: bool = False
    trades_today: int = 0
    open_positions: int = 0
    consecutive_losses: int = 0   # today
    streak: int = 0               # losses in a row across sessions
    locked: int = 0               # sessions left in a timeout, including today
    day_block: str = ""           # a reason no entries are allowed today (e.g. a VIX spike)


@dataclass
class Verdict:
    ok: bool
    reason: str
    code: str


def check(s: Settings, st: GateState, *, minute_end: int, symbol_cooldown_until: Optional[int],
          qty: int, entry: float, dist: float, validated: bool, holding_symbol: bool) -> Verdict:
    """minute_end is the IST minute at which the signal bar closed."""
    if st.killed:
        return Verdict(False, "Kill switch is on", "killed")
    if st.halted:
        return Verdict(False, f"Daily loss limit of ₹{s.loss_halt:,.0f} reached", "loss_halt")
    if st.locked > 0:
        return Verdict(False, f"Timeout after {s.lockout_after_losses} losses in a row ({st.locked} session{'s' if st.locked > 1 else ''} left)", "timeout")
    if st.day_block:
        return Verdict(False, st.day_block, "day_block")
    if st.stale:
        return Verdict(False, "Market data is stale", "stale")
    if st.consecutive_losses >= s.max_consecutive_losses:
        return Verdict(False, f"{st.consecutive_losses} losses in a row, done for the day", "loss_streak")
    if st.trades_today >= s.max_trades_per_day:
        return Verdict(False, f"Already {st.trades_today} trades today", "trade_cap")
    if holding_symbol:
        return Verdict(False, "Already holding this stock", "holding")
    if st.open_positions >= s.max_positions:
        return Verdict(False, f"{st.open_positions} position open, limit {s.max_positions}", "max_positions")
    if minute_end < hhmm_to_min(s.entry_start) or minute_end > hhmm_to_min(s.entry_end):
        return Verdict(False, f"Outside entry window {s.entry_start}–{s.entry_end}", "window")
    if hhmm_to_min(s.lunch_start) < minute_end <= hhmm_to_min(s.lunch_end):
        return Verdict(False, f"Midday lull {s.lunch_start}–{s.lunch_end}", "lunch")
    if symbol_cooldown_until is not None and minute_end < symbol_cooldown_until:
        return Verdict(False, f"Cooldown until {_hhmm(symbol_cooldown_until)}", "cooldown")
    if not validated and s.require_validated:
        return Verdict(False, "Strategy not validated", "not_validated")
    if qty < 1:
        return Verdict(False, f"Stop too wide for ₹{s.risk_per_trade:,.0f} risk", "size")
    ok, reward, charges = cost_gate(qty, entry, dist, s)
    if not ok:
        return Verdict(False, f"Target ₹{reward:,.0f} is under {s.cost_gate_mult:g}× charges ₹{charges:,.0f}", "costs")
    return Verdict(True, "Pass", "pass")
