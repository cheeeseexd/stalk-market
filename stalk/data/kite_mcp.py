"""Async client for the Kite MCP server (hosted at mcp.kite.trade or self-hosted).

Speaks MCP JSON-RPC over streamable HTTP. The MCP session id is tied to a Kite login:
call `login()`, open the returned URL in a browser, sign in to Zerodha, and every
later tool call on the same session is authenticated until Kite expires the token
(early next morning). The session id is kept in data/kite_session.json and is never
sent to the browser.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

from stalk.config import DATA_DIR

logger = logging.getLogger("kite_mcp")

SESSION_FILE = os.path.join(DATA_DIR, "kite_session.json")
INSTRUMENTS_FILE = os.path.join(DATA_DIR, "kite_instruments.json")
PROTOCOL_VERSION = "2025-03-26"

# Well-known index tokens on Kite.
INDEX_TOKENS = {"NIFTY 50": 256265, "NIFTY BANK": 260105}

URL_RE = re.compile(r"https?://[^\s)\]]+")


class KiteMCPError(RuntimeError):
    pass


class KiteAuthRequired(KiteMCPError):
    pass


def kite_instrument(symbol: str) -> str:
    return f"NSE:{symbol}"


class KiteMCP:
    def __init__(self, url: str = "https://mcp.kite.trade/mcp", transport: Optional[httpx.AsyncBaseTransport] = None,
                 session_file: Optional[str] = SESSION_FILE, instruments_file: Optional[str] = INSTRUMENTS_FILE,
                 min_interval_s: float = 0.35):
        self.url = url
        self._transport = transport
        self._session_file = session_file
        self._instruments_file = instruments_file
        self.session_id: Optional[str] = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._min_interval = min_interval_s
        self._last_call = 0.0
        self.logged_in = False
        self.user_name: Optional[str] = None
        self.last_error: Optional[str] = None
        self.last_check = 0.0
        self.tools: List[str] = []
        self._tokens: Dict[str, int] = dict(INDEX_TOKENS)
        self._load_files()

    # ----------------------------------------------------------- persistence
    def _load_files(self) -> None:
        if self._session_file and os.path.exists(self._session_file):
            try:
                with open(self._session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("url") == self.url:
                    self.session_id = data.get("session_id")
            except Exception:
                self.session_id = None
        if self._instruments_file and os.path.exists(self._instruments_file):
            try:
                with open(self._instruments_file, "r", encoding="utf-8") as f:
                    self._tokens.update({k: int(v) for k, v in json.load(f).items()})
            except Exception:
                pass

    def _save_session(self) -> None:
        if not self._session_file:
            return
        os.makedirs(os.path.dirname(self._session_file), exist_ok=True)
        with open(self._session_file, "w", encoding="utf-8") as f:
            json.dump({"url": self.url, "session_id": self.session_id}, f)

    def _save_tokens(self) -> None:
        if not self._instruments_file:
            return
        with open(self._instruments_file, "w", encoding="utf-8") as f:
            json.dump(self._tokens, f, indent=1)

    # ------------------------------------------------------------- transport
    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=httpx.Timeout(20.0), transport=self._transport)

    async def _post(self, body: Dict[str, Any], expect_response: bool = True) -> Optional[Dict[str, Any]]:
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        async with self._client() as client:
            async with client.stream("POST", self.url, headers=headers, json=body) as resp:
                if resp.status_code == 404 and self.session_id:
                    raise KiteAuthRequired("MCP session expired")
                if resp.status_code >= 400:
                    text = (await resp.aread()).decode("utf-8", "replace")[:200]
                    if resp.status_code == 400 and self.session_id and "invalid session" in text.lower():
                        raise KiteAuthRequired("MCP session expired")     # a saved session the server has forgotten
                    raise KiteMCPError(f"HTTP {resp.status_code}: {text}")
                sid = resp.headers.get("mcp-session-id")
                if sid:
                    self.session_id = sid
                if not expect_response:
                    await resp.aread()
                    return None
                ctype = resp.headers.get("content-type", "")
                if "event-stream" in ctype:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            payload = json.loads(line[5:].strip())
                            if payload.get("id") == body.get("id"):
                                return payload
                    raise KiteMCPError("stream ended without a response")
                raw = await resp.aread()
                return json.loads(raw) if raw else None

    async def _initialize(self) -> None:
        self.session_id = None
        self._req_id += 1
        res = await self._post({
            "jsonrpc": "2.0", "id": self._req_id, "method": "initialize",
            "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                       "clientInfo": {"name": "nexusquant", "version": "3.0"}},
        })
        if not self.session_id:
            raise KiteMCPError("server did not return an MCP session id")
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        self._save_session()
        self.logged_in = False
        if res and "error" in res:
            raise KiteMCPError(str(res["error"]))

    async def _rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            if not self.session_id:
                await self._initialize()
            self._req_id += 1
            body = {"jsonrpc": "2.0", "id": self._req_id, "method": method, "params": params}
            try:
                res = await self._post(body)
            except KiteAuthRequired:
                await self._initialize()
                self._req_id += 1
                body["id"] = self._req_id
                res = await self._post(body)
            finally:
                self._last_call = time.monotonic()
            if res is None:
                raise KiteMCPError("empty response")
            if "error" in res:
                raise KiteMCPError(str(res["error"].get("message", res["error"])))
            return res.get("result", {})

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
        text = "\n".join(texts)
        if result.get("isError"):
            if "log in" in text.lower() or "login" in text.lower():
                self.logged_in = False
                raise KiteAuthRequired(text)
            raise KiteMCPError(text or f"{name} failed")
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return text

    # ------------------------------------------------------------------ auth
    async def list_tools(self) -> List[str]:
        result = await self._rpc("tools/list", {})
        self.tools = [t["name"] for t in result.get("tools", [])]
        return self.tools

    async def login(self) -> Dict[str, Any]:
        """Returns {"logged_in": True, "user": ...} or {"logged_in": False, "url": <kite login url>}."""
        text = await self.call_tool("login")
        text = text if isinstance(text, str) else json.dumps(text)
        if "already logged in" in text.lower():
            self.logged_in = True
            m = re.search(r"logged in as (.+)$", text.strip(), re.IGNORECASE)
            self.user_name = m.group(1).strip() if m else self.user_name
            return {"logged_in": True, "user": self.user_name}
        urls = URL_RE.findall(text)
        if not urls:
            raise KiteMCPError("login tool did not return a URL")
        return {"logged_in": False, "url": urls[0]}

    async def check(self) -> bool:
        """Verify the session with get_profile. Never raises."""
        self.last_check = time.time()
        try:
            profile = await self.call_tool("get_profile")
            self.logged_in = True
            if isinstance(profile, dict):
                self.user_name = profile.get("user_name") or profile.get("user_shortname") or profile.get("user_id")
            self.last_error = None
        except KiteMCPError:
            # The hosted server answers "Please log in first" for new sessions and a generic
            # "Failed to execute get_profile" for stale ones; both mean a fresh login is needed.
            self.logged_in = False
            self.last_error = "login required"
        except Exception as e:  # network problem
            self.logged_in = False
            self.last_error = f"unreachable: {str(e)[:140]}"
        return self.logged_in

    def status(self) -> Dict[str, Any]:
        return {"url": self.url, "logged_in": self.logged_in, "user": self.user_name,
                "error": self.last_error, "can_trade": "place_order" in self.tools if self.tools else None}

    # ----------------------------------------------------------- market data
    async def ltp(self, symbols: List[str]) -> Dict[str, float]:
        inst = [kite_instrument(s) for s in symbols]
        data = await self.call_tool("get_ltp", {"instruments": inst})
        out = {}
        if isinstance(data, dict):
            for s in symbols:
                row = data.get(kite_instrument(s))
                if row and row.get("last_price"):
                    out[s] = float(row["last_price"])
        return out

    async def instrument_token(self, symbol: str) -> Optional[int]:
        if symbol in self._tokens:
            return self._tokens[symbol]
        data = await self.call_tool("search_instruments", {"query": kite_instrument(symbol), "filter_on": "id", "limit": 20})
        items = data.get("data", data) if isinstance(data, dict) else data
        for it in items or []:
            if it.get("tradingsymbol") == symbol and it.get("exchange") == "NSE":
                self._tokens[symbol] = int(it["instrument_token"])
                self._save_tokens()
                return self._tokens[symbol]
        return None

    async def historical(self, symbol: str, interval: str, from_dt: str, to_dt: str) -> List[List[float]]:
        """Rows of [epoch_seconds, o, h, l, c, v]. Dates are 'YYYY-MM-DD HH:MM:SS' IST."""
        token = await self.instrument_token(symbol)
        if not token:
            raise KiteMCPError(f"no instrument token for {symbol}")
        data = await self.call_tool("get_historical_data", {
            "instrument_token": token, "from_date": from_dt, "to_date": to_dt, "interval": interval,
        })
        rows = []
        for c in data or []:
            ts = _parse_kite_ts(c.get("date"))
            if ts is None:
                continue
            rows.append([ts, c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)])
        return rows

    # ---------------------------------------------------------------- orders
    async def place_order(self, symbol: str, side: str, qty: int, order_type: str = "MARKET",
                          price: Optional[float] = None, trigger_price: Optional[float] = None,
                          tag: str = "nexus") -> str:
        args: Dict[str, Any] = {
            "variety": "regular", "exchange": "NSE", "tradingsymbol": symbol, "transaction_type": side,
            "quantity": int(qty), "product": "MIS", "order_type": order_type, "validity": "DAY",
            "tag": re.sub(r"[^A-Za-z0-9]", "", tag)[:20],
        }
        if price is not None:
            args["price"] = round(price, 2)
        if trigger_price is not None:
            args["trigger_price"] = round(trigger_price, 2)
        data = await self.call_tool("place_order", args)
        order_id = data.get("order_id") if isinstance(data, dict) else None
        if not order_id:
            raise KiteMCPError(f"place_order returned no order id: {str(data)[:160]}")
        return str(order_id)

    async def cancel_order(self, order_id: str) -> None:
        await self.call_tool("cancel_order", {"variety": "regular", "order_id": order_id})

    async def order_history(self, order_id: str) -> List[Dict[str, Any]]:
        data = await self.call_tool("get_order_history", {"order_id": order_id})
        return data if isinstance(data, list) else []

    async def positions(self) -> Dict[str, Any]:
        data = await self.call_tool("get_positions")
        return data if isinstance(data, dict) else {}


def _parse_kite_ts(value: Any) -> Optional[int]:
    from datetime import datetime
    if not value:
        return None
    s = str(value).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return None
