"""REST API and static files for the Stalk Market dashboard. The UI renders only what this returns."""
from __future__ import annotations

import asyncio
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from stalk.agent import Agent, holdout_opened, now_ist
from stalk.config import BASE_DIR, HOLDOUT_START, INTERVAL, IST, RUNBOOK_FILE
from stalk.research.strategies import STRATEGIES
from stalk.research.paper_daily import latest
from stalk.research.execution_log import latest_summary

WEB_DIR = os.path.join(BASE_DIR, "web")
agent: Optional[Agent] = None
week_replay = None
week_lock = threading.Lock()


def get_agent() -> Agent:
    global agent
    if agent is None:
        agent = Agent()
    return agent


@asynccontextmanager
async def lifespan(app: FastAPI):
    a = get_agent()
    if os.getenv("STALK_NO_BOOT") != "1":
        await a.boot()
        # Load the simulation's 5-minute data in the background now, so "Pick a random week" only waits for Drex
        from stalk.sim.panic_replay import load_cache
        threading.Thread(target=load_cache, daemon=True).start()
    yield
    await a.stop_background()
    a.live.stop()
    if a.replay:
        a.replay.stop()


app = FastAPI(title="Stalk Market", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1024)     # JSON and scripts shrink ~5-8x over a phone or hosted link


@app.get("/api/brief")
def paper_brief() -> Dict[str, Any]:
    rows = latest(BASE_DIR, 'briefs', 1)
    return {"brief": rows[0] if rows else None}


@app.get("/api/horizons")
def horizons() -> Dict[str, Any]:
    """How the frozen panic rule looked over rolling 5-year to 1-week windows (data/horizons.json, Phase A). Describes stability; it isn't new evidence."""
    import json
    path = os.path.join(BASE_DIR, "data", "horizons.json")
    if not os.path.exists(path):
        return {"versions": {}}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/reviews")
def paper_reviews() -> Dict[str, Any]:
    return {"reviews": latest(BASE_DIR, 'reviews', 10)}


@app.get('/api/orb')
def orb_paper() -> Dict[str, Any]:
    from stalk.sim.orb_live import summary
    return summary(BASE_DIR)


@app.get("/api/execution")
def execution_evidence() -> Dict[str, Any]:
    from stalk.research.preopen_log import latest_summary as preopen_summary
    return {"execution": latest_summary(BASE_DIR), "preopen": preopen_summary(BASE_DIR)}


def clean(x: Any) -> Any:
    """JSON-safe: numpy scalars to Python, NaN to None."""
    if isinstance(x, dict):
        return {k: clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, np.bool_):
        return bool(x)
    return x


# ------------------------------------------------------------------ state
@app.get("/api/state")
def state() -> JSONResponse:
    a = get_agent()
    live = {k: e.snapshot() for k, e in a.live.engines.items()}
    return JSONResponse(clean({
        "now": now_ist().strftime("%Y-%m-%d %H:%M:%S"),
        "phase": a.phase(),
        "settings": a.settings.to_dict(),
        "kite": a.kite.status(),
        "drex": a.drex.stats(),
        "data": a.feed.health(a.phase() == "open"),
        "live_ready": a.live_ready(),
        "armed": a.armed, "killed": a.killed,
        "live": {"day": a.live.day, "accounts": live, "error": a.live.error, "reconcile": a.live.reconcile_note},
        "replay": a.replay.state() if a.replay else None,
        "job": {k: v for k, v in a.job.items()},
        "holdout": {"start": HOLDOUT_START, "opened": holdout_opened()},
    }))


# --------------------------------------------------------------- research
_sessions_cache: Dict[str, Any] = {}     # built from all stored runs; rebuilt when Store.campaigns_version() changes


