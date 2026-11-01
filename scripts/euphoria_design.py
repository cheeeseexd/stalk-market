"""H8 cached design screen only. No confirmation mode and no order code."""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.panic_final import COST, nifty, pool, tstat  # noqa: E402
from stalk.config import BASE_DIR, IST  # noqa: E402
from stalk.market.costs import fill_cost  # noqa: E402


def main() -> None:
    n = nifty()
    # Match the original seed-7 control draw before restricting to design years.
    b = pd.read_pickle(Path(BASE_DIR) / "data" / "wide_1d_10y.pkl")["NIFTY 50"]
    dates = pd.to_datetime(b["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    original = pd.DataFrame({"o": b["o"].values, "c": b["c"].values}, index=dates.values)
    original = original[~original.index.duplicated(keep="last")]
    original["gap"] = original["o"] / original["c"].shift() - 1
    original = original.loc[(original.index >= "2016-11-01") & (original.index <= "2026-10-31")].dropna()
    controls = sorted(random.Random(7).sample(list(original.index[original["gap"] >= -0.005]), 200))
    days = [d for d in controls if d <= "2021-10-31" and n.at[d, "gap"] >= 0.005]
    pools, coverage = [], []
    for day in days:
        p, status, invalid = pool(day, n.at[day, "prev_day"])
        coverage.append({"day": day, "status": status, "invalid_rows": invalid})
        if p is not None:
            pools.append(p.sort_values(["gap", "symbol"], ascending=[False, True], kind="stable"))
    out = Path(BASE_DIR) / "data" / "euphoria_design"
    out.mkdir(exist_ok=True)
    pd.DataFrame(coverage).to_csv(out / "coverage.csv", index=False)
    summaries = []
    for threshold in (0.02, 0.03, 0.04):
        parts = [p[p["gap"] > threshold].head(2).copy() for p in pools]
        if not parts or not sum(len(p) for p in parts):
            summaries.append({"stock_gap": threshold, "trades": 0, "days": 0})
            continue
        x = pd.concat(parts, ignore_index=True)
        stopped = x["high"] >= x["open"] * 1.03
        x["gross"] = np.where(stopped, -0.032, 1 - x["close"] / x["open"] - 0.0008)
        x["net"] = x["gross"] - COST
        pnl = []
        for r in x.itertuples():
            qty = int(5000 // (r.open * 1.001))
            entry = r.open * 0.999
            cover = (entry * 1.03 * 1.002 if r.high >= entry * 1.03 else r.close * 1.0008) * 1.001
            fees = fill_cost("SELL", qty, entry).total + fill_cost("BUY", qty, cover).total
            pnl.append((entry - cover) * qty - fees)
        x["account_pnl"] = pnl
        x.to_csv(out / f"trades_gap{int(threshold * 100)}.csv", index=False)
        by_day = x.groupby("day")["net"].mean()
        daily_sum = x.groupby("day")["net"].sum()
        summaries.append({"stock_gap": threshold, "trades": len(x), "days": len(by_day),
                          "gross_bps": x["gross"].mean() * 1e4, "net_bps": x["net"].mean() * 1e4,
                          "t_day": tstat(by_day), "account_pnl": x["account_pnl"].sum(),
                          "best_day_share": daily_sum.max() / daily_sum.sum() if daily_sum.sum() > 0 else None})
    result = {"design_control_days": len(days), "covered_days": len(pools), "cells": summaries,
              "confirmation": "Reserved future sessions; never loaded or scored"}
    (out / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
