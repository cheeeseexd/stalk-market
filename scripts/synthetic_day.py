"""A blind practice day: Drex sets the odds, a hidden 5-minute day is generated and sealed, traders commit plans after only the
first candle, then the day is played fast. A drill, not evidence: the day is synthetic.

    python scripts/synthetic_day.py make REDINGTON POONAWALLA      # Drex odds + sealed day; prints only the first candle and a hash
    python scripts/synthetic_day.py play <run_id>                   # plays the sealed day against data/synthetic/<run_id>/plans.json
How the hidden day is built: for each stock, its last 120 real sessions (Kite 5-minute) are reduced to shapes (opening gap, then
every bar relative to the open). Drex gives P(opens above Friday's close), P(closes above its open), P(moves more than 1.5% open to
close). Each outcome is drawn from those odds with a secret random seed, and one real session whose shape matches all three is
picked and rescaled to Friday's close. The full day goes to sealed.json; the trader only ever sees first_candle.json.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT/'.env')
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from stalk.config import IST  # noqa: E402
from stalk.market.costs import round_trip_cost  # noqa: E402

OUT = ROOT/'data/synthetic'
FRIDAY, MONDAY = '2026-10-30', '2026-11-02'


def sessions(symbol, n=120):
    f = pd.read_pickle(ROOT/f'data/kite_5m/{symbol}.pkl')
    d = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
    f = f.assign(day=d.dt.strftime('%Y-%m-%d'), hm=d.dt.hour*100+d.dt.minute)
    f = f[f.day <= FRIDAY]
    days = sorted(f.day.unique())[-(n+1):]
    g = {k: v.sort_values('hm') for k, v in f[f.day.isin(days)].groupby('day')}
    out = []
    for a, b in zip(days[:-1], days[1:]):
        prev, cur = g[a], g[b]
        if len(cur) < 70 or cur.hm.iloc[0] != 915:
            continue
        o = cur.o.iloc[0]
        out.append(dict(source_day=b, gap=float(o/prev.c.iloc[-1]-1), up_close=bool(cur.c.iloc[-1] > o), big=bool(abs(cur.c.iloc[-1]/o-1) > 0.015),
                        bars=(cur[['hm', 'o', 'h', 'l', 'c', 'v']].assign(o=cur.o/o, h=cur.h/o, l=cur.l/o, c=cur.c/o)).values.tolist()))
    return out, float(g[days[-1]].c.iloc[-1])


async def drex_odds(symbol, close, friday_move, ret5):
    from stalk.llm.drex import DrexClient
    q = {"gap_up": {"type": "noul", "instructions": f"On Monday 2 Nov 2026, NSE:{symbol} opens above Friday's close of ₹{close:,.2f}.",
                    "criteria": {"true": "Opens higher", "false": "Opens at or below"}},
         "close_up": {"type": "noul", "instructions": f"On Monday 2 Nov 2026, NSE:{symbol} closes above its own opening price.",
                      "criteria": {"true": "Closes above the open", "false": "Closes at or below the open"}},
         "big_move": {"type": "noul", "instructions": f"On Monday 2 Nov 2026, NSE:{symbol} moves more than 1.5% between its open and its close (either way).",
                      "criteria": {"true": "More than 1.5%", "false": "1.5% or less"}}}
    state = {"task": "Forecast one Indian stock's next session from its recent facts.", "stock": symbol,
             "facts": {"friday_close": close, "friday_move_pct": round(friday_move*100, 2), "five_day_move_pct": round(ret5*100, 2),
                       "market_note": "NIFTY 50 closed Fri 30 Oct 2026 at 23,053 (+0.51%)."}}
    a = await DrexClient(timeout_s=40).ask(state, q)
    return {k: a.noul(k) for k in q} if a.ok else {"error": a.error}


def make(symbols):
    run = datetime.now(IST).strftime('%Y%m%d_%H%M%S')
    d = OUT/run
    d.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(secrets.randbits(64))         # secret: never printed or saved
    sealed, first, odds = {}, {}, {}
    for s in symbols:
        shapes, close = sessions(s)
        f = pd.read_pickle(ROOT/f'data/kite_5m/{s}.pkl')
        dd = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST).dt.strftime('%Y-%m-%d')
        daily = f.groupby(dd).c.last()
        fri_move, ret5 = float(daily.iloc[-1]/daily.iloc[-2]-1), float(daily.iloc[-1]/daily.iloc[-6]-1)
        p = asyncio.run(drex_odds(s, close, fri_move, ret5))
        odds[s] = p
        want = dict(up=rng.random() < (p.get('gap_up') or 0.5), close=rng.random() < (p.get('close_up') or 0.5), big=rng.random() < (p.get('big_move') or 0.5))
        pool = [x for x in shapes if (x['gap'] > 0) == want['up'] and x['up_close'] == want['close'] and x['big'] == want['big']] \
            or [x for x in shapes if (x['gap'] > 0) == want['up'] and x['up_close'] == want['close']] or shapes
        pick = pool[int(rng.integers(len(pool)))]
        o = close*(1+pick['gap'])
        bars = [[int(r[0]), round(o*r[1], 2), round(o*r[2], 2), round(o*r[3], 2), round(o*r[4], 2), int(r[5])] for r in pick['bars']]
        sealed[s] = dict(friday_close=close, bars=bars, shape_from=pick['source_day'])
        first[s] = dict(friday_close=close, friday_move_pct=round(fri_move*100, 2), five_day_move_pct=round(ret5*100, 2),
                        drex=p, first_candle_0915=dict(zip(['hm', 'o', 'h', 'l', 'c', 'v'], bars[0])))
    blob = json.dumps(sealed, sort_keys=True).encode()
    (d/'sealed.json').write_bytes(blob)
    (d/'first_candle.json').write_text(json.dumps(first, indent=1), encoding='utf-8')
    print(json.dumps(dict(run=run, sealed_sha256=hashlib.sha256(blob).hexdigest(), visible=first), indent=1))


def play(run, speed=50.0):
    d = OUT/run
    sealed = json.loads((d/'sealed.json').read_text())
    plans = json.loads((d/'plans.json').read_text())       # {trader: {symbol: {side, stop, target}}}; entry = 09:20 open
    book = {}
    for trader, ps in plans.items():
        for s, pl in ps.items():
            if pl.get('side') in ('long', 'short'):
                book[(trader, s)] = dict(pl, open=True, entry=None, exit=None, why=None)
    hms = [b[0] for b in sealed[next(iter(sealed))]['bars']]
    print(f"Playing {MONDAY} (synthetic) at {speed:.0f}x: 09:15 → 15:10 in about {len(hms)*300/speed/60:.1f} minutes")
    for hm in hms:
        if hm > 1510:
            break
        for (trader, s), p in book.items():
            bar = next((b for b in sealed[s]['bars'] if b[0] == hm), None)
            if bar is None or not p['open']:
                continue
            _, o, hi, lo, c, v = bar
            if hm == 920:
                p['entry'] = o
                sg = 1 if p['side'] == 'long' else -1
                if 'stop_pct' in p:                                  # plans are committed as % of the (unknown) 09:20 entry
                    p['stop'] = round(o*(1-sg*p['stop_pct']), 2)
                if p.get('target_pct'):
                    p['target'] = round(o*(1+sg*p['target_pct']), 2)
                continue
            if p['entry'] is None:
                continue
            lg = p['side'] == 'long'
            if hm == 1510:
                p.update(exit=o, why='15:10', open=False)
            elif (lo <= p['stop']) if lg else (hi >= p['stop']):
                p.update(exit=(min(o, p['stop']) if lg else max(o, p['stop'])), why='stop', open=False)
            elif p.get('target') and ((hi >= p['target']) if lg else (lo <= p['target'])):
                p.update(exit=(max(o, p['target']) if lg else min(o, p['target'])), why='target', open=False)
        line = ' | '.join(f"{t}:{s} {'open' if p['open'] else p['why']} {((1 if p['side'] == 'long' else -1)*((p['exit'] or next(b for b in sealed[s]['bars'] if b[0] == hm)[4])-p['entry'])/p['entry']*100 if p['entry'] else 0):+.2f}%"
                          for (t, s), p in book.items())
        print(f"{hm//100:02d}:{hm % 100:02d}  {line}", flush=True)
        time.sleep(300/speed)
    res = []
    for (t, s), p in book.items():
        qty = int(5000 // p['entry'])
        sg = 1 if p['side'] == 'long' else -1
        buy, sell = (p['entry'], p['exit']) if sg > 0 else (p['exit'], p['entry'])
        gross = sg*(p['exit']-p['entry'])*qty
        net = gross-float(round_trip_cost(qty, buy, sell))
        res.append(dict(trader=t, symbol=s, side=p['side'], qty=qty, entry=p['entry'], stop=p['stop'], target=p.get('target'),
                        exit=p['exit'], how=p['why'], net=round(net, 2)))
    (d/'result.json').write_text(json.dumps(dict(results=res, shapes_from={s: v['shape_from'] for s, v in sealed.items()}), indent=1))
    print(json.dumps(res, indent=1))


if __name__ == '__main__':
    if sys.argv[1] == 'make':
        make(sys.argv[2:])
    else:
        play(sys.argv[2])
