"""Results-gap drift (RUNBOOK H2 / H3): after quarterly results, a stock that opens more than X% away from its previous close
tends to keep going that way for the rest of the day.

  python scripts/results_drift.py            # NIFTY 50 stocks (where it was found)
  python scripts/results_drift.py --confirm  # the 241 wider stocks it was never examined on (H3)

Entry at the 09:15 open (orders placed in the 09:00–09:08 pre-open fill at the auction price), exit at 15:15, in the gap's direction.
"""
from __future__ import annotations

import os
import sys
import sqlite3

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402

CHARGES = 0.00106
SLIP = {"nifty50": 0.0003, "next50": 0.0004, "midcap": 0.0006, "smallcap": 0.0010}


def day_rows(d: pd.DataFrame) -> pd.DataFrame:
    """Hourly bars → one row per day: open, 10:15 close, 15:15 close, day high and low."""
    t = pd.to_datetime(d["ts"], unit="s", utc=True).dt.tz_convert(IST)
    d = d.assign(day=t.dt.strftime("%Y-%m-%d"), hm=t.dt.hour * 100 + t.dt.minute)
    g = d.groupby("day")
    out = pd.DataFrame({"first_hm": g["hm"].first(), "last_hm": g["hm"].last(), "open": g["o"].first(),
                        "close": g["c"].last(), "hi": g["h"].max(), "lo": g["l"].min()})
    out = out[(out["first_hm"] == 915) & (out["last_hm"] == 1415)].copy()
    out["prev_close"] = out["close"].shift(1)
    out["gap"] = out["open"] / out["prev_close"] - 1
    out["oc"] = out["close"] / out["open"] - 1
    return out.dropna().reset_index()


def results_days(events: pd.DataFrame) -> pd.DataFrame:
    text = events["purpose"].fillna("") + " " + events["detail"].fillna("")
    return events[text.str.contains("result", case=False)][["symbol", "day"]].drop_duplicates()


def trades(days: pd.DataFrame, res: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """The first of M, M+1 with |gap| > threshold, per results meeting."""
    out = []
    by_sym = {s: g.reset_index(drop=True) for s, g in days.groupby("symbol")}
    for sym, m in res.itertuples(index=False):
        g = by_sym.get(sym)
        if g is None:
            continue
        i = int(np.searchsorted(g["day"].values, m))
        for k in (i, i + 1):
            if k < len(g) and abs(g.at[k, "gap"]) > threshold:
                out.append(g.iloc[k])
                break
    t = pd.DataFrame(out).drop_duplicates(["symbol", "day"])
    if t.empty:
        return t
    t["side"] = np.sign(t["gap"])
    t["gross"] = t["side"] * t["oc"]
    t["adverse"] = np.where(t["side"] > 0, t["lo"] / t["open"] - 1, 1 - t["hi"] / t["open"])   # worst excursion against us
    return t


def report(t: pd.DataFrame, label: str) -> dict:
    x = t["net"]
    per_day = x.groupby(t["day"]).sum()
    share = per_day.max() / x.sum() if x.sum() > 0 else float("nan")
    tt = x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 else 0.0
    return {"rule": label, "trades": len(x), "gross_bps": round(t["gross"].mean() * 1e4, 1), "net_bps": round(x.mean() * 1e4, 1),
            "hit": round(float((x > 0).mean()), 3), "t": round(float(tt), 2), "best_day_share": round(float(share), 2),
            "worst_bps": round(x.min() * 1e4), "median_adverse_bps": round(t["adverse"].median() * 1e4)}


def main() -> None:
    confirm = "--confirm" in sys.argv
    meta = pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv")).set_index("symbol")["index"]
    if confirm:
        bars = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_60m.pkl"))
        syms = [s for s in bars if s in meta.index and s not in DEFAULT_UNIVERSE]
        events = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_events.pkl"))
        frames = [day_rows(bars[s]).assign(symbol=s) for s in syms]
    else:
        con = sqlite3.connect(os.path.join(BASE_DIR, "data", "stalk.db"))
        syms = DEFAULT_UNIVERSE
        events = pd.read_sql("SELECT symbol, day, purpose, detail FROM events", con)
        frames = [day_rows(pd.read_sql("SELECT ts, o, h, l, c FROM bars WHERE symbol=? AND interval='60minute' ORDER BY ts", con, params=(s,))).assign(symbol=s)
                  for s in syms]
    days = pd.concat([f for f in frames if len(f)], ignore_index=True)
    days["tier"] = days["symbol"].map(meta).fillna("nifty50")
    res = results_days(events[events["symbol"].isin(syms)])
    print(f"{'Confirm: ' + str(len(syms)) + ' stocks never examined' if confirm else 'NIFTY 50 stocks (where it was found)'}; "
          f"{days['day'].min()} → {days['day'].max()}; {len(res)} results meetings\n")
    rows = []
    for th in (0.01, 0.015, 0.02, 0.03, 0.04):
        t = trades(days, res, th)
        t["net"] = t["gross"] - CHARGES - 2 * t["tier"].map(SLIP)
        rows.append(report(t, f"gap > {th:.1%}"))
        if th == 0.02:
            main_t = t
    print(pd.DataFrame(rows).to_string(index=False))
    t = main_t
    print("\nThe 2% rule broken down:")
    t["year"] = t["day"].str[:4]
    parts = [report(g, f"{y}") for y, g in t.groupby("year")]
    parts += [report(g, f"tier {k}") for k, g in t.groupby("tier")]
    parts += [report(t[t["side"] > 0], "gap up, long"), report(t[t["side"] < 0], "gap down, short")]
    print(pd.DataFrame(parts).to_string(index=False))
    t.to_pickle(os.path.join(BASE_DIR, "data", f"results_trades_{'confirm' if confirm else 'nifty50'}.pkl"))


if __name__ == "__main__":
    main()
