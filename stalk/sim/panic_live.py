"""Forward paper test of the frozen panic-buy rule (stalk/sim/panic.py, RUNBOOK 2026-11-03). Paper only: it never places an order.

Each weekday, from 09:25 (once the 09:20 five-minute bar is published):
  1. Is NIFTY's 09:15 open more than 0.5% under yesterday's close? If not, log "not a panic morning" and stop for the day.
  2. Gaps for the wider universe come from NSE's pre-open snapshot (one request: auction price and previous close for every stock),
     results meetings from NSE's board-meeting calendar, lower-circuit locks from each candidate's first 5-minute bar (Yahoo).
  3. The two biggest eligible gap-downs are bought on paper at the 09:20 bar's open. Bid and ask are recorded from Kite when
     logged in, so real slippage can be measured later; overnight announcements are recorded for each pick.
  4. Every few minutes the 5-minute lows are checked against the 3% stop; anything still open is sold at the 15:10 bar's open.
Everything is appended to data/panic_paper.jsonl, one line per event, and closed trades go to the trades table (session "panic:<day>").

`replay(day)` runs the same steps on a past day from Yahoo's 5-minute bars (last 60 days only), to test the code path.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST
from stalk.data.yahoo import HEADERS, fetch
from stalk.sim import panic
from stalk.sim.price_basis import differs, resolve

logger = logging.getLogger("stalk.panic")
LOG = os.path.join(BASE_DIR, "data", "panic_paper.jsonl")
LOOSE_LOG = os.path.join(BASE_DIR, "data", "panic_paper_loose.jsonl")
NSE_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
          "Accept": "application/json", "Referer": "https://www.nseindia.com/"}


def _hm(ts: int) -> int:
    d = datetime.fromtimestamp(ts, IST)
    return d.hour * 100 + d.minute


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")


def universe() -> Dict[str, str]:
    """symbol → tier, for the wider stocks (the NIFTY 50 names the desk trades are excluded, as in the research)."""
    m = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv"))
    return {s: t for s, t in zip(m["symbol"], m["index"]) if s not in DEFAULT_UNIVERSE}


def five_min(symbol: str, client: Optional[httpx.Client] = None) -> List[List[float]]:
    return fetch(symbol, "5m", "5d", client=client)


def split_days(bars: List[List[float]]) -> Dict[str, List[List[float]]]:
    out: Dict[str, List[List[float]]] = {}
    for b in bars:
        out.setdefault(_day(int(b[0])), []).append(b)
    return out


def nse_client() -> httpx.Client:
    c = httpx.Client(headers=NSE_UA, timeout=30, follow_redirects=True)
    c.get("https://www.nseindia.com/")
    return c


def preopen(c: httpx.Client) -> Dict[str, Tuple[float, float]]:
    """symbol → (previous close, auction price) for every stock in today's pre-open."""
    d = c.get("https://www.nseindia.com/api/market-data-pre-open", params={"key": "ALL"}).json()
    out = {}
    for row in d.get("data", []):
        m = row.get("metadata") or {}
        # lastPrice may already be a normal-session print at 09:25. Without
        # an auction price there is no verified NSE opening gap to rank.
        if m.get("previousClose") and m.get("iep"):
            pc, op = float(m["previousClose"]), float(m["iep"])
            if not differs(pc, pc) and not differs(op, op):
                out[m["symbol"]] = (pc, op)
    return out


def liquid_universe(prev_day: str, c: Optional[httpx.Client] = None) -> set:
    """The 10-year evidence's universe (scripts/panic_bhav.py): EQ stocks with >= Rs10 crore traded in the previous session,
    price >= Rs50, not our NIFTY 50 names. From NSE's bhavcopy for prev_day (cached in data/bhav). Empty set if unavailable."""
    try:
        from scripts.panic_bhav import MIN_VALUE, bhav
        df = bhav(prev_day, c) if c is not None else pd.read_pickle(os.path.join(BASE_DIR, "data", "bhav", f"{prev_day}.pkl"))
    except Exception:
        return set()
    if df is None or df.empty:
        return set()
    ok = (df["value"] >= MIN_VALUE) & (df["close"] >= 50) & ~df.index.isin(DEFAULT_UNIVERSE)
    return set(df.index[ok])


