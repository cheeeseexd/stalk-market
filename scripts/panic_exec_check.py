"""Panic buy with real 5-minute fills: does the 10-year daily-data edge survive entries a real order can get?

    python scripts/panic_exec_check.py
For every strict + looser panic pick with Kite 5-minute candles (data/pick_5m, or data/kite_5m for the 285-stock list):
entry at the 09:15 open or the 09:20 open (after the first candle), 3% stop from that entry filled at the stop or, if a candle
opens below it, at that candle's open, exit at the 15:10 open; 0.40% round-trip costs (small-cap level). Compared with the
same trades' daily-data result. Descriptive execution check on already-scored picks; no rule is chosen from it.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd

from stalk.config import IST

COST, STOP = 0.004, 0.03


def bars(symbol, day, cache={}):
    p = ROOT/f'data/pick_5m/{symbol}_{day}.pkl'
    if p.exists():
        f = pd.read_pickle(p)
    else:
        q = ROOT/f'data/kite_5m/{symbol}.pkl'
        if not q.exists():
            return None
        if symbol not in cache:
            g = pd.read_pickle(q)
            cache.clear()
            cache[symbol] = g
        g = cache[symbol]
        lo = int(pd.Timestamp(day+' 09:00', tz=IST).timestamp())
        f = g[(g.ts >= lo) & (g.ts < lo+7*3600)]
    if f.empty:
        return None
    hm = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
    return f.assign(hm=(hm.dt.hour*100+hm.dt.minute).values).set_index('hm').sort_index()


def walk(b, entry_hm):
    if entry_hm not in b.index or 1510 not in b.index:
        return None
    e = b.at[entry_hm, 'o']
    stop = e*(1-STOP)
    for hm, r in b.loc[entry_hm:1505].iterrows():
        if r.o <= stop:
            return (r.o-e)/e-COST, 'stop (gapped)'
        if r.l <= stop:
            return (stop-e)/e-COST, 'stop'
    return (b.at[1510, 'o']-e)/e-COST, 'close'


def main():
    sel = pd.concat([pd.read_csv(ROOT/'data/h17/strict_cleaned_selected.csv').assign(version='strict'),
                     pd.read_csv(ROOT/'data/h17/loose_cleaned_selected.csv').assign(version='loose')])
    acc = pd.concat([pd.read_csv(ROOT/'data/h17/strict_cleaned_stop3_account.csv'), pd.read_csv(ROOT/'data/h17/loose_cleaned_stop3_account.csv')])
    acc = acc.drop_duplicates(['day', 'symbol'])[['day', 'symbol', 'net_return']]
    sel = sel.drop_duplicates(['day', 'symbol']).merge(acc, on=['day', 'symbol'], how='inner')
    rows = []
    for r in sel.sort_values('symbol').itertuples():
        b = bars(r.symbol, r.day)
        if b is None or 915 not in b.index:
            continue
        a, b2 = walk(b, 915), walk(b, 920)
        if not a or not b2:
            continue
        rows.append(dict(day=r.day, symbol=r.symbol, official_open=r.open, kite_open=b.at[915, 'o'], move_0915_0920=b.at[920, 'o']/b.at[915, 'o']-1,
                         first_bar_range=(b.at[915, 'h']-b.at[915, 'l'])/b.at[915, 'o'], daily=r.net_return,
                         at_0915=a[0], how_0915=a[1], at_0920=b2[0], how_0920=b2[1]))
    t = pd.DataFrame(rows)
    t.to_csv(ROOT/'data/h17/panic_exec_check.csv', index=False)
    out = dict(picks_with_candles=len(t), of_picks=len(sel),
               kite_open_vs_official_median=round(float((t.kite_open/t.official_open-1).median()*100), 3),
               move_0915_to_0920_avg=round(float(t.move_0915_0920.mean()*100), 3), first_bar_range_avg=round(float(t.first_bar_range.mean()*100), 2),
               daily_data_bps=round(float(t.daily.mean()*1e4), 1), at_0915_bps=round(float(t.at_0915.mean()*1e4), 1),
               at_0920_bps=round(float(t.at_0920.mean()*1e4), 1), win_0920=round(float((t.at_0920 > 0).mean()), 3),
               gapped_stops_0920=int((t.how_0920 == 'stop (gapped)').sum()), stops_0920=int(t.how_0920.str.startswith('stop').sum()),
               by_year_0920_bps={k: round(float(v*1e4)) for k, v in t.groupby(t.day.str[:4]).at_0920.mean().items()})
    (ROOT/'data/h17/panic_exec_check.json').write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    main()
