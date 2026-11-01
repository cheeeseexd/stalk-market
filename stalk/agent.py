"""The running app: settings, data, the live paper loop, day replay, campaign jobs and the live guards.

Live trading runs two paper accounts side by side every session (Pranjal's "paper beside live"):
  desk    the real candidate: only validated strategies, every risk rule
  shadow  every strategy signal, same risk rules, so the day's opportunities are measured even
          while nothing is validated
Real Kite orders mirror the desk account only when every guard in `live_ready` passes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from stalk.config import (BASE_DIR, DATA_DIR, HOLDOUT_START, INDEX_SYMBOL, INTERVAL, IST, SESSION_CLOSE_MIN,
                          SESSION_OPEN_MIN, Settings, load_settings, save_settings)
from stalk.data.feed import Feed
from stalk.data.kite_mcp import KiteMCP
from stalk.data.store import Store
from stalk.llm.drex import QUESTIONS, DrexClient, setup_state
from stalk.research.load import load_features
from stalk.research.strategies import STRATEGIES
from stalk.research.validate import all_events, validate_as_of
from stalk.sim.broker import LiveKiteBroker
from stalk.sim.campaign import run_campaign
from stalk.sim.engine import DayEngine
from stalk.sim.panic_live import PanicPaper
from stalk.research.paper_daily import PaperDaily
from stalk.sim.orb_live import ORBPaper
from stalk.research.execution_log import ExecutionLog

logger = logging.getLogger("agent")

SETTLE_SECONDS = 12
LOOKBACK_DAYS = 100
HOLDOUT_FILE = os.path.join(DATA_DIR, "holdout_opened.json")


def now_ist() -> datetime:
    return datetime.now(IST)


def market_phase(dt: datetime) -> str:
    if dt.weekday() >= 5:
        return "weekend"
    m = dt.hour * 60 + dt.minute
    if m < SESSION_OPEN_MIN:
        return "pre_open"
    if m < SESSION_CLOSE_MIN:
        return "open"
    return "closed"


def holdout_opened() -> bool:
    return os.path.exists(HOLDOUT_FILE)


class Validation:
    """Strategy verdicts as of a day, from stored history. Cached per (day, settings shape)."""

    def __init__(self, agent: "Agent"):
        self.agent = agent
        self._cache: Dict[str, Any] = {}
        self.events: Dict[str, Any] = {}

    def as_of(self, day: str, feats=None) -> Dict[str, Dict[str, Any]]:
        s = self.agent.settings
        key = json.dumps([day, s.stop_atr, s.target_r, s.risk_per_trade, s.cost_gate_mult, s.strategies])
        if key not in self._cache:
            feats = feats if feats is not None else load_features(self.agent.store, s)
            self.events = all_events(feats, s, [k for k in s.strategies if k in STRATEGIES])
            self._cache = {key: {k: validate_as_of(ev, k, day, holdout_opened()) for k, ev in self.events.items()}}
        return self._cache[key]


class LiveRunner:
    def __init__(self, agent: "Agent"):
        self.agent = agent
        self.day: Optional[str] = None
        self.engines: Dict[str, DayEngine] = {}
        self.states: Dict[str, Dict[str, Any]] = {}
        self.last_bar_ts: Optional[int] = None
        self.error: Optional[str] = None
        self.reconcile_note: Optional[str] = None
        self.stop_ids: Dict[str, str] = {}
        self._task: Optional[asyncio.Task] = None

    def session_id(self, account: str) -> str:
        return f"live:{self.day}:{account}"

    def _restore(self) -> None:
        """After a restart, pick up today's accounts where they left off."""
        for acct in ("desk", "shadow"):
            row = self.agent.store.session(self.session_id(acct))
            self.states[acct] = (row or {}).get("state") or {}
            if not self.states[acct]:
                # A new session: carry the loss streak and any timeout from the last live session.
                prev = next((r for r in self.agent.store.sessions(kind="live", account=f"live:{acct}", limit=5)
                             if r["day"] < (self.day or "") and r.get("state")), None)
                g = ((prev or {}).get("state") or {}).get("gate") or {}
                if g:
                    self.states[acct] = {"gate": {"streak": int(g.get("streak", 0)), "locked": max(0, int(g.get("locked", 0)) - 1)}}
            if row and row.get("state"):
                self.last_bar_ts = max(self.last_bar_ts or 0, row["state"].get("last_ts") or 0) or None

    def _latest_complete_bar(self, dt: datetime) -> Optional[int]:
        minute = dt.hour * 60 + dt.minute
        elapsed = (minute - SESSION_OPEN_MIN) * 60 + dt.second - SETTLE_SECONDS
        n = elapsed // 300
        if n < 1:
            return None
        n = min(n, (SESSION_CLOSE_MIN - SESSION_OPEN_MIN) // 5)
        start = SESSION_OPEN_MIN + (n - 1) * 5
        return int(dt.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0).timestamp())

    async def process(self, bar_ts: int) -> None:
        a = self.agent
        s = a.settings
        await a.feed.refresh_today()
        start = int((now_ist() - timedelta(days=LOOKBACK_DAYS)).timestamp())
        feats = await asyncio.to_thread(load_features, a.store, s, None, start)
        verdicts = await asyncio.to_thread(a.validation.as_of, self.day)
        have = sum(1 for f in feats.values() if (f["ts"] == bar_ts).any())
        stale = have < 0.7 * max(1, len(s.universe))
        for acct, require in (("desk", True), ("shadow", False)):
            acc_s = Settings.from_dict({**s.to_dict(), "require_validated": require})
            eng = DayEngine(acc_s, self.day, self.session_id(acct), s.capital, feats, s.strategies,
                            {k: v["validated"] for k, v in verdicts.items()}, events=a.validation_events(),
                            broker_name="kite" if (acct == "desk" and a.live_ready()["ready"]) else "paper",
                            avoid=a.events_for(self.day))
            eng.load_state(self.states.get(acct) or {})
            eng.gate.stale = stale or bool(self.reconcile_note and acct == "desk")
            eng.gate.killed = a.killed
            if stale:
                eng.log(bar_ts, "data", f"Only {have} of {len(s.universe)} stocks have the latest bar; entries paused")
            for ts in eng.timeline():
                if eng.last_ts is None or ts > eng.last_ts:
                    if ts <= bar_ts:
                        eng.on_bar(ts)
            if acct == "desk":
                await self._mirror(eng)
            if s.drex_enabled and a.drex.configured:
                await self._drex_votes(eng)
            self.engines[acct] = eng
            self.states[acct] = eng.to_state()
            a.store.save_session(self.session_id(acct), "live", self.day, f"live:{acct}", acc_s.to_dict(),
                                 eng.summary(), eng.to_state())
            for tr in eng.closed:
                a.store.save_trade(tr)

    async def _mirror(self, eng: DayEngine) -> None:
        """Send the desk account's entries and exits to Zerodha, only when every live guard passes."""
        events, eng.events_out = eng.events_out, []
        if not self.agent.live_ready()["ready"]:
            return
        broker = LiveKiteBroker(self.agent.kite)
        for ev in events:
            try:
                if ev["type"] == "entry":
                    res = await broker.enter(ev["symbol"], ev["side"], ev["qty"], ev["stop"])
                    self.stop_ids[ev["symbol"]] = res["stop_order_id"]
                elif ev["type"] == "exit":
                    await broker.exit(ev["symbol"], ev["side"], ev["qty"], self.stop_ids.pop(ev["symbol"], None))
            except Exception as e:
                eng.gate.killed = True
                self.agent.killed = True
                eng.log(eng.last_ts or 0, "error", f"Live order failed, kill switch on: {e}")

    async def _drex_votes(self, eng: DayEngine) -> None:
        for p in eng.pending:
            if "drex_p" in p:
                continue
            setup = next((c for c in reversed(eng.setups) if c["symbol"] == p["symbol"] and c["status"] == "queued"), None)
            if setup is None:
                continue
            ans = await self.agent.drex.ask(setup_state(setup, self.agent.settings.target_r), QUESTIONS)
            p["drex_p"] = ans.noul("reaches_target") if ans.ok else None

    async def reconcile(self) -> None:
        """If Zerodha shows MIS positions the desk account does not know about, pause desk entries."""
        a = self.agent
        if not (a.kite.logged_in and a.settings.mode == "live"):
            self.reconcile_note = None
            return
        try:
            broker_pos = await LiveKiteBroker(a.kite).open_positions()
        except Exception as e:
            self.reconcile_note = f"Could not read Zerodha positions: {e}"[:160]
            return
        known = {k: (d["qty"] if d["side"] == "long" else -d["qty"])
                 for k, d in ((self.states.get("desk") or {}).get("positions") or {}).items()}
        self.reconcile_note = None if broker_pos == known else f"Zerodha shows {broker_pos}, desk expects {known}"

    async def tick(self) -> None:
        dt = now_ist()
        day = dt.strftime("%Y-%m-%d")
        if self.day != day:
            self.day, self.engines, self.states, self.last_bar_ts = day, {}, {}, None
            self._restore()
            await self.reconcile()
        if market_phase(dt) != "open":
            return
        latest = self._latest_complete_bar(dt)
        if latest and (self.last_bar_ts is None or latest > self.last_bar_ts):
            self.last_bar_ts = latest
            await self.process(latest)

    async def loop(self) -> None:
        while True:
            try:
                await self.tick()
                self.error = None
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("live tick failed")
                self.error = str(e)[:200]
            await asyncio.sleep(self.agent.settings.poll_seconds)

    def start(self) -> None:
        self._task = asyncio.create_task(self.loop())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


