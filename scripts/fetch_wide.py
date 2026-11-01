"""Fetch a wider universe (NIFTY Next 50, Midcap 100, Smallcap 100) for research only. The trading universe is unchanged.

  python scripts/fetch_wide.py

Writes data/wide_5m.pkl (last 60 days of 5-minute bars), data/wide_60m.pkl (about two years of hourly bars) and
data/wide_meta.csv (symbol, index, industry). Constituent lists are today's, from NSE's archive CSVs, so older days
carry survivorship bias: stocks that fell out of these indices aren't in the sample.
"""
from __future__ import annotations

import os
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE  # noqa: E402
from stalk.data.yahoo import HEADERS, fetch  # noqa: E402

LISTS = {"next50": "ind_niftynext50list", "midcap": "ind_niftymidcap100list", "smallcap": "ind_niftysmallcap100list"}


def meta() -> pd.DataFrame:
    frames = []
    for name, f in LISTS.items():
        d = pd.read_csv(os.path.join(BASE_DIR, "data", f + ".csv"))
        frames.append(pd.DataFrame({"symbol": d["Symbol"], "index": name, "industry": d["Industry"]}))
    m = pd.concat(frames).drop_duplicates("symbol")
    nifty = pd.DataFrame({"symbol": DEFAULT_UNIVERSE, "index": "nifty50", "industry": ""})
    return pd.concat([nifty, m[~m["symbol"].isin(DEFAULT_UNIVERSE)]], ignore_index=True)


def grab(sym: str, interval: str, range_: str):
    try:
        with httpx.Client(timeout=30, headers=HEADERS) as c:
            rows = fetch(sym, interval, range_, client=c)
        return sym, pd.DataFrame(rows, columns=["ts", "o", "h", "l", "c", "v"])
    except Exception as e:  # a delisted or renamed symbol shouldn't stop the run
        return sym, str(e)


def main() -> None:
    m = meta()
    m.to_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv"), index=False)
    syms = list(m["symbol"]) + ["NIFTY 50"]
    for interval, range_, out in (("5m", "60d", "wide_5m.pkl"), ("60m", "730d", "wide_60m.pkl")):
        with ThreadPoolExecutor(6) as ex:
            got = list(ex.map(lambda s: grab(s, interval, range_), syms))
        ok = {s: d for s, d in got if isinstance(d, pd.DataFrame) and len(d)}
        bad = [s for s, d in got if not (isinstance(d, pd.DataFrame) and len(d))]
        pd.to_pickle(ok, os.path.join(BASE_DIR, "data", out))
        print(f"{interval}: {len(ok)} symbols, {sum(len(d) for d in ok.values()):,} bars; missing {len(bad)}: {bad[:10]}")


if __name__ == "__main__":
    main()
