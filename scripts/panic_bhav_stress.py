"""H7c stress test for Sol's two objections (RUNBOOK 2026-11-03): no look-ahead lock filter, and worse entry prices than the official open.

  python scripts/panic_bhav_stress.py      # needs the cached files from scripts/panic_bhav.py

Locked stocks are kept (you'd be filled at the lower circuit and sit there: open → close as recorded).
Entry is penalised by +0 / +0.5% / +1% over the official open, as if the price ran away before the order filled.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402
from scripts.panic_bhav import CACHE, COST, MIN_VALUE  # noqa: E402


def main() -> None:
    nb = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d_10y.pkl"))["NIFTY 50"]
    nd = pd.to_datetime(nb["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    n = pd.DataFrame({"o": nb["o"].values, "c": nb["c"].values}, index=nd.values)
    n = n[~n.index.duplicated(keep="last")]
    n["gap"] = n["o"] / n["c"].shift(1) - 1
    n["prev_day"] = pd.Series(n.index, index=n.index).shift(1)
    n = n[(n.index >= "2016-11-01") & (n.index <= "2026-10-31")].dropna()
    rows = []
    for day in n.index[n["gap"] < -0.005]:
        p_today, p_prev = os.path.join(CACHE, f"{day}.pkl"), os.path.join(CACHE, f"{n.at[day, 'prev_day']}.pkl")
        if not (os.path.exists(p_today) and os.path.exists(p_prev)):
            continue
        today, prev = pd.read_pickle(p_today), pd.read_pickle(p_prev)
        near = pd.read_pickle(os.path.join(CACHE, f"bm_{day}.pkl")) or set()
        if today.empty or prev.empty:
            continue
        u = today.join(prev[["value"]].rename(columns={"value": "prev_value"}), how="inner")
        u = u[(u["prev_value"] >= MIN_VALUE) & (u["prev_close"] >= 50) & ~u.index.isin(DEFAULT_UNIVERSE) & ~u.index.isin(near)]
        u["gap"] = u["open"] / u["prev_close"] - 1
        for sym, r in u[u["gap"] < -0.02].sort_values("gap").head(2).iterrows():
            rows.append({"day": day, "symbol": sym, "open": r["open"], "low": r["low"], "close": r["close"],
                         "locked": r["open"] == r["high"] == r["low"]})
    t = pd.DataFrame(rows)
    print(f"{len(t)} trades on {t['day'].nunique()} panic mornings; locked all day: {int(t['locked'].sum())}\n")
    for pen in (0.0, 0.005, 0.01):
        for stop in (None, 0.03):
            entry = t["open"] * (1 + pen)
            ret = t["close"] / entry - 1
            if stop:
                ret = np.where(t["low"] <= entry * (1 - stop), -stop - 0.002, ret)
            net = pd.Series(ret, index=t.index) - COST
            per_day = net.groupby(t["day"]).mean()
            yrs = net.groupby(t["day"].str[:4]).mean()
            print(f"entry +{pen:.1%} over the open, {'3% stop' if stop else 'no stop'}: net {net.mean() * 1e4:+.0f} bps, hit {(net > 0).mean():.0%}, "
                  f"t {net.mean() / (net.std() / np.sqrt(len(net))):.2f} by trade, {per_day.mean() / (per_day.std() / np.sqrt(len(per_day))):.2f} by day, "
                  f"years positive {(yrs > 0).sum()} of {len(yrs)}")


if __name__ == "__main__":
    main()
