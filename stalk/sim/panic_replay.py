"""A past week of the frozen panic-buy rule (stalk/sim/panic.py), played on the dashboard as if it were live. Paper only: no broker, no paper log.

build_week() turns stored 5-minute candles into one frame per candle. Each frame holds only what was knowable at that moment
(completed candles plus the current candle's open), a one-line insight, a short decision log, the morning's scan funnel,
open positions and the week's running numbers. WeekSim plays the frames against a simulated clock at a chosen speed and skips
quiet stretches (no position open, nothing left to decide) so a week takes minutes, not hours.
"""
from __future__ import annotations

import copy
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST
from stalk.sim import panic
from stalk.sim.price_basis import resolve

SPEEDS = (4, 60, 300, 1200)          # simulated seconds per real second
QUIET_PAUSE = 3.0                     # real seconds a quiet moment stays on screen before skipping ahead
HOLD_LOG_EVERY = 30                   # minutes between "still holding" lines, so the log stays short
_CACHE: Dict[str, Any] = {}


def stamp(day: str, hm: int) -> int:
    return int(datetime.strptime(f"{day} {hm:04d}", "%Y-%m-%d %H%M").replace(tzinfo=IST).timestamp())


def pct(x: float, digits: int = 2) -> str:
    """+0.10% / −0.97%, with a real minus sign."""
    return f"{x:+.{digits}%}".replace("-", "−")


def money(x: float) -> str:
    """+₹18.11 / −₹110.57"""
    return f"{'+' if x >= 0 else '−'}₹{abs(x):,.2f}"


def day_label(day: str) -> str:
    return datetime.strptime(day, "%Y-%m-%d").strftime("%a %d %b").replace(" 0", " ")


