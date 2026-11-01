"""Zerodha intraday equity (MIS) charges, in rupees.

Schedule used (NSE, equity intraday):
  brokerage     min(₹20, 0.03% of order value) per executed order
  STT           0.025% of sell-side value
  exchange txn  0.00297% of order value (legacy research schedule)
  SEBI fee      ₹10 per crore (0.0001%)
  stamp duty    0.003% of buy-side value
  GST           18% on brokerage + exchange txn + SEBI fee

Re-check https://zerodha.com/charges before relying on these numbers for live trading.
"""
from __future__ import annotations

from dataclasses import dataclass

FEE_SCHEDULE_VERSION = "zerodha-mis-2026-09"
CURRENT_FEE_SCHEDULE_VERSION = "zerodha-mis-2026-10"
CURRENT_EXCHANGE_PCT = 0.0000307  # zerodha.com/charges, checked 2026-10-31; H26

BROKERAGE_PCT = 0.0003
BROKERAGE_CAP = 20.0
STT_SELL_PCT = 0.00025
EXCHANGE_PCT = 0.0000297
SEBI_PCT = 0.000001
STAMP_BUY_PCT = 0.00003
GST_PCT = 0.18


@dataclass(frozen=True)
class FillCost:
    brokerage: float
    stt: float
    exchange: float
    sebi: float
    stamp: float
    gst: float

    @property
    def total(self) -> float:
        return self.brokerage + self.stt + self.exchange + self.sebi + self.stamp + self.gst


def fill_cost(side: str, qty: float, price: float, *, exchange_pct: float = EXCHANGE_PCT) -> FillCost:
    """Charges for one order. H26 explicitly supplies CURRENT_EXCHANGE_PCT.

    Retain the legacy default so frozen older research keeps its registered fees.
    """
    value = abs(qty) * price
    brokerage = min(BROKERAGE_CAP, value * BROKERAGE_PCT)
    stt = value * STT_SELL_PCT if side == "SELL" else 0.0
    exchange = value * exchange_pct
    sebi = value * SEBI_PCT
    stamp = value * STAMP_BUY_PCT if side == "BUY" else 0.0
    gst = (brokerage + exchange + sebi) * GST_PCT
    return FillCost(brokerage, stt, exchange, sebi, stamp, gst)


def round_trip_cost(qty: float, entry: float, exit_price: float) -> float:
    """Total charges for opening and closing one intraday position (either direction)."""
    return fill_cost("BUY", qty, entry).total + fill_cost("SELL", qty, exit_price).total


def round_trip_cost_pct(notional: float) -> float:
    """Approximate round-trip charges as a fraction of notional, used for labels and research."""
    if notional <= 0:
        return 0.0
    return round_trip_cost(notional, 1.0, 1.0) / notional