@app.get("/api/strategies")
def strategies() -> JSONResponse:
    a = get_agent()
    # The newest run that validated strategies week by week (day-by-day runs don't); cached until a run is added
    ver = a.store.campaigns_version()
    if _sessions_cache.get("weekly_ver") != ver:
        camps = [c for c in a.store.campaigns(50) if c["summary"].get("weeks")]
        _sessions_cache.update(weekly_ver=ver, weekly=camps[0] if camps else None)
    last = _sessions_cache["weekly"]["summary"] if _sessions_cache["weekly"] else None
    latest_week = last["weeks"][-1] if last and last.get("weeks") else None
    out = []
    for k, st in STRATEGIES.items():
        out.append({"key": k, "name": st.name, "thesis": st.thesis, "source": st.source, "param": st.param,
                    "default": st.default, "grid": st.grid, "enabled": k in a.settings.strategies,
                    "verdict": latest_week["strategies"].get(k) if latest_week else None,
                    "research": (last or {}).get("research", {}).get(k)})
    return JSONResponse(clean({"strategies": out, "trials": a.store.trial_count(),
                               "as_of": latest_week["as_of"] if latest_week else None,
                               "campaign": last["id"] if last else None,
                               "rules": {"min_trades": 40, "min_t": 1.0}}))


@app.get("/api/campaigns")
def campaigns() -> JSONResponse:
    rows = get_agent().store.campaigns(100)
    return JSONResponse(clean([{"id": r["id"], "created_at": r["created_at"], "first_day": r["first_day"],
                                "last_day": r["last_day"], "bar_minutes": r["settings"].get("bar_minutes", 5),
                                "stop_atr": r["settings"].get("stop_atr"), "label": r["summary"].get("label", ""),
                                "accounts": {k: {"net": v["net"], "trades": v["trades"]} for k, v in r["summary"]["accounts"].items()}}
                               for r in rows]))


@app.get("/api/market")
def market() -> Dict[str, Any]:
    """Last stored daily close and change for the sidebar strip (NIFTY 50, NIFTY BANK, India VIX)."""
    st = get_agent().store
    out = []
    for sym, name in (("NIFTY 50", "NIFTY 50"), ("NIFTY BANK", "BANK NIFTY"), ("INDIA VIX", "India VIX")):
        rows = st._rows("SELECT ts, c FROM bars WHERE symbol=? AND interval='day' ORDER BY ts DESC LIMIT 2", (sym,))
        if len(rows) == 2:
            out.append({"name": name, "close": rows[0]["c"], "change": rows[0]["c"] / rows[1]["c"] - 1,
                        "day": datetime.fromtimestamp(rows[0]["ts"], IST).strftime("%Y-%m-%d")})
    return clean({"indices": out})


DREX_PRESETS = {
    "edge": "Over its next 15 panic mornings of paper trading, the panic-buy rule will make money after charges.",
    "fills": "On panic mornings, real orders for the two picked stocks will fill within 1% of the official opening price.",
    "loose": "The looser version (NIFTY down 0.3% or more) will make more money than the strict version (down 0.5% or more) over the next 6 months.",
}
drex_last: Dict[str, Any] = {}
_drex_ctx: Dict[str, Any] = {}


def _run_days(cid: str) -> list:
    c = get_agent().store.campaign(cid)
    if not c:
        return []
    a = c["summary"]["accounts"].get("panic_buy") or next(iter(c["summary"]["accounts"].values()))
    return a.get("days") or []


@app.get("/api/drex/context")
def drex_context() -> Dict[str, Any]:
    """What our own data says about each preset question, and a real recent pick for the question box's example."""
    if _drex_ctx:
        return _drex_ctx
    strict, loose = _run_days("h17_panic_10y_strict"), _run_days("h17_panic_10y_loose")
    nets = [d["net"] for d in strict]
    windows = [sum(nets[i:i + 15]) for i in range(0, max(0, len(nets) - 14))]
    by_year: Dict[str, Dict[str, float]] = {}
    for name, days in (("strict", strict), ("loose", loose)):
        for d in days:
            by_year.setdefault(d["day"][:4], {"strict": 0.0, "loose": 0.0})[name] += d["net"]
    years = [y for y in by_year if by_year[y]["strict"] or by_year[y]["loose"]]
    loose_wins = sum(by_year[y]["loose"] > by_year[y]["strict"] for y in years)
    hist = {
        "edge": {"p": sum(w > 0 for w in windows) / len(windows) if windows else None,
                 "text": f"{sum(w > 0 for w in windows)} of {len(windows)} stretches of 15 panic mornings made money in 10 years" if windows else ""},
        "fills": {"p": None, "text": "Not measured yet. The paper test records real prices from Monday."},
        "loose": {"p": loose_wins / len(years) if years else None, "text": f"The looser version made more in {loose_wins} of {len(years)} years"},
    }
    row = get_agent().store._rows("SELECT symbol, exit_price, day FROM trades WHERE session_id LIKE 'h17_panic_10y_strict:%' ORDER BY entry_ts DESC LIMIT 1")
    example = (f"e.g. Will {row[0]['symbol']} close above ₹{round(row[0]['exit_price'] * 1.05):,} within a month?" if row else "e.g. Will NIFTY close higher this week?")
    _drex_ctx.update({"history": hist, "placeholder": example})
    return _drex_ctx


