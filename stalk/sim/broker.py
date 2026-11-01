"""Order routing for live trading through Kite MCP.

Paper trading never touches a broker: the engine fills trades itself with `research.trade`.
The live broker mirrors each engine trade at Zerodha:
  - entry: MARKET MIS order, then an SL-M stop placed immediately at the broker;
  - exit: the existing SL-M is MODIFIED to a MARKET order, so the position is never left without
    a stop. If the modify fails, a separate market exit is sent and the stop is cancelled only
    after that exit is confirmed filled.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from stalk.data.kite_mcp import KiteMCP, KiteMCPError

logger = logging.getLogger("broker")

TICK = 0.05


def to_tick(price: float, tick: float = TICK) -> float:
    """NSE equity prices move in ₹0.05 steps; Zerodha rejects anything else."""
    return round(round(price / tick) * tick, 2)


@dataclass
class LiveFill:
    order_id: str
    price: float
    qty: int


class LiveKiteBroker:
    name = "kite"

    def __init__(self, kite: KiteMCP, tag: str = "stalk", fill_timeout_s: float = 12.0):
        self.kite = kite
        self.tag = tag
        self.fill_timeout_s = fill_timeout_s

    async def _wait_fill(self, order_id: str) -> Dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.fill_timeout_s
        last: Dict[str, Any] = {}
        while loop.time() < deadline:
            hist = await self.kite.order_history(order_id)
            if hist:
                last = hist[-1]
                status = str(last.get("status", "")).upper()
                if status == "COMPLETE":
                    return last
                if status in ("REJECTED", "CANCELLED"):
                    raise KiteMCPError(f"order {order_id} {status}: {last.get('status_message', '')}")
            await asyncio.sleep(0.8)
        raise KiteMCPError(f"order {order_id} not filled within {self.fill_timeout_s:.0f}s (last: {last.get('status')})")

    async def enter(self, symbol: str, side: str, qty: int, stop: float) -> Dict[str, Any]:
        """MARKET entry, then the protective SL-M. Returns entry fill and stop order id."""
        buy_sell = "BUY" if side == "long" else "SELL"
        oid = await self.kite.place_order(symbol, buy_sell, qty, "MARKET", tag=self.tag)
        row = await self._wait_fill(oid)
        fill = LiveFill(oid, float(row.get("average_price") or 0.0), int(row.get("filled_quantity") or qty))
        stop_side = "SELL" if side == "long" else "BUY"
        stop_id = await self.kite.place_order(symbol, stop_side, fill.qty, "SL-M", trigger_price=to_tick(stop), tag=self.tag)
        return {"fill": fill, "stop_order_id": stop_id}

    async def move_stop(self, stop_order_id: str, qty: int, trigger: float) -> None:
        await self.kite.call_tool("modify_order", {"variety": "regular", "order_id": stop_order_id,
                                                   "order_type": "SL-M", "quantity": qty,
                                                   "trigger_price": to_tick(trigger)})

    async def exit(self, symbol: str, side: str, qty: int, stop_order_id: Optional[str]) -> LiveFill:
        """Close the position without ever leaving it unprotected."""
        if stop_order_id:
            try:
                await self.kite.call_tool("modify_order", {"variety": "regular", "order_id": stop_order_id,
                                                           "order_type": "MARKET", "quantity": qty})
                row = await self._wait_fill(stop_order_id)
                return LiveFill(stop_order_id, float(row.get("average_price") or 0.0), int(row.get("filled_quantity") or qty))
            except KiteMCPError as e:
                logger.warning("converting stop %s to market failed, sending a separate exit: %s", stop_order_id, e)
        exit_side = "SELL" if side == "long" else "BUY"
        oid = await self.kite.place_order(symbol, exit_side, qty, "MARKET", tag=self.tag)
        row = await self._wait_fill(oid)
        if stop_order_id:
            try:
                await self.kite.cancel_order(stop_order_id)
            except KiteMCPError as e:
                logger.warning("cancel stop %s after exit failed: %s", stop_order_id, e)
        return LiveFill(oid, float(row.get("average_price") or 0.0), int(row.get("filled_quantity") or qty))

    async def stop_filled(self, stop_order_id: Optional[str]) -> Optional[LiveFill]:
        if not stop_order_id:
            return None
        hist = await self.kite.order_history(stop_order_id)
        if hist and str(hist[-1].get("status", "")).upper() == "COMPLETE":
            row = hist[-1]
            return LiveFill(stop_order_id, float(row.get("average_price") or 0.0), int(row.get("filled_quantity") or 0))
        return None

    async def open_positions(self) -> Dict[str, int]:
        """Net MIS quantity per symbol at the broker (the source of truth on restart)."""
        data = await self.kite.positions()
        out: Dict[str, int] = {}
        for p in (data.get("net") or data.get("day") or []):
            if p.get("product") == "MIS" and int(p.get("quantity") or 0) != 0:
                out[str(p.get("tradingsymbol"))] = int(p["quantity"])
        return out
