"""Ask Drex whether each material NSE announcement is good or bad news for the share price, and store the answer.

  python scripts/label_news.py            # label every material announcement that has no Drex answer yet

Drex sees only the announcement's category and text, never the company's later price moves.
The answer is stored in news.drex_p (probability that it is good news). Between 0.35 and 0.65 counts as neutral.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv  # noqa: E402

from stalk.config import BASE_DIR  # noqa: E402
from stalk.data.store import Store  # noqa: E402
from stalk.llm.drex import DrexClient  # noqa: E402
from stalk.research.news import classify  # noqa: E402

load_dotenv(os.path.join(BASE_DIR, ".env"))

QUESTION = {
    "good_news": {
        "type": "noul",
        "instructions": "This stock-exchange announcement by an Indian listed company is good news for its share price "
                        "in the next trading session (routine or neutral filings count as not good news).",
        "criteria": {"true": "Likely to lift the share price", "false": "Neutral, routine, or likely to weigh on the share price"},
    },
    "bad_news": {
        "type": "noul",
        "instructions": "This stock-exchange announcement by an Indian listed company is bad news for its share price "
                        "in the next trading session (routine or neutral filings count as not bad news).",
        "criteria": {"true": "Likely to push the share price down", "false": "Neutral, routine, or likely to lift the share price"},
    },
}


async def main() -> None:
    st = Store()
    rows = [r for r in st._rows("SELECT seq, category, text FROM news WHERE drex_p IS NULL") if classify(r["category"], r["text"])[0]]
    client = DrexClient(timeout_s=30)
    if not client.configured:
        sys.exit("DREX_API_KEY is not set in .env")
    sem = asyncio.Semaphore(4)
    done = [0]

    async def one(r):
        async with sem:
            state = {"task": "Judge one corporate announcement filed on the National Stock Exchange of India.",
                     "category": r["category"], "announcement": r["text"]}
            ans = await client.ask(state, QUESTION)
        if ans.ok:
            good, bad = ans.noul("good_news") or 0.0, ans.noul("bad_news") or 0.0
            p = 0.5 + (good - bad) / 2          # 1 = clearly good, 0 = clearly bad, 0.5 = neutral
            with st._lock:
                st._conn.execute("UPDATE news SET drex_p=? WHERE seq=?", (round(p, 4), r["seq"]))
                st._conn.commit()
        done[0] += 1
        if done[0] % 200 == 0:
            print(f"  {done[0]} of {len(rows)}", flush=True)

    await asyncio.gather(*(one(r) for r in rows))
    print(f"labelled {done[0]} announcements; Drex calls {client.calls}, errors {client.errors}, last error {client.last_error}")


if __name__ == "__main__":
    asyncio.run(main())