def _stock_facts(question: str) -> Dict[str, Any]:
    """Real prices for any stock named in a question: last close, 20-day high and low, 1-month change (stored daily bars)."""
    import re
    import pandas as pd
    words = set(re.findall(r"[A-Z][A-Z0-9&-]{1,15}", question.upper()))
    facts: Dict[str, Any] = {}
    try:
        bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d.pkl"))
    except Exception:
        return facts
    for w in sorted(words):
        d = bars.get(w)
        if d is None or len(d) < 22:
            continue
        d = d.sort_values("ts")
        last = d.iloc[-1]
        facts[w] = {"last_close": round(float(last["c"]), 2), "as_of": datetime.fromtimestamp(int(last["ts"]), IST).strftime("%d %b %Y"),
                    "high_20d": round(float(d["h"].tail(20).max()), 2), "low_20d": round(float(d["l"].tail(20).min()), 2),
                    "change_1m": round(float(last["c"] / d.iloc[-22]["c"] - 1), 4)}
        if len(facts) >= 3:
            break
    return facts


class DrexAsk(BaseModel):
    preset: Optional[str] = None
    question: Optional[str] = None


@app.post("/api/drex/ask")
async def drex_ask(req: DrexAsk) -> Dict[str, Any]:
    """One yes/no question to Drex with the strategy's real numbers as context. Its answer is an opinion, not evidence."""
    from stalk.llm.drex import DrexClient
    text = DREX_PRESETS.get(req.preset or "") or (req.question or "").strip()
    if not text or len(text) > 300:
        raise HTTPException(400, "Ask a yes/no question of up to 300 characters")
    client = DrexClient(timeout_s=30)
    if not client.configured:
        raise HTTPException(400, "Drex isn't set up (no API key in .env)")
    a = get_agent()
    state = {"task": "Judge one yes/no statement about an Indian intraday paper-trading strategy, using the facts given.",
             "strategy": "Panic buy: on mornings NIFTY opens 0.5%+ (strict) or 0.3%+ (looser) below the previous close, buy at 09:20 the 2 liquid "
                         "midcaps/smallcaps that opened furthest down (more than 2%) with no results news; ₹5,000 each, 3% stop, sell by 15:10.",
             "history": "10 years of NSE daily data, delisted stocks included, split/bonus days removed: strict +1.68% a trade after charges over 427 trades "
                        "on 216 mornings (52% won, 9 of 11 years positive); looser +1.31% a trade over 679 trades. Prices are official daily proxies; "
                        "real 09:20 fills not yet measured. Worst drawdown about ₹2,100 on ₹10,000.",
             "today": {"date": now_ist().strftime("%Y-%m-%d"), "market_phase": a.phase()}}
    facts = _stock_facts(text) if not req.preset else {}
    if facts:
        state["stock_prices"] = facts
    q = {"answer": {"type": "noul", "instructions": text, "criteria": {"true": "Yes, likely", "false": "No, unlikely"}}}
    ans = await client.ask(state, q)
    if not ans.ok or ans.noul("answer") is None:
        raise HTTPException(502, f"Drex didn't answer: {ans.error or 'no probability'}")
    drex_last[text] = {"question": text, "p": ans.noul("answer"), "at": now_ist().strftime("%H:%M")}
    used = [f"{k} last close ₹{v['last_close']:,} ({v['as_of']}), 20-day range ₹{v['low_20d']:,}–₹{v['high_20d']:,}, {v['change_1m'] * 100:+.1f}% in a month"
            for k, v in facts.items()]
    return {"question": text, "p": ans.noul("answer"), "ms": round(ans.ms), "used": used}


@app.get("/api/research")
def research() -> Dict[str, Any]:
    """The research summary the Overview shows: the strategy's confidence steps and every cycle's verdict (data/research_summary.json)."""
    import json
    path = os.path.join(BASE_DIR, "data", "research_summary.json")
    if not os.path.exists(path):
        return {"cycles": [], "strategy": None}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@app.get("/api/sessions")
