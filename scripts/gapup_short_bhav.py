"""H37 every-morning gap-up short (RUNBOOK "## H37:"), from NSE bhavcopy files. Scored once; paper research only.

    python scripts/gapup_short_bhav.py --download
    python scripts/gapup_short_bhav.py --score        # guarded by data/h37/started.json
Every morning Nov 2016 -> Oct 2026 that is neither an H36 euphoria morning nor one of H36's 200 control mornings: short the 2 biggest
no-news gap-ups above +2% (EQ, previous traded value >= Rs10 crore, price >= Rs50, not the 40 NIFTY 50 names) at the open, 3% stop
above the open, else cover at the close. Harsher costs than H36: 0.40% round trip, stop fills 0.5% beyond the stop, a short that
closes at the day's high after a +3% run is covered at that high + 0.5%.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.euphoria_bhav import client, mornings  # noqa: E402
from scripts.panic_bhav import MIN_VALUE, bhav, results_symbols  # noqa: E402
from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402

OUT = os.path.join(BASE_DIR, "data", "h37")
STOCK_GAP, STOP, COST, STOP_SLIP, LOCK_PENALTY = 0.02, 0.03, 0.004, 0.005, 0.005


def sample():
    n, euph, ctrl = mornings()
    fresh = [d for d in n.index if d not in set(euph) | set(ctrl)]
    return n, fresh, euph, ctrl


def download():
    n, fresh, _, _ = sample()
    c = client()
    for k, day in enumerate(fresh):
        if k % 40 == 0:
            print(f"  {k} of {len(fresh)}", flush=True)
            c.get("https://www.nseindia.com/")
        bhav(day, c)
        bhav(n.at[day, "prev_day"], c)
        results_symbols(day, c)


def picks(n, days, c, group):
    rows = []
    for day in days:
        today, prev = bhav(day, c), bhav(n.at[day, "prev_day"], c)
        if today.empty or prev.empty:
            continue
        near = results_symbols(day, c)
        if near is not None and len(near) == 0:
            near = None
        u = today.join(prev[["value"]].rename(columns={"value": "prev_value"}), how="inner")
        u = u[(u["prev_value"] >= MIN_VALUE) & (u["prev_close"] >= 50) & ~u.index.isin(DEFAULT_UNIVERSE)]
        u["gap"] = u["open"]/u["prev_close"]-1
        cands = u[(u["gap"] > STOCK_GAP) & (~u.index.isin(near) if near is not None else True)].sort_values("gap", ascending=False)
        for sym, r in cands.head(2).iterrows():
            rows.append({"day": day, "group": group, "nifty_gap": float(n.at[day, "gap"]), "symbol": sym, "gap": r["gap"], "open": r["open"],
                         "close": r["close"], "oc": r["close"]/r["open"]-1, "high": r["high"]/r["open"]-1, "results_known": near is not None})
    return rows


def payoff(t, entry=1.0):
    """Short P&L as a fraction of the entry price, harsh costs. entry < 1 simulates selling below the open."""
    stop_lvl = entry*(1+STOP)-1                                                # stop level in open terms
    locked = (t["high"] >= stop_lvl) & (t["oc"] >= t["high"]-0.0005)            # ran up and closed at the high: couldn't cover at the stop
    stopped = (t["high"] >= stop_lvl) & ~locked
    ret = (entry-(1+t["oc"]))/entry
    ret = np.where(stopped, -(STOP+STOP_SLIP), ret)
    ret = np.where(locked, -((1+t["high"])*(1+LOCK_PENALTY)-entry)/entry, ret)
    return ret-COST, stopped, locked


def summarise(x):
    per_day = x.groupby("day").net.mean()
    yrs = x.groupby(x.day.str[:4]).net.mean()
    return dict(trades=int(len(x)), mornings=int(x.day.nunique()), net_bps=round(float(x.net.mean()*1e4), 1),
                worse_bps=round(float(x.net_worse.mean()*1e4), 1), win=round(float((x.net > 0).mean()), 3),
                stopped=round(float(x.stopped.mean()), 3), locked=round(float(x.locked.mean()), 3),
                day_t=round(float(per_day.mean()/(per_day.std()/np.sqrt(len(per_day)))), 2),
                years_positive=int((yrs > 0).sum()), years=int(len(yrs)), by_year_bps={k: round(float(v*1e4)) for k, v in yrs.items()},
                trades_per_year=round(len(x)/10, 1))


def score():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "started.json"), "x", encoding="utf-8") as fh:
        json.dump(dict(at=datetime.now(IST).isoformat()), fh)
    n, fresh, euph, ctrl = sample()
    c = client()
    t = pd.DataFrame(picks(n, fresh, c, "fresh")+picks(n, euph, c, "euphoria")+picks(n, ctrl, c, "control"))
    t["net"], t["stopped"], t["locked"] = payoff(t)
    t["net_worse"], _, _ = payoff(t, entry=0.995)
    t.to_csv(os.path.join(OUT, "trades.csv"), index=False)
    f = t[t.group == "fresh"]
    out = {"fresh (gated)": summarise(f), "fresh, known calendar": summarise(f[f.results_known]),
           "euphoria mornings (not gated)": summarise(t[t.group == "euphoria"]), "H36 controls (not gated)": summarise(t[t.group == "control"]),
           "all mornings": summarise(t)}
    p = out["fresh (gated)"]
    gates = dict(trades=p["trades"] >= 500, net=p["net_bps"] > 0, t=p["day_t"] >= 3, years=p["years_positive"] >= 8,
                 worse=p["worse_bps"] > 0, known_calendar=out["fresh, known calendar"]["net_bps"] > 0)
    out.update(gates=gates, verdict="Passed" if all(gates.values()) else "Failed")
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--download", action="store_true")
    g.add_argument("--score", action="store_true")
    a = ap.parse_args()
    download() if a.download else score()
