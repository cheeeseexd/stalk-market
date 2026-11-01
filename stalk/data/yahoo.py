"""Yahoo chart API: free 5-minute bars (last 60 days) and daily bars (2 years) for NSE symbols."""
from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

import httpx

from stalk.config import BAR_MINUTES, IST, SESSION_CLOSE_MIN, SESSION_OPEN_MIN

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
YAHOO_INDEX = {"NIFTY 50": "^NSEI", "NIFTY BANK": "^NSEBANK"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def yahoo_symbol(symbol: str) -> str:
    return YAHOO_INDEX.get(symbol, f"{symbol}.NS")


def in_session(ts: int, bar_minutes: int = BAR_MINUTES) -> bool:
    dt = datetime.fromtimestamp(ts, IST)
    minute = dt.hour * 60 + dt.minute
    return dt.weekday() < 5 and SESSION_OPEN_MIN <= minute and minute + bar_minutes <= SESSION_CLOSE_MIN


def parse_chart(payload: dict, bar_minutes: Optional[int], now_ts: Optional[float] = None) -> List[List[float]]:
    """Rows of [ts, o, h, l, c, v]. Intraday (bar_minutes set): only completed in-session bars."""
    now_ts = now_ts if now_ts is not None else time.time()
    result = payload["chart"]["result"][0]
    ts_list = result.get("timestamp") or []
    q = result["indicators"]["quote"][0]
    rows = []
    for i, ts in enumerate(ts_list):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c) or c <= 0:
            continue
        if bar_minutes is not None and (not in_session(ts, bar_minutes) or ts + bar_minutes * 60 > now_ts):
            continue
        if bar_minutes is None:
            # Daily bars: key by the IST date at 09:15 so a day maps to one timestamp.
            d = datetime.fromtimestamp(ts, IST)
            ts = int(d.replace(hour=9, minute=15, second=0, microsecond=0).timestamp())
        rows.append([int(ts), float(o), float(h), float(l), float(c), float(q["volume"][i] or 0)])
    return rows


def fetch(symbol: str, interval: str = "5m", range_: str = "60d", client: Optional[httpx.Client] = None) -> List[List[float]]:
    own = client is None
    client = client or httpx.Client(timeout=20, headers=HEADERS)
    try:
        r = client.get(YAHOO_URL.format(sym=quote(yahoo_symbol(symbol))), params={"interval": interval, "range": range_})
        r.raise_for_status()
        return parse_chart(r.json(), {"5m": BAR_MINUTES, "60m": 60}.get(interval))
    finally:
        if own:
            client.close()


def last_price(symbol: str, client: Optional[httpx.Client] = None) -> Optional[float]:
    own = client is None
    client = client or httpx.Client(timeout=10, headers=HEADERS)
    try:
        r = client.get(YAHOO_URL.format(sym=quote(yahoo_symbol(symbol))), params={"interval": "1m", "range": "1d"})
        px = r.json()["chart"]["result"][0]["meta"].get("regularMarketPrice")
        return float(px) if px else None
    except Exception:
        return None
    finally:
        if own:
            client.close()