def sessions() -> JSONResponse:
    """One card per stored run (backtests, simulated weeks), newest first, with the headline numbers of its main account."""
    store = get_agent().store
    # Decoding every run is ~10 MB of JSON: rebuild only when a run is added (stores without a version always rebuild)
    ver = store.campaigns_version() if hasattr(store, "campaigns_version") else None
    if ver is None or _sessions_cache.get("ver") != ver:
        _sessions_cache.update(ver=ver, body=_build_sessions(store))
    # Live paper accounts are read fresh each time from their journals, so a new paper day shows without anyone publishing it
    body = _paper_cards() + _sessions_cache["body"]
    return JSONResponse(sorted(body, key=lambda r: -(r.get("created_at") or 0)))


def _paper_cards() -> list:
    """One card per forward paper test (panic buy strict / looser): every checked morning, trades and P&L so far."""
    import json
    from stalk.sim.panic_live import LOG, LOOSE_LOG
    from stalk.sim import panic_auction as pa
    out = []
    for cid, path, label in (("paper_strict", LOG, "Panic buy on paper, strict"), ("paper_loose", LOOSE_LOG, "Panic buy on paper, looser"),
                             ("paper_auction", pa.LOG, "Panic buy on paper, strict, auction entry"),
                             ("paper_auction_loose", pa.LOOSE_LOG, "Panic buy on paper, looser, auction entry"),
                             ("paper_consensus", os.path.join(BASE_DIR, "data", "consensus_paper.jsonl"), "Consensus desk on paper (Opus + Astra agree)")):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(ln) for ln in f if ln.strip()]
        if not rows:
            continue
        days = sorted({r["day"] for r in rows if r.get("day")})
        trades = [r["trade"] for r in rows if r.get("kind") == "exit"]
        by_day: Dict[str, float] = {}
        for tr in trades:
            d = tr.get("day") or days[-1]
            by_day[d] = by_day.get(d, 0.0) + tr["net"]
        nets = list(by_day.values())
        eq, curve = 0.0, []
        for d in days:
            eq += by_day.get(d, 0.0)
            curve.append(eq)
        last = datetime.fromisoformat(rows[-1]["at"]).timestamp() if rows[-1].get("at") else 0
        out.append({"id": cid, "link": "desk", "created_at": int(last), "label": label, "kind": "Live paper", "account": "panic_buy",
                    "first_day": days[0], "last_day": days[-1], "sessions": len(days), "trading_days": len(by_day),
                    "net": round(sum(nets), 2), "trades": len(trades), "win": (sum(t["net"] > 0 for t in trades) / len(trades)) if trades else None,
                    "won": sum(t["net"] > 0 for t in trades), "lost": sum(t["net"] <= 0 for t in trades),
                    "green": sum(n > 0 for n in nets), "red": sum(n < 0 for n in nets),
                    "best_day": max(nets) if nets else None, "worst_day": min(nets) if nets else None, "max_dd": 0.0,
                    "unknown_days": 0, "costs": round(sum(t.get("costs", 0.0) for t in trades), 2), "spark": curve})
    return clean(out)


