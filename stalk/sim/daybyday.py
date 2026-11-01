"""Day-by-day trading, run like the live market: one session at a time, a journal entry every evening.

Each simulated morning only data up to that day is loaded. The session is traded with the rules (RULES.md),
then checked, then journalled. The account learns between sessions, using only what it has already seen:
  - 3 losing days in a row → a review of those days. If one strategy caused most of the loss it is benched
    for 5 sessions. If most losing trades went against NIFTY's direction that day, trades must follow
    the index from then on.
  - 3 winning days in a row → a review of what worked. The strategy that earned most gets priority
    when two setups appear on the same bar. Risk per trade never grows.
Every check that fails stops the run, so a broken step can't hide behind the next day's numbers.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from stalk.config import IST, VIX_SYMBOL, Settings, bar_interval, hhmm_to_min
from stalk.data.store import Store
from stalk.research.load import load_features
from stalk.research.strategies import STRATEGIES
from stalk.sim.campaign import account_summary
from stalk.config import SECTORS
from stalk.research.news import session_news
from stalk.research.trade import walk
from stalk.sim.engine import DayEngine


def drex_vetter(store: Store, s: Settings, day: str, feats, open_ts: int, log: List[Dict[str, Any]]):
    """Build the Drex checklist callback for one session. Every call and answer is kept in `log`."""
    import asyncio
    import os
    from dotenv import load_dotenv
    from stalk.config import BASE_DIR
    from stalk.llm.drex import CHECKLIST, DrexClient, checklist_state, checklist_verdict
    load_dotenv(os.path.join(BASE_DIR, ".env"))
    client = DrexClient(timeout_s=20)
    v = store.load_bars(VIX_SYMBOL, "day", open_ts - 20 * 86400, open_ts - 1)
    vix = round(float(v["c"].iat[-1]), 2) if len(v) else None
    rows = store._rows("SELECT symbol, day, purpose FROM events WHERE day BETWEEN date(?, '-4 day') AND date(?, '+4 day') "
                       "AND (announced_ts IS NULL OR announced_ts < ?)", (day, day, open_ts))

    def vet(c: Dict[str, Any], i: int) -> Dict[str, Any]:
        if not client.configured or i < 0:
            return {"veto": False, "reason": "Drex not configured"}
        f = feats[c["symbol"]]
        row = {k: (float(f[k].iat[i]) if k in f and f[k].iat[i] == f[k].iat[i] else None)
               for k in ("rday", "adr", "vwap", "gap", "pdh", "pdl", "hi52", "idx_rday", "bank_rday")}
        evs = [f"{r['purpose']} on {r['day']}" for r in rows if r["symbol"] == c["symbol"]]
        state = checklist_state(c, row, c.get("context", {}), STRATEGIES[c["strategy"]].thesis,
                                SECTORS.get(c["symbol"], "Other"), evs, vix, s.target_r, s.bar_minutes)
        ans = asyncio.run(client.ask(state, CHECKLIST))
        verdict = checklist_verdict(ans)
        log.append({"day": day, "symbol": c["symbol"], "side": c["side"], "strategy": c["strategy"], "clock": c["clock"],
                    "veto": verdict["veto"], "reason": verdict["reason"], "answers": verdict.get("answers"), "error": ans.error})
        return verdict
    return vet


def would_have(feats, c: Dict[str, Any], s: Settings) -> Optional[float]:
    """Net ₹ a vetoed setup would have made, traded exactly as the engine would have."""
    f = feats[c["symbol"]]
    later = f[(f["ts"] > c["ts"]) & (f["day"] == f.loc[f["ts"] == c["ts"], "day"].iat[0])]
    bars = []
    for r in later.itertuples():
        bars.append((int(r.ts), float(r.o), float(r.h), float(r.l), float(r.c), int(r.end_minute)))
        if r.end_minute >= s.flatten_min:
            break
    t = walk(bars, c["symbol"], c["side"], c["strategy"], c["dist"], c["qty"], s)
    return t.result()["net"] if t else None

LOOKBACK_DAYS = 100
BENCH_SESSIONS = 5
SQUARE_OFF = "15:20"          # Zerodha closes open MIS positions here and charges a fee


class CheckFailed(RuntimeError):
    pass


def check_day(eng: DayEngine, s: Settings, start_equity: float) -> List[str]:
    """Hard checks on a finished session. Returns what was verified; raises CheckFailed on the first problem."""
    sq = hhmm_to_min(SQUARE_OFF)
    if eng.positions:
        raise CheckFailed(f"{eng.day}: positions still open after the session: {list(eng.positions)}")
    for t in eng.closed:
        exit_dt = datetime.fromtimestamp(t["exit_ts"], IST)
        bar_end = exit_dt.hour * 60 + exit_dt.minute + eng._bar_s // 60
        if bar_end > sq:
            raise CheckFailed(f"{eng.day}: {t['symbol']} closed after {SQUARE_OFF} (auto square-off fee)")
        if not t["stop_initial"] or not t["target"]:
            raise CheckFailed(f"{eng.day}: {t['symbol']} traded without a stop or target")
        long_ = t["side"] == "long"
        if (long_ and not t["stop_initial"] < t["entry_price"] < t["target"]) or \
           (not long_ and not t["target"] < t["entry_price"] < t["stop_initial"]):
            raise CheckFailed(f"{eng.day}: {t['symbol']} stop and target on the wrong sides of entry")
        if t["reason"] not in ("stop", "target", "flatten", "loss_halt", "breakeven"):
            raise CheckFailed(f"{eng.day}: {t['symbol']} exited for an unknown reason {t['reason']}")
    if len(eng.closed) > s.max_trades_per_day:
        raise CheckFailed(f"{eng.day}: {len(eng.closed)} trades, cap is {s.max_trades_per_day}")
    if abs(sum(t["net"] for t in eng.closed) - eng.realized) > 0.05 or abs(eng.equity - start_equity - eng.realized) > 0.05:
        raise CheckFailed(f"{eng.day}: P&L doesn't reconcile")
    return ["flat before 15:20", "stop and target on every trade", f"≤ {s.max_trades_per_day} trades", "P&L reconciles"]


def describe_trade(t: Dict[str, Any]) -> str:
    when = datetime.fromtimestamp(t["entry_ts"], IST).strftime("%H:%M")
    out = datetime.fromtimestamp(t["exit_ts"], IST).strftime("%H:%M")
    why = {"stop": "stop hit", "target": "target hit", "flatten": "closed at day end", "loss_halt": "loss limit"}.get(t["reason"], t["reason"])
    return (f"{'Bought' if t['side'] == 'long' else 'Sold'} {t['qty']} {t['symbol']} at {when} (₹{t['entry_price']:,.2f}, "
            f"stop ₹{t['stop_initial']:,.2f}, target ₹{t['target']:,.2f}) · {STRATEGIES[t['strategy']].name} · {why} at {out} · "
            f"net ₹{t['net']:,.2f}")


def vix_spike(store: Store, day: str) -> Optional[str]:
    """A reason to stand aside today if yesterday's India VIX close was a fear spike, else None."""
    start = int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp())
    v = store.load_bars(VIX_SYMBOL, "day", start - 60 * 86400, start - 1)
    if len(v) < 21:
        return None
    last, base = float(v["c"].iat[-1]), float(v["c"].iloc[-21:-1].mean())
    return f"India VIX spiked to {last:.1f} (20-day average {base:.1f})" if last > 1.15 * base else None


