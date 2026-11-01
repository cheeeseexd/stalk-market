"""Daily bars (5 years) for the wide research universe → data/wide_1d.pkl. Research only.

  python scripts/fetch_daily_wide.py
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

from stalk.config import BASE_DIR  # noqa: E402
from stalk.data.yahoo import HEADERS, fetch  # noqa: E402


def grab(sym: str):
    try:
        with httpx.Client(timeout=30, headers=HEADERS) as c:
            return sym, pd.DataFrame(fetch(sym, "1d", "5y", client=c), columns=["ts", "o", "h", "l", "c", "v"])
    except Exception as e:
        return sym, str(e)


def main() -> None:
    syms = list(pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv"))["symbol"]) + ["NIFTY 50"]
    with ThreadPoolExecutor(6) as ex:
        got = list(ex.map(grab, syms))
    ok = {s: d for s, d in got if isinstance(d, pd.DataFrame) and len(d)}
    pd.to_pickle(ok, os.path.join(BASE_DIR, "data", "wide_1d.pkl"))
    print(f"{len(ok)} symbols, {sum(len(d) for d in ok.values()):,} daily bars; missing {[s for s, d in got if s not in ok]}")


if __name__ == "__main__":
    main()
