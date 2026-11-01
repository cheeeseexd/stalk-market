"""Drex decision API (TypeSafe System One schema). Copied from the archived app's Jev client.

Drex only votes. It gets a masked description of a queued setup (no ticker, no date) and answers
whether the target is reached before the stop. The vote is stored on the trade so its Brier score
can be compared with the Time Machine's. It never places, sizes or blocks an order.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

DREX_URL = "https://drex.nace.ai/v1/systemone"
TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"
JEV_URL = TYPESAFE_URL


def api_url() -> str:
    if os.getenv("DREX_URL"):
        return os.getenv("DREX_URL")
    if os.getenv("SYSTEMONE_URL"):
        return os.getenv("SYSTEMONE_URL")
    if os.getenv("DREX_API_KEY"):
        return DREX_URL
    return TYPESAFE_URL


def api_key() -> str:
    return os.getenv("DREX_API_KEY") or os.getenv("TYPESAFE_API_KEY") or os.getenv("TYPESAFE_AI_API_KEY") or ""


def default_model(target_url: Optional[str] = None) -> str:
    u = target_url or api_url()
    if "drex" in u or os.getenv("DREX_API_KEY"):
        return "drex-latest"
    return "jev-latest"


@dataclass
class DrexAnswer:
    ok: bool
    answers: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    ms: float = 0.0
    tokens: int = 0
    error: Optional[str] = None

    def noul(self, name: str) -> Optional[float]:
        a = self.answers.get(name) or {}
        v = a.get("noul")
        return float(v) if v is not None else None

    def score(self, name: str) -> Optional[Dict[str, float]]:
        a = self.answers.get(name) or {}
        if a.get("score") is None:
            return None
        return {"score": float(a["score"]), "confidence": float(a.get("confidence") or 0.0)}


class DrexClient:
    def __init__(self, model: Optional[str] = None, transport: Optional[httpx.AsyncBaseTransport] = None,
                 key: Optional[str] = None, url: Optional[str] = None, timeout_s: float = 2.5):
        self._url = url
        self._key = key
        if model and model != "jev-latest":
            self.model = model
        elif model == "jev-latest" and (os.getenv("DREX_API_KEY") or (url and "drex" in url)):
            self.model = "drex-latest"
        else:
            self.model = model or default_model(self._url)
        self._transport = transport
        self.timeout_s = timeout_s
        self.calls = 0
        self.errors = 0
        self.tokens = 0
        self.latencies: deque = deque(maxlen=300)
        self.last_error: Optional[str] = None

    @property
    def url(self) -> str:
        return self._url or api_url()

    @property
    def key(self) -> str:
        return self._key if self._key is not None else api_key()

    @property
    def configured(self) -> bool:
        return bool(self.key)

    @property
    def provider(self) -> str:
        return "Drex" if ("drex" in self.url or "drex" in self.model) else "Jev"

    async def ask(self, state: Any, questions: Dict[str, Dict[str, Any]]) -> DrexAnswer:
        if not self.configured:
            return DrexAnswer(ok=False, error="DREX_API_KEY / TYPESAFE_API_KEY not set")
        body = {"model": self.model, "state": state, "questions": questions}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        t0 = time.perf_counter()
        err = "no response"
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_s, transport=self._transport) as client:
                    r = await client.post(self.url, headers=headers, json=body)
                if r.status_code in (429, 529) and attempt == 0:
                    await asyncio.sleep(0.3)
                    continue
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:160]}")
                data = r.json()
                ms = (time.perf_counter() - t0) * 1000
                usage = data.get("usage") or {}
                tokens = int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
                self.calls += 1
                self.tokens += tokens
                self.latencies.append(ms)
                self.last_error = None
                return DrexAnswer(ok=True, answers=data.get("answers") or {}, ms=ms, tokens=tokens)
            except Exception as e:
                err = str(e)[:200]
                if attempt == 1 or "HTTP 4" in err:
                    break
        self.errors += 1
        self.last_error = err
        return DrexAnswer(ok=False, ms=(time.perf_counter() - t0) * 1000, error=err)

    def stats(self) -> Dict[str, Any]:
        lat = sorted(self.latencies)
        def pct(q: float) -> Optional[int]:
            return int(lat[min(len(lat) - 1, int(q * len(lat)))]) if lat else None
        return {"configured": self.configured, "model": self.model, "provider": self.provider,
                "calls": self.calls, "errors": self.errors,
                "tokens": self.tokens, "p50_ms": pct(0.5), "p90_ms": pct(0.9), "last_error": self.last_error}


def setup_state(setup: Dict[str, Any], target_r: float) -> Dict[str, Any]:
    tm = setup.get("tm") or {}
    return {
        "task": "Evaluate one proposed intraday equity trade on the Indian market (NSE), 5-minute bars.",
        "proposed_trade": {"direction": setup["side"], "strategy": setup["strategy"],
                           "stop": "1.5 average true ranges from entry", "target": f"{target_r:g} times the stop distance",
                           "exit": "flat by 15:10 IST"},
        "time_ist": setup["clock"],
        "history_odds": {"target_first": tm.get("p_target"), "stop_first": tm.get("p_stop")},
    }


QUESTIONS = {
    "reaches_target": {
        "type": "noul",
        "instructions": "The proposed trade reaches its target before its stop, before the 15:10 exit.",
        "criteria": {"true": "Target is hit first", "false": "Stop is hit first, or the trade times out"},
    },
}


# ------------------------------------------------------------------ factor checklist
# Drex checks a queued setup against named factors and may veto it. It never places, sizes or exits a trade.

CHECKLIST = {
    "with_market": {
        "type": "noul",
        "instructions": "The proposed trade is in the same direction as the broader Indian market today (NIFTY 50, Bank NIFTY and market breadth).",
        "criteria": {"true": "The market is moving the same way as the trade", "false": "The market is moving against the trade, or is mixed"},
    },
    "no_event_risk": {
        "type": "noul",
        "instructions": "The stock has no scheduled event (results, board meeting) within two sessions that could gap the price against the trade.",
        "criteria": {"true": "No such event is known", "false": "An event is scheduled close enough to matter"},
    },
    "sector_supports": {
        "type": "noul",
        "instructions": "The stock's sector peers are moving in the direction of the trade today.",
        "criteria": {"true": "Peers support the direction", "false": "Peers move against it, or there is no clear sector move"},
    },
    "room_to_target": {
        "type": "noul",
        "instructions": "Given how far the stock has already moved today relative to its usual daily range, there is room left to reach the target before the session ends.",
        "criteria": {"true": "The target is within a normal day's remaining move", "false": "The stock has already used most of its usual range in this direction"},
    },
    "fits_thesis": {
        "type": "noul",
        "instructions": "The current price action matches the strategy's stated idea, not just its trigger.",
        "criteria": {"true": "Price action fits the idea", "false": "The trigger fired but the context contradicts the idea"},
    },
    "quality": {
        "type": "score",
        "instructions": "Overall quality of this trade setup, judged only from the state given.",
        "criteria": ["Clearly against the trade", "Weak", "Neutral", "Good", "Strong: market, sector, trend and room all aligned"],
    },
}


def _pct(x: Any) -> Any:
    return None if x is None or x != x else round(float(x) * 100, 2)


def checklist_state(c: Dict[str, Any], f_row: Dict[str, Any], ctx: Dict[str, Any], thesis: str, sector_name: str,
                    events: List[str], vix: Optional[float], target_r: float, bar_minutes: int) -> Dict[str, Any]:
    """Everything known about a setup at the moment it was queued. No ticker, no date: Drex judges the situation only."""
    entry = c["entry"]
    adr = f_row.get("adr")
    return {
        "task": f"Check one proposed intraday equity trade on NSE ({bar_minutes}-minute bars) against named factors. "
                "Answer only from this state.",
        "proposed_trade": {
            "direction": c["side"], "strategy_idea": thesis,
            "stop_percent_from_entry": round(abs(entry - c["stop"]) / entry * 100, 2),
            "target_percent_from_entry": round(abs(c["target"] - entry) / entry * 100, 2),
            "reward_to_risk": target_r, "must_close_by": "15:15 IST (intraday, no overnight)",
        },
        "time_ist": c["clock"],
        "stock_now": {
            "move_since_open_percent": _pct(f_row.get("rday")),
            "usual_daily_range_percent": round(adr / entry * 100, 2) if adr and adr == adr else None,
            "distance_from_vwap_percent": _pct((entry / f_row["vwap"] - 1) if f_row.get("vwap") else None),
            "rsi_14": c.get("context", {}).get("rsi"),
            "relative_volume_vs_usual": round(c.get("rvol", 0.0), 2),
            "opening_gap_percent": _pct(f_row.get("gap")),
            "distance_from_previous_day_high_percent": _pct((entry / f_row["pdh"] - 1) if f_row.get("pdh") else None),
            "distance_from_previous_day_low_percent": _pct((entry / f_row["pdl"] - 1) if f_row.get("pdl") else None),
            "distance_from_52_week_high_percent": _pct((entry / f_row["hi52"] - 1) if f_row.get("hi52") and f_row["hi52"] == f_row["hi52"] else None),
        },
        "market_now": {
            "nifty_move_since_open_percent": _pct(f_row.get("idx_rday")),
            "bank_nifty_move_since_open_percent": _pct(f_row.get("bank_rday")),
            "share_of_large_caps_above_vwap": c.get("context", {}).get("breadth"),
            "india_vix": vix,
        },
        "sector": {"name": sector_name, "peers_average_move_since_open_percent": _pct(c.get("context", {}).get("sector"))},
        "scheduled_events": events or ["none known"],
    }


def checklist_verdict(ans: "DrexAnswer", floor: float = 0.3, min_score: float = 1.5) -> Dict[str, Any]:
    """Veto when any check is below the floor or the quality score (0 = clearly against … 4 = strong) is below min_score."""
    if not ans.ok:
        return {"ok": True, "veto": False, "reason": "Drex unavailable", "answers": {}}
    checks = {k: ans.noul(k) for k in CHECKLIST if CHECKLIST[k]["type"] == "noul"}
    score = (ans.score("quality") or {}).get("score")
    low = [k for k, v in checks.items() if v is not None and v < floor]
    veto = bool(low) or (score is not None and score < min_score)
    names = {"with_market": "against the market", "no_event_risk": "event risk", "sector_supports": "sector against",
             "room_to_target": "no room to target", "fits_thesis": "doesn't fit the idea"}
    reason = ("Drex: " + ", ".join(names[k] for k in low)) if low else (f"Drex: quality {score:.1f} of 4" if veto else "Drex: pass")
    return {"ok": True, "veto": veto, "reason": reason, "answers": {**checks, "quality": score}}
