"""Read-only Kite download of 5-minute candles for specific (symbol, day) picks, e.g. panic buy's real trades.

    python scripts/pick_5m.py panic          # every strict + looser panic-buy pick (data/h17), one request per stock-day
    python scripts/pick_5m.py gapup          # H36/H37 gap-up-short picks outside the 285-stock list
Saves data/pick_5m/<SYMBOL>_<day>.pkl; a stock Kite can't find is logged in data/pick_5m/missing.json. No order tools.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd

from stalk.data.kite_mcp import KiteAuthRequired, KiteMCP

OUT = ROOT/'data/pick_5m'


def wanted(kind):
    if kind == 'panic':
        a = pd.concat([pd.read_csv(ROOT/'data/h17/strict_cleaned_stop3_account.csv'), pd.read_csv(ROOT/'data/h17/loose_cleaned_stop3_account.csv')])
        return sorted(set(zip(a.symbol, a.day)))
    t = pd.read_csv(ROOT/'data/h37/trades.csv')
    have = {p.stem for p in (ROOT/'data/kite_5m').glob('*.pkl')}
    return sorted({(s, d) for s, d in zip(t.symbol, t.day) if s not in have})


async def main(kind):
    OUT.mkdir(parents=True, exist_ok=True)
    k = KiteMCP()
    if not await k.check():
        raise KiteAuthRequired('Log into Kite first')
    miss_path = OUT/'missing.json'
    missing = json.loads(miss_path.read_text()) if miss_path.exists() else {}
    todo = [(s, d) for s, d in wanted(kind) if not (OUT/f'{s}_{d}.pkl').exists() and f'{s}|{d}' not in missing]
    print(kind, 'to fetch', len(todo), flush=True)
    for n, (s, d) in enumerate(todo):
        if n % 50 == 0:
            print(f'  {n} of {len(todo)}', flush=True)
        try:
            rows = await k.historical(s, '5minute', f'{d} 09:00:00', f'{d} 15:35:00')
            pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v']).to_pickle(OUT/f'{s}_{d}.pkl')
        except KiteAuthRequired:
            raise
        except Exception as e:
            missing[f'{s}|{d}'] = str(e)[:80]
            miss_path.write_text(json.dumps(missing, indent=0))
        await asyncio.sleep(0.4)
    print('done; missing', len(missing), flush=True)


if __name__ == '__main__':
    asyncio.run(main(sys.argv[1]))
