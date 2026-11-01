"""H31 design lab: try one change at a time to the H30 rule on the 2021-01..2025-07 design window ONLY.

The sealed final year (2025-08..2026-07) is refused here; it is opened once, by a separate registered scorer.
Every variant run is appended to data/h31_explore/trials.csv, so the number of tries is always known.

    python scripts/h31_lab.py --build            # once: H30 arrays -> data/h31_explore/B_*.npy (~10 min)
    python scripts/h31_lab.py --variant base     # see VARIANTS below
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
from stalk.research.orb import universe
from stalk.sim.panic import SLIP

OUT = ROOT/'data/h31_explore'
DESIGN_END = '2025-07-31'
ARRAYS = ('pc', 'eligible', 'vavg', 'o', 'h', 'l', 'c', 'v')
SECTORS = json.loads((ROOT/'data/sectors.json').read_text()) if (ROOT/'data/sectors.json').exists() else {}


def build_and_save():
    tiers = universe()
    # The stock files now hold 2015 onward; keep only the lab's years (from the warm-up) so the build fits in memory
    cut = int(pd.Timestamp(h.CALENDAR_START, tz='Asia/Kolkata').timestamp())
    frames = {}
    for p in sorted((ROOT/'data/kite_5m').glob('*.pkl')):
        if p.stem in tiers:
            f = pd.read_pickle(p)
            frames[p.stem] = f[f.ts >= cut]
    B = h.build(frames, tiers, causal=True)      # eligibility known at 09:15 (Astra audit fix)
    OUT.mkdir(parents=True, exist_ok=True)
    for k in ARRAYS:
        np.save(OUT/f'B_{k}.npy', B[k])
    (OUT/'B_meta.json').write_text(json.dumps(dict(symbols=B['symbols'], calendar=B['calendar'], tiers=B['tiers'])))


def load():
    meta = json.loads((OUT/'B_meta.json').read_text())
    keep = [i for i, d in enumerate(meta['calendar']) if d <= DESIGN_END]       # the lab never sees the final year
    B = dict(meta, calendar=[meta['calendar'][i] for i in keep])
    for k in ARRAYS:
        B[k] = np.load(OUT/f'B_{k}.npy', mmap_mode='r')[keep[0]:keep[-1]+1]
    return B


def signals(B, p):
    """H30 signal with variant knobs: sides, move, volume ratio, signal bars, bar-close condition, max a day."""
    cal, syms = B['calendar'], B['symbols']
    bars = [hm for hm in h.SIGNAL if p['first_bar'] <= hm <= p['last_bar']]
    out = []
    for i, d in enumerate(cal):
        if not (p.get('start', h.DESIGN_START) <= d <= p.get('end', DESIGN_END)) or not B['eligible'][i].any() \
                or (p.get('days') is not None and d not in p['days']):
            continue
        elig = B['eligible'][i].copy()
        for s in p.get('excluded', {}).get(d, ()):            # same-day results notices known before 09:20
            if s in syms:
                elig[syms.index(s)] = False
        taken, count = set(), 0
        for hm in bars:
            j, b = h.SIGNAL.index(hm), h.GRID.index(hm)
            c, v = np.asarray(B['c'][i, j], float), np.asarray(B['v'][i, j], float)
            hi, lo = np.asarray(B['h'][i, b], float), np.asarray(B['l'][i, b], float)
            ret = c/B['pc'][i]-1
            ok = elig & np.isfinite(ret)
            if ok.sum() < 20:
                continue
            market = float(np.median(ret[ok]))
            ratio = v/np.asarray(B['vavg'][i, j], float)
            spike = ok & np.isfinite(ratio) & (ratio >= p['vol_x'])
            mid = (hi+lo)/2
            cand = []
            if p.get('isolated') is not None:            # the stock moves alone: its sector's median is below this
                sec = B.setdefault('_sector', np.array([SECTORS.get(s, '') for s in syms]))
                smed = np.full(len(syms), np.nan)
                for sc in set(sec[ok]):
                    mem = ok & (sec == sc)
                    if mem.sum() >= 3:
                        smed[mem] = np.median(ret[mem])
                spike = spike & np.isfinite(smed) & (smed < p['isolated'])
            if 'short' in p['sides']:
                hit = spike & (ret >= p['move']) & (market < p['calm'])
                if p['close_half']:
                    hit &= c <= mid
                cand += [(-float(ratio[k]), syms[k], 'short', int(k)) for k in np.flatnonzero(hit)]
            if 'long' in p['sides']:
                hit = spike & (ret <= -p['move']) & (market > -p['calm'])
                if p['close_half']:
                    hit &= c >= mid
                cand += [(-float(ratio[k]), syms[k], 'long', int(k)) for k in np.flatnonzero(hit)]
            for nr, s, side, k in sorted(cand):
                if count >= p['max_trades'] or s in taken:
                    continue
                taken.add(s)
                count += 1
                out.append(dict(day=d, i=i, hm=hm, symbol=s, k=k, side=side, ratio=-nr, ret=float(ret[k]), market=market))
            if count >= p['max_trades']:
                break
    return out


def walk(B, i, hm, k, side, stop, target, exit_hm=h.EXIT_HM, breakeven=None, time_exit=None, stress=0.0):
    """One trade: next-bar open entry with tier slippage; stop / target checked bar by bar (stop first); flatten at exit_hm open.
    breakeven: once a bar's close is this far in profit, the stop moves to the entry price.
    time_exit: (hm) leave at that bar's open if the trade is not in profit by then."""
    e, x = h.GRID.index(hm)+1, h.GRID.index(exit_hm)
    o = np.asarray(B['o'][i, e:x+1, k], float)
    hi, lo = np.asarray(B['h'][i, e:x, k], float), np.asarray(B['l'][i, e:x, k], float)
    if not np.isfinite(o[0]):
        return None                                   # no print at the entry bar: no trade (known at the time)
    prev = float(B['o'][i, e-1, k]) if e > 0 and np.isfinite(B['o'][i, e-1, k]) else np.nan
    if np.isfinite(prev) and prev > 0 and abs(o[0]/prev-1) > 0.20:
        return None                                   # a >20% jump from the previous bar is a bad print, not a price (PIIND 2018-12-11)
    # A bar with no trades keeps the last price (it can't stop us out); the exit uses the last price at or before 15:10.
    # Earlier versions dropped such trades, which hid stocks that froze or stopped trading (Astra audit 2026-11-01).
    o = pd.Series(o).ffill().to_numpy()
    hi = np.where(np.isfinite(hi), hi, o[:-1])
    lo = np.where(np.isfinite(lo), lo, o[:-1])
    slip = SLIP.get(B['tiers'][k], 0.0006)
    sg = 1.0 if side == 'long' else -1.0
    entry = o[0]*(1+sg*slip)*(1+sg*stress)                 # stress: entry this much worse (0.25% / 0.5% checks)
    sp = entry*(1-sg*stop) if stop else None
    tp = entry*(1+sg*target) if target else None
    px, why = o[-1], 'flatten'
    te = h.GRID.index(time_exit)-e if time_exit else None
    cl = np.asarray(B['o'][i, e+1:x+1, k], float)          # next bar's open stands in for this bar's close
    for t in range(len(hi)):
        if te is not None and t == te and sg*(o[t]-entry) <= 0:
            px, why = o[t], 'time'; break
        if sp is not None and (sg*(o[t]-sp) <= 0):
            px, why = o[t], 'stop'; break
        if tp is not None and (sg*(o[t]-tp) >= 0):
            px, why = o[t], 'target'; break
        if sp is not None and ((lo[t] <= sp) if side == 'long' else (hi[t] >= sp)):
            px, why = sp, 'stop'; break
        if tp is not None and ((hi[t] >= tp) if side == 'long' else (lo[t] <= tp)):
            px, why = tp, 'target'; break
        if breakeven and sg*(cl[t]-entry) >= breakeven*entry and (sp is None or sg*(entry-sp) > 0):
            sp = entry                                       # from the next bar on, a loser can't come back past entry
    exit_px = px*(1-sg*slip)
    qty = np.floor(h.SLOT/entry)
    if qty <= 0:
        return None
    buy, sell = (entry, exit_px) if side == 'long' else (exit_px, entry)
    gross = sg*(exit_px-entry)*qty
    cost = float(h.round_trip_cost(qty, buy, sell))
    # The equal-weight market over the same window: what the trade made beyond the day's drift
    mo = np.asarray(B['o'][i, e, :], float); mx = np.asarray(B['o'][i, x, :], float)
    m = B['eligible'][i] & np.isfinite(mo) & np.isfinite(mx) & (mo > 0)
    mkt = float(np.mean(mx[m]/mo[m]-1)) if m.sum() >= 20 else np.nan
    return dict(entry=entry, exit=exit_px, qty=qty, gross=gross, costs=cost, net=gross-cost, net_pct=(gross-cost)/(qty*entry),
                gross_pct=gross/(qty*entry), reason=why, mkt_pct=mkt, excess_pct=gross/(qty*entry)-sg*mkt)


