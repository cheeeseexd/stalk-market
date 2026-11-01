"""History refresh and live bars: Kite MCP when logged in, Yahoo otherwise.

Only completed in-session bars are stored, so a stored bar never changes after the fact.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import httpx

from stalk.config import BAR_MINUTES, INDICES, INTERVAL, IST, Settings
from stalk.data import yahoo
from stalk.data.store import Store

logger = logging.getLogger("feed")


class Feed:
    def __init__(self, store: Store, settings: Settings, kite=None):
        self.store = store
        self.settings = settings
        self.kite = kite
        self.provider = "stored"
        self.last_refresh: Optional[float] = None
        self.last_error: Optional[str] = None
        self.failures = 0

    @property
    def symbols(self) -> List[str]:
        return list(self.settings.universe) + INDICES

    def refresh_history(self, symbols: Optional[Sequence[str]] = None, daily: bool = True) -> Dict[str, int]:
        """Yahoo 5-minute bars for the last 60 days and daily bars for 2 years. Blocking."""
        symbols = list(symbols or self.symbols)

        def one(sym: str) -> int:
            n = 0
            with httpx.Client(timeout=20, headers=yahoo.HEADERS) as client:
                try:
                    n += self.store.upsert_bars(sym, INTERVAL, yahoo.fetch(sym, "5m", "60d", client), "yahoo")
                except Exception as e:
                    self.last_error = f"Yahoo 5m {sym}: {e}"[:160]
                if daily:
                    try:
                        self.store.upsert_bars(sym, "day", yahoo.fetch(sym, "1d", "5y", client), "yahoo")
                    except Exception as e:
                        self.last_error = f"Yahoo 1d {sym}: {e}"[:160]
                    try:
                        self.store.upsert_bars(sym, "60minute", yahoo.fetch(sym, "60m", "730d", client), "yahoo")
                    except Exception as e:
                        self.last_error = f"Yahoo 60m {sym}: {e}"[:160]
            return n

        with ThreadPoolExecutor(max_workers=6) as ex:
            counts = dict(zip(symbols, ex.map(one, symbols)))
        self.last_refresh = time.time()
        self.provider = "yahoo"
        return counts

    async def refresh_today(self) -> int:
        """Today's completed bars for every symbol. Kite first, Yahoo as fallback."""
        total, ok = 0, 0
        now = datetime.now(IST)
        if self.kite is not None and self.kite.logged_in:
            for sym in self.symbols:
                try:
                    rows = await self.kite.historical(sym, INTERVAL, now.strftime("%Y-%m-%d 09:15:00"),
                                                      now.strftime("%Y-%m-%d %H:%M:%S"))
                    rows = [r for r in rows if r[0] + BAR_MINUTES * 60 <= time.time() and yahoo.in_session(int(r[0]))]
                    total += self.store.upsert_bars(sym, INTERVAL, rows, "kite")
                    ok += 1
                except Exception as e:
                    self.last_error = f"Kite: {e}"[:160]
            if ok:
                self.provider = "kite"
        if not ok:
            import asyncio

            def one(sym: str) -> int:
                return self.store.upsert_bars(sym, INTERVAL, yahoo.fetch(sym, "5m", "1d"), "yahoo")

            loop = asyncio.get_running_loop()
            results = await asyncio.gather(*(loop.run_in_executor(None, one, s) for s in self.symbols),
                                           return_exceptions=True)
            for res in results:
                if isinstance(res, Exception):
                    self.last_error = f"Yahoo: {res}"[:160]
                else:
                    total += res
                    ok += 1
            if ok:
                self.provider = "yahoo"
        if ok:
            self.last_refresh = time.time()
            self.failures = 0
        else:
            self.failures += 1
        return total

    async def last_prices(self, symbols: Sequence[str]) -> Dict[str, float]:
        prices: Dict[str, float] = {}
        if self.kite is not None and self.kite.logged_in:
            try:
                prices = await self.kite.ltp(list(symbols))
            except Exception as e:
                self.last_error = f"Kite LTP: {e}"[:160]
        import asyncio
        loop = asyncio.get_running_loop()
        missing = [s for s in symbols if s not in prices]
        got = await asyncio.gather(*(loop.run_in_executor(None, yahoo.last_price, s) for s in missing))
        prices.update({s: p for s, p in zip(missing, got) if p})
        return prices

    def health(self, market_open: bool) -> Dict[str, object]:
        age = None if self.last_refresh is None else time.time() - self.last_refresh
        stale = market_open and (age is None or age > BAR_MINUTES * 120 + 60 or self.failures >= 3)
        return {"provider": self.provider, "last_bar_ts": self.store.last_bar_ts("NIFTY 50", INTERVAL),
                "age_s": None if age is None else int(age), "stale": bool(stale), "error": self.last_error}
