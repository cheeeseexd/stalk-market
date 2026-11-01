"""H36 euphoria short, the mirror of panic buy (RUNBOOK "## H36:"), from NSE bhavcopy files. Scored once; paper research only.

    python scripts/euphoria_bhav.py --download     # fetch the daily files and results calendars it needs (no returns computed)
    python scripts/euphoria_bhav.py --score        # guarded by data/h36/started.json
Mornings NIFTY opens more than +0.3% above its previous close (Nov 2016 -> Oct 2026): short the 2 biggest no-news gap-ups above +2%
(EQ, previous traded value >= Rs10 crore, price >= Rs50, not the 40 NIFTY 50 names) at the open, 3% stop above
the open, else cover at the close. Charges + 10 bps slippage a side, as panic buy.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.panic_bhav import COST, MIN_VALUE, UA, bhav, results_symbols  # noqa: E402
from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402

OUT = os.path.join(BASE_DIR, "data", "h36")
EUPHORIA, SECONDARY, STOCK_GAP, STOP = 0.003, 0.005, 0.02, 0.03


def mornings():
    nb = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d_10y.pkl"))["NIFTY 50"]
    nd = pd.to_datetime(nb["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    n = pd.DataFrame({"o": nb["o"].values, "c": nb["c"].values}, index=nd.values)
    n = n[~n.index.duplicated(keep="last")]
    n["gap"] = n["o"]/n["c"].shift(1)-1
    n["prev_day"] = pd.Series(n.index, index=n.index).shift(1)
    n = n[(n.index >= "2016-11-01") & (n.index <= "2026-10-31")].dropna()
    days = list(n.index[n["gap"] > EUPHORIA])
    others = sorted(random.Random(36).sample(list(n.index[n["gap"] <= EUPHORIA]), 200))
    return n, days, others


def client():
    c = httpx.Client(headers=UA, timeout=40, follow_redirects=True)
    c.get("https://www.nseindia.com/")
    return c


def download():
    n, days, others = mornings()
    c = client()
    for k, day in enumerate(days+others):
        if k % 40 == 0:
            print(f"  {k} of {len(days)+len(others)}", flush=True)
            c.get("https://www.nseindia.com/")
        bhav(day, c)
        bhav(n.at[day, "prev_day"], c)
        results_symbols(day, c)


def score():
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "started.json"), "x", encoding="utf-8") as fh:
        json.dump(dict(at=datetime.now(IST).isoformat()), fh)
    n, days, others = mornings()
    c = client()
    rows = []
    for day in days+others:
        today, prev = bhav(day, c), bhav(n.at[day, "prev_day"], c)
        if today.empty or prev.empty:
            continue
        near = results_symbols(day, c)
        if near is not None and len(near) == 0:
            near = None                           # an empty calendar for a 7-day window means the fetch failed, not "no meetings" 
        u = today.join(prev[["value"]].rename(columns={"value": "prev_value"}), how="inner")
        u = u[(u["prev_value"] >= MIN_VALUE) & (u["prev_close"] >= 50) & ~u.index.isin(DEFAULT_UNIVERSE)]
        # No "locked all day" filter: at 09:15 that is unknowable (Astra r5). A stock that stays locked simply returns 0 before costs.
        u["gap"] = u["open"]/u["prev_close"]-1
        cands = u[(u["gap"] > STOCK_GAP) & (~u.index.isin(near) if near is not None else True)].sort_values("gap", ascending=False)
        for sym, r in cands.head(2).iterrows():
            rows.append({"day": day, "euphoria": day in days, "nifty_gap": float(n.at[day, "gap"]), "symbol": sym, "gap": r["gap"],
                         "oc": r["close"]/r["open"]-1, "high": r["high"]/r["open"]-1, "results_known": near is not None})
    t = pd.DataFrame(rows)
    short_ret = -t["oc"]                                                       # a short gains when the close is below the open
    stopped = t["high"] >= STOP
    t["net_stop3"] = np.where(stopped, -STOP-0.002, short_ret)-COST
    # Worse entry, simulated (Astra r5): sell 0.5% below the open; the stop sits 3% above THAT price, so it is nearer the open
    entry_w = 1-0.005
    stop_w = entry_w*(1+STOP)-1                                               # in open terms: +2.485%
    t["net_stop3_worse"] = np.where(t["high"] >= stop_w, -(stop_w+1-entry_w)/entry_w-0.002, (entry_w-(1+t["oc"]))/entry_w)-COST
    t["stopped"] = stopped
    t.to_csv(os.path.join(OUT, "trades.csv"), index=False)
    out = {}
    for lab, m in (("euphoria_0.3", t.euphoria), ("euphoria_0.5", t.euphoria & (t.nifty_gap > SECONDARY)),
                   ("euphoria_0.3_known_calendar", t.euphoria & t.results_known), ("control", ~t.euphoria)):
        x = t[m]
        if x.empty:
            continue
        per_day = x.groupby("day").net_stop3.mean()
        yrs = x.groupby(x.day.str[:4]).net_stop3.mean()
        out[lab] = dict(trades=int(len(x)), mornings=int(x.day.nunique()), net_bps=round(float(x.net_stop3.mean()*1e4), 1),
                        worse_bps=round(float(x.net_stop3_worse.mean()*1e4), 1), win=round(float((x.net_stop3 > 0).mean()), 3),
                        stopped=round(float(x.stopped.mean()), 3), day_t=round(float(per_day.mean()/(per_day.std()/np.sqrt(len(per_day)))), 2),
                        years_positive=int((yrs > 0).sum()), years=int(len(yrs)), by_year_bps={k: round(float(v*1e4)) for k, v in yrs.items()},
                        unknown_calendar_days=int(x.loc[~x.results_known, "day"].nunique()))
    p = out["euphoria_0.3"]
    gates = dict(trades=p["trades"] >= 200, net=p["net_bps"] > 0, t=p["day_t"] >= 2.5, years=p["years_positive"] >= 7,
                 worse=p["worse_bps"] > 0, beats_control=p["net_bps"] > out.get("control", {}).get("net_bps", 1e9))
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
