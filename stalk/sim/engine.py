"""One trading day for one paper account, bar by bar. Shared by the campaign, replay and live paper.

Order of work on each completed bar:
  1. fill entries queued on the previous bar at this bar's open,
  2. step open trades through this bar (stop, target, breakeven, flatten),
  3. daily loss limit,
  4. read strategy signals at this bar's close, run each through the risk gate, add Time Machine
     odds, and queue the best passing one for the next bar's open.
This is the same order research uses (`research.trade.walk`), so a backtest and a replay agree.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from stalk.config import IST, SECTORS, Settings, hhmm_to_min
from stalk.market.costs import FEE_SCHEDULE_VERSION
from stalk.research import timemachine
from stalk.research.news import news_view
from stalk.research.strategies import STRATEGIES, entry_mask, first_per_day
from stalk.research.trade import Trade, size, stop_distance
from stalk.sim import risk

MAX_SETUPS = 200  # a whole day of signals, so the UI can group every skip


def clock(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def signal_table(feats: Dict[str, pd.DataFrame], keys: List[str], s: Settings) -> Dict[str, Dict[str, np.ndarray]]:
    """First in-window signal per side per day, for every symbol and strategy (same filter as research)."""
    es, ee = hhmm_to_min(s.entry_start), hhmm_to_min(s.entry_end)
    ls, le = hhmm_to_min(s.lunch_start), hhmm_to_min(s.lunch_end)
    out = {}
    for sym, f in feats.items():
        mask = entry_mask(f, es, ee, ls, le)
        out[sym] = {k: first_per_day(f, STRATEGIES[k].signals(f) * mask) for k in keys}
    return out


class DayEngine:
    def __init__(self, s: Settings, day: str, session_id: str, equity: float,
                 feats: Dict[str, pd.DataFrame], strategies: List[str], validated: Dict[str, bool],
                 events: Optional[Dict[str, pd.DataFrame]] = None, use_tm: bool = True,
                 on_log: Optional[Callable[[int, str, str], None]] = None, broker_name: str = "paper",
                 sigs: Optional[Dict[str, Dict[str, np.ndarray]]] = None,
                 priority: Optional[Dict[str, float]] = None, avoid: Optional[set] = None,
                 vetter: Optional[Callable[[Dict[str, Any], int], Dict[str, Any]]] = None,
                 news: Optional[Dict[str, List[Any]]] = None):
        self.s = s
        self.day = day
        self.session_id = session_id
        self.start_equity = equity
        self.feats = feats
        self.strategies = [k for k in strategies if k in STRATEGIES]
        self.validated = validated
        self.events = events or {}
        self.use_tm = use_tm
        self.on_log = on_log
        self.broker_name = broker_name
        self.priority = priority or {}
        self.vetter = vetter                    # e.g. the Drex checklist; it can only block
        self.vetoed: List[Dict[str, Any]] = []
        self.news = news or {}
        self.avoid = set(avoid or ())          # stocks with a board meeting around today (events layer)
        self._ctx: Dict[str, Any] = {}          # this bar's market context: breadth and sector moves
        self.mood: Dict[str, Any] = {"label": "unclear", "index": None, "breadth": None}
        self.last_entry_min: Optional[int] = None
        self.mood_bars: Dict[str, int] = {"bullish": 0, "bearish": 0, "unclear": 0}

        self.positions: Dict[str, Trade] = {}
        self.pending: List[Dict[str, Any]] = []
        self.closed: List[Dict[str, Any]] = []
        self.setups: List[Dict[str, Any]] = []
        self.logs: List[Dict[str, Any]] = []
        self.cooldown: Dict[str, int] = {}
        self.gate = risk.GateState()
        self.realized = 0.0
        self.costs = 0.0
        self.last_ts: Optional[int] = None
        self.marks: Dict[str, float] = {}
        self.equity_curve: List[List[float]] = []
        self.events_out: List[Dict[str, Any]] = []   # entries and exits for a live mirror

        any_f = next(iter(feats.values()), None)
        self._bar_s = int((any_f["end_minute"].iat[0] - any_f["minute"].iat[0]) * 60) if any_f is not None and len(any_f) else 300
        # Per symbol: row index of each bar today, and each strategy's first in-window signal per side.
        self._rows: Dict[str, Dict[int, int]] = {}
        self._sig: Dict[str, Dict[str, np.ndarray]] = {}
        for sym, f in feats.items():
            today = np.flatnonzero(f["day"].to_numpy() == day)
            if len(today) == 0:
                continue
            ts = f["ts"].to_numpy()
            self._rows[sym] = {int(ts[i]): int(i) for i in today}
            self._sig[sym] = sigs[sym] if sigs is not None and sym in sigs else signal_table({sym: f}, self.strategies, s)[sym]

    # ------------------------------------------------------------- helpers
    def log(self, ts: int, kind: str, text: str) -> None:
        self.logs.append({"ts": ts, "kind": kind, "text": text})
        if self.on_log:
            self.on_log(ts, kind, text)

    @property
    def equity(self) -> float:
        return self.start_equity + self.realized

    def open_pnl(self) -> float:
        out = 0.0
        for sym, t in self.positions.items():
            px = self.marks.get(sym, t.entry)
            out += t.sign * (px - t.entry) * t.qty
        return out

    def day_pnl(self) -> float:
        return self.realized + self.open_pnl()

    def _bar(self, sym: str, ts: int):
        i = self._rows.get(sym, {}).get(ts)
        if i is None:
            return None, None
        f = self.feats[sym]
        row = f.iloc[i]
        return (int(row["ts"]), float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"]),
                int(row["end_minute"])), i

    # ------------------------------------------------------------- the bar
    def timeline(self) -> List[int]:
        return sorted({ts for rows in self._rows.values() for ts in rows})

    def on_bar(self, ts: int) -> None:
        self.last_ts = ts
        bars = {}
        for sym in self._rows:
            b, i = self._bar(sym, ts)
            if b is not None:
                bars[sym] = (b, i)
                self.marks[sym] = b[4]

        # 1. queued entries fill at this bar's open
        for p in list(self.pending):
            self.pending.remove(p)
            sym = p["symbol"]
            if sym not in bars or sym in self.positions:
                continue
            b = bars[sym][0]
            qty = size(b[1], p["dist"], self.s, self.equity)
            if qty < 1:
                self.log(ts, "skip", f"{sym}: no size at the open")
                continue
            t = Trade.open(sym, p["side"], p["strategy"], b[1], ts, p["dist"], qty, self.s)
            t.__dict__["meta"] = p
            self.positions[sym] = t
            self.gate.trades_today += 1
            dt_ = datetime.fromtimestamp(ts, IST)
            self.last_entry_min = dt_.hour * 60 + dt_.minute
            self.events_out.append({"type": "entry", "symbol": sym, "side": t.side, "qty": qty, "stop": t.stop})
            self.log(ts, "entry", f"{'Bought' if t.side == 'long' else 'Sold'} {qty} {sym} at ₹{t.entry:,.2f} · "
                                  f"stop ₹{t.stop:,.2f} · target ₹{t.target:,.2f} · {STRATEGIES[t.strategy].name}")

        # 2. step open trades
        for sym, t in list(self.positions.items()):
            if sym in bars and t.step(bars[sym][0], self.s):
                self._close(t)

        # 3. daily loss limit
        if not self.gate.halted and self.day_pnl() <= -self.s.loss_halt:
            self.gate.halted = True
            for sym, t in list(self.positions.items()):
                t.force_exit(ts, self.marks.get(sym, t.entry), "loss_halt", self.s)
                self._close(t)
            self.log(ts, "halt", f"Daily loss limit ₹{self.s.loss_halt:,.0f} reached; done for the day")

        # 4. new signals, judged against this bar's market context
        above = [float(self.feats[s_]["c"].iat[i_]) > float(self.feats[s_]["vwap"].iat[i_]) for s_, (_, i_) in bars.items()
                 if self.feats[s_]["vwap"].iat[i_] == self.feats[s_]["vwap"].iat[i_]]
        sector_moves: Dict[str, List[float]] = {}
        for s_, (_, i_) in bars.items():
            sector_moves.setdefault(SECTORS.get(s_, s_), []).append(float(self.feats[s_]["rday"].iat[i_]))
        self._ctx = {"breadth": (sum(above) / len(above)) if above else 0.5, "sectors": sector_moves, "bars": bars}
        idx_now = next((float(self.feats[s_]["idx_rday"].iat[i_]) for s_, (_, i_) in bars.items()
                        if "idx_rday" in self.feats[s_] and self.feats[s_]["idx_rday"].iat[i_] == self.feats[s_]["idx_rday"].iat[i_]), None)
        br = self._ctx["breadth"]
        chop = next((float(self.feats[s_]["idx_cross4"].iat[i_]) for s_, (_, i_) in bars.items()
                     if "idx_cross4" in self.feats[s_] and self.feats[s_]["idx_cross4"].iat[i_] == self.feats[s_]["idx_cross4"].iat[i_]), None)
        label = "unclear"
        if idx_now is not None and idx_now >= 0.001 and br >= 0.55:
            label = "bullish"
        elif idx_now is not None and idx_now <= -0.001 and br <= 0.45:
            label = "bearish"
        if self.s.mood_chop and chop is not None and chop >= 2:
            label = "unclear"
        self.mood = {"label": label, "index": idx_now, "breadth": br, "ts": ts, "crossings": chop}
        self.mood_bars[label] += 1
        cands = []
        for sym, (b, i) in bars.items():
            for k in self.strategies:
                sig = int(self._sig[sym][k][i])
                if sig:
                    cands.append(self._evaluate(sym, k, sig, b, i))
        passing = [c for c in cands if c["verdict"]["ok"]]
        free = self.s.max_positions - len(self.positions) - len(self.pending)
        if self.s.rank_by_score:
            passing.sort(key=lambda c: (-c.get("score", 0), -c.get("rvol", 0.0)))
        else:
            passing.sort(key=lambda c: (-self.priority.get(c["strategy"], 0.0), -(c["tm"]["exp_r"] if c.get("tm") else -9)))
        if self.vetter is not None and free > 0:
            kept = []
            for c in passing:
                if len(kept) >= free:
                    break
                v = self.vetter(c, c.pop("_i", -1))
                c["drex"] = v.get("answers")
                if v.get("veto"):
                    c["verdict"] = {"ok": False, "reason": v["reason"], "code": "drex"}
                    c["status"] = "blocked"
                    self.vetoed.append(c)
                else:
                    kept.append(c)
            passing = kept + [c for c in passing if c not in kept and c.get("status") != "blocked"]
        for c in passing[:max(0, free)]:
            c["status"] = "queued"
            self.pending.append({"symbol": c["symbol"], "side": c["side"], "strategy": c["strategy"],
                                 "dist": c["dist"], "tm": c.get("tm"), "signal_ts": ts,
                                 "drex_p": (c.get("drex") or {}).get("quality")})
        for c in passing[max(0, free):]:
            c["status"] = "blocked"
            c["verdict"] = {"ok": False, "reason": "Another setup ranked higher this bar", "code": "ranked"}
        for c in cands:
            c.pop("_i", None)
            c.setdefault("status", "blocked")
            self.setups.append(c)
        self.setups = self.setups[-MAX_SETUPS:]
        self.equity_curve.append([ts, round(self.day_pnl(), 2)])

    def _evaluate(self, sym: str, key: str, sig: int, b, i: int) -> Dict[str, Any]:
        f = self.feats[sym]
        atr = float(f["atr"].iat[i])
        adr = float(f["adr"].iat[i]) if "adr" in f else float("nan")
        side = "long" if sig > 0 else "short"
        entry = b[4]
        dist = stop_distance(atr, adr, self.s)
        qty = size(entry, dist, self.s, self.equity)
        self.gate.open_positions = len(self.positions) + len(self.pending)
        v = risk.check(self.s, self.gate, minute_end=b[5], symbol_cooldown_until=self.cooldown.get(sym),
                       qty=qty, entry=entry, dist=dist, validated=self.validated.get(key, False),
                       holding_symbol=sym in self.positions)
        sign = 1 if side == "long" else -1
        idx = float(f["idx_rday"].iat[i]) if "idx_rday" in f else float("nan")
        breadth = self._ctx.get("breadth", 0.5)
        peers = [x for x in self._ctx.get("sectors", {}).get(SECTORS.get(sym, sym), []) if x == x]
        own = float(f["rday"].iat[i])
        peers_wo = list(peers)
        if own in peers_wo:
            peers_wo.remove(own)
        sector = (sum(peers_wo) / len(peers_wo)) if peers_wo else float("nan")
        agree = {"index": idx == idx and idx * sign > 0, "breadth": (breadth - 0.5) * sign > 0,
                 "sector": sector == sector and sector * sign > 0}
        net_news, heads = news_view(self.news.get(sym), b[0] + self._bar_s)
        if self.s.news_filter:
            agree["news"] = net_news * sign > 0
        if v.ok and self.s.with_index and idx == idx and idx * sign < 0:
            v = risk.Verdict(False, "Against NIFTY's direction today", "against_index")
        m = self.mood
        mood_txt = (f"NIFTY {m['index'] * 100:+.2f}%, {m['breadth']:.0%} of stocks above VWAP" if m.get("index") is not None else "no index data")
        if self.s.mood_chop and (m.get("crossings") or 0) >= 2:
            mood_txt += f", choppy: NIFTY crossed its average {int(m['crossings'])} times in the last hour"
        if v.ok and self.s.mood_gate and m["label"] == "unclear":
            v = risk.Verdict(False, f"Market mood unclear ({mood_txt})", "mood")
        elif v.ok and self.s.mood_gate and (m["label"] == "bullish") != (side == "long"):
            v = risk.Verdict(False, f"Market mood is {m['label']} ({mood_txt})", "mood")
        agreeing = sum(agree[k] for k in ("index", "breadth", "sector"))
        if v.ok and self.s.min_score and agreeing < self.s.min_score:
            v = risk.Verdict(False, f"Only {agreeing} of 3 factors agree (index, breadth, sector)", "confidence")
        if v.ok and self.s.min_gap_min and self.last_entry_min is not None and b[5] < self.last_entry_min + self.s.min_gap_min:
            nxt = self.last_entry_min + self.s.min_gap_min
            v = risk.Verdict(False, f"Next trade allowed after {nxt // 60:02d}:{nxt % 60:02d}", "spacing")
        if v.ok and self.s.news_filter and net_news * sign < 0:
            v = risk.Verdict(False, f"News against the trade: {heads[-1][:80] if heads else 'net negative'}", "news")
        if v.ok and self.s.avoid_events and sym in self.avoid:
            v = risk.Verdict(False, "Board meeting or results around today", "event")
        if v.ok and self.s.breadth_filter and not agree["breadth"]:
            v = risk.Verdict(False, f"Market breadth against the trade ({breadth:.0%} of stocks above VWAP)", "breadth")
        if v.ok and self.s.sector_filter and sector == sector and not agree["sector"]:
            v = risk.Verdict(False, f"{SECTORS.get(sym, 'Sector')} peers moving the other way", "sector")
        c = {"ts": b[0], "clock": clock(b[0] + self._bar_s), "symbol": sym, "strategy": key, "side": side,
             "entry": round(entry, 2), "stop": round(entry - sign * dist, 2),
             "target": round(entry + sign * self.s.target_r * dist, 2), "qty": qty, "atr": atr, "dist": dist,
             "risk": round(qty * dist, 2), "reward": round(qty * dist * self.s.target_r, 2),
             "score": sum(agree.values()), "rvol": float(f["rvol"].iat[i]) if f["rvol"].iat[i] == f["rvol"].iat[i] else 0.0,
             "news": heads[-3:],
             "context": {"breadth": round(breadth, 3), "sector": None if sector != sector else round(sector, 4),
                         "index": None if idx != idx else round(idx, 4), "rsi": round(float(f["rsi"].iat[i]), 1) if f["rsi"].iat[i] == f["rsi"].iat[i] else None},
             "verdict": {"ok": v.ok, "reason": v.reason, "code": v.code}, "_i": i}
        if self.use_tm and v.ok:
            c["tm"] = timemachine.odds(f, self.day, b[5], side, entry, dist, self.s.target_r, self.s.flatten_min)
            c["analogs"] = timemachine.analogs(self.events.get(key), sym, self.day)
        return c

    def _close(self, t: Trade) -> None:
        self.positions.pop(t.symbol, None)
        res = t.result()
        self.realized += res["net"]
        self.costs += res["costs"]
        self.gate.consecutive_losses = self.gate.consecutive_losses + 1 if res["net"] < 0 else 0
        self.gate.streak = self.gate.streak + 1 if res["net"] < 0 else 0
        if self.s.lockout_after_losses and self.gate.streak >= self.s.lockout_after_losses and not self.gate.locked:
            self.gate.locked = self.s.lockout_sessions + 1      # the rest of today plus the next sessions
            self.gate.streak = 0
            self.log(t.exit_ts, "halt", f"{self.s.lockout_after_losses} losses in a row: no new trades for {self.s.lockout_sessions} sessions")
        if self.equity <= self.s.equity_floor and not self.gate.killed:
            self.gate.killed = True                           # every losing strategy tested would have hit 50% (RUNBOOK H31)
            self.log(t.exit_ts, "halt", f"Account at ₹{self.equity:,.0f}, at or below the ₹{self.s.equity_floor:,.0f} floor: kill switch on")
        exit_min = int(datetime.fromtimestamp(t.exit_ts, IST).strftime("%H")) * 60 + \
            int(datetime.fromtimestamp(t.exit_ts, IST).strftime("%M")) + self._bar_s // 60
        self.cooldown[t.symbol] = exit_min + self.s.cooldown_min
        meta = t.__dict__.get("meta") or {}
        tm = meta.get("tm") or {}
        rec = {"id": f"{self.session_id}-{len(self.closed) + 1}", "session_id": self.session_id, "day": self.day,
               "strategy": t.strategy, "symbol": t.symbol, "side": t.side, "qty": t.qty,
               "entry_ts": t.entry_ts, "entry_price": round(t.entry, 2), "exit_ts": t.exit_ts,
               "exit_price": round(t.exit, 2), "stop_initial": round(t.stop, 2), "target": round(t.target, 2),
               **res, "reason": t.reason, "tm_target": tm.get("p_target"), "tm_stop": tm.get("p_stop"),
               "tm_open": tm.get("p_open"), "drex_p": meta.get("drex_p"),
               "fee_version": FEE_SCHEDULE_VERSION, "broker": self.broker_name}
        self.closed.append(rec)
        self.events_out.append({"type": "exit", "symbol": t.symbol, "side": t.side, "qty": t.qty, "reason": t.reason})
        self.log(t.exit_ts, "exit", f"Closed {t.symbol} ({t.reason}) at ₹{t.exit:,.2f} · net ₹{res['net']:,.2f} "
                                    f"({res['r']:+.2f}R) after ₹{res['costs']:,.2f} charges")

    def carry(self) -> dict:
        """Cross-session risk state for the next session: the loss streak and any timeout still running."""
        return {"streak": self.gate.streak, "locked": max(0, self.gate.locked - 1)}

    def start_from(self, carry: Optional[dict]) -> None:
        if carry:
            self.gate.streak = int(carry.get("streak", 0))
            self.gate.locked = int(carry.get("locked", 0))

    def finish(self) -> None:
        """Close anything still open at the last price (data ended before flatten)."""
        for sym, t in list(self.positions.items()):
            t.force_exit(self.last_ts or t.entry_ts, self.marks.get(sym, t.entry), "flatten", self.s)
            self._close(t)
        self.pending.clear()

    # ------------------------------------------------------------- output
    def summary(self) -> Dict[str, Any]:
        nets = [t["net"] for t in self.closed]
        return {"day": self.day, "start_equity": round(self.start_equity, 2), "end_equity": round(self.equity, 2),
                "gross": round(sum(t["gross"] for t in self.closed), 2), "costs": round(self.costs, 2),
                "net": round(self.realized, 2), "trades": len(self.closed),
                "wins": sum(1 for n in nets if n > 0), "losses": sum(1 for n in nets if n < 0),
                "halted": self.gate.halted,
                "max_dd": round(max([0.0] + [-p for _, p in self.equity_curve]), 2),
                "blocked": sum(1 for c in self.setups if c["status"] == "blocked")}

    def snapshot(self) -> Dict[str, Any]:
        pos = []
        for sym, t in self.positions.items():
            px = self.marks.get(sym, t.entry)
            pos.append({"symbol": sym, "side": t.side, "qty": t.qty, "entry": round(t.entry, 2), "price": round(px, 2),
                        "stop": round(t.entry if t.be_active else t.stop, 2), "target": round(t.target, 2),
                        "strategy": t.strategy, "pnl": round(t.sign * (px - t.entry) * t.qty, 2),
                        "r": round(t.r_at(px), 2), "since": clock(t.entry_ts)})
        return {"day": self.day, "clock": clock(self.last_ts + self._bar_s) if self.last_ts else None,
                "summary": self.summary(), "day_pnl": round(self.day_pnl(), 2), "equity": round(self.equity, 2),
                "positions": pos, "closed": self.closed, "setups": list(reversed(self.setups)),
                "logs": list(reversed(self.logs[-80:])), "curve": self.equity_curve, "mood": self.mood,
                "gate": asdict(self.gate)}

    # ------------------------------------------------------- persistence
    def to_state(self) -> Dict[str, Any]:
        return {"positions": {k: {**{f: v for f, v in t.__dict__.items() if f != "meta"}, "meta": t.__dict__.get("meta")}
                              for k, t in self.positions.items()},
                "pending": self.pending, "closed": self.closed, "cooldown": self.cooldown,
                "gate": asdict(self.gate), "realized": self.realized, "costs": self.costs,
                "last_ts": self.last_ts, "curve": self.equity_curve[-400:], "logs": self.logs[-200:]}

    def load_state(self, st: Dict[str, Any]) -> None:
        self.positions = {}
        for k, d in (st.get("positions") or {}).items():
            meta = d.pop("meta", None)
            t = Trade(**d)
            t.__dict__["meta"] = meta
            self.positions[k] = t
        self.pending = st.get("pending") or []
        self.closed = st.get("closed") or []
        self.cooldown = {k: int(v) for k, v in (st.get("cooldown") or {}).items()}
        self.gate = risk.GateState(**(st.get("gate") or {}))
        self.realized = float(st.get("realized") or 0.0)
        self.costs = float(st.get("costs") or 0.0)
        self.last_ts = st.get("last_ts")
        self.equity_curve = st.get("curve") or []
        self.logs = st.get("logs") or []
