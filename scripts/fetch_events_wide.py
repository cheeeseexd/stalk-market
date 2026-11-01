"""NSE board meetings for the wide research universe (scripts/fetch_wide.py), saved to data/wide_events.pkl.

  python scripts/fetch_events_wide.py

Free NSE API; it needs the cookie the homepage sets. One request per stock, paced gently.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import httpx
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, IST  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
      "Accept": "application/json", "Referer": "https://www.nseindia.com/"}


def main() -> None:
    syms = list(pd.read_csv(os.path.join(BASE_DIR, "data", "wide_meta.csv"))["symbol"])
    rows, failed = [], []
    with httpx.Client(headers=UA, timeout=30, follow_redirects=True) as c:
        c.get("https://www.nseindia.com/")
        for i, s in enumerate(syms):
            try:
                r = c.get("https://www.nseindia.com/api/corporate-board-meetings",
                          params={"index": "equities", "symbol": s, "from_date": "01-11-2023", "to_date": "31-10-2026"})
                for x in r.json():
                    day = datetime.strptime(x["bm_date"], "%d-%b-%Y").strftime("%Y-%m-%d")
                    ann = int(datetime.strptime(x["bm_timestamp"], "%d-%b-%Y %H:%M:%S").replace(tzinfo=IST).timestamp()) if x.get("bm_timestamp") else None
                    rows.append({"symbol": s, "day": day, "purpose": x.get("bm_purpose") or "", "detail": x.get("bm_desc") or "", "announced_ts": ann})
            except Exception as e:
                failed.append(s)
                if i % 40 == 0:
                    c.get("https://www.nseindia.com/")   # refresh the cookie now and then
            time.sleep(0.35)
    df = pd.DataFrame(rows).drop_duplicates()
    df.to_pickle(os.path.join(BASE_DIR, "data", "wide_events.pkl"))
    print(f"{len(df)} meetings for {df['symbol'].nunique()} stocks; failed {len(failed)}: {failed[:10]}")


if __name__ == "__main__":
    main()
