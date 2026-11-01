"""H6: fade gaps with no results behind them, on the 240 wider stocks (RUNBOOK 2026-11-03).

  python scripts/gap_fade_wide.py

Entry at the 09:15 open against the gap, exit 15:15 (hourly bars). Days within one session of a results meeting are excluded.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE  # noqa: E402
from scripts.results_drift import CHARGES, SLIP, day_rows, results_days  # noqa: E402


def main() -> None:
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")["index"]
    bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_60m.pkl"))
    syms = [s for s in bars if s in meta.index and s not in DEFAULT_UNIVERSE]
    days = pd.concat([day_rows(bars[s]).assign(symbol=s) for s in syms if len(bars[s])], ignore_index=True)
    res = results_days(pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_events.pkl")))
    all_days = np.array(sorted(days["day"].unique()))
    near = set()
    for sym, m in res.itertuples(index=False):
        i = int(np.searchsorted(all_days, m))
        for k in (i - 1, i, i + 1):
            if 0 <= k < len(all_days):
                near.add((sym, all_days[k]))
    days["news"] = [(s, d) in near for s, d in zip(days["symbol"], days["day"])]
    days["tier"] = days["symbol"].map(meta)
    days["cost"] = CHARGES + 2 * days["tier"].map(SLIP)
    days["year"] = days["day"].str[:4]
    rows = []
    for th in (0.015, 0.02, 0.03, 0.05):
        t = days[(days["gap"].abs() > th) & ~days["news"]].copy()
        t["gross"] = -np.sign(t["gap"]) * t["oc"]
        t["net"] = t["gross"] - t["cost"]
        x = t["net"]
        per_day = x.groupby(t["day"]).mean()
        rows.append({"gap >": f"{th:.1%}", "trades": len(t), "days": len(per_day), "gross_bps": round(t["gross"].mean() * 1e4, 1),
                     "net_bps": round(x.mean() * 1e4, 1), "hit": round(float((t["gross"] > 0).mean()), 3),
                     "t_by_day": round(float(per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day)))), 2),
                     "by_year_net": t.groupby("year")["net"].mean().mul(1e4).round(0).to_dict(),
                     "up_gaps_net": round(t.loc[t["gap"] > 0, "net"].mean() * 1e4, 1), "down_gaps_net": round(t.loc[t["gap"] < 0, "net"].mean() * 1e4, 1)})
        if th == 0.02:
            t.to_pickle(os.path.join(BASE_DIR, "data", "gap_fade_trades.pkl"))
    pd.set_option("display.width", 240)
    print(f"240 wider stocks, non-results days, {days['day'].min()} → {days['day'].max()}\n")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()
