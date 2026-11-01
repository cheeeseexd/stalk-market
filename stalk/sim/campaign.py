"""Campaign: "9:00 on a Monday", repeated over every stored session, as continuous paper accounts.

Each week the strategies are re-validated using only trades from earlier days. Then each day is
played bar by bar through the same engine as live. Several accounts run over the same days so they
can be compared:
  validated     trades only strategies that pass validation that week (the real candidate)
  every_signal  trades every strategy regardless (the baseline)
  solo_<key>    one strategy each, regardless of validation (to see each thesis on its own)
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from stalk.config import HOLDOUT_START, INDEX_SYMBOL, Settings, bar_interval
from stalk.data.store import Store
from stalk.research.load import load_features
from stalk.research.strategies import STRATEGIES
from stalk.research.validate import all_events, stats, validate_as_of
from stalk.sim.engine import DayEngine, signal_table

WARMUP_DAYS = 25


def campaign_days(all_days: List[str], until: Optional[str], start: Optional[str], open_holdout: bool) -> List[str]:
    days = [d for d in all_days if (open_holdout or d < HOLDOUT_START) and (until is None or d <= until)]
    if len(days) <= WARMUP_DAYS:
        return []
    if start:
        return [d for d in days[WARMUP_DAYS:] if d >= start]
    tail = days[WARMUP_DAYS:]
    first_monday = next((i for i, d in enumerate(tail) if datetime.strptime(d, "%Y-%m-%d").weekday() == 0), 0)
    return tail[first_monday:]


def week_key(day: str) -> str:
    y, w, _ = datetime.strptime(day, "%Y-%m-%d").isocalendar()
    return f"{y}-W{w:02d}"


def max_drawdown(equity: List[float]) -> float:
    peak, dd = -1e18, 0.0
    for e in equity:
        peak = max(peak, e)
        dd = max(dd, peak - e)
    return round(dd, 2)


def tm_calibration(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    buckets = [(0.0, 0.2), (0.2, 0.35), (0.35, 0.5), (0.5, 1.01)]
    out = []
    for lo, hi in buckets:
        sel = [t for t in trades if t.get("tm_target") is not None and lo <= t["tm_target"] < hi]
        if sel:
            out.append({"bucket": f"{lo:.0%}–{min(hi, 1):.0%}", "n": len(sel),
                        "predicted": round(float(np.mean([t["tm_target"] for t in sel])), 3),
                        "realised": round(sum(1 for t in sel if t["reason"] == "target") / len(sel), 3)})
    return out


def run_campaign(store: Store, s: Settings, until: Optional[str] = None, start: Optional[str] = None,
                 open_holdout: bool = False, accounts: Optional[Dict[str, Dict[str, Any]]] = None,
                 progress: Optional[Callable[[str, float], None]] = None, cid: Optional[str] = None) -> Dict[str, Any]:
    t0 = time.time()
    say = progress or (lambda m, f: None)
    say("Building features", 0.02)
    feats = load_features(store, s)
    days = campaign_days(store.trading_days(INDEX_SYMBOL, bar_interval(s.bar_minutes)), until, start, open_holdout)
    if not days:
        raise ValueError("not enough stored sessions for a campaign")
    keys = [k for k in s.strategies if k in STRATEGIES]
    say("Trading every signal on history", 0.08)
    events = all_events(feats, s, keys)
    sigs = signal_table(feats, keys, s)

    # Trials ledger: each strategy setting with this trade shape counts once, however often it is rerun.
    shape = {"stop_atr": s.stop_atr, "target_r": s.target_r, "risk": s.risk_per_trade, "cost_gate": s.cost_gate_mult}
    for k in keys:
        for v, ev in events[k].items():
            e = ev[ev["day"] <= days[-1]]
            st = stats(e["r"].to_numpy())
            store.add_trial(k, {STRATEGIES[k].param: v, **shape}, days[-1], st["n"], st["mean_r"], st["t"])

    # Weekly validation, as of each week's first trading day.
    weeks: Dict[str, Dict[str, Any]] = {}
    for d in days:
        wk = week_key(d)
        if wk not in weeks:
            weeks[wk] = {"week": wk, "as_of": d,
                         "strategies": {k: validate_as_of(events[k], k, d, open_holdout) for k in keys}}

    accounts = accounts or {
        "validated": {"strategies": keys, "require_validated": True},
        "every_signal": {"strategies": keys, "require_validated": False},
        **{f"solo_{k}": {"strategies": [k], "require_validated": False} for k in keys},
    }
    cid = cid or f"c{int(time.time())}"
    out_accounts: Dict[str, Any] = {}
    all_trades: List[Dict[str, Any]] = []
    main_events = {k: events[k][STRATEGIES[k].default] for k in keys}
    total_steps = len(accounts) * len(days)
    step = 0
    for name, acct in accounts.items():
        acc_s = Settings.from_dict({**s.to_dict(), "require_validated": acct["require_validated"]})
        account = f"{cid}:{name}"
        store.delete_sessions(account)
        equity = s.capital
        carry = None
        rows, trades = [], []
        for d in days:
            step += 1
            say(f"{name}: {d}", 0.1 + 0.88 * step / total_steps)
            validated = {k: bool(weeks[week_key(d)]["strategies"][k]["validated"]) for k in keys}
            sid = f"{account}:{d}"
            eng = DayEngine(acc_s, d, sid, equity, feats, acct["strategies"], validated,
                            events=main_events, sigs=sigs)
            eng.start_from(carry)
            locked_today = eng.gate.locked > 0
            for ts in eng.timeline():
                eng.on_bar(ts)
            eng.finish()
            carry = eng.carry()
            summ = {**eng.summary(), "timeout": locked_today}
            for tr in eng.closed:
                store.save_trade(tr)
            store.save_session(sid, "campaign", d, account, {"strategies": acct["strategies"],
                               "require_validated": acct["require_validated"]}, summ, ended=True)
            equity = eng.equity
            rows.append({**summ, "week": week_key(d),
                         "validated": [k for k, v in validated.items() if v and k in acct["strategies"]]})
            trades.extend(eng.closed)
        out_accounts[name] = account_summary(name, acct, rows, trades, s)
        all_trades.extend(trades)

    summary = {
        "id": cid, "first_day": days[0], "last_day": days[-1], "days": len(days),
        "holdout": {"start": HOLDOUT_START, "opened": open_holdout},
        "capital": s.capital, "accounts": out_accounts,
        "weeks": list(weeks.values()),
        "research": {k: {str(v): stats(ev[ev["day"] <= days[-1]]["r"].to_numpy(),
                                       ev[ev["day"] <= days[-1]]["net"].to_numpy())
                         for v, ev in events[k].items()} for k in keys},
        "tm_calibration": tm_calibration(all_trades),
        "trials": store.trial_count(),
        "seconds": round(time.time() - t0, 1),
    }
    store.save_campaign(cid, days[0], days[-1], s.to_dict(), summary)
    say("Done", 1.0)
    return summary


def account_summary(name: str, acct: Dict[str, Any], rows: List[Dict[str, Any]], trades: List[Dict[str, Any]],
                    s: Settings) -> Dict[str, Any]:
    net = round(sum(r["net"] for r in rows), 2)
    gross = round(sum(r["gross"] for r in rows), 2)
    costs = round(sum(r["costs"] for r in rows), 2)
    tax = round(max(0.0, net) * s.tax_slab, 2)
    equity = [s.capital] + [r["end_equity"] for r in rows]
    per_strategy = {}
    for k in acct["strategies"]:
        tk = [t for t in trades if t["strategy"] == k]
        st = stats(np.array([t["r"] for t in tk]), np.array([t["net"] for t in tk]))
        per_strategy[k] = {**st, "gross": round(sum(t["gross"] for t in tk), 2), "costs": round(sum(t["costs"] for t in tk), 2)}
    st = stats(np.array([t["r"] for t in trades]), np.array([t["net"] for t in trades]))
    green = sum(1 for r in rows if r["net"] > 0)
    red = sum(1 for r in rows if r["net"] < 0)
    return {"name": name, "strategies": acct["strategies"], "require_validated": acct["require_validated"],
            "start": s.capital, "end": round(equity[-1], 2), "gross": gross, "costs": costs, "net": net,
            "tax": tax, "after_tax": round(net - tax, 2), "trades": len(trades), "green_days": green,
            "red_days": red, "flat_days": len(rows) - green - red, "max_dd": max_drawdown(equity),
            "stats": st, "per_strategy": per_strategy, "days": rows,
            "curve": [[r["day"], r["end_equity"]] for r in rows]}
