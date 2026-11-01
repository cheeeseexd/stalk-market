"""H33: six popular intraday strategies from books and Indian trading YouTube / X, each run ONCE on 2021-01..2025-07.

Rules are written here before the first run; no tuning. Shared: 285 liquid NSE stocks (Kite 5-minute), signal on a
completed bar, entry at the next bar's open with tier slippage, the stop / target below, flatten at the 15:10 open,
max 2 trades a day (earliest signal first, then symbol), Rs5,000 slots, Zerodha MIS charges. The sealed year is refused.

  orb15     Opening-range breakout (Toby Crabel, "Day Trading with Short Term Price Patterns"): range = 09:15-09:30;
            first 5-min close beyond it between 09:30 and 11:00 -> trade that way; stop at the other side (max 3%).
  nr7_orb   Same breakout, only for stocks whose previous day had the narrowest range of the last 7 (Crabel NR7).
  cpr       Narrow-CPR breakout (Frank Ochoa, "Secrets of a Pivot Boss", very popular in India): previous day's
            central pivot range in the narrowest 20% of the stock's last 20 days; first close above the previous day's
            high (long) / below its low (short) between 09:20 and 11:00; stop at the pivot (max 3%).
  ema5      5-EMA (Subasish Pani / Power of Stocks): an alert candle entirely above the 5-period EMA of 5-minute
            closes; a later candle breaking the alert low -> short (mirror for longs below the EMA); stop at the alert
            candle's other end (max 3%), target 3x the risk. Signals 09:30-14:30.
  gapfill   Gap fill: gap of 1%-3% at the open, trade back toward the prior close at the 09:20 open (first bar
            done); target = prior close, stop = the gap size beyond entry (max 3%).
  vwap2sd   VWAP reversion: after 10:30, a 5-min close beyond VWAP +/- 2 standard deviations (volume-weighted, from
            09:15) -> fade it; target VWAP at entry time, stop 1%.

    python scripts/h33_lab.py --run orb15 nr7_orb cpr ema5 gapfill vwap2sd
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
from scripts import h32_lab as h32

G = h.GRID
IX = {hm: n for n, hm in enumerate(G)}
MAXSTOP = 0.03


def day_arrays(B, i):
    o, hi, lo = (np.asarray(B[k][i], float) for k in ('o', 'h', 'l'))
    c, v = np.asarray(B['cf'][i], float), np.asarray(B['vf'][i], float)
    return o, hi, lo, c, v


def daily(B):
    """Per (day, stock): high, low, close of the whole session from the 5-minute bars."""
    H = np.nanmax(np.asarray(B['h'], float), axis=1)
    L = np.nanmin(np.asarray(B['l'], float), axis=1)
    C = np.asarray(B['cf'][:, IX[1525]], float)
    return H, L, C


def first_cross(series_hit, start, end):
    """Index of the first True bar between start and end (inclusive), else -1, per stock column."""
    seg = series_hit[start:end+1]
    any_ = seg.any(axis=0)
    return np.where(any_, seg.argmax(axis=0)+start, -1)


def picks_orb(B, i, elig, nr7=None):
    o, hi, lo, c, v = day_arrays(B, i)
    rh, rl = np.nanmax(hi[:3], axis=0), np.nanmin(lo[:3], axis=0)
    ok = elig & np.isfinite(rh) & np.isfinite(rl) & (rl > 0)
    if nr7 is not None:
        ok &= nr7
    a, b = IX[930], IX[1055]
    up, dn = first_cross(c > rh, a, b), first_cross(c < rl, a, b)
    out = []
    for k in np.flatnonzero(ok):
        for t, side in ((up[k], 'long'), (dn[k], 'short')):
            if t >= 0:
                stop = min(MAXSTOP, abs(c[t, k]-(rl[k] if side == 'long' else rh[k]))/c[t, k])
                out.append((t, side, int(k), max(stop, 0.002), None))
        # only the first breakout of the day for this stock
        out = [x for x in out if x[2] != k] + sorted([x for x in out if x[2] == k])[:1]
    return out


def picks_cpr(B, i, elig, D):
    H, L, C = D
    o, hi, lo, c, v = day_arrays(B, i)
    if i < 21:
        return []
    P = (H[i-1]+L[i-1]+C[i-1])/3
    BC = (H[i-1]+L[i-1])/2
    width = np.abs(2*P-2*BC)/P
    hist = []
    for j in range(i-20, i):
        Pj = (H[j-1]+L[j-1]+C[j-1])/3
        hist.append(np.abs(2*Pj-2*(H[j-1]+L[j-1])/2)/Pj)
    narrow = width <= np.nanpercentile(np.array(hist), 20, axis=0)
    ok = elig & narrow & np.isfinite(P)
    a, b = IX[920], IX[1055]
    up, dn = first_cross(c > H[i-1], a, b), first_cross(c < L[i-1], a, b)
    out = []
    for k in np.flatnonzero(ok):
        cand = [(t, s) for t, s in ((up[k], 'long'), (dn[k], 'short')) if t >= 0]
        if cand:
            t, side = min(cand)
            stop = min(MAXSTOP, abs(c[t, k]-P[k])/c[t, k])
            out.append((t, side, int(k), max(stop, 0.002), None))
    return out


def picks_ema5(B, i, elig):
    o, hi, lo, c, v = day_arrays(B, i)
    ema = np.full_like(c, np.nan)
    ema[0] = c[0]
    for t in range(1, len(c)):
        ema[t] = np.where(np.isfinite(c[t]), ema[t-1]+(c[t]-ema[t-1])*2/6, ema[t-1])
    out = []
    a, b = IX[930], IX[1430]
    for k in np.flatnonzero(elig):
        alert = None
        for t in range(1, b+1):
            if alert is not None and t >= a:
                at, side = alert
                if side == 'short' and lo[t, k] < lo[at, k]:
                    risk = (hi[at, k]-lo[at, k])/lo[at, k]
                    if 0.001 < risk <= MAXSTOP:
                        out.append((t, 'short', int(k), risk, 3*risk))
                    break
                if side == 'long' and hi[t, k] > hi[at, k]:
                    risk = (hi[at, k]-lo[at, k])/hi[at, k]
                    if 0.001 < risk <= MAXSTOP:
                        out.append((t, 'long', int(k), risk, 3*risk))
                    break
            if lo[t, k] > ema[t, k]:
                alert = (t, 'short')
            elif hi[t, k] < ema[t, k]:
                alert = (t, 'long')
    return out


def picks_gapfill(B, i, elig):
    o, hi, lo, c, v = day_arrays(B, i)
    pc = B['pc'][i]
    gap = o[0]/pc-1
    out = []
    for k in np.flatnonzero(elig & np.isfinite(gap) & (np.abs(gap) >= 0.01) & (np.abs(gap) <= 0.03)):
        side = 'short' if gap[k] > 0 else 'long'
        g = abs(gap[k])
        out.append((IX[915], side, int(k), min(MAXSTOP, g), g*0.95))     # target ~ prior close from the 09:20 entry
    return out


def picks_vwap(B, i, elig):
    o, hi, lo, c, v = day_arrays(B, i)
    tp = (hi+lo+c)/3
    vv = np.nan_to_num(v)
    cv = np.cumsum(vv, axis=0)
    vw = np.cumsum(tp*vv, axis=0)/np.maximum(cv, 1)
    var = np.cumsum(vv*(tp-vw)**2, axis=0)/np.maximum(cv, 1)
    sd = np.sqrt(var)
    a, b = IX[1030], IX[1430]
    up = first_cross(c > vw+2*sd, a, b)
    dn = first_cross(c < vw-2*sd, a, b)
    out = []
    for k in np.flatnonzero(elig):
        cand = [(t, s) for t, s in ((up[k], 'short'), (dn[k], 'long')) if t >= 0]
        if cand:
            t, side = min(cand)
            tgt = abs(c[t, k]-vw[t, k])/c[t, k]
            if tgt > 0.001:
                out.append((t, side, int(k), 0.01, tgt))
    return out


def signals(B, name):
    D = daily(B) if name in ('cpr', 'nr7_orb') else None
    rng = None
    if name == 'nr7_orb':
        H, L, _ = D
        R = H-L
    out = []
    for i, d in enumerate(B['calendar']):
        if d < h.DESIGN_START:
            continue
        elig = B['eligible'][i] & np.isfinite(B['pc'][i])
        if elig.sum() < 20:
            continue
        if name == 'orb15':
            p = picks_orb(B, i, elig)
        elif name == 'nr7_orb':
            if i < 8:
                continue
            nr7 = R[i-1] <= np.nanmin(R[i-7:i], axis=0)
            p = picks_orb(B, i, elig, nr7=nr7)
        elif name == 'cpr':
            p = picks_cpr(B, i, elig, D)
        elif name == 'ema5':
            p = picks_ema5(B, i, elig)
        elif name == 'gapfill':
            p = picks_gapfill(B, i, elig)
        else:
            p = picks_vwap(B, i, elig)
        for t, side, k, stop, tgt in sorted(p, key=lambda x: (x[0], B['symbols'][x[2]]))[:2]:
            out.append(dict(day=d, i=i, hm=G[t], symbol=B['symbols'][k], k=k, side=side, stop=stop, target=tgt))
    return out


def run(name, B):
    picks = signals(B, name)
    rows = []
    for q in picks:
        if q['hm'] >= h.EXIT_HM:
            continue
        r = lab.walk(B, q['i'], q['hm'], q['k'], q['side'], q['stop'], q['target'])
        rows.append(dict(q, **(r or {}), known=r is not None, strategy=name))
    t = pd.DataFrame(rows)
    unknown = set(t.loc[~t.known, 'day'])
    m = t[t.known & ~t.day.isin(unknown)]
    days = m.groupby('day').net.sum()
    res = dict(strategy=name, trades=len(m), days=int(days.size), per_year=round(len(m)/4.58, 1),
               net_pct=round(m.net_pct.mean()*100, 3), gross_pct=round(m.gross_pct.mean()*100, 3),
               excess_pct=round(m.excess_pct.mean()*100, 3), win=round((m.net > 0).mean()*100, 1),
               avg_win=round(m.loc[m.net > 0, 'net'].mean(), 1), avg_loss=round(m.loc[m.net <= 0, 'net'].mean(), 1),
               net_rs=round(m.net.sum()), day_t=round(float(days.mean()/days.std()*np.sqrt(len(days))), 2),
               long_pct=round(m.loc[m.side == 'long', 'net_pct'].mean()*100, 3),
               short_pct=round(m.loc[m.side == 'short', 'net_pct'].mean()*100, 3),
               exits=m.reason.value_counts().to_dict(), at=datetime.now().isoformat(timespec='seconds'))
    log = lab.OUT/'trials_h33.csv'
    prior = pd.read_csv(log) if log.exists() else pd.DataFrame()
    pd.concat([prior, pd.DataFrame([dict(res, exits=json.dumps(res['exits']))])], ignore_index=True).to_csv(log, index=False)
    m.drop(columns=['i', 'k']).to_csv(lab.OUT/f'trades_h33_{name}.csv', index=False)
    print(json.dumps(res), flush=True)


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run', nargs='+', required=True)
    a = ap.parse_args()
    B = h32.load()
    for n in a.run:
        run(n, B)