class ReplayRunner:
    """Plays one stored day bar by bar for the desk and shadow accounts, at a chosen speed."""

    def __init__(self, agent: "Agent", day: str, speed: float):
        self.agent = agent
        self.day = day
        self.speed = speed           # bars per second, 0 = as fast as possible
        self.playing = False
        self.preparing = True
        self.error: Optional[str] = None
        self.engines: Dict[str, DayEngine] = {}
        self.timeline: List[int] = []
        self.idx = 0
        self.verdicts: Dict[str, Any] = {}
        self._task: Optional[asyncio.Task] = None

    async def prepare(self) -> None:
        a = self.agent
        s = a.settings
        try:
            feats = await asyncio.to_thread(load_features, a.store, s)
            self.verdicts = await asyncio.to_thread(a.validation.as_of, self.day, feats)
            validated = {k: v["validated"] for k, v in self.verdicts.items()}
            for acct, require in (("desk", True), ("shadow", False)):
                acc_s = Settings.from_dict({**s.to_dict(), "require_validated": require})
                self.engines[acct] = DayEngine(acc_s, self.day, f"replay:{self.day}:{acct}", s.capital, feats,
                                               s.strategies, validated, events=a.validation_events(),
                                               avoid=a.events_for(self.day))
            self.timeline = self.engines["desk"].timeline()
            self.preparing = False
            self.playing = True
        except Exception as e:
            logger.exception("replay prepare failed")
            self.error = str(e)[:200]
            self.preparing = False

    def step(self) -> None:
        if self.idx >= len(self.timeline):
            self.playing = False
            return
        ts = self.timeline[self.idx]
        for eng in self.engines.values():
            eng.on_bar(ts)
        self.idx += 1
        if self.idx >= len(self.timeline):
            for eng in self.engines.values():
                eng.finish()
            self.playing = False

    async def run(self) -> None:
        await self.prepare()
        while not self.error:
            if self.playing and self.idx < len(self.timeline):
                self.step()
                await asyncio.sleep(0 if self.speed <= 0 else 1.0 / self.speed)
            else:
                await asyncio.sleep(0.1)
            if self.idx >= len(self.timeline) and not self.playing:
                await asyncio.sleep(0.2)

    def seek(self, idx: int) -> None:
        """Seeking backwards replays from the open (engines are deterministic)."""
        idx = max(0, min(idx, len(self.timeline)))
        if idx < self.idx:
            s = self.agent.settings
            for acct, eng in self.engines.items():
                self.engines[acct] = DayEngine(eng.s, self.day, eng.session_id, s.capital, eng.feats, eng.strategies,
                                               eng.validated, events=eng.events, avoid=eng.avoid)
            self.idx = 0
        while self.idx < idx:
            self.step()

    def state(self) -> Dict[str, Any]:
        return {"day": self.day, "speed": self.speed, "playing": self.playing, "preparing": self.preparing,
                "error": self.error, "idx": self.idx, "total": len(self.timeline),
                "validated": {k: v["validated"] for k, v in self.verdicts.items()},
                "accounts": {k: e.snapshot() for k, e in self.engines.items()}}

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    def stop(self) -> None:
        if self._task:
            self._task.cancel()


