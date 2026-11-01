"""H7: on a morning NIFTY opens more than 0.5% down, buy the wider stocks that opened more than 2% down with no results news;
sell at the close (RUNBOOK 2026-11-03).

  python scripts/panic_buy.py                 # the untouched test: Nov 2021 → Oct 2023, daily bars
  python scripts/panic_buy.py --recent        # Nov 2023 → Oct 2026 (where the lead was found; for comparison only)
  python scripts/panic_buy.py --y2016         # second untouched test: Nov 2016 → Oct 2021 (H7b)
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

CHARGES = 0.00106
SLIP = {"nifty50": 0.0003, "next50": 0.0004, "midcap": 0.0006, "smallcap": 0.0010}


def load(recent: bool, y2016: bool = False):
    lo, hi = ("2023-11-01", "2026-10-31") if recent else ("2016-11-01", "2021-10-31") if y2016 else ("2021-11-01", "2023-10-31")
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")["index"]
    bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d_10y.pkl" if y2016 else "wide_1d.pkl"))
    rows = []
    for s, d in bars.items():
        if s == "NIFTY 50" or s in DEFAULT_UNIVERSE or s not in meta.index:
            continue
        day = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
        x = pd.DataFrame({"day": day.values, "o": d["o"].values, "l": d["l"].values, "c": d["c"].values}).drop_duplicates("day")
        x["gap"] = x["o"] / x["c"].shift(1) - 1
        x["oc"] = x["c"] / x["o"] - 1
        x["symbol"] = s
        rows.append(x)
    df = pd.concat(rows).dropna()
    df = df[(df["day"] >= lo) & (df["day"] <= hi)]
    if y2016:
        nb = bars["NIFTY 50"]
        nd = pd.to_datetime(nb["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
        n = pd.DataFrame({"o": nb["o"].values, "c": nb["c"].values}, index=nd.values)
        n = n[~n.index.duplicated(keep="last")]
    else:
        n = pd.read_pickle(os.path.join(BASE_DIR, "data", "nifty_6y.pkl"))
    n_gap = (n["o"] / n["c"].shift(1) - 1)
    df["n_gap"] = df["day"].map(n_gap)
    ev = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_events_2016.pkl" if y2016 else "wide_events.pkl" if recent else "wide_events_old.pkl"))
    ev = ev[(ev["purpose"].fillna("") + " " + ev["detail"].fillna("")).str.contains("result", case=False)]
    days = np.array(sorted(df["day"].unique()))
    near = set()
    for sym, m in ev[["symbol", "day"]].drop_duplicates().itertuples(index=False):
        i = int(np.searchsorted(days, m))
        for k in (i - 1, i, i + 1):
            if 0 <= k < len(days):
                near.add((sym, days[k]))
    df["news"] = [(s, d) in near for s, d in zip(df["symbol"], df["day"])]
    df["tier"] = df["symbol"].map(meta)
    df["cost"] = CHARGES + 2 * df["tier"].map(SLIP)
    return df.dropna(subset=["n_gap"])


def report(name: str, t: pd.DataFrame) -> dict:
    x = t["oc"] - t["cost"]
    per_day = x.groupby(t["day"]).mean()
    share = x.groupby(t["day"]).sum().max() / x.sum() if x.sum() > 0 else float("nan")
    return {"version": name, "trades": len(t), "days": len(per_day), "gross_bps": round(t["oc"].mean() * 1e4, 1),
            "net_bps": round(x.mean() * 1e4, 1), "hit": round(float((x > 0).mean()), 3),
            "t_trade": round(float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))), 2),
            "t_day": round(float(per_day.mean() / (per_day.std(ddof=1) / np.sqrt(len(per_day)))), 2),
            "best_day_share": round(float(share), 2), "by_year_net": (x.groupby(t["day"].str[:4]).mean() * 1e4).round(0).to_dict()}


def main() -> None:
    recent = "--recent" in sys.argv
    df = load(recent, "--y2016" in sys.argv)
    market_down = df["n_gap"] < -0.005
    print(f"{'RECENT (where found)' if recent else 'UNTOUCHED 2016-21' if '--y2016' in sys.argv else 'UNTOUCHED'}: {df['day'].min()} → {df['day'].max()}, {df['symbol'].nunique()} stocks, "
          f"{df.loc[market_down, 'day'].nunique()} NIFTY gap-down mornings\n")
    rows = []
    for th in (0.015, 0.02, 0.03):
        q = df[market_down & (df["gap"] < -th) & ~df["news"]]
        top2 = q.sort_values("gap").groupby("day").head(2)
        rows.append(report(f"gap < −{th:.1%}, all", q))
        rows.append(report(f"gap < −{th:.1%}, 2 biggest a day (trading rule)", top2))
    # control: the same stocks on mornings NIFTY did not gap down
    q = df[~market_down & (df["gap"] < -0.02) & ~df["news"]]
    rows.append(report("control: gap < −2% on other mornings", q))
    pd.set_option("display.width", 240)
    print(pd.DataFrame(rows).to_string(index=False))

    # Stress tests on the trading rule (2% gap, 2 biggest a day): a stop under the open, a 15:10 exit, worse slippage.
    # With daily bars a stop is exact for "stop or close" exits: if the day's low reached it, the trade was stopped.
    base = df[market_down & (df["gap"] < -0.02) & ~df["news"]].sort_values("gap").groupby("day").head(2)
    rows = []
    for stop in (None, 0.04, 0.03, 0.02):
        for extra in (0.0, 0.0015, 0.003):
            t = base.copy()
            ret = t["oc"] - 0.0008                                   # 15:10 exit instead of the close (measured: about −8 bps)
            if stop:
                hit = t["l"] <= t["o"] * (1 - stop)
                ret = ret.where(~hit, -stop - 0.002)                 # stopped: the stop level, plus 20 bps of slippage on the stop
            t["oc"] = ret
            t["cost"] = t["cost"] + 2 * extra
            r = report(f"stop {stop:.0%}" if stop else "no stop", t)
            r["extra_slip_bps_side"] = round(extra * 1e4)
            rows.append(r)
    print("\nStress tests, 2 biggest a day, gap < −2%:")
    print(pd.DataFrame(rows).drop(columns=["best_day_share"]).to_string(index=False))


if __name__ == "__main__":
    main()
