"""A random past week of the desk we plan to run, played on the dashboard as if live. Paper only: no broker, no paper log.

The desk: on panic mornings (NIFTY opens 0.5%+ down) panic buy only (the 2 biggest clean gap-downs at 09:20, 3% stop);
on every other day the ML ranker, setting B (H34): up to 2 stocks at 09:35, long or short, only when the model's predicted
lead beats 4x charges, 5% stop. Everything is sold at 15:10. Rs10,000 split into two Rs5,000 slots, so never more than two
positions. Weeks come from 2023-01..2025-07, where the ML picks were made by models trained only on earlier years
(data/h31_explore/h34_preds_dev2.pkl) and panic picks are the cleaned 10-year list (data/h17). Candles are Kite 5-minute.
Frames use the same shape as stalk/sim/panic_replay.py so the same player (WeekSim) and page play them.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from stalk.config import BASE_DIR, IST
from stalk.market.costs import round_trip_cost
from stalk.sim import panic
from stalk.sim.panic_replay import WeekSim, day_label, money, pct, stamp

ROOT = Path(BASE_DIR)
SLOT = 5000.0
ML_HURDLE, ML_STOP, ML_ENTRY = 4*0.00106, 0.05, 935
ML_TARGET_X = 8.0          # profit target = 8x the model's predicted move (about 5% for a typical pick; RUNBOOK H34 exits)
FIRST, LAST = "2023-01-02", "2025-07-31"
_CACHE: Dict[str, Any] = {}


def _cache():
    if "ml" not in _CACHE:
        p = pd.read_pickle(ROOT/"data/h31_explore/h34_preds_dev2.pkl")
        p = p[p.pred.abs() > ML_HURDLE]
        ml = {d: g.loc[g.pred.abs().sort_values(ascending=False).index].head(2)[["symbol", "pred", "r930", "ret5", "ret20"]].to_dict("records")
              for d, g in p.groupby("day")}
        pan = pd.read_csv(ROOT/"data/h17/strict_cleaned_stop3_account.csv")
        nifty = pd.read_pickle(ROOT/"data/h31_explore/nifty_daily_2007.pkl")
        gaps = (nifty.open/nifty.close.shift()-1).dropna()
        _CACHE.update(ml=ml, panic={d: g.to_dict("records") for d, g in pan.groupby("day")},
                      gap={k.strftime("%Y-%m-%d"): float(v) for k, v in gaps.items()}, bars={})
    return _CACHE


def day_bars(symbol: str, day: str) -> List[List[float]]:
    """That stock's 5-minute candles for the day, [ts, o, h, l, c, v], from data/kite_5m (cached per stock)."""
    c = _cache()["bars"]
    if symbol not in c:
        path = ROOT/f"data/kite_5m/{symbol}.pkl"
        if not path.exists():
            c[symbol] = {}
        else:
            f = pd.read_pickle(path)
            f = f[(f.ts >= stamp(FIRST, 915)-86400*10) & (f.ts <= stamp(LAST, 1530))]
            days = pd.to_datetime(f.ts, unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
            c[symbol] = {d: g.sort_values("ts")[["ts", "o", "h", "l", "c", "v"]].values.tolist() for d, g in f.groupby(days)}
    return c[symbol].get(day, [])


def weeks_available() -> List[str]:
    days = sorted(d for d in _cache()["gap"] if FIRST <= d <= LAST)
    return sorted({(datetime.strptime(d, "%Y-%m-%d")-timedelta(days=datetime.strptime(d, "%Y-%m-%d").weekday())).strftime("%Y-%m-%d") for d in days})


def build_week(monday: str) -> List[Dict[str, Any]]:
    cache = _cache()
    friday = (datetime.strptime(monday, "%Y-%m-%d")+timedelta(days=4)).strftime("%Y-%m-%d")
    week = sorted(d for d in cache["gap"] if monday <= d <= friday)
    if not week:
        raise ValueError("No stored sessions for that week")
    frames, log, closed, curve = [], [], [], []
    day_status = {d: {"day": d, "label": day_label(d), "state": "pending", "net": 0.0, "trades": 0} for d in week}
    for day in week:
        n_gap = cache["gap"][day]
        panic_day = panic.market_qualifies(1+n_gap, 1.0)             # NIFTY opened more than 0.5% down
        positions: Dict[str, Dict[str, Any]] = {}
        top: List[Dict[str, Any]] = []
        insight = {"headline": "", "detail": ""}
        decided = False
        realized_before = sum(t["net"] for t in closed)
        start, end = stamp(day, 915), stamp(day, panic.EXIT_HM)

        def say(clock, kind, text):
            log.append({"day": day, "when": f"{day_label(day)[:3]} {clock}", "kind": kind, "text": text})

        def open_trade(symbol, side, hm, stop_pct, strategy, why, target_pct=None):
            rows = day_bars(symbol, day)
            bar = next((b for b in rows if int(b[0]) == stamp(day, hm)), None)
            if bar is None:
                say(f"{hm//100:02d}:{hm % 100:02d}", "skip", f"{symbol}: no candle at the entry time, so no simulated fill.")
                return
            raw = float(bar[1])
            slip = panic.SLIP.get("smallcap" if strategy == "panic" else "midcap", 0.0006)
            sg = 1 if side == "long" else -1
            entry = round(raw*(1+sg*slip), 2)
            qty = int(SLOT // entry)
            if qty < 1:
                say(f"{hm//100:02d}:{hm % 100:02d}", "skip", f"{symbol}: one share costs more than ₹5,000.")
                return
            stop = round(raw*(1-stop_pct) if side == "long" else entry*(1+stop_pct), 2)
            target = round(entry*(1+sg*target_pct), 2) if target_pct else None
            positions[symbol] = dict(symbol=symbol, side=side, qty=qty, entry=entry, raw=raw, stop=stop, target=target,
                                     since=f"{hm//100:02d}:{hm % 100:02d}", strategy=strategy, slip=slip, last=raw, why=why)
            verb = "Bought" if side == "long" else "Shorted"
            exit_plan = f"target ₹{target:,.2f} ({target_pct:.1%}), stop ₹{stop:,.2f}, else out at 15:10" if target else f"stop ₹{stop:,.2f}, else out at 15:10"
            say(positions[symbol]["since"], "buy" if side == "long" else "sell", f"{verb} {qty} {symbol} at ₹{entry:,.2f} ({why}). Exit: {exit_plan}.")

        def close_trade(p, px, clock, reason):
            sg = 1 if p["side"] == "long" else -1
            exit_px = round(px*(1-sg*p["slip"]), 2)
            gross = round(sg*(exit_px-p["entry"])*p["qty"], 2)
            buy, sell = (p["entry"], exit_px) if p["side"] == "long" else (exit_px, p["entry"])
            costs = round(float(round_trip_cost(p["qty"], buy, sell)), 2)
            t = dict(day=day, symbol=p["symbol"], side=p["side"], strategy=p["strategy"], qty=p["qty"], entry=p["entry"], exit=exit_px,
                     stop=p["stop"], target=p["target"], gross=gross, costs=costs, net=round(gross-costs, 2), reason=reason,
                     clock=clock, hm=int(p["since"].replace(":", "")) - 5 if p["strategy"] == "ml" else 915)
            closed.append(t)
            word = {"stop": "the stop was hit", "target": "the target was hit"}.get(reason, "15:10, as the rule says")
            say(clock, "stop" if reason == "stop" else "sell", f"Closed {p['symbol']} ({word}). Net {money(t['net'])}.")

        for ts in range(start, end+1, 300):
            clock = datetime.fromtimestamp(ts, IST).strftime("%H:%M")
            hm = int(clock.replace(":", ""))
            if hm == 915:
                if panic_day:
                    day_status[day]["state"] = "watch"
                    insight = {"headline": "Panic morning: panic buy runs today, the ML ranker sits out.",
                               "detail": f"NIFTY opened {pct(n_gap)}. The 2 biggest clean falls get bought at 09:20."}
                    say(clock, "check", f"NIFTY opened {pct(n_gap)}: a panic morning. Panic buy picks at 09:20.")
                else:
                    insight = {"headline": "Normal morning: the ML ranker scores every stock at 09:35.",
                               "detail": f"NIFTY opened {pct(n_gap)}. The model trades up to 2 stocks, only if it is confident."}
                    say(clock, "check", f"NIFTY opened {pct(n_gap)}: not a panic morning. ML ranker decides at 09:35.")
            if hm == 920 and panic_day:
                picks = cache["panic"].get(day, [])
                for p in picks[:2]:
                    open_trade(p["symbol"], "long", 920, panic.STOP, "panic", f"opened {pct(p['gap'], 1)}, a clean panic fall")
                    top.append({"symbol": p["symbol"], "gap": round(p["gap"], 4), "picked": p["symbol"] in positions,
                                "why": "Panic buy pick" if p["symbol"] in positions else "No 5-minute candles stored"})
                decided = True
                if positions:
                    day_status[day]["state"] = "trade"
                    insight = {"headline": f"Bought {' and '.join(positions)}. Holding until the 3% stop or 15:10.", "detail": "Panic buy, the proven rule."}
                else:
                    day_status[day]["state"] = "skip"
                    insight = {"headline": "Panic morning, but no stored candles for its picks.",
                               "detail": "The panic picks are often small caps outside the 285 stocks with 5-minute history."}
            if hm == ML_ENTRY and not panic_day:
                picks = cache["ml"].get(day, [])
                for p in picks:
                    side = "long" if p["pred"] > 0 else "short"
                    open_trade(p["symbol"], side, ML_ENTRY, ML_STOP, "ml",
                               f"model expects it to {'beat' if side == 'long' else 'trail'} the market by {abs(p['pred'])*100:.2f}%",
                               target_pct=ML_TARGET_X*abs(p["pred"]))
                    top.append({"symbol": p["symbol"], "gap": round(p["r930"], 4), "picked": p["symbol"] in positions,
                                "why": f"{'Long' if side == 'long' else 'Short'} · predicted {p['pred']*100:+.2f}% vs market"})
                decided = True
                if positions:
                    day_status[day]["state"] = "trade"
                    insight = {"headline": f"ML ranker took {', '.join(f'{s} ({q['side']})' for s, q in positions.items())}.",
                               "detail": "Holding until the 5% stop or 15:10. No adding, no early exits."}
                else:
                    day_status[day]["state"] = "skip"
                    insight = {"headline": "The ML ranker sat out today.", "detail": "No stock's predicted lead beat 4× charges, so no trade."}
                    say(clock, "skip", "No confident pick today: the model stands aside.")
            for symbol, p in list(positions.items()):
                rows = day_bars(symbol, day)
                done = [b for b in rows if int(b[0])+300 <= ts and int(b[0]) >= stamp(day, int(p["since"].replace(":", "")))]
                cur = next((b for b in rows if int(b[0]) == ts), None)
                hit, why = None, None
                lg, tg = p["side"] == "long", p["target"]
                for b in done:                     # same order as the backtest: gaps at the open first, then the stop, then the target
                    if int(b[0]) >= end:
                        break
                    o_, h_, l_ = b[1], b[2], b[3]
                    if (o_ <= p["stop"]) if lg else (o_ >= p["stop"]):
                        hit, why = o_, "stop"
                    elif tg and ((o_ >= tg) if lg else (o_ <= tg)):
                        hit, why = o_, "target"
                    elif (l_ <= p["stop"]) if lg else (h_ >= p["stop"]):
                        hit, why = p["stop"], "stop"
                    elif tg and ((h_ >= tg) if lg else (l_ <= tg)):
                        hit, why = tg, "target"
                    if hit is not None:
                        break
                if hit is not None:
                    close_trade(p, hit, clock, why)
                    del positions[symbol]
                elif hm == panic.EXIT_HM:
                    close_trade(p, cur[1] if cur else (done[-1][4] if done else p["raw"]), clock, "close")
                    del positions[symbol]
                else:
                    p["last"] = cur[1] if cur else (done[-1][4] if done else p["last"])
            day_trades = [t for t in closed if t["day"] == day]
            if hm == panic.EXIT_HM:
                net = round(sum(t["net"] for t in day_trades), 2)
                day_status[day].update(state="done" if day_trades else "skip", net=net, trades=len(day_trades))
                if day_trades:
                    say(clock, "day", f"{day_label(day)} done: {len(day_trades)} trades, net {money(net)}. All positions flat.")
                    insight = {"headline": f"Day done: net {money(net)} after charges.", "detail": "Everything is closed before 15:15."}
            elif day_trades:
                day_status[day]["net"] = round(sum(t["net"] for t in day_trades), 2)
            opened = [{"symbol": s, "side": p["side"], "qty": p["qty"], "entry": p["entry"], "stop": p["stop"], "target": p["target"], "last": p["last"], "since": p["since"],
                       "pnl": round((1 if p["side"] == "long" else -1)*(p["last"]-p["entry"])*p["qty"], 2), "strategy": p["strategy"]}
                      for s, p in positions.items()]
            if positions and decided:
                pnl = sum(x["pnl"] for x in opened)
                insight = {"headline": f"Holding {', '.join(positions)}. {'Up' if pnl >= 0 else 'Down'} ₹{abs(pnl):,.0f} on open trades.",
                           "detail": insight["detail"] if hm < panic.EXIT_HM else ""}
            realized = round(sum(t["net"] for t in closed), 2)
            unrealized = round(sum(x["pnl"] for x in opened), 2)
            quiet = decided and not positions and hm < panic.EXIT_HM
            if not quiet and decided:
                curve.append([ts, round(realized+unrealized, 2)])
            frames.append(copy.deepcopy({
                "mode": "desk", "day": day, "ts": ts, "clock": clock, "label": day_label(day), "nifty_gap": n_gap, "qualifies": True,
                "panic_day": panic_day, "quiet": quiet, "insight": insight, "log": log[-40:], "funnel": None, "top": top,
                "positions": opened, "trades": closed, "realized": realized, "unrealized": unrealized, "total": round(realized+unrealized, 2),
                "charges": round(sum(t["costs"] for t in closed), 2), "won": sum(t["net"] > 0 for t in closed), "lost": sum(t["net"] <= 0 for t in closed),
                "days": list(day_status.values()), "curve": curve[-450:], "day_net": round(realized+unrealized-realized_before, 2)}))
    return frames


class DeskWeekSim(WeekSim):
    """Same player; candles come from the Kite 5-minute history and both long and short trades are drawn."""

    def bars(self, symbol: str = "") -> Dict[str, Any]:
        with self.lock:
            self._advance()
            f = self.frames[self.idx]
            trades = [t for t in f["trades"] if t["day"] == f["day"]]
            picks = list(dict.fromkeys(p["symbol"] for p in [*f["positions"], *trades]))
            symbol = symbol if symbol in picks else (picks[0] if picks else "")
            rows = day_bars(symbol, f["day"]) if symbol else []
            visible = [list(b) if b[0]+300 <= f["ts"] else [b[0], b[1], b[1], b[1], b[1], 0] for b in rows if b[0] <= f["ts"]]
            trade = next((p for p in [*f["positions"], *trades] if p["symbol"] == symbol), None)
            levels, markers = [], []
            if trade:
                side = trade.get("side", "long")
                entry_hm = 920 if trade.get("strategy") == "panic" else ML_ENTRY
                levels = [{"y": trade["entry"], "kind": "entry"}, {"y": trade["stop"], "kind": "stop"}]
                if trade.get("target"):
                    levels.append({"y": trade["target"], "kind": "target"})
                markers = [{"kind": "entry", "ts": stamp(f["day"], entry_hm), "price": trade["entry"], "side": side}]
                if "exit" in trade:
                    markers.append({"kind": "exit", "ts": stamp(f["day"], int(trade["clock"].replace(":", ""))), "price": trade["exit"], "side": side})
            note = ("Entry and stop shown; markers appear as trades open and close." if picks else
                    "No trade yet today." if not f["quiet"] else "No trade today.")
            return {"week": self.week, "day": f["day"], "ts": f["ts"], "idx": self.idx, "symbol": symbol or "—", "picks": picks,
                    "bars": visible, "levels": levels, "markers": markers, "note": note,
                    "entry": trade["entry"] if trade else None, "stop": trade["stop"] if trade else None, "exit": trade.get("exit") if trade else None}


def save_to_history(frames: List[Dict[str, Any]], week: str) -> str:
    """A finished desk week becomes a Sessions run ("Random week · Desk: panic buy + ML ranker B")."""
    from scripts.publish_run import publish
    last = frames[-1]
    t = pd.DataFrame(last["trades"])
    if t.empty:
        t = pd.DataFrame(columns=["day", "symbol", "side", "hm", "qty", "entry", "exit", "reason", "gross", "costs", "net", "strategy"])
    else:
        t["reason"] = t.reason.map({"stop": "stop", "close": "flatten", "target": "target"})
        t["strategy"] = t.strategy.map({"panic": "panic_buy", "ml": "h34_ml"})
    cid = f"desk_{week}"
    publish(cid, "Random week · Desk: panic buy + ML ranker B", t, last["days"][0]["day"], last["days"][-1]["day"], "Random check",
            "Watched on the Simulation page: panic buy on panic mornings, ML ranker setting B on other days, ₹10,000 in two ₹5,000 slots.")
    return cid