BASE = dict(sides=('short', 'long'), move=0.03, vol_x=3.0, calm=0.005, close_half=True, first_bar=930, last_bar=1055,
            max_trades=2, stop=0.015, target=None)
VARIANTS = {
    'base': {},                                       # H30 as scored (sanity check: must match its design numbers)
    'short_only': dict(sides=('short',)),
    'short_stop3': dict(sides=('short',), stop=0.03),
    'short_stop5': dict(sides=('short',), stop=0.05),
    'short_nostop': dict(sides=('short',), stop=None),
    'short_tgt2_stop3': dict(sides=('short',), stop=0.03, target=0.02),
    # Exit family, compared together (Sol round 16): all keep the per-trade risk at or under Rs100 on a Rs5,000 slot
    'short_s15_t2': dict(sides=('short',), target=0.02),
    'short_s15_t3': dict(sides=('short',), target=0.03),
    'short_s15_be1': dict(sides=('short',), breakeven=0.01),
    'short_s15_time1130': dict(sides=('short',), time_exit=1130),
    'short_s2': dict(sides=('short',), stop=0.02),
    'short_vol10': dict(sides=('short',), vol_x=10.0),
    'short_vol10_isolated': dict(sides=('short',), vol_x=10.0, isolated=0.01),   # trial 11, Astra r1 idea 2
    'short_vol10_stop3': dict(sides=('short',), vol_x=10.0, stop=0.03),        # trial 12, user's choice of a 3% stop
}