def run_days(store: Store, s: Settings, start: str, end: str, account: str = "daily",
             say: Callable[[str], None] = print, label: str = "") -> Dict[str, Any]:
    days = [d for d in store.trading_days("NIFTY 50", bar_interval(s.bar_minutes)) if start <= d <= end]
    if not days:
        raise ValueError("no stored sessions in that range")
    keys = [k for k in s.strategies if k in STRATEGIES]
    cid = f"d{int(time.time())}"
    acct_name = f"{cid}:{account}"
    store.delete_sessions(acct_name)
    run_s = Settings.from_dict({**s.to_dict(), "require_validated": False})
    equity, carry = s.capital, None
    bench: Dict[str, int] = {}                 # strategy -> sessions left on the bench
    priority: Dict[str, float] = defaultdict(float)
    rows, trades, journal = [], [], []
    red, green = 0, 0
    recent: List[Dict[str, Any]] = []          # the last three sessions' trades, for reviews
    drex_log: List[Dict[str, Any]] = []        # every Drex checklist answer
    vetoes: List[Dict[str, Any]] = []          # vetoed setups with what they would have made

    for d in days:
        end_ts = int((datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=IST) + timedelta(days=1)).timestamp()) - 1
        start_ts = end_ts - LOOKBACK_DAYS * 86400
        feats = load_features(store, run_s, end_ts=end_ts, start_ts=start_ts)      # nothing after this day exists yet
        active = [k for k in keys if bench.get(k, 0) <= 0]
        open_ts = int(datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=IST, hour=9, minute=15).timestamp())
        avoid = store.event_symbols(d, open_ts) if run_s.avoid_events else set()
        vet = drex_vetter(store, run_s, d, feats, open_ts, drex_log) if run_s.drex_veto else None
        news = session_news(store, d, run_s.news_source) if run_s.news_filter else {}
        eng = DayEngine(run_s, d, f"{acct_name}:{d}", equity, feats, active, {k: True for k in active},
                        priority=dict(priority), avoid=avoid, vetter=vet, news=news)
        eng.start_from(carry)
        spike = vix_spike(store, d) if run_s.vix_filter else None
        if spike:
            eng.gate.day_block = spike
        for ts in eng.timeline():
            eng.on_bar(ts)
        eng.finish()
        checks = check_day(eng, run_s, equity)
        carry = eng.carry()
        summ = eng.summary()
        for t in eng.closed:
            store.save_trade(t)
        store.save_session(f"{acct_name}:{d}", "daily", d, acct_name, run_s.to_dict(), summ, ended=True)
        equity = eng.equity
        trades.extend(eng.closed)
        rows.append({**summ, "benched": sorted(k for k, v in bench.items() if v > 0), "with_index": run_s.with_index})
        for k in list(bench):
            bench[k] -= 1

        # ------------------------------------------------------------ journal
        net = summ["net"]
        lines = [f"### {datetime.strptime(d, '%Y-%m-%d').strftime('%a %d %b %Y')}: {'+' if net > 0 else ''}₹{net:,.2f}, account ₹{equity:,.2f}"]
        mb = eng.mood_bars
        lines.append(f"- Market mood: bullish {mb['bullish']} bars, bearish {mb['bearish']}, unclear {mb['unclear']}"
                     + (f"; at the close NIFTY {eng.mood['index'] * 100:+.2f}%, {eng.mood['breadth']:.0%} of stocks above VWAP." if eng.mood.get("index") is not None else "."))
        if eng.closed:
            lines += [f"- {describe_trade(t)}" for t in eng.closed]
        else:
            blocked = [c for c in eng.setups if c["status"] == "blocked"]
            why = blocked[-1]["verdict"]["reason"] if blocked else "no strategy signalled"
            lines.append(f"- No trade. {len(eng.setups)} signals; last reason: {why}.")
        if eng.gate.locked:
            lines.append("- Timeout running after 3 losses in a row.")
        if spike:
            lines.append(f"- Stood aside: {spike}.")
        for c in eng.vetoed:
            wh = would_have(feats, c, run_s)
            vetoes.append({"day": d, "symbol": c["symbol"], "side": c["side"], "strategy": c["strategy"],
                           "reason": c["verdict"]["reason"], "would_have": wh, "answers": c.get("drex")})
            lines.append(f"- Drex vetoed {c['side']} {c['symbol']} at {c['clock']} ({c['verdict']['reason']}); "
                         f"it would have made ₹{wh:,.2f}." if wh is not None else f"- Drex vetoed {c['symbol']} ({c['verdict']['reason']}).")
        if avoid:
            lines.append(f"- Skipped for board meetings or results: {', '.join(sorted(avoid))}.")
        news_blocks = sorted({c["symbol"] for c in eng.setups if c["verdict"]["code"] == "news"})
        if news_blocks:
            lines.append(f"- Blocked by company news: {', '.join(news_blocks)}.")
        lines.append(f"- Checks passed: {', '.join(checks)}.")
        recent = (recent + [{"day": d, "net": net, "trades": list(eng.closed)}])[-3:]

        if net < 0:
            red, green = red + 1, 0
        elif net > 0:
            green, red = green + 1, 0
        if red == 3:
            lines += review_losses(recent, run_s, bench, feats_idx=feats)
            patch = {"with_index": run_s.with_index or _against_index(recent, feats)}
            if _early_losses(recent) and hhmm_to_min(run_s.entry_start) < hhmm_to_min("10:00"):
                patch["entry_start"] = "10:00"
            run_s = Settings.from_dict({**run_s.to_dict(), **patch})
            red = 0
        if green == 3:
            lines += review_wins(recent, priority)
            green = 0
        journal.append("\n".join(lines))
        say(lines[0].replace("### ", ""))

    acct = account_summary(account, {"strategies": keys, "require_validated": False}, rows, trades, s)
    summary = {"id": cid, "first_day": days[0], "last_day": days[-1], "days": len(days),
               "holdout": {"start": None, "opened": True}, "capital": s.capital, "accounts": {account: acct},
               "weeks": [], "research": {}, "tm_calibration": [], "trials": store.trial_count(), "kind": "day_by_day",
               "label": label, "drex": {"calls": len(drex_log), "vetoes": vetoes,
                                        "allowed": [x for x in drex_log if not x["veto"]]}}
    store.save_campaign(cid, days[0], days[-1], s.to_dict(), summary)
    return {"summary": summary, "journal": journal, "final_settings": run_s.to_dict(), "bench": bench,
            "priority": dict(priority)}