def load_cache():
    """5-minute candles for NIFTY and the wider universe (data/wide_5m.pkl), grouped by day, plus results meetings. Loaded once."""
    if "grouped" not in _CACHE:
        root = Path(BASE_DIR) / "data"
        frames = pd.read_pickle(root / "wide_5m.pkl")
        meta = pd.read_csv(root / "wide_meta.csv")
        tiers = {s: t for s, t in zip(meta.symbol, meta["index"]) if s not in DEFAULT_UNIVERSE}
        grouped = {}
        for symbol in ["NIFTY 50", *tiers]:
            if symbol not in frames:
                continue
            frame = frames[symbol]
            dates = pd.to_datetime(frame.ts, unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
            grouped[symbol] = {day: g.sort_values("ts")[["ts", "o", "h", "l", "c", "v"]].values.tolist() for day, g in frame.groupby(dates)}
        events = pd.read_pickle(root / "wide_events.pkl")
        results = events[(events.purpose.fillna("") + " " + events.detail.fillna("")).str.contains("result", case=False)]
        _CACHE.update(grouped=grouped, tiers=tiers, results=results)
    return _CACHE["grouped"], _CACHE["tiers"], _CACHE["results"]


def weeks_available(grouped) -> List[str]:
    """Mondays whose week has at least 3 stored sessions and a stored session before it."""
    days = sorted(grouped["NIFTY 50"])
    out = []
    for d in days[1:]:
        dt = datetime.strptime(d, "%Y-%m-%d")
        monday = (dt - timedelta(days=dt.weekday())).strftime("%Y-%m-%d")
        week = [x for x in days if monday <= x <= (dt - timedelta(days=dt.weekday()) + timedelta(days=4)).strftime("%Y-%m-%d")]
        if monday not in out and len(week) >= 3 and days.index(week[0]) > 0:
            out.append(monday)
    return out


def build_week(grouped, tiers, results, monday: str, index_gap: Optional[float] = None) -> List[Dict[str, Any]]:
    days_all = sorted(grouped["NIFTY 50"])
    friday = (datetime.strptime(monday, "%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d")
    week = [d for d in days_all if monday <= d <= friday]
    if not week or days_all.index(week[0]) == 0:
        raise ValueError("No stored sessions for that week")
    frames: List[Dict[str, Any]] = []
    log: List[Dict[str, str]] = []
    closed: List[Dict[str, Any]] = []
    curve: List[List[float]] = []
    day_status = {d: {"day": d, "label": day_label(d), "state": "pending", "net": 0.0, "trades": 0} for d in week}

    for day in week:
        today = grouped["NIFTY 50"][day]
        prev_day = days_all[days_all.index(day) - 1]
        if int(today[0][0]) != stamp(day, 915):
            raise ValueError(f"Missing NIFTY's opening candle on {day}")
        n_open, n_prev = today[0][1], grouped["NIFTY 50"][prev_day][-1][4]
        n_gap = n_open / n_prev - 1
        threshold = panic.INDEX_GAP if index_gap is None else index_gap
        qualifies = panic.market_qualifies(n_open, n_prev, threshold)
        positions: Dict[str, Dict[str, Any]] = {}
        funnel: Optional[Dict[str, int]] = None
        top: List[Dict[str, Any]] = []
        insight = {"headline": "", "detail": ""}
        decided, last_hold_log = False, {}
        start, end = stamp(day, 915), stamp(day, panic.EXIT_HM)
        realized_before = sum(t["net"] for t in closed)

        def say(clock: str, kind: str, text: str) -> None:
            log.append({"day": day, "when": f"{day_label(day)[:3]} {clock}", "kind": kind, "text": text})

        for ts in range(start, end + 1, 300):
            clock = datetime.fromtimestamp(ts, IST).strftime("%H:%M")
            hm = int(clock.replace(":", ""))
            if hm == 915:
                if qualifies:
                    day_status[day]["state"] = "watch"
                    insight = {"headline": "Panic morning. Waiting for the first 5-minute candle before picking.",
                               "detail": f"NIFTY opened {pct(n_gap)} (the rule needs below {pct(threshold)}). Ranking starts at 09:20."}
                    say(clock, "check", f"NIFTY opened {pct(n_gap)}: a panic morning. Will scan at 09:20.")
                else:
                    day_status[day]["state"] = "skip"
                    decided = True
                    insight = {"headline": "Not a panic morning. No trades today.",
                               "detail": f"NIFTY opened {pct(n_gap)}; the rule only buys when it opens below {pct(threshold)}. Standing aside is part of the plan."}
                    say(clock, "skip", f"NIFTY opened {pct(n_gap)}. Not below {pct(threshold)}, so no trades today.")

            if hm == 920 and qualifies:
                d = datetime.strptime(day, "%Y-%m-%d")
                near = set(results.loc[results.day.between((d - timedelta(days=3)).strftime("%Y-%m-%d"),
                                                           (d + timedelta(days=3)).strftime("%Y-%m-%d")), "symbol"])
                reviewed, fell, with_results, locked, eligible = 0, [], 0, 0, []
                price_mismatches, fallback, sources = 0, 0, {}
                for symbol, tier in tiers.items():
                    rows, prev = grouped.get(symbol, {}).get(day, []), grouped.get(symbol, {}).get(prev_day, [])
                    if not rows or int(rows[0][0]) != start:
                        continue
                    reviewed += 1
                    basis = resolve(day, symbol, rows[0][1], prev[-1][4] if prev else None)
                    sources[symbol] = basis["source"]
                    if basis["source"] == "yahoo_fallback":
                        fallback += 1
                        say(clock, "data", f"{symbol}: Yahoo fallback; official OPEN/PREVCLOSE missing.")
                    if basis["mismatch"]:
                        price_mismatches += 1
                        say(clock, "skip", f"{symbol}: price data mismatch ({'; '.join(basis['details'])}).")
                        continue
                    c = panic.Candidate(symbol, basis["prev_close"], basis["open"], tier, symbol in near, rows[0][2] == rows[0][3])
                    if c.gap >= panic.STOCK_GAP:
                        continue
                    fell.append(c)
                    if c.results_near:
                        with_results += 1
                    elif c.locked:
                        locked += 1
                    else:
                        eligible.append(c)
                picks = panic.pick(eligible)
                chosen = {c.symbol for c in picks}
                for c in sorted(fell, key=lambda x: x.gap)[:8]:
                    why = ("Picked: one of the 2 biggest clean falls" if c.symbol in chosen else "Results within 3 days" if c.results_near
                           else "Stuck at its lower limit" if c.locked else "Smaller fall than the 2 picked")
                    top.append({"symbol": c.symbol, "gap": round(c.gap, 4), "tier": c.tier, "picked": c.symbol in chosen, "why": why,
                                "gap_source": sources[c.symbol]})
                funnel = {"reviewed": reviewed, "fell": len(fell), "results": with_results, "locked": locked, "eligible": len(eligible), "picked": len(picks),
                          "price_mismatches": price_mismatches, "yahoo_fallback": fallback}
                say(clock, "scan", f"Scanned {reviewed} stocks: {len(fell)} fell over 2%, {with_results} had results, {locked} locked, {len(eligible)} clean.")
                for c in picks:
                    rows = grouped[c.symbol][day]
                    if not set(range(stamp(day, 920), end + 1, 300)).issubset({int(b[0]) for b in rows}):
                        say(clock, "skip", f"{c.symbol}: candles missing later in the day, so no simulated fill.")
                        continue
                    raw = next(b for b in rows if int(b[0]) == ts)[1]
                    qty = panic.shares(raw)
                    if not qty:
                        say(clock, "skip", f"{c.symbol}: one share costs more than ₹5,000.")
                        continue
                    entry = round(raw * (1 + panic.SLIP.get(c.tier, 0.0006)), 2)
                    positions[c.symbol] = {"candidate": c, "raw": raw, "qty": qty, "entry": entry, "stop": round(raw * (1 - panic.STOP), 2), "since": clock,
                                           "gap_source": sources[c.symbol]}
                    say(clock, "buy", f"Bought {qty} {c.symbol} at ₹{entry:,.2f} (opened {pct(c.gap, 1)}). Stop ₹{raw * (1 - panic.STOP):,.2f}, sell by 15:10.")
                decided = True
                if positions:
                    day_status[day]["state"] = "trade"
                    names = " and ".join(positions)
                    insight = {"headline": f"Bought {names}. Holding until the 3% stop or 15:10.",
                               "detail": f"Reviewed {reviewed} stocks: {len(fell)} fell over 2%, {with_results} had results news, {locked} were locked. "
                                         f"Picked the {len(positions)} biggest clean falls."}
                else:
                    day_status[day]["state"] = "skip"
                    insight = {"headline": "Panic morning, but nothing clean to buy.",
                               "detail": f"Reviewed {reviewed} stocks; {len(fell)} fell over 2%, none passed the results and lock checks."}

            # Positions: a stop only counts once its candle has completed; the current candle shows only its open
            for symbol, p in list(positions.items()):
                rows = grouped[symbol][day]
                completed = [b for b in rows if int(b[0]) + 300 <= ts and int(b[0]) >= stamp(day, 920)]
                current = next((b for b in rows if int(b[0]) == ts), None)
                visible = completed + ([[current[0], current[1], current[1], current[1], current[1], 0]] if current else [])
                stopped = any(b[3] <= p["stop"] for b in completed if int(b[0]) < end)
                if stopped or hm == panic.EXIT_HM:
                    hms = [int(datetime.fromtimestamp(b[0], IST).strftime("%H%M")) for b in visible]
                    trade = panic.as_dict(panic.walk(day, p["candidate"], visible, hms))
                    trade["clock"] = clock
                    trade["stop"] = p["stop"]
                    trade["gap_source"] = p["gap_source"]
                    closed.append(trade)
                    del positions[symbol]
                    if stopped:
                        say(clock, "stop", f"Sold {symbol}: the 3% stop was hit. Net {money(trade['net'])}.")
                    else:
                        say(clock, "sell", f"Sold {symbol} at 15:10 as the rule says. Net {money(trade['net'])}.")
                else:
                    p["last"] = current[1] if current else completed[-1][4]
            # one "still holding" line for all open positions every HOLD_LOG_EVERY minutes, so the log stays short
            if positions and qualifies and hm > 920 and ((hm // 100 * 60 + hm % 100) - 560) % HOLD_LOG_EVERY == 0 and last_hold_log.get("all") != hm:
                last_hold_log["all"] = hm
                parts = [f"{s_} {'+' if (q['last'] - q['entry']) >= 0 else '−'}₹{abs((q['last'] - q['entry']) * q['qty']):,.0f}" for s_, q in positions.items()]
                say(clock, "hold", f"Holding {', '.join(parts)}. No stop hit; nothing to do.")

            if decided and qualifies and hm > 920:
                open_names = ", ".join(positions)
                if positions:
                    pnl = sum((p["last"] - p["entry"]) * p["qty"] for p in positions.values())
                    insight = {"headline": f"Holding {open_names}. {'Up' if pnl >= 0 else 'Down'} ₹{abs(pnl):,.0f} on open trades.",
                               "detail": "No action needed: the plan is to hold to 15:10 unless a 3% stop is hit. No adding, no early selling."}
                elif any(t["day"] == day for t in closed) and hm < panic.EXIT_HM:
                    insight = {"headline": "Both trades closed early. Nothing more to do today.", "detail": "Stops did their job; the rule doesn't re-enter."}

            day_trades = [t for t in closed if t["day"] == day]
            if hm == panic.EXIT_HM:
                net = round(sum(t["net"] for t in day_trades), 2)
                day_status[day].update(state="done" if day_trades else "skip", net=net, trades=len(day_trades))
                if day_trades:
                    say(clock, "day", f"{day_label(day)} done: {len(day_trades)} trades, net {money(net)}. All positions flat.")
                    insight = {"headline": f"Day done: net {money(net)} after charges.",
                               "detail": "Everything is sold before 15:15, so there is no overnight risk and no auto square-off fee."}
            elif day_status[day]["state"] in ("trade",) and day_trades:
                day_status[day]["net"] = round(sum(t["net"] for t in day_trades), 2)

            opened = [{"symbol": s, "qty": p["qty"], "entry": p["entry"], "stop": p["stop"], "last": p.get("last", p["raw"]), "since": p["since"],
                       "pnl": round((p.get("last", p["raw"]) - p["entry"]) * p["qty"], 2)} for s, p in positions.items()]
            realized = round(sum(t["net"] for t in closed), 2)
            unrealized = round(sum(p["pnl"] for p in opened), 2)
            quiet = decided and not positions and hm < panic.EXIT_HM
            if qualifies and not quiet:                     # the chart shows panic mornings only, while something is open or still to decide
                curve.append([ts, round(realized + unrealized, 2)])
            frames.append(copy.deepcopy({
                "day": day, "ts": ts, "clock": clock, "label": day_label(day), "nifty_gap": n_gap, "qualifies": qualifies, "quiet": quiet,
                "insight": insight, "log": log[-40:], "funnel": funnel, "top": top, "positions": opened, "trades": closed,
                "realized": realized, "unrealized": unrealized, "total": round(realized + unrealized, 2),
                "charges": round(sum(t["costs"] for t in closed), 2), "won": sum(t["net"] > 0 for t in closed), "lost": sum(t["net"] <= 0 for t in closed),
                "days": list(day_status.values()), "curve": curve[-450:], "day_net": round(realized + unrealized - realized_before, 2)}))
    return frames


class WeekSim:
    """Plays frames against a simulated clock. Quiet stretches and nights are skipped after a short pause."""

    def __init__(self, frames: List[Dict[str, Any]], week: str, pick: Optional[Dict[str, Any]] = None):
        self.frames, self.week, self.pick = frames, week, pick or {}
        self.idx, self.speed, self.playing = 0, 300, False
        self.sim_ts = float(frames[0]["ts"])
        self.last = time.monotonic()
        self.hold_since: Optional[float] = None       # when the current quiet moment reached the screen
        self.lock = threading.Lock()

    def _next_day_start(self) -> Optional[int]:
        day = self.frames[self.idx]["day"]
        return next((i for i in range(self.idx + 1, len(self.frames)) if self.frames[i]["day"] != day), None)

    def _advance(self) -> None:
        now = time.monotonic()
        dt, self.last = now - self.last, now
        if not self.playing:
            return
        f = self.frames[self.idx]
        if f["quiet"] or f["clock"] == f"{panic.EXIT_HM // 100}:{panic.EXIT_HM % 100:02d}" or self.idx == len(self.frames) - 1:
            if self.hold_since is None:
                self.hold_since = now - dt                 # it was reached at the previous update, not now
            if now - self.hold_since >= QUIET_PAUSE:
                nxt = self._next_day_start()
                self.hold_since = None
                if nxt is None:
                    self.playing = False
                else:
                    self.idx, self.sim_ts = nxt, float(self.frames[nxt]["ts"])
            return
        self.sim_ts += dt * self.speed
        while self.idx + 1 < len(self.frames) and self.frames[self.idx + 1]["day"] == f["day"] and self.frames[self.idx + 1]["ts"] <= self.sim_ts:
            self.idx += 1
            if self.frames[self.idx]["quiet"]:
                self.sim_ts = float(self.frames[self.idx]["ts"])
                break

    def control(self, action: str, value: Any = None) -> None:
        with self.lock:
            self._advance()
            if action == "play":
                if self._next_day_start() is None and (self.frames[self.idx]["quiet"] or self.idx == len(self.frames) - 1):
                    self.idx, self.sim_ts = 0, float(self.frames[0]["ts"])
                self.playing = True
            elif action == "pause":
                self.playing = False
            elif action == "speed" and value is not None and int(value) in SPEEDS:
                self.speed = int(value)
            elif action == "day" and value is not None:
                i = next((k for k, f in enumerate(self.frames) if f["day"] == value), None)
                if i is None:
                    raise ValueError("That day isn't in this week")
                self.idx, self.sim_ts, self.hold_since = i, float(self.frames[i]["ts"]), None
            elif action == "restart":
                self.idx, self.sim_ts, self.playing, self.hold_since = 0, float(self.frames[0]["ts"]), False, None
            else:
                raise ValueError("Unknown simulation control")
            self.last = time.monotonic()

    def bars(self, symbol: str = "") -> Dict[str, Any]:
        """One atomic frame and its visible candles; current candle exposes only its open."""
        grouped, _, _ = load_cache()
        with self.lock:
            self._advance()
            f = self.frames[self.idx]
            trades = [t for t in f["trades"] if t["day"] == f["day"]]
            picks = list(dict.fromkeys(p["symbol"] for p in [*f["positions"], *trades]))
            if symbol and symbol not in grouped:
                raise ValueError("No cached candles for that symbol")
            symbol = symbol or (picks[0] if picks else "NIFTY 50")
            rows = grouped.get(symbol, {}).get(f["day"], [])
            visible = [list(b) if b[0] + 300 <= f["ts"] else [b[0], b[1], b[1], b[1], b[1], 0]
                       for b in rows if b[0] <= f["ts"]]
            trade = next((p for p in [*f["positions"], *trades] if p["symbol"] == symbol), None)
            levels, markers = [], []
            if trade:
                levels = [{"y": trade["entry"], "kind": "entry"}, {"y": trade["stop"], "kind": "stop"}]
                markers = [{"kind": "entry", "ts": stamp(f["day"], panic.ENTRY_HM), "price": trade["entry"], "side": "long"}]
                if "exit" in trade:
                    markers.append({"kind": "exit", "ts": stamp(f["day"], int(trade["clock"].replace(":", ""))),
                                    "price": trade["exit"], "side": "long"})
            note = ("Entry and stop shown; buy and sell markers appear as they happen." if picks else
                    f"No trade today: NIFTY opened {pct(f['nifty_gap'])}" if not f["qualifies"] else
                    "NIFTY candles while waiting for a clean trade.")
            return {"week": self.week, "day": f["day"], "ts": f["ts"], "idx": self.idx, "symbol": symbol,
                    "picks": picks, "bars": visible, "levels": levels, "markers": markers, "note": note,
                    "entry": trade["entry"] if trade else None, "stop": trade["stop"] if trade else None,
                    "exit": trade.get("exit") if trade else None}

    def state(self) -> Dict[str, Any]:
        with self.lock:
            self._advance()
            f = copy.deepcopy(self.frames[self.idx])
            sim_ts = max(self.sim_ts, f["ts"])
            return {**f, "sim_ts": sim_ts, "sim_clock": datetime.fromtimestamp(sim_ts, IST).strftime("%H:%M:%S"),
                    "idx": self.idx, "last_idx": len(self.frames) - 1, "playing": self.playing, "speed": self.speed, "speeds": list(SPEEDS),
                    "holding_screen": self.hold_since is not None, "week": self.week, "pick": self.pick, "capital": 10000.0,
                    "finished": not self.playing and self._next_day_start() is None and (f["quiet"] or self.idx == len(self.frames) - 1)}


def save_to_history(store, frames: List[Dict[str, Any]], week: str, label_suffix: str = "", prefix: str = "w") -> str:
    """Store a finished week as a History run ("Panic buy, week of …"). Re-watching the same week overwrites it."""
    last = frames[-1]
    cid = f"{prefix}{week.replace('-', '')}"
    acct = f"{cid}:panic_buy"
    # Replacing a research run must remove picks that disappeared after a data repair.
    store.delete_sessions(acct)
    equity, days, curve, peak, max_dd = 10000.0, [], [], 10000.0, 0.0
    for d in last["days"]:
        trades = [t for t in last["trades"] if t["day"] == d["day"]]
        start = equity
        equity += sum(t["net"] for t in trades)
        peak, max_dd = max(peak, equity), max(max_dd, peak - equity)
        row = {"day": d["day"], "start_equity": round(start, 2), "end_equity": round(equity, 2), "gross": round(sum(t["gross"] for t in trades), 2),
               "costs": round(sum(t["costs"] for t in trades), 2), "net": round(sum(t["net"] for t in trades), 2), "trades": len(trades),
               "wins": sum(t["net"] > 0 for t in trades), "losses": sum(t["net"] <= 0 for t in trades)}
        days.append(row)
        curve.append([d["day"], round(equity, 2)])
        store.save_session(f"{acct}:{d['day']}", "panic_buy", d["day"], acct, {}, row, ended=True)
        for t in trades:
            exit_hm = int(t.get("clock", "15:10").replace(":", ""))
            store.save_trade({"id": f"{acct}:{t['day']}:{t['symbol']}", "session_id": f"{acct}:{t['day']}", "day": t["day"], "strategy": "panic_buy",
                              "symbol": t["symbol"], "side": "long", "qty": t["qty"], "entry_ts": stamp(t["day"], panic.ENTRY_HM), "entry_price": t["entry"],
                              "exit_ts": stamp(t["day"], exit_hm), "exit_price": t["exit"], "stop_initial": round(t["entry"] * (1 - panic.STOP), 2),
                              "gross": t["gross"], "costs": t["costs"], "net": t["net"], "reason": "stop" if t["reason"] == "stop" else "flatten",
                              "fee_version": "zerodha-2026", "broker": "simulation"})
    nets = [t["net"] for t in last["trades"]]
    account = {"name": "panic_buy", "strategies": ["panic_buy"], "require_validated": False, "start": 10000.0, "end": round(equity, 2),
               "gross": round(sum(t["gross"] for t in last["trades"]), 2), "costs": round(sum(t["costs"] for t in last["trades"]), 2),
               "net": round(sum(nets), 2), "tax": 0.0, "after_tax": round(sum(nets), 2), "trades": len(nets),
               "green_days": sum(d["net"] > 0 for d in days), "red_days": sum(d["net"] < 0 for d in days), "flat_days": sum(d["net"] == 0 for d in days),
               "max_dd": round(max_dd, 2), "stats": {"n": len(nets), "win": round(sum(n > 0 for n in nets) / len(nets), 3) if nets else 0.0, "net": round(sum(nets), 2)},
               "per_strategy": {}, "days": days, "curve": curve}
    label = f"Panic buy{label_suffix}, week of {day_label(week)[4:]}"
    summary = {"id": cid, "first_day": days[0]["day"], "last_day": days[-1]["day"], "days": len(days), "capital": 10000.0,
               "accounts": {"panic_buy": account}, "weeks": [], "kind": "panic_buy", "label": label,
               "note": "Watched on the Simulation page: 5-minute candles, entries at 09:20, 3% stop, sold by 15:10"}
    store.save_campaign(cid, days[0]["day"], days[-1]["day"], {"bar_minutes": 5, "label": label}, summary)
    return cid