def control(B, q, p):
    """Sol's matched control: other eligible stocks that had moved just as far (same side, >= move) at the same bar,
    without the volume / candle conditions, traded with the same exits. Mean gross %, or NaN if none."""
    j, i = h.SIGNAL.index(q['hm']), q['i']
    ret = np.asarray(B['c'][i, j], float)/B['pc'][i]-1
    same = (ret >= p['move']) if q['side'] == 'short' else (ret <= -p['move'])
    ks = [k for k in np.flatnonzero(B['eligible'][i] & np.isfinite(ret) & same) if k != q['k']]
    vals = [r['gross_pct'] for k in ks if (r := walk(B, i, q['hm'], k, q['side'], p['stop'], p['target'],
                                                      breakeven=p.get('breakeven'), time_exit=p.get('time_exit')))]
    return float(np.mean(vals)) if vals else np.nan


def run(name):
    p = dict(BASE, **VARIANTS[name])
    B = load()
    picks = signals(B, p)
    rows = []
    for q in picks:
        r = walk(B, q['i'], q['hm'], q['k'], q['side'], p['stop'], p['target'], breakeven=p.get('breakeven'), time_exit=p.get('time_exit'))
        ctl = control(B, q, p) if r is not None else np.nan
        rows.append(dict(q, **(r or {}), known=r is not None, control_pct=ctl))
    t = pd.DataFrame(rows)
    unknown = set(t.loc[~t.known, 'day'])
    m = t[t.known & ~t.day.isin(unknown)]
    days = m.groupby('day').net.sum()
    res = dict(variant=name, trades=len(m), days=int(days.size), per_year=round(len(m)/4.58, 1),
               net_pct=round(m.net_pct.mean()*100, 3), gross_pct=round(m.gross_pct.mean()*100, 3),
               excess_pct=round(m.excess_pct.mean()*100, 3), win=round((m.net > 0).mean()*100, 1),
               vs_control_pct=round((m.gross_pct-m.control_pct).mean()*100, 3), controlled=int(m.control_pct.notna().sum()),
               avg_win=round(m.loc[m.net > 0, 'net'].mean(), 1), avg_loss=round(m.loc[m.net <= 0, 'net'].mean(), 1),
               net_rs=round(m.net.sum()), day_t=round(float(days.mean()/days.std()*np.sqrt(len(days))), 2),
               stops=round((m.reason == 'stop').mean()*100, 1), at=datetime.now().isoformat(timespec='seconds'),
               params=json.dumps({k: v for k, v in p.items() if k != 'days'}))
    log = OUT/'trials.csv'
    prior = pd.read_csv(log) if log.exists() else pd.DataFrame()
    res = dict(trial=len(prior)+1, **{k: v for k, v in res.items() if k != 'params'})
    pd.concat([prior, pd.DataFrame([res])], ignore_index=True).to_csv(log, index=False)
    m.drop(columns=['i', 'k']).to_csv(OUT/f'trades_{name}.csv', index=False)
    print(json.dumps({k: v for k, v in res.items() if k != 'params'}))
    return m


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--build', action='store_true')
    ap.add_argument('--variant', nargs='*', default=[])
    a = ap.parse_args()
    if a.build:
        build_and_save()
    for v in a.variant:
        run(v)