def results_near(c: httpx.Client, day: str) -> set:
    """Symbols with a results board meeting from the previous session to the next (calendar days ±3 covers weekends)."""
    d = datetime.strptime(day, "%Y-%m-%d")
    rows = c.get("https://www.nseindia.com/api/corporate-board-meetings",
                 params={"index": "equities", "from_date": (d - timedelta(days=3)).strftime("%d-%m-%Y"),
                         "to_date": (d + timedelta(days=3)).strftime("%d-%m-%Y")}).json()
    out = set()
    for r in rows if isinstance(rows, list) else []:
        text = f"{r.get('bm_purpose') or ''} {r.get('bm_desc') or ''}"
        if "result" in text.lower():
            out.add(r.get("bm_symbol"))
    return out


def announcements(c: httpx.Client, symbol: str, day: str) -> List[str]:
    d = datetime.strptime(day, "%Y-%m-%d")
    try:
        rows = c.get("https://www.nseindia.com/api/corporate-announcements",
                     params={"index": "equities", "symbol": symbol, "from_date": (d - timedelta(days=3)).strftime("%d-%m-%Y"),
                             "to_date": d.strftime("%d-%m-%Y")}).json()
        return [f"{r.get('an_dt', '')}: {r.get('desc', '')}: {(r.get('attchmntText') or '')[:160]}" for r in rows[:6]]
    except Exception as e:  # context only; never blocks the test
        return [f"unavailable: {e}"]


