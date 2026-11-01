"""H7c: the panic-buy rule with no survivorship bias, from NSE bhavcopy files (every stock that traded that day, delisted ones included).

  python scripts/panic_bhav.py

Days: every morning NIFTY opened more than 0.5% below its previous close, Nov 2016 → Oct 2026, plus 200 other mornings (seed 7) as a control.
Universe that morning: EQ-series stocks with the previous session's traded value ≥ ₹10 crore and price ≥ ₹50, minus our 40 NIFTY 50 names.
No results board meeting within 3 calendar days either side. Stocks whose open, high and low are all equal (locked all day) are skipped.
The 2 biggest gap-downs below −2%, open to close, charges + 10 bps slippage a side. Files are cached in data/bhav/.
"""
from __future__ import annotations

import io
import os
import random
import sys
import time
import zipfile
from datetime import datetime, timedelta

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402

CACHE = os.path.join(BASE_DIR, "data", "bhav")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json", "Referer": "https://www.nseindia.com/"}
COST = 0.00106 + 2 * 0.0010
MIN_VALUE = 1e8          # ₹10 crore traded the previous session


def bhav(day: str, c: httpx.Client) -> pd.DataFrame:
    """One day's EQ-series bhavcopy as symbol → open, high, low, close, prev_close, value."""
    path = os.path.join(CACHE, f"{day}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    d = datetime.strptime(day, "%Y-%m-%d")
    if d >= datetime(2024, 7, 8):
        url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    else:
        mon = d.strftime("%b").upper()
        url = f"https://archives.nseindia.com/content/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
    for attempt in range(3):
        r = c.get(url)
        if r.status_code == 200:
            break
        time.sleep(1.5)
    else:
        df = pd.DataFrame()
        df.to_pickle(path)
        return df
    raw = pd.read_csv(io.BytesIO(zipfile.ZipFile(io.BytesIO(r.content)).read(zipfile.ZipFile(io.BytesIO(r.content)).namelist()[0])))
    raw.columns = [x.strip() for x in raw.columns]
    if "TckrSymb" in raw.columns:
        raw = raw[raw["SctySrs"] == "EQ"]
        df = pd.DataFrame({"symbol": raw["TckrSymb"], "open": raw["OpnPric"], "high": raw["HghPric"], "low": raw["LwPric"],
                           "close": raw["ClsPric"], "prev_close": raw["PrvsClsgPric"], "value": raw["TtlTrfVal"]})
    else:
        raw = raw[raw["SERIES"].str.strip() == "EQ"]
        df = pd.DataFrame({"symbol": raw["SYMBOL"].str.strip(), "open": raw["OPEN"], "high": raw["HIGH"], "low": raw["LOW"],
                           "close": raw["CLOSE"], "prev_close": raw["PREVCLOSE"], "value": raw["TOTTRDVAL"]})
    df = df.set_index("symbol")
    df.to_pickle(path)
    time.sleep(0.25)
    return df


def results_symbols(day: str, c: httpx.Client) -> set:
    path = os.path.join(CACHE, f"bm_{day}.pkl")
    if os.path.exists(path):
        return pd.read_pickle(path)
    d = datetime.strptime(day, "%Y-%m-%d")
    out = set()
    try:
        rows = c.get("https://www.nseindia.com/api/corporate-board-meetings",
                     params={"index": "equities", "from_date": (d - timedelta(days=3)).strftime("%d-%m-%Y"),
                             "to_date": (d + timedelta(days=3)).strftime("%d-%m-%Y")}).json()
        for r in rows if isinstance(rows, list) else []:
            if "result" in f"{r.get('bm_purpose') or ''} {r.get('bm_desc') or ''}".lower():
                out.add(r.get("bm_symbol"))
    except Exception:
        out = None                                   # unknown: the day is reported separately
    pd.to_pickle(out, path)
    time.sleep(0.3)
    return out


def main() -> None:
    os.makedirs(CACHE, exist_ok=True)
    nb = pd.read_pickle(os.path.join(BASE_DIR, "data", "wide_1d_10y.pkl"))["NIFTY 50"]
    nd = pd.to_datetime(nb["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    n = pd.DataFrame({"o": nb["o"].values, "c": nb["c"].values}, index=nd.values)
    n = n[~n.index.duplicated(keep="last")]
    n["gap"] = n["o"] / n["c"].shift(1) - 1
    n["prev_day"] = pd.Series(n.index, index=n.index).shift(1)
    n = n[(n.index >= "2016-11-01") & (n.index <= "2026-10-31")].dropna()
    panic_days = list(n.index[n["gap"] < -0.005])
    others = sorted(random.Random(7).sample(list(n.index[n["gap"] >= -0.005]), 200))
    print(f"{len(panic_days)} panic mornings, {len(others)} control mornings", flush=True)

    nse = httpx.Client(headers=UA, timeout=40, follow_redirects=True)
    nse.get("https://www.nseindia.com/")
    rows = []
    for k, day in enumerate(panic_days + others):
        if k % 40 == 0:
            print(f"  {k} of {len(panic_days) + len(others)}", flush=True)
            nse.get("https://www.nseindia.com/")
        today, prev = bhav(day, nse), bhav(n.at[day, "prev_day"], nse)
        if today.empty or prev.empty:
            continue
        near = results_symbols(day, nse)
        u = today.join(prev[["value"]].rename(columns={"value": "prev_value"}), how="inner")
        u = u[(u["prev_value"] >= MIN_VALUE) & (u["prev_close"] >= 50) & ~u.index.isin(DEFAULT_UNIVERSE)]
        u = u[~((u["open"] == u["high"]) & (u["high"] == u["low"]))]
        u["gap"] = u["open"] / u["prev_close"] - 1
        cands = u[(u["gap"] < -0.02) & (~u.index.isin(near) if near is not None else True)].sort_values("gap")
        for sym, r in cands.head(2).iterrows():
            rows.append({"day": day, "panic": day in panic_days, "symbol": sym, "gap": r["gap"], "oc": r["close"] / r["open"] - 1,
                         "low": r["low"] / r["open"] - 1, "pool": len(u), "eligible": len(cands), "results_known": near is not None})
    t = pd.DataFrame(rows)
    t["net"] = t["oc"] - COST
    t["net_stop3"] = np.where(t["low"] <= -0.03, -0.03 - 0.002, t["oc"]) - COST
    t.to_pickle(os.path.join(BASE_DIR, "data", "panic_bhav_trades.pkl"))
    for lab, m in (("PANIC MORNINGS (the rule)", t["panic"]), ("control: other mornings", ~t["panic"])):
        x = t[m]
        for col in ("net", "net_stop3"):
            per_day = x.groupby("day")[col].mean()
            print(f"\n{lab}, {col}: {len(x)} trades on {x['day'].nunique()} days, net {x[col].mean() * 1e4:+.1f} bps, hit {(x[col] > 0).mean():.0%}, "
                  f"t {x[col].mean() / (x[col].std() / np.sqrt(len(x))):.2f} by trade, {per_day.mean() / (per_day.std() / np.sqrt(len(per_day))):.2f} by day")
        by_year = x.groupby(x["day"].str[:4])["net"].agg(["count", "mean"])
        print("  by year (net bps):", {y: (int(r["count"]), round(r["mean"] * 1e4)) for y, r in by_year.iterrows()})
    print(f"\nPanic mornings with unknown results calendar: {t.loc[t['panic'] & ~t['results_known'], 'day'].nunique()}; "
          f"median pool {int(t['pool'].median())} stocks")


if __name__ == "__main__":
    main()
