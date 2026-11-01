"""H32 design lab, long side: buy the sector's leader after its first orderly pullback to VWAP (Astra, 2026-11-01).

Rule as written BEFORE the first run (trial 1), design window 2021-01..2025-07 only (the final year is refused):
  * At 10:00 (close of the 09:55 bar), each sector with >= 3 eligible stocks whose median return vs prior close is > 0
    nominates its leader: the stock with the largest lead over the sector median, if it is up >= 1.5% and leads by >= 1%.
  * Session VWAP from 09:15 using typical price (h+l+c)/3. Between 10:00 and 11:25 the leader must trade at or below
    VWAP (a bar low <= VWAP: the pullback), then a later bar must close above VWAP and above its own open (the reclaim).
  * Buy at the next bar's open; 1.5% stop; flatten at the 15:10 open. Max 2 a day, earliest reclaim first.
Counterparty: short-term profit takers selling the leader's first dip while sector money keeps rotating into it.
Every run is logged to data/h31_explore/trials_h32.csv.

    python scripts/h32_lab.py --build      # once: full-day close/volume arrays (~10 min)
    python scripts/h32_lab.py --variant base
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd

from scripts import h30_capitulation as h
from scripts import h31_lab as lab
from stalk.research.orb import normalize, universe

OUT = lab.OUT
G = h.GRID


def build_full_cv():
    meta = json.loads((OUT/'B_meta.json').read_text())
    cal, syms = meta['calendar'], meta['symbols']
    c = np.full((len(cal), len(G), len(syms)), np.nan, np.float32)
    v = np.full_like(c, np.nan)
    for j, s in enumerate(syms):
        f = normalize(pd.read_pickle(ROOT/f'data/kite_5m/{s}.pkl'))
        f = f[f.day >= cal[0]]
        for k, arr in (('c', c), ('v', v)):
            arr[:, :, j] = f.pivot_table(index='day', columns='hm', values=k, aggfunc='first').reindex(index=cal, columns=G).to_numpy(np.float32)
    np.save(OUT/'B_cfull.npy', c)
    np.save(OUT/'B_vfull.npy', v)


def load():
    B = lab.load()
    n = len(B['calendar'])
    B['cf'] = np.load(OUT/'B_cfull.npy', mmap_mode='r')[:n]
    B['vf'] = np.load(OUT/'B_vfull.npy', mmap_mode='r')[:n]
    sec = json.loads((ROOT/'data/sectors.json').read_text())
    B['sector'] = np.array([sec.get(s, '') for s in B['symbols']])
    return B


BASE = dict(snap=955, lead=0.01, up=0.015, last=1125, stop=0.015, max_trades=2, min_sector=3)
VARIANTS = {'base': {}}


def signals(B, p):
    out = []
    snap, last = G.index(p['snap']), G.index(p['last'])
    for i, d in enumerate(B['calendar']):
        if d < h.DESIGN_START:
            continue
        pc = B['pc'][i]
        elig = B['eligible'][i] & np.isfinite(pc)
        c = np.asarray(B['cf'][i], float); v = np.asarray(B['vf'][i], float)
        hi, lo = np.asarray(B['h'][i], float), np.asarray(B['l'][i], float)
        o = np.asarray(B['o'][i], float)
        r = c[snap]/pc-1
        leaders = []
        for sct in set(B['sector'][elig]):
            ks = np.flatnonzero(elig & (B['sector'] == sct) & np.isfinite(r))
            if len(ks) < p['min_sector']:
                continue
            med = float(np.median(r[ks]))
            if med <= 0:
                continue
            k = ks[np.argmax(r[ks])]
            if r[k] >= p['up'] and r[k]-med >= p['lead']:
                leaders.append((int(k), sct, float(r[k]-med)))
        cand = []
        for k, sct, lead in leaders:
            tp = (hi[:, k]+lo[:, k]+c[:, k])/3
            if not np.isfinite(tp[:last+1]).all() or not np.isfinite(v[:last+1, k]).all():
                continue
            cv = np.cumsum(tp*np.nan_to_num(v[:, k]))/np.maximum(np.cumsum(np.nan_to_num(v[:, k])), 1)
            touched = False
            for b in range(snap+1, last+1):
                if lo[b, k] <= cv[b]:
                    touched = True
                elif touched and c[b, k] > cv[b] and c[b, k] > o[b, k]:
                    cand.append((b, -lead, B['symbols'][k], k, sct))
                    break
        for b, nl, s, k, sct in sorted(cand)[:p['max_trades']]:
            out.append(dict(day=d, i=i, hm=G[b], symbol=s, k=k, side='long', sector=sct, lead=-nl))
    return out


def run(name):
    p = dict(BASE, **VARIANTS[name])
    B = load()
    picks = signals(B, p)
    rows = []
    for q in picks:
        r = lab.walk(B, q['i'], q['hm'], q['k'], 'long', p['stop'], None)
        rows.append(dict(q, **(r or {}), known=r is not None))
    t = pd.DataFrame(rows)
    unknown = set(t.loc[~t.known, 'day'])
    m = t[t.known & ~t.day.isin(unknown)]
    days = m.groupby('day').net.sum()
    res = dict(variant=name, trades=len(m), days=int(days.size), per_year=round(len(m)/4.58, 1),
               net_pct=round(m.net_pct.mean()*100, 3), gross_pct=round(m.gross_pct.mean()*100, 3),
               excess_pct=round(m.excess_pct.mean()*100, 3), win=round((m.net > 0).mean()*100, 1),
               avg_win=round(m.loc[m.net > 0, 'net'].mean(), 1), avg_loss=round(m.loc[m.net <= 0, 'net'].mean(), 1),
               net_rs=round(m.net.sum()), day_t=round(float(days.mean()/days.std()*np.sqrt(len(days))), 2),
               stops=round((m.reason == 'stop').mean()*100, 1), at=datetime.now().isoformat(timespec='seconds'),
               params=json.dumps(p))
    log = OUT/'trials_h32.csv'
    pd.DataFrame([res]).to_csv(log, mode='a', header=not log.exists(), index=False)
    m.drop(columns=['i', 'k']).to_csv(OUT/f'trades_h32_{name}.csv', index=False)
    print(json.dumps({k: v for k, v in res.items() if k != 'params'}))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--variant', nargs='*', default=[])
    a = ap.parse_args()
    if a.build:
        build_full_cv()
    for v in a.variant:
        run(v)