def _early_losses(recent: List[Dict[str, Any]]) -> bool:
    """True when most losing trades were opened in the first 30 minutes (before 09:45)."""
    losers = [t for r in recent for t in r["trades"] if t["net"] < 0]
    early = [t for t in losers if datetime.fromtimestamp(t["entry_ts"], IST).hour * 60
             + datetime.fromtimestamp(t["entry_ts"], IST).minute < hhmm_to_min("09:45")]
    return bool(losers) and len(early) * 2 > len(losers)


def _against_index(recent: List[Dict[str, Any]], feats: Dict[str, Any]) -> bool:
    """True when most losing trades in the window went against NIFTY's move since the open."""
    losers = [t for r in recent for t in r["trades"] if t["net"] < 0]
    if not losers:
        return False
    against = 0
    for t in losers:
        f = feats.get(t["symbol"])
        if f is None:
            continue
        row = f[f["ts"] <= t["entry_ts"]].tail(1)
        if row.empty:
            continue
        idx = float(row["idx_rday"].iat[0])
        if (t["side"] == "long" and idx < 0) or (t["side"] == "short" and idx > 0):
            against += 1
    return against * 2 > len(losers)


def review_losses(recent, s: Settings, bench: Dict[str, int], feats_idx) -> List[str]:
    tr = [t for r in recent for t in r["trades"]]
    total = sum(r["net"] for r in recent)
    by_strat = defaultdict(float)
    for t in tr:
        by_strat[t["strategy"]] += t["net"]
    reasons = defaultdict(int)
    for t in tr:
        reasons[t["reason"]] += 1
    out = [f"- **Review after 3 losing days ({recent[0]['day']} → {recent[-1]['day']}, ₹{total:,.2f}):** "
           f"{len(tr)} trades; exits {dict(reasons)}; by strategy " +
           ", ".join(f"{STRATEGIES[k].name} ₹{v:,.0f}" for k, v in sorted(by_strat.items(), key=lambda kv: kv[1]))]
    if by_strat:
        worst, loss = min(by_strat.items(), key=lambda kv: kv[1])
        if loss < 0 and loss <= 0.5 * total:
            bench[worst] = BENCH_SESSIONS
            out.append(f"- Action: {STRATEGIES[worst].name} caused most of the loss; benched for {BENCH_SESSIONS} sessions.")
    if _against_index(recent, feats_idx) and not s.with_index:
        out.append("- Action: most losing trades went against NIFTY's direction that day; from now on trades must follow the index.")
    if _early_losses(recent) and hhmm_to_min(s.entry_start) < hhmm_to_min("10:00"):
        out.append("- Action: most losing trades were opened in the first 30 minutes; from now on the first entry is 10:00.")
    if len(out) == 1:
        out.append("- No single cause; nothing changed (losses inside the rules are allowed).")
    return out


def review_wins(recent, priority: Dict[str, float]) -> List[str]:
    tr = [t for r in recent for t in r["trades"]]
    by_strat = defaultdict(float)
    for t in tr:
        by_strat[t["strategy"]] += t["net"]
    total = sum(r["net"] for r in recent)
    out = [f"- **Review after 3 winning days ({recent[0]['day']} → {recent[-1]['day']}, +₹{total:,.2f}):** " +
           ", ".join(f"{STRATEGIES[k].name} ₹{v:,.0f}" for k, v in sorted(by_strat.items(), key=lambda kv: -kv[1]))]
    if by_strat:
        best, gain = max(by_strat.items(), key=lambda kv: kv[1])
        if gain > 0:
            priority[best] += 1.0
            out.append(f"- Action: {STRATEGIES[best].name} earned most; it now gets priority when setups clash. Risk per trade stays ₹{100:,}.")
    return out