class Agent:
    def __init__(self, store: Optional[Store] = None, settings: Optional[Settings] = None,
                 kite: Optional[KiteMCP] = None):
        self.store = store or Store()
        self.settings = settings or load_settings()
        self.kite = kite or KiteMCP(self.settings.kite_mcp_url)
        self.feed = Feed(self.store, self.settings, self.kite)
        self.drex = DrexClient()
        self.validation = Validation(self)
        self.live = LiveRunner(self)
        self.panic = PanicPaper(self.store, self.kite)    # forward paper test of the frozen panic-buy rule (RUNBOOK 2026-11-03)
        self.panic_loose = PanicPaper(self.store, self.kite, index_gap=-0.003, name="panic03")
        # Same picks, filled at the 09:15 opening auction (2026-11-01: the 09:20 entry forfeits the edge). Paper only, beside the frozen rule.
        from stalk.sim.panic_auction import PanicAuctionPaper
        self.panic_auction = PanicAuctionPaper(self.store, self.kite)
        self.panic_auction_loose = PanicAuctionPaper(self.store, self.kite, index_gap=-0.003, name="panic03_auction")
        self.panic_auction_error: Optional[str] = None
        self.panic_auction_loose_error: Optional[str] = None
        self.panic_error: Optional[str] = None
        self.panic_loose_error: Optional[str] = None
        self.paper_daily = PaperDaily()
        self.execution_log = ExecutionLog(self.kite)
        self.orb = ORBPaper(self.store, self.kite)
        self._background_tasks: List[asyncio.Task] = []
        self.replay: Optional[ReplayRunner] = None
        self.armed = False
        self.killed = False
        self.job: Dict[str, Any] = {"running": False, "kind": None, "message": "", "frac": 0.0, "error": None,
                                    "result": None}

    def events_for(self, day: str) -> set:
        """Stocks to skip today for board meetings or results, if that layer is on (only meetings announced before 09:15)."""
        if not self.settings.avoid_events:
            return set()
        open_ts = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST, hour=9, minute=15).timestamp())
        return self.store.event_symbols(day, open_ts)

    def validation_events(self) -> Dict[str, Any]:
        ev = self.validation.events
        return {k: v.get(STRATEGIES[k].default) for k, v in ev.items()} if ev else {}

    # ------------------------------------------------------------- jobs
    def _run_job(self, kind: str, fn) -> None:
        if self.job["running"]:
            raise ValueError("another job is running")
        self.job = {"running": True, "kind": kind, "message": "Starting", "frac": 0.0, "error": None, "result": None}

        def progress(msg: str, frac: float) -> None:
            self.job["message"], self.job["frac"] = msg, round(frac, 3)

        def work():
            try:
                self.job["result"] = fn(progress)
            except Exception as e:
                logger.exception("%s failed", kind)
                self.job["error"] = str(e)[:300]
            finally:
                self.job["running"] = False

        threading.Thread(target=work, daemon=True).start()

    def start_campaign(self, until: Optional[str] = None, open_holdout: bool = False) -> None:
        if open_holdout and not holdout_opened():
            raise ValueError("open the holdout with scripts/open_holdout.py after writing the prediction in RUNBOOK.md")

        def fn(progress):
            res = run_campaign(self.store, self.settings, until=until, open_holdout=open_holdout, progress=progress)
            return {"id": res["id"]}
        self._run_job("campaign", fn)

    def start_refresh(self) -> None:
        def fn(progress):
            progress("Downloading bars from Yahoo", 0.1)
            counts = self.feed.refresh_history()
            self.validation._cache = {}
            return {"rows": sum(counts.values())}
        self._run_job("refresh", fn)

    # ------------------------------------------------------------- settings
    def update_settings(self, patch: Dict[str, Any]) -> Settings:
        if int(patch.get("bar_minutes", 5)) == 60:
            raise ValueError("hourly bars are for backtests only; live trading uses 5 or 15 minutes")
        if "mode" in patch and patch["mode"] == "live" and self.settings.mode != "live":
            self.armed = False
        new = self.settings.patched(patch)
        self.settings = new
        self.feed.settings = new
        save_settings(new)
        self.validation._cache = {}
        return new

    # ------------------------------------------------------------- live guards
    def live_ready(self) -> Dict[str, Any]:
        """Every check must pass before a real order is sent. Never weaken these."""
        today = now_ist().strftime("%Y-%m-%d")
        verdicts = self.validation._cache and next(iter(self.validation._cache.values()))
        any_validated = bool(verdicts) and any(v["validated"] for v in verdicts.values())
        checks = {
            "mode_live": self.settings.mode == "live",
            "env_allow_live": os.getenv("ALLOW_LIVE_ORDERS", "") == "1",
            "armed": self.armed,
            "kite_logged_in": bool(self.kite.logged_in),
            "kite_can_trade": "place_order" in (self.kite.tools or []),
            "strategy_validated": any_validated,
            "static_ip_confirmed": bool(self.settings.static_ip_confirmed),
            "positions_reconciled": self.live.reconcile_note is None,
            "not_killed": not self.killed,
        }
        return {"ready": all(checks.values()), "checks": checks, "day": today}

    def arm(self, confirm: str) -> Dict[str, Any]:
        if confirm != "LIVE":
            raise ValueError("type LIVE to arm live orders")
        pre = self.live_ready()["checks"]
        missing = [k for k, v in pre.items() if not v and k != "armed"]
        if missing:
            raise ValueError("cannot arm live trading: " + ", ".join(missing))
        self.armed = True
        return self.live_ready()

    def disarm(self) -> None:
        self.armed = False

    def kill(self) -> None:
        self.killed = True
        for eng in self.live.engines.values():
            eng.gate.killed = True

    # ------------------------------------------------------------- replay
    def replay_days(self) -> List[str]:
        days = self.store.trading_days(INDEX_SYMBOL, INTERVAL)
        return [d for d in days if holdout_opened() or d < HOLDOUT_START]

    async def start_replay(self, day: str, speed: float) -> ReplayRunner:
        if day not in self.replay_days():
            raise ValueError("that day is not stored, or it is inside the unopened holdout")
        if self.replay:
            self.replay.stop()
        self.replay = ReplayRunner(self, day, speed)
        self.replay.start()
        return self.replay

    # ------------------------------------------------------------- boot
    async def boot(self) -> None:
        try:
            await self.kite.check()
        except Exception:
            pass
        self.live.start()
        if not self._background_tasks:
            self._background_tasks = [asyncio.create_task(self._panic_loop()),
                                      asyncio.create_task(self._brief_loop()),
                                      asyncio.create_task(self._review_loop()),
                                      asyncio.create_task(self._execution_loop()),
                                      asyncio.create_task(self._orb_loop()),
                                      asyncio.create_task(self._consensus_loop())]

    async def stop_background(self) -> None:
        for task in self._background_tasks:
            task.cancel()
        await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()

    async def _daily_loop(self, method: str) -> None:
        while True:
            try:
                await asyncio.to_thread(getattr(self.paper_daily, method), now_ist())
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Daily paper %s failed", method)
            await asyncio.sleep(30)

    async def _brief_loop(self) -> None:
        await self._daily_loop("brief")

    async def _review_loop(self) -> None:
        await self._daily_loop("review")

    async def _execution_loop(self) -> None:
        """Independent prospective quote recorder; no runner or live-guard calls."""
        while True:
            try:
                await self.execution_log.tick(now_ist())
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("Execution evidence capture failed")
            await asyncio.sleep(5)

    async def _orb_loop(self) -> None:
        """Independent H26 paper bars; no broker order or live-ready path."""
        while True:
            try:
                await self.orb.tick(now_ist())
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception('ORB paper tick failed')
            await asyncio.sleep(15)

    async def _consensus_loop(self) -> None:
        """Consensus desk (owner adopted 2026-11-01, scripts/consensus_desk.py): weekdays after 09:31 Opus and Astra pick blind and only
        agreed trades are paper-traded; after 15:15 they are settled. Each step runs once a day, in a subprocess, only when Kite is logged in."""
        import subprocess
        import sys
        done: Dict[str, str] = {}
        while True:
            now = now_ist()
            day, hm = now.strftime("%Y-%m-%d"), now.hour*100+now.minute
            step = "morning" if 931 <= hm < 1100 else "settle" if hm >= 1515 else None
            if now.weekday() < 5 and step and done.get(step) != day and getattr(self.kite, "logged_in", False):
                done[step] = day
                try:
                    await asyncio.to_thread(subprocess.run, [sys.executable, os.path.join(BASE_DIR, "scripts", "consensus_desk.py"), step],
                                            cwd=BASE_DIR, capture_output=True, timeout=1500)
                except Exception:
                    logger.exception("consensus desk %s failed", step)
            await asyncio.sleep(60)

    async def _panic_loop(self) -> None:
        """Once a minute on weekdays from 09:25: both panic-buy paper tests. Their network calls run in a thread so the API stays responsive."""
        while True:
            runners = [(getattr(self, name, None), field) for name, field in
                       (("panic", "panic_error"), ("panic_loose", "panic_loose_error"),
                        ("panic_auction", "panic_auction_error"), ("panic_auction_loose", "panic_auction_loose_error"))]
            for runner, error_field in [(r, f) for r, f in runners if r is not None]:
                try:
                    await asyncio.to_thread(asyncio.run, runner.tick())
                    setattr(self, error_field, None)
                except asyncio.CancelledError:
                    return
                except Exception as e:
                    logger.exception("%s paper tick failed", runner.name)
                    setattr(self, error_field, str(e)[:200])
            await asyncio.sleep(60)

    def phase(self) -> str:
        return market_phase(now_ist())
