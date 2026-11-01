"""Guarded synthetic practice, with separate prepare / commit / score / publish steps.

No orders, credential files, live rule changes, or sealed-year analysis.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import httpx
import numpy as np
import pandas as pd
from stalk.config import IST
from scripts.publish_run import publish, record_cycle

OUT = ROOT / 'data/synthetic/20261101_random_pair'
DAY = '2026-11-02'
COLS = ['day', 'symbol', 'side', 'hm', 'entry_hm', 'exit_hm', 'qty', 'entry', 'exit',
        'reason', 'gross', 'costs', 'net', 'net_pct']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def cost_rate(tier):
    return .0015 if tier in ('nifty50', 'next50') else .0025 if tier == 'midcap' else .004


async def odds(symbol, close):
    prompts = {
        'gap_up': f'On Monday 2 Nov 2026, will {symbol} open above its stored Friday close of Rs{close:.2f}? This is an opinion for a synthetic practice generator.',
        'close_up': f'On Monday 2 Nov 2026, will {symbol} close above its opening price? This is an opinion for a synthetic practice generator.',
        'big_move': f'On Monday 2 Nov 2026, will {symbol} move more than 1.5% between its open and close in either direction? This is an opinion for a synthetic practice generator.',
    }
    async def one(key, question):
        try:
            async with httpx.AsyncClient(timeout=70) as client:
                r = await client.post('http://127.0.0.1:8080/api/drex/ask', json={'question': question})
            r.raise_for_status()
            a = r.json()
            p = float(a['p'])
            if not 0 <= p <= 1:
                raise ValueError('invalid probability')
            return key, {'p': p, 'source': 'Drex opinion', 'question': question, 'used': a.get('used', [])}
        except Exception as e:
            return key, {'p': None, 'generator_p': .5, 'source': 'unavailable; neutral generator only', 'error_type': type(e).__name__}
    return dict(await asyncio.gather(*(one(k, q) for k, q in prompts.items())))


def source_inventory(symbol):
    f = pd.read_pickle(ROOT / f'data/kite_5m/{symbol}.pkl')
    d = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
    # Exclude the sealed year before grouping, returns, or selection.
    dev = f.loc[(d >= '2024-01-01') & (d < '2025-08-01')].copy()
    recent = f.loc[(d >= '2026-08-01') & (d < '2026-10-31')].copy()
    del f
    for part in (dev, recent):
        dt = pd.to_datetime(part.ts, unit='s', utc=True).dt.tz_convert(IST)
        part['day'], part['hm'] = dt.dt.strftime('%Y-%m-%d'), dt.dt.hour * 100 + dt.dt.minute
    daily = recent.sort_values('ts').groupby('day').c.last()
    if len(daily) < 6 or daily.index[-1] != '2026-10-30':
        raise ValueError(f'{symbol}: missing Friday/five-session anchor; no replacement')
    anchor = float(daily.iloc[-1])
    if not np.isfinite(anchor) or anchor < 50:
        raise ValueError(f'{symbol}: invalid anchor; no replacement')
    groups = {day: g.sort_values('hm') for day, g in dev.groupby('day')}
    days = sorted(groups)
    shapes, rejected = [], 0
    required = [h * 100 + m for h in range(9, 16) for m in range(0, 60, 5) if 915 <= h * 100 + m <= 1510]
    for prev, day in zip(days, days[1:]):
        g = groups[day]
        g = g[g.hm <= 1510]
        prices = g[['o', 'h', 'l', 'c']].to_numpy(dtype=float)
        pc = float(groups[prev].c.iloc[-1])
        valid = (g.hm.tolist() == required and np.isfinite(prices).all() and (prices > 0).all()
                 and np.isfinite(pc) and pc > 0 and (g.h >= g[['o', 'c']].max(axis=1)).all()
                 and (g.l <= g[['o', 'c']].min(axis=1)).all())
        if not valid:
            rejected += 1
            continue
        op = float(g.o.iloc[0])
        gap = op / pc - 1
        if abs(gap) > .1:
            rejected += 1
            continue
        end = float(g.c.iloc[-1])
        shapes.append({'source_day': day, 'gap': gap, 'close_up': end > op,
                       'big_move': abs(end / op - 1) > .015,
                       'bars': [[int(row.hm), *[round(float(getattr(row, k)) / pc * anchor, 2) for k in ('o', 'h', 'l', 'c')]]
                                for row in g.itertuples()]})
    if len(shapes) < 2:
        raise ValueError(f'{symbol}: insufficient complete paths; no replacement')
    return shapes, {'friday_close': anchor, 'five_session_change': float(daily.iloc[-1] / daily.iloc[-6] - 1),
                    'complete_source_sessions': len(shapes), 'excluded_source_sessions': rejected}


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    meta = pd.read_csv(ROOT / 'data/wide_meta.csv')
    pool = sorted(s for s in set(meta.symbol) - {'REDINGTON', 'POONAWALLA'} if (ROOT / f'data/kite_5m/{s}.pkl').exists())
    selected = secrets.SystemRandom().sample(pool, 2)
    write(OUT / 'draw.json', {'at': datetime.now(IST).isoformat(), 'pool': pool, 'symbols': selected,
                            'rule': 'uniform without replacement; no outcome redraw'})
    rng = np.random.default_rng(secrets.randbits(64))
    packets = [{'drill': n, 'stocks': {}} for n in (2, 3)]
    sealed = [{'drill': n, 'stocks': {}} for n in (2, 3)]
    forecasts = {}
    for s in selected:
        shapes, facts = source_inventory(s)
        tier = str(meta.loc[meta.symbol == s, 'index'].iloc[0])
        ps = asyncio.run(odds(s, facts['friday_close']))
        forecasts[s] = ps
        probs = {k: v['p'] if v['p'] is not None else .5 for k, v in ps.items()}
        weights = []
        for shape in shapes:
            values = {'gap_up': shape['gap'] > 0, 'close_up': shape['close_up'], 'big_move': shape['big_move']}
            weights.append(np.prod([max(1e-6, probs[k] if yes else 1 - probs[k]) for k, yes in values.items()]))
        w = np.array(weights, dtype=float)
        picks = rng.choice(len(shapes), size=2, replace=False, p=w / w.sum())
        for i, index in enumerate(picks):
            shape = shapes[int(index)]
            sealed[i]['stocks'][s] = shape
            packets[i]['stocks'][s] = {**facts, 'tier': tier, 'round_trip_cost_rate': cost_rate(tier),
                                      'first_candle': shape['bars'][0], 'drex': ps,
                                      'market_panic_verified': False, 'full_universe_rank_verified': False,
                                      'events_verified': False}
    write(OUT / 'forecasts.json', forecasts)
    write(OUT / 'sealed.json', sealed)
    write(OUT / 'visible.json', packets)
    write(OUT / 'manifest.json', {p.name: digest(p) for p in (OUT/'sealed.json', OUT/'visible.json', ROOT/'scripts/monday_practice.py', ROOT/'RUNBOOK.md')})
    print(json.dumps({'symbols': selected, 'visible': packets}, ensure_ascii=False, indent=2))


def plan_for(facts):
    side = 'long' if facts['five_session_change'] < 0 else 'short'
    base = {'side': side, 'stop_pct': .03, 'reason': 'Practice baseline fades the five-session move; this is not a validated signal.'}
    revised = dict(base)
    if side == 'long':
        gap = facts['first_candle'][1] / facts['friday_close'] - 1
        reasons = []
        if gap >= -.02:
            reasons.append('Opening fall is smaller than the panic-buy stock threshold.')
        if not all(facts.get(k) is True for k in ('market_panic_verified', 'full_universe_rank_verified', 'events_verified')):
            reasons.append('The market panic, full-universe top-two ranking and event eligibility are not all verified.')
        if reasons:
            revised.update(side='none', reason=' '.join(reasons) + ' A recent fall and one candle are not enough to buy.')
    else:
        revised['reason'] = 'Same exploratory short as baseline, retained only to isolate the rebound-long skip. It remains unproven and is not enabled in the paper runner.'
    return {'baseline': base, 'checklist': revised}


def commit():
    visible = read(OUT / 'visible.json')
    plans = [{'drill': p['drill'], 'stocks': {s: plan_for(f) for s, f in p['stocks'].items()}} for p in visible]
    write(OUT / 'plans.json', plans)
    write(OUT / 'commit.json', {'at': datetime.now(IST).isoformat(), 'plans_sha256': digest(OUT/'plans.json'),
                              'sealed_sha256': digest(OUT/'sealed.json'), 'visible_sha256': digest(OUT/'visible.json')})
    print(json.dumps(plans, ensure_ascii=False, indent=2))


def simulate(symbol, bars, plan, rate, worse=0):
    side = plan['side']
    if side == 'none':
        return None
    sign = 1 if side == 'long' else -1
    i = next(i for i, b in enumerate(bars) if b[0] == 920)
    entry = float(bars[i][1]) * (1 + sign * worse)
    qty = int(5000 // entry)
    if qty < 1:
        return None
    stop = entry * (1 - sign * plan['stop_pct'])
    exit_price, reason, exit_hm = None, None, None
    for hm, op, high, low, close in bars[i:]:
        if hm == 1510:
            exit_price, reason, exit_hm = op, 'flatten', hm
            break
        if (low <= stop if side == 'long' else high >= stop):
            exit_price = min(op, stop) if side == 'long' else max(op, stop)
            reason, exit_hm = 'stop', hm
            break
    if exit_price is None:
        raise ValueError('Incomplete execution path')
    gross = sign * (exit_price - entry) * qty
    costs = entry * qty * rate
    net = gross - costs
    return dict(day=DAY, symbol=symbol, side=side, hm=915, entry_hm=920, exit_hm=exit_hm, qty=qty,
                entry=entry, exit=exit_price, reason=reason, gross=round(gross, 2), costs=round(costs, 2),
                net=round(net, 2), net_pct=net/(entry*qty)*100)


def score():
    commit_data = read(OUT/'commit.json')
    for name in ('plans', 'sealed', 'visible'):
        if digest(OUT/f'{name}.json') != commit_data[f'{name}_sha256']:
            raise ValueError('Committed artifact changed')
    if digest(ROOT/'scripts/monday_practice.py') != read(OUT/'manifest.json')['monday_practice.py']:
        raise ValueError('Scoring code changed after preparation')
    write(OUT/'started.json', {'at': datetime.now(IST).isoformat(), **commit_data})
    plans, visible, sealed = [read(OUT/f'{name}.json') for name in ('plans', 'visible', 'sealed')]
    results = []
    for p, v, hidden in zip(plans, visible, sealed):
        item = {'drill': p['drill'], 'accounts': {}, 'source_days': {s: b['source_day'] for s, b in hidden['stocks'].items()}}
        for account in ('baseline', 'checklist'):
            trades, stress, decisions, audit = [], [], [], []
            for s, variants in p['stocks'].items():
                plan, facts, bars = variants[account], v['stocks'][s], hidden['stocks'][s]['bars']
                t = simulate(s, bars, plan, facts['round_trip_cost_rate'])
                stressed = simulate(s, bars, plan, facts['round_trip_cost_rate'], .005)
                original = simulate(s, bars, variants['baseline'], facts['round_trip_cost_rate'])
                decisions.append(dict(symbol=s, action='Skip' if plan['side'] == 'none' else plan['side'].title(),
                                      reason=plan['reason'], counterfactual_net=original['net'] if original else None))
                if t:
                    trades.append(t)
                    stress.append(stressed)
                    j = next(j for j, b in enumerate(bars) if b[0] == t['exit_hm'])
                    audit.append({'symbol': s, 'entry_neighbours': bars[:3], 'exit_neighbours': bars[max(0,j-1):j+2],
                                  'extreme_return': abs(t['net_pct']) > 15, 'entry_matches_0920': t['entry'] == bars[1][1]})
            item['accounts'][account] = dict(trades=trades, stress=stress, decisions=decisions, audit=audit,
                                             net=round(sum(t['net'] for t in trades), 2),
                                             worse_net=round(sum(t['net'] for t in stress), 2))
        results.append(item)
    write(OUT/'result.json', results)
    print(json.dumps(results, ensure_ascii=False, indent=2))


def publish_saved():
    results = read(OUT/'result.json')
    # Back up before adding any runs; old records are not changed by publication.
    backup = OUT/'before_publication.db'
    if not backup.exists():
        with sqlite3.connect(ROOT/'data/stalk.db') as src, sqlite3.connect(backup) as dst:
            src.backup(dst)
    ids = []
    for r in results:
        for account, data in r['accounts'].items():
            cid = f'synthetic_monday_{r["drill"]}_{account}'
            note = (f'Synthetic practice only; not actual Monday prices or a profit forecast. '
                    f'Random pair: {", ".join(r["source_days"])}. Drex-weighted reused historical shapes, independently sampled per stock. '
                    f'Plans committed before reveal. Costs include charges and modeled friction by tier. '
                    f'Net with an additional 0.5% worse entry: Rs{data["worse_net"]:.2f}. '
                    'Only unqualified rebound longs are removed; shorts remain unproven practice. Too few trades for a strategy verdict.')
            publish(cid, f'Synthetic Monday {r["drill"]} - {account}', pd.DataFrame(data['trades'], columns=COLS),
                    DAY, DAY, 'Practice', note, session_days=[DAY], kind='synthetic', decisions=data['decisions'])
            ids.append(cid)
        a, b = r['accounts']['baseline'], r['accounts']['checklist']
        record_cycle(f'Monday drill {r["drill"]}', f'Synthetic Monday {r["drill"]}: entry checklist', 'Practice',
                     f'{", ".join(r["source_days"])}: baseline Rs{a["net"]:+.2f}; checklist Rs{b["net"]:+.2f}. Synthetic practice, not a forecast or strategy pass.',
                     f'See Sessions for both accounts, decisions and skipped-trade outcomes. Plans committed before reveal. {OUT.relative_to(ROOT)}', 1, 1)
    # Publish the already finished original drill without rerunning its price path or changing its costs.
    old = ROOT/'data/synthetic/20261101_183724'
    saved, plans = read(old/'result.json'), read(old/'plans.json')
    for trader in ('Claude', 'Astra'):
        trades = []
        for row in saved['results']:
            if row['trader'] != trader:
                continue
            gross = (1 if row['side'] == 'long' else -1) * (row['exit']-row['entry']) * row['qty']
            trades.append(dict(day=DAY, symbol=row['symbol'], side=row['side'], hm=915, entry_hm=920, exit_hm=1510,
                               qty=row['qty'], entry=row['entry'], exit=row['exit'], reason='flatten', gross=round(gross,2),
                               costs=round(gross-row['net'],2), net=row['net'], net_pct=row['net']/(row['entry']*row['qty'])*100))
        decisions = [dict(symbol=s, action='Skip' if p['side']=='none' else p['side'].title(), reason=p['why']) for s,p in plans[trader].items()]
        cid = f'synthetic_monday_1_{trader.lower()}'
        publish(cid, f'Synthetic Monday 1 - {trader}', pd.DataFrame(trades, columns=COLS), DAY, DAY, 'Practice',
                'Original REDINGTON/POONAWALLA drill, saved result imported without rescoring. Charges only; no additional slippage model. '
                'Original simulator skipped stop checks in its entry bar. These original figures are not directly comparable with the stricter new drills. '
                'Synthetic practice, not a Monday forecast or strategy evidence.', session_days=[DAY], kind='synthetic', decisions=decisions)
        ids.append(cid)
    write(OUT/'published.json', {'campaign_ids': ids})
    print(json.dumps(ids))


if __name__ == '__main__':
    {'prepare': prepare, 'commit': commit, 'score': score, 'publish': publish_saved}[sys.argv[1]]()
