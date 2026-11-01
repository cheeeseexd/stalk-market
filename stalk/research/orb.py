"""Frozen H26 causal selection and bar execution. Research and paper share this code.

No broker object or order interface is accepted here. Amounts are before income tax.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path
import re

import numpy as np
import pandas as pd

from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST
from stalk.market.costs import fill_cost, CURRENT_EXCHANGE_PCT
from stalk.sim.panic import SLIP

VERSIONS = {'v1': .10, 'v2': .25, 'v3': .50}
STRESSES = {'assumed': 0., 'worse025': .0025, 'worse050': .005}
COLS = ['ts', 'o', 'h', 'l', 'c', 'v']


def universe(root=BASE_DIR):
    root = Path(root)
    meta = pd.read_csv(root/'data/wide_meta.csv')
    inv = json.loads((root/'data/h9/instrument_inventory.json').read_text(encoding='utf-8'))
    eq = set(inv['ordinary_equity_symbols'])
    funds = set(inv['etf_or_fund_symbols'])
    tiers = dict(zip(meta.symbol, meta['index']))
    tiers.update({s: 'nifty50' for s in DEFAULT_UNIVERSE})
    return {s: t for s, t in sorted(tiers.items()) if s in eq and s not in funds
            and not re.search(r'BEES$|IETF$|ETF|REIT|INVIT', s, re.I)}


def normalize(frame):
    f = pd.DataFrame(frame, columns=COLS).copy() if not isinstance(frame, pd.DataFrame) else frame[COLS].copy()
    f = f.drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
    for c in COLS:
        f[c] = pd.to_numeric(f[c], errors='coerce')
    f = f.dropna()
    f = f[(f[['o', 'h', 'l', 'c']] > 0).all(axis=1) & (f.v >= 0)
          & (f.h >= f[['o', 'c', 'l']].max(axis=1)) & (f.l <= f[['o', 'c', 'h']].min(axis=1))]
    dt = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
    f['day'], f['hm'] = dt.dt.strftime('%Y-%m-%d'), dt.dt.hour*100+dt.dt.minute
    return f[(f.hm >= 915) & (f.hm <= 1525)].reset_index(drop=True)


def prepare(frames, tiers):
    """Features shifted before selection. Later same-day data never enter morning picks."""
    normalized = {s: normalize(frames[s]) for s in sorted(set(frames) & set(tiers))}
    calendar = sorted({d for f in normalized.values() for d in f.day})
    data, features = {}, {}
    for symbol in sorted(set(frames) & set(tiers)):
        f = normalized[symbol]
        groups = {d: g for d, g in f.groupby('day', sort=True)}
        data[symbol] = groups
        daily = f.groupby('day').agg(o=('o', 'first'), h=('h', 'max'), l=('l', 'min'),
                                     c=('c', 'last'), n=('ts', 'size'), first=('hm', 'min'), last=('hm', 'max'))
        daily = daily.reindex(calendar)
        daily['turnover'] = (f.c*f.v).groupby(f.day).sum()
        daily['or_volume'] = f[f.hm == 915].set_index('day').v
        complete = (daily.n >= 70) & (daily['first'] == 915) & (daily['last'] >= 1525)
        pc = daily.c.shift()
        tr = pd.concat([daily.h-daily.l, (daily.h-pc).abs(), (daily.l-pc).abs()], axis=1).max(axis=1)
        tr[pc.isna()] = np.nan
        # Do not let unverified split/bonus-scale jumps manufacture ATR eligibility.
        coherent = (daily.o/pc-1).abs() <= .10
        daily['atr'] = tr.where(complete & coherent).rolling(14, min_periods=14).mean().shift()
        daily['avg_turnover'] = daily.turnover.where(complete).rolling(14, min_periods=14).mean().shift()
        daily['avg_or_volume'] = daily.or_volume.where(complete).rolling(14, min_periods=14).mean().shift()
        features[symbol] = daily[['atr', 'avg_turnover', 'avg_or_volume']]
    return data, features


def event_exclusions(root, day):
    """Only same-day result notices known at the 09:20 decision, no retrospective veto."""
    p = Path(root)/'data/wide_events.pkl'
    if not p.exists():
        return set(), 'unknown'
    ev = pd.read_pickle(p)
    text = ev.purpose.fillna('')+' '+ev.detail.fillna('')
    cutoff = int(datetime.fromisoformat(day+'T09:20:00').replace(tzinfo=IST).timestamp())
    known = ev.announced_ts.notna() & (ev.announced_ts < cutoff)
    return set(ev.loc[(ev.day == day) & known & text.str.contains('result', case=False), 'symbol']), 'stored calendar, completeness unknown'


def select(day, data, features, tiers, excluded=()):
    pool = []
    for s in sorted(data):
        if s in excluded or day not in data[s] or day not in features[s].index:
            continue
        first = data[s][day]
        first = first[first.hm == 915]
        if len(first) != 1:
            continue
        b = first.iloc[0]
        r = features[s].loc[day]
        if r.isna().any() or b.o < 50 or r.avg_turnover < 1e8 or r.atr < .015*b.o or r.avg_or_volume <= 0 or b.h == b.l:
            continue
        rv = float(b.v/r.avg_or_volume)
        pool.append(dict(day=day, symbol=s, tier=tiers[s], atr=float(r.atr), rv=rv,
                         high=float(b.h), low=float(b.l), side='long' if b.c > b.o else 'short' if b.c < b.o else None))
    ranked = sorted((p for p in pool if p['rv'] >= 1 and p['side']), key=lambda p: (-p['rv'], p['symbol']))
    return ranked[:2], pool


def random_picks(pool, rng):
    if not pool:
        return []
    return [dict(pool[int(i)], side='long' if rng.integers(2) else 'short')
            for i in rng.choice(len(pool), size=min(2, len(pool)), replace=False)]


def walk(pick, frame, fraction, adverse=0., final=True):
    """Returns status + paper fill, and a closed trade when observable.

    Stop first on ambiguous entry candles; missing earlier path never inferred.
    Live caller supplies only completed bars (15:10 open may be supplied separately).
    """
    sign = 1 if pick['side'] == 'long' else -1
    boundary = pick['high'] if sign == 1 else pick['low']
    slip = SLIP.get(pick['tier'], .0006)
    bars = frame[frame.hm >= 920].sort_values('ts')
    trade = None
    previous = None
    for b in bars.itertuples():
        if b.hm > 1510:
            break
        if previous is None:
            if b.hm != 920:
                return dict(status='unknown', trade=None, fill=None)
        elif int(b.ts)-previous != 300:
            return dict(status='unknown', trade=None, fill=trade)
        previous = int(b.ts)
        if trade is None and b.hm <= 1130 and (b.h >= boundary if sign == 1 else b.l <= boundary):
            raw = max(boundary, b.o) if sign == 1 else min(boundary, b.o)
            entry = raw*(1+sign*(slip+adverse))
            qty = math.floor(5000/entry)
            if qty <= 0:
                return dict(status='unfundable', trade=None, fill=None)
            stop = entry-sign*fraction*pick['atr']
            trade = dict(**pick, qty=qty, entry=entry, entry_ts=int(b.ts), stop=stop)
        if trade:
            if b.hm == 1510:
                exit_raw, reason = b.o, 'flatten'
            elif b.l <= trade['stop'] if sign == 1 else b.h >= trade['stop']:
                exit_raw = min(trade['stop'], b.o) if sign == 1 else max(trade['stop'], b.o)
                # The entry candle's open precedes entry: it cannot gap through a later stop.
                if int(b.ts) == trade['entry_ts']:
                    exit_raw = trade['stop']
                reason = 'stop'
            else:
                continue
            exit_price = exit_raw*(1-sign*slip)
            q, entry = trade['qty'], trade['entry']
            cost = (fill_cost('BUY' if sign == 1 else 'SELL', q, entry, exchange_pct=CURRENT_EXCHANGE_PCT).total
                    + fill_cost('SELL' if sign == 1 else 'BUY', q, exit_price, exchange_pct=CURRENT_EXCHANGE_PCT).total)
            gross = sign*(exit_price-entry)*q
            result = dict(trade, exit=exit_price, exit_ts=int(b.ts), reason=reason,
                          gross=gross, costs=cost, net=gross-cost, net_pct=(gross-cost)/(entry*q)*100)
            return dict(status='closed', trade=result, fill=trade)
        if b.hm >= 1130 and trade is None:
            return dict(status='no_trigger', trade=None, fill=None)
    return dict(status='unknown' if final else 'pending', trade=None, fill=trade)


def tstat(values):
    a = np.array(values, dtype=float)
    return float(a.mean()/a.std(ddof=1)*np.sqrt(len(a))) if len(a) > 1 and a.std(ddof=1) else None


def metrics(trades, days, unknown=()):
    ts = pd.DataFrame(trades)
    known = [d for d in days if d not in set(unknown)]
    daily = {d: sum(t['net'] for t in trades if t['day'] == d) for d in known}
    amounts = np.array(list(daily.values()))
    curve = np.r_[10000., 10000.+amounts.cumsum()]
    wins = [t['net'] for t in trades if t['net'] > 0]
    losses = [t['net'] for t in trades if t['net'] <= 0]
    return dict(trades=len(ts), independent_days=len({t['day'] for t in trades}), sessions=len(days),
                known_days=len(known), unknown_days=len(set(unknown)), trades_per_day=len(ts)/len(known) if known else None,
                days_traded_share=len({t['day'] for t in trades})/len(known) if known else None,
                win_rate=len(wins)/len(ts) if len(ts) else None, avg_win_rs=float(np.mean(wins)) if wins else None,
                avg_loss_rs=float(np.mean(losses)) if losses else None,
                net_pct=float(ts.net_pct.mean()) if len(ts) else None,
                net=float(ts.net.sum()) if len(ts) else 0. if known else None,
                day_t=tstat(amounts), max_drawdown_rs=float((np.maximum.accumulate(curve)-curve).max()) if known else None,
                daily=daily)