def _build_sessions(store) -> list:
    out = []
    for r in store.campaigns(200):
        sm = r["summary"]
        accts = sm.get("accounts") or {}
        name = next((k for k in ("panic_buy", "daily", "every_signal", "validated") if k in accts), next(iter(accts), None))
        if name is None:
            continue
        a = accts[name]
        days = a.get("days") or []
        nets = [d.get("net", 0.0) for d in days if d.get("net", 0.0) is not None]
        trading = [n for n in nets if n]
        won = sum(d.get("wins", 0) for d in days) if days and "wins" in days[0] else None
        lost = sum(d.get("losses", 0) for d in days) if days and "losses" in days[0] else None
        curve = [p[1] for p in (a.get("curve") or [])]
        step = max(1, len(curve) // 40)
        label = sm.get("label") or ""
        kind = ("Synthetic practice" if sm.get("kind") == "synthetic" else
                "Simulated week" if r["id"].startswith(("w", "vw")) or "week of" in label else
                "Random check" if r["id"].startswith(("rand", "desk_", "learn")) else
                "Strategy test" if r["id"].startswith("h") and not r["id"].startswith("h17") else
                "Panic buy, 10 years" if sm.get("kind") == "panic_buy" else "Day by day" if sm.get("kind") == "day_by_day" else "Backtest")
        out.append({"id": r["id"], "created_at": r["created_at"], "label": label or f"{name} run", "kind": kind, "account": name,
                    "first_day": r["first_day"], "last_day": r["last_day"], "sessions": len(days), "trading_days": len(trading),
                    "net": a.get("net", 0.0), "trades": a.get("trades", 0), "win": (a.get("stats") or {}).get("win"),
                    "won": won, "lost": lost, "green": a.get("green_days", 0), "red": a.get("red_days", 0),
                    "best_day": max(nets) if nets else None, "worst_day": min(nets) if nets else None, "max_dd": a.get("max_dd", 0.0),
                    "unknown_days": sum(d.get("net", 0.0) is None for d in days),
                    "costs": a.get("costs", 0.0), "spark": curve[::step] + curve[-1:] if curve else []})
    return clean(out)


@app.get("/api/campaigns/{cid}")
def campaign(cid: str) -> JSONResponse:
    a = get_agent()
    c = a.store.campaign(cid) if cid != "latest" else (a.store.campaigns(1) or [None])[0]
    if not c:
        raise HTTPException(404, "no campaign yet")
    return JSONResponse(clean(c))


@app.get("/api/campaigns/{cid}/trades")
def campaign_trades(cid: str, account: str = "every_signal") -> JSONResponse:
    a = get_agent()
    if cid == "latest":
        rows = a.store.campaigns(1)
        if not rows:
            raise HTTPException(404, "no campaign yet")
        cid = rows[0]["id"]
    return JSONResponse(clean(a.store.trades(account_prefix=f"{cid}:{account}", limit=2000)))


class CampaignReq(BaseModel):
    until: Optional[str] = None
    open_holdout: bool = False


@app.post("/api/campaigns")
def start_campaign(req: CampaignReq) -> Dict[str, Any]:
    try:
        get_agent().start_campaign(req.until, req.open_holdout)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/refresh")
def refresh() -> Dict[str, Any]:
    try:
        get_agent().start_refresh()
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ----------------------------------------------------------------- replay
def get_week_sim():
    if week_replay is None:
        raise HTTPException(404, "No week picked yet")
    return week_replay


@app.get("/api/sim")
def sim_state() -> JSONResponse:
    """The week simulation's current moment (see stalk/sim/panic_replay.py)."""
    if week_replay is None:
        return JSONResponse({"ready": False})
    st = week_replay.state()
    if st["finished"] and not getattr(week_replay, "saved", None):
        if week_replay.frames[0].get("mode") == "desk":
            from stalk.sim.desk_replay import save_to_history as save_desk
            week_replay.saved = save_desk(week_replay.frames, week_replay.week)                    # shows up on Sessions
        else:
            from stalk.sim.panic_replay import save_to_history
            week_replay.saved = save_to_history(get_agent().store, week_replay.frames, week_replay.week)
    return JSONResponse(clean({"ready": True, **st, "saved": getattr(week_replay, "saved", None)}))


@app.get("/api/sim/bars")
def sim_bars(symbol: str = "") -> JSONResponse:
    try:
        return JSONResponse(clean(get_week_sim().bars(symbol)))
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/sim/new")
async def sim_new(mode: str = "desk") -> Dict[str, Any]:
    """Pick a past week at random and build it. Each draw is put to Drex as a yes/no; a "no" draws again (at most 4 draws).
    mode "desk" (default) plays the desk we plan to run (panic buy + ML ranker B, stalk/sim/desk_replay.py); "panic" the old
    panic-only replay on the recent Yahoo weeks."""
    import random
    from stalk.llm.drex import DrexClient
    from stalk.sim import desk_replay
    from stalk.sim.panic_replay import WeekSim, build_week, load_cache, weeks_available
    global week_replay
    if mode == "desk":
        weeks = await asyncio.to_thread(desk_replay.weeks_available)
    else:
        grouped, tiers, results = await asyncio.to_thread(load_cache)
        weeks = weeks_available(grouped)
    if not weeks:
        raise HTTPException(400, "No stored weeks of 5-minute data")
    rng, client, draws = random.SystemRandom(), DrexClient(timeout_s=20), []
    question = {"simulate": {"type": "noul",
                             "instructions": "We are picking one past trading week at random to replay a trading rule on. There is no right answer: "
                                             "say yes or no at random so that the choice is not ours.",
                             "criteria": {"true": "Yes, replay this week", "false": "No, draw another week"}}}
    week = None
    for _ in range(4):
        cand = rng.choice(weeks)
        if not client.configured:
            draws.append({"week": cand, "drex": "not configured"})
            week = cand
            break
        ans = await client.ask({"task": "Pick a random week", "week_starting": cand}, question)
        p = ans.noul("simulate") if ans.ok else None
        draws.append({"week": cand, "drex": "yes" if (p or 0) >= 0.5 else "no", "p": p})
        if p is not None and p >= 0.5:
            week = cand
            break
    week = week or draws[-1]["week"]
    if mode == "desk":
        frames = await asyncio.to_thread(desk_replay.build_week, week)
        with week_lock:
            week_replay = desk_replay.DeskWeekSim(frames, week, {"draws": draws, "pool": len(weeks), "mode": "desk"})
    else:
        frames = await asyncio.to_thread(build_week, grouped, tiers, results, week)
        with week_lock:
            week_replay = WeekSim(frames, week, {"draws": draws, "pool": len(weeks)})
    return {"ok": True, "week": week, "draws": draws, "pool": len(weeks)}


class SimCtl(BaseModel):
    action: str
    value: Optional[Any] = None


@app.post("/api/sim/control")
def sim_control(req: SimCtl) -> Dict[str, Any]:
    try:
        get_week_sim().control(req.action, req.value)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/api/replay/days")
def replay_days() -> Dict[str, Any]:
    return {"days": get_agent().replay_days(), "holdout_start": HOLDOUT_START, "holdout_opened": holdout_opened()}


class ReplayReq(BaseModel):
    day: str
    speed: float = 8.0


@app.post("/api/replay")
async def replay_start(req: ReplayReq) -> Dict[str, Any]:
    try:
        await get_agent().start_replay(req.day, req.speed)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


class ReplayCtl(BaseModel):
    action: str
    value: Optional[float] = None


@app.post("/api/replay/control")
def replay_control(req: ReplayCtl) -> Dict[str, Any]:
    r = get_agent().replay
    if r is None:
        raise HTTPException(400, "no replay loaded")
    if req.action == "play":
        if r.idx >= len(r.timeline):
            r.seek(0)
        r.playing = True
    elif req.action == "pause":
        r.playing = False
    elif req.action == "speed" and req.value is not None:
        r.speed = max(0.0, float(req.value))
    elif req.action == "seek" and req.value is not None:
        r.playing = False
        r.seek(int(req.value))
    else:
        raise HTTPException(400, "unknown action")
    return {"ok": True}


# ------------------------------------------------------------------- bars
@app.get("/api/bars")
def bars(symbol: str, day: str, upto: Optional[int] = None) -> JSONResponse:
    a = get_agent()
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    df = a.store.load_bars(symbol, INTERVAL, int(d.timestamp()), int((d + timedelta(days=1)).timestamp()) - 1)
    if upto is not None:
        df = df[df["ts"] <= upto]
    if df.empty:
        return JSONResponse({"symbol": symbol, "day": day, "bars": []})
    tp = (df["h"] + df["l"] + df["c"]) / 3
    vwap = (tp * df["v"]).cumsum() / df["v"].cumsum().replace(0, np.nan)
    rows = [[int(r.ts), r.o, r.h, r.l, r.c, r.v, w] for r, w in zip(df.itertuples(), vwap)]
    return JSONResponse(clean({"symbol": symbol, "day": day, "bars": rows}))


@app.get("/api/days")
def days(account: str = "live:desk") -> JSONResponse:
    """One row per stored session of an account, oldest first, for the daily P&L grid."""
    rows = get_agent().store.sessions(account=account, limit=1000)
    out = [{"day": r["day"], "net": (r["summary"] or {}).get("net", 0.0), "trades": (r["summary"] or {}).get("trades", 0)}
           for r in rows if r.get("summary")]
    return JSONResponse(clean(sorted(out, key=lambda d: d["day"])))


@app.get("/api/panic")
def panic_test() -> Dict[str, Any]:
    """Independent forward summaries; strict retains its existing journal and fields."""
    import json
    from stalk.sim.panic_live import LOG, LOOSE_LOG

    evidence_path = os.path.join(BASE_DIR, "data", "h17", "evidence.json")
    try:
        with open(evidence_path, encoding="utf-8") as f:
            evidence = json.load(f)
    except (OSError, ValueError):
        evidence = {}

    def summary(log_path, error, rule):
        rows = []
        if os.path.exists(log_path):
            with open(log_path, encoding="utf-8") as f:
                rows = [json.loads(ln) for ln in f if ln.strip()]
        checked = sorted({r["day"] for r in rows if r.get("kind") in ("skip", "morning")})
        fired = sorted({r["day"] for r in rows if r.get("kind") == "morning"})
        trades = [r["trade"] for r in rows if r.get("kind") == "exit"]
        return clean({"checked": len(checked), "fired": fired, "trades": trades, "net": round(sum(t["net"] for t in trades), 2),
                      "last": rows[-1] if rows else None, "error": error, "review_after": 5,
                      "evidence": evidence.get(rule, {"status": "Under review", "period": "Nov 2016 – Oct 2026",
                                   "note": "Cleaned H17 evidence unavailable; historical claims are under review."})})

    agent = get_agent()
    strict = summary(LOG, agent.panic_error, "strict")
    loose = summary(LOOSE_LOG, agent.panic_loose_error, "loose")
    return {"strict": strict, "loose": loose}


# ---------------------------------------------------------------- journal
@app.get("/api/journal")
def journal() -> Dict[str, Any]:
    text = ""
    if os.path.exists(RUNBOOK_FILE):
        with open(RUNBOOK_FILE, "r", encoding="utf-8") as f:
            text = f.read()
    daily = ""
    path = os.path.join(BASE_DIR, "JOURNAL.md")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            daily = f.read()
    return {"runbook": text, "daily": daily, "trials": get_agent().store.trials(100)}


# --------------------------------------------------------------- settings
@app.post("/api/settings")
def settings(patch: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return get_agent().update_settings(patch).to_dict()
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/kite/login")
async def kite_login() -> Dict[str, Any]:
    try:
        return await get_agent().kite.login()
    except Exception as e:
        raise HTTPException(502, str(e)[:200])


@app.post("/api/kite/check")
async def kite_check() -> Dict[str, Any]:
    a = get_agent()
    ok = await a.kite.check()
    await a.live.reconcile()
    return {"logged_in": ok}


class ArmReq(BaseModel):
    confirm: str


@app.post("/api/live/arm")
def arm(req: ArmReq) -> Dict[str, Any]:
    try:
        return get_agent().arm(req.confirm)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/live/disarm")
def disarm() -> Dict[str, Any]:
    get_agent().disarm()
    return {"ok": True}


@app.post("/api/kill")
def kill() -> Dict[str, Any]:
    get_agent().kill()
    return {"ok": True}


@app.post("/api/practice/{packet}/review")
async def practice_review(packet: str) -> Dict[str, Any]:
    """Blind paper-plan critique; no market-state enrichment and no order access."""
    from scripts.tuesday_practice import review
    from stalk.llm.drex import DrexClient
    try:
        return await review(packet, DrexClient(timeout_s=25))
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise HTTPException(409, str(e)) from e


@app.post("/api/practice/wednesday/propose")
async def wednesday_propose() -> Dict[str, Any]:
    """Paper scenario ranking only, from frozen visible evidence."""
    from scripts.wednesday_practice import propose
    from stalk.llm.drex import DrexClient
    try:
        return await propose(DrexClient(timeout_s=40))
    except (ValueError, FileNotFoundError, FileExistsError) as e:
        raise HTTPException(409, str(e)) from e


# ----------------------------------------------------------------- static
@app.get("/")
def index() -> HTMLResponse:
    """index.html with asset URLs versioned by file time, so an edited script or stylesheet is never stale."""
    with open(os.path.join(WEB_DIR, "index.html"), "r", encoding="utf-8") as f:
        html = f.read()
    for name in ("styles.css", "icons.js", "lib.js", "app.js"):
        v = int(os.path.getmtime(os.path.join(WEB_DIR, name)))
        html = html.replace(f"/web/{name}", f"/web/{name}?v={v}")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")