class PanicPaper:
    entry_hm = panic.ENTRY_HM
    entry_basis = "09:20 bar-open paper reference; not a realised fill."

    def __init__(self, store=None, kite=None, log_path: Optional[str] = None,
                 index_gap: float = panic.INDEX_GAP, name: str = "panic"):
        self.store, self.kite = store, kite
        self.index_gap, self.name = index_gap, name
        self.log_path = log_path if log_path is not None else (LOOSE_LOG if name == "panic03" else LOG)
        self.strategy = "panic_buy_loose" if name == "panic03" else "panic_buy"
        self.day: Optional[str] = None
        self.state: Dict[str, Any] = {}

    # ------------------------------------------------------------ journal
    def _write(self, kind: str, **data: Any) -> None:
        row = {"at": datetime.now(IST).isoformat(timespec="seconds"), "day": self.day, "kind": kind, **data}
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    def _done_today(self, day: str) -> bool:
        if not os.path.exists(self.log_path):
            return False
        with open(self.log_path, encoding="utf-8") as f:
            return any(json.loads(ln).get("day") == day and json.loads(ln).get("kind") in ("skip", "closed_all") for ln in f if ln.strip())

    # ------------------------------------------------------------ morning
    def morning(self, day: str, nifty_bars: Dict[str, List[List[float]]], gaps: Dict[str, Tuple[float, float]],
                near: set, bars_for, liquid_for=None) -> List[panic.Candidate]:
        """Decide the day's picks. gaps: symbol → (prev close, open). bars_for(symbol) → that day's 5-minute bars."""
        days = sorted(nifty_bars)
        if day not in nifty_bars or days.index(day) == 0:
            self._write("skip", why="no NIFTY bars for today")
            return []
        n_open, n_prev = nifty_bars[day][0][1], nifty_bars[days[days.index(day) - 1]][-1][4]
        n_gap = n_open / n_prev - 1
        if not panic.market_qualifies(n_open, n_prev, self.index_gap):
            self._write("skip", why="not a panic morning", nifty_gap=round(n_gap, 4))
            return []
        tiers = universe()
        # Owner's yes, 2026-11-01: rank from the universe the 10-year evidence used (every liquid EQ stock), not only the stored
        # index list; stocks outside the list get small-cap slippage. Falls back to the stored list if the bhavcopy is unavailable.
        liquid = liquid_for(days[days.index(day) - 1]) if liquid_for else set()
        allowed = (set(tiers) | liquid) - set(DEFAULT_UNIVERSE)
        cands = [panic.Candidate(s, pc, op, tiers.get(s, "smallcap"), s in near) for s, (pc, op) in gaps.items() if s in allowed]
        ranked = sorted([c for c in cands if c.gap < panic.STOCK_GAP and not c.results_near], key=lambda c: c.gap)
        picks: List[panic.Candidate] = []
        for c in ranked:                                   # walk down the list until two are tradable (not locked)
            b = bars_for(c.symbol)
            if not b or _hm(int(b[0][0])) != 915:
                self._write("candidate_skip", symbol=c.symbol, why="first five-minute bar unavailable")
                continue
            # Same adjusted NSE denominator on both sides: only compare opening
            # prices, not an unadjusted Yahoo previous session close.
            if differs(b[0][1], c.open):
                self._write("candidate_skip", symbol=c.symbol, why="price data mismatch",
                            nse_open=c.open, yahoo_open=b[0][1], nse_gap=c.gap,
                            yahoo_gap_same_reference=b[0][1] / c.prev_close - 1)
                continue
            c.locked = b[0][2] == b[0][3]
            if not c.locked:
                picks.append(c)
            else:
                self._write("candidate_skip", symbol=c.symbol, why="first five-minute bar locked")
            if len(picks) == panic.PICKS:
                break
        self._write("morning", nifty_gap=round(n_gap, 4), eligible=len(ranked),
                    candidates=[(c.symbol, round(c.gap, 4), c.tier) for c in ranked[:8]], picks=[c.symbol for c in picks],
                    ranking=[{"rank": i+1, "symbol": c.symbol, "gap": c.gap, "tier": c.tier,
                              "official_open": c.open, "previous_close": c.prev_close} for i, c in enumerate(ranked)],
                    ranking_basis="Opening gaps; observation time is at. " + self.entry_basis)
        return picks

    async def quotes(self, symbols: List[str]) -> Dict[str, Any]:
        if not (self.kite and getattr(self.kite, "logged_in", False)):
            return {"source": "none", "note": "Kite not logged in; slippage not measured"}
        try:
            from stalk.data.kite_mcp import kite_instrument
            data = await self.kite.call_tool("get_quotes", {"instruments": [kite_instrument(s) for s in symbols]})
            out = {}
            for s in symbols:
                row = (data or {}).get(kite_instrument(s)) or {}
                depth = row.get("depth") or {}
                bid = (depth.get("buy") or [{}])[0].get("price")
                ask = (depth.get("sell") or [{}])[0].get("price")
                out[s] = {"bid": bid, "ask": ask, "last": row.get("last_price"),
                          "bid_size": (depth.get("buy") or [{}])[0].get("quantity"),
                          "ask_size": (depth.get("sell") or [{}])[0].get("quantity"),
                          "exchange_timestamp": row.get("timestamp")}
            return {"source": "kite", "quotes": out}
        except Exception as e:
            return {"source": "error", "note": str(e)[:200]}

    def settle(self, day: str, c: panic.Candidate, bars: List[List[float]]) -> Optional[panic.PanicTrade]:
        """Close a pick if its stop was hit or the 15:10 bar is in; otherwise None (still open)."""
        hms = [_hm(int(b[0])) for b in bars]
        if panic.ENTRY_HM not in hms:
            return None
        i0 = hms.index(panic.ENTRY_HM)
        entry = bars[i0][1]
        stopped = any(b[3] <= entry * (1 - panic.STOP) for b, hm in zip(bars[i0:], hms[i0:]) if hm < panic.EXIT_HM)
        if not stopped and max(hms) < panic.EXIT_HM:
            return None
        t = panic.walk(day, c, bars, hms)
        if t and self.store is not None:
            exit_hm = next((hm for b, hm in zip(bars[i0:], hms[i0:]) if hm >= panic.EXIT_HM or b[3] <= entry * (1 - panic.STOP)), hms[-1])
            ets = int(datetime.strptime(f"{day} {exit_hm:04d}", "%Y-%m-%d %H%M").replace(tzinfo=IST).timestamp())
            self.store.save_trade({"id": f"{self.name}:{day}:{c.symbol}", "session_id": f"{self.name}:{day}", "day": day, "strategy": self.strategy,
                                   "symbol": c.symbol, "side": "long", "qty": t.qty, "entry_ts": int(bars[i0][0]), "entry_price": t.entry,
                                   "exit_ts": ets, "exit_price": t.exit, "stop_initial": round(entry * (1 - panic.STOP), 2),
                                   "gross": t.gross, "costs": t.costs, "net": t.net, "reason": "flatten" if t.reason == "close" else t.reason, "fee_version": "zerodha-2026",
                                   "broker": "paper"})
        return t

    # ------------------------------------------------------------ live
    async def tick(self, now: Optional[datetime] = None) -> None:
        now = now or datetime.now(IST)
        day = now.strftime("%Y-%m-%d")
        if now.weekday() >= 5 or now.hour * 100 + now.minute < 925:
            return
        if self.day != day:
            self.day, self.state = day, {}
            if self._done_today(day):
                self.state["finished"] = True
        if self.state.get("finished"):
            return
        with httpx.Client(timeout=20, headers=HEADERS) as yc:
            if "picks" not in self.state:
                nse = nse_client()
                try:
                    gaps, near = preopen(nse), results_near(nse, day)
                    cache: Dict[str, List[List[float]]] = {}

                    def bars_for(s: str) -> List[List[float]]:
                        if s not in cache:
                            cache[s] = split_days(five_min(s, yc)).get(day, [])
                        return cache[s]
                    picks = self.morning(day, split_days(five_min("NIFTY 50", yc)), gaps, near, bars_for,
                                         liquid_for=lambda prev: liquid_universe(prev, nse))
                    self.state["picks"] = picks
                    if not picks:
                        self.state["finished"] = True
                        return
                    q = await self.quotes([c.symbol for c in picks])
                    q["observed_at"] = datetime.now(IST).isoformat(timespec="seconds")
                    self._write("entry", entry_hm=self.entry_hm, entry_basis=self.entry_basis,
                                picks=[{"symbol": c.symbol, "gap": round(c.gap, 4), "tier": c.tier,
                                                 "official_open": c.open,
                                                 "entry_bar_open": next((b[1] for b in bars_for(c.symbol) if _hm(int(b[0])) == self.entry_hm), None),
                                                 "entry_bar_at": next((datetime.fromtimestamp(b[0], IST).isoformat() for b in bars_for(c.symbol) if _hm(int(b[0])) == self.entry_hm), None),
                                                 "news": announcements(nse, c.symbol, day)} for c in picks], quotes=q)
                finally:
                    nse.close()
            open_picks = [c for c in self.state["picks"] if c.symbol not in self.state.setdefault("closed", {})]
            for c in open_picks:
                t = self.settle(day, c, split_days(five_min(c.symbol, yc)).get(day, []))
                if t:
                    self.state["closed"][c.symbol] = panic.as_dict(t)
                    self._write("exit", trade=panic.as_dict(t))
            if len(self.state["closed"]) == len(self.state["picks"]):
                net = sum(t["net"] for t in self.state["closed"].values())
                self._write("closed_all", net=round(net, 2))
                self.state["finished"] = True

    # ------------------------------------------------------------ replay
    def replay(self, day: str) -> Dict[str, Any]:
        """Run the same decision and settlement on a past day from Yahoo bars (gaps from the 09:15 bar, results from the stored calendar)."""
        self.day = day
        with httpx.Client(timeout=30, headers=HEADERS) as yc:
            nifty = split_days(fetch("NIFTY 50", "5m", "60d", client=yc))
            days = sorted(nifty)
            prev = days[days.index(day) - 1]
            tiers = universe()
            allb: Dict[str, Dict[str, List[List[float]]]] = {}
            bars5 = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_5m.pkl"))
            for s in tiers:
                if s in bars5:
                    allb[s] = split_days(bars5[s][["ts", "o", "h", "l", "c", "v"]].values.tolist())
            gaps = {}
            for s, b in allb.items():
                if day not in b or _hm(int(b[day][0][0])) != 915:
                    continue
                basis = resolve(day, s, b[day][0][1], b[prev][-1][4] if prev in b else None)
                if basis["mismatch"]:
                    self._write("candidate_skip", symbol=s, why="price data mismatch", details=basis["details"])
                else:
                    if basis["source"] == "yahoo_fallback":
                        self._write("gap_source", symbol=s, source=basis["source"])
                    gaps[s] = (basis["prev_close"], basis["open"])
            ev = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_events.pkl"))
            ev = ev[(ev["purpose"].fillna("") + " " + ev["detail"].fillna("")).str.contains("result", case=False)]
            i = days.index(day)
            window = set(days[max(0, i - 1): i + 2])
            near = set(ev.loc[ev["day"].isin(window), "symbol"])
            picks = self.morning(day, nifty, gaps, near, lambda s: allb.get(s, {}).get(day, []))
            trades = [self.settle(day, c, allb[c.symbol][day]) for c in picks]
        return {"day": day, "picks": [c.symbol for c in picks], "trades": [panic.as_dict(t) for t in trades if t]}
