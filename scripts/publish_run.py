"""Publish a research trades CSV as one Sessions run (campaign + day sessions + trades). Never re-scores anything.

    python scripts/publish_run.py <run_id> "<label>" <trades.csv> <first_day> <last_day> "<status>" ["<note>"]
The CSV needs day, symbol, side, hm (or signal_hm), qty, entry, exit, reason, gross, costs, net, net_pct and, when present,
a `known` column (days with an unknown trade are kept as unknown days, as the scorers count them).
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from stalk.config import IST
from stalk.data.store import Store
from stalk.market.costs import CURRENT_FEE_SCHEDULE_VERSION
from stalk.research.orb import normalize


def ts(day, hm):
    return int(datetime.fromisoformat(f'{day}T{hm//100:02d}:{hm%100:02d}:00').replace(tzinfo=IST).timestamp())


def next_hm(hm):
    return hm+5 if hm % 100 < 55 else hm+45


def publish(cid, label, trades, first, last, status, note='', *, session_days=None, kind='research', decisions=None):
    t = trades.copy()
    if 'hm' not in t and 'signal_hm' in t:
        t['hm'] = t.signal_hm
    unknown = set(t.loc[t.known == False, 'day']) if 'known' in t else set()   # noqa: E712 (pandas mask)
    t = t[~t.day.isin(unknown)]
    if 'known' in t:
        t = t[t.known == True]                                                    # noqa: E712
    if session_days is None:
        ref = normalize(pd.read_pickle(ROOT/'data/kite_5m/RELIANCE.pkl'))
        calendar = sorted(d for d in ref.day.unique() if first <= d <= last)
    else:
        if kind != 'synthetic':
            raise ValueError('Explicit session dates are only for labelled synthetic drills')
        calendar = sorted(set(session_days))
    if not calendar or any(not first <= d <= last for d in calendar) or not set(t.day).issubset(calendar):
        raise ValueError('Invalid or incomplete publication calendar')
    store, acct_id = Store(), cid+':main'
    days, curve, equity = [], [], 10000.
    by_day = {d: g for d, g in t.groupby('day')}
    for day in calendar:
        g = by_day.get(day, t.iloc[0:0])
        net = None if day in unknown else round(float(g.net.sum()), 2)
        start = equity
        if net is not None:
            equity += net
        row = dict(day=day, start_equity=start, end_equity=equity if net is not None else None, net=net,
                   gross=round(float(g.gross.sum()), 2) if net is not None else None,
                   costs=round(float(g.costs.sum()), 2) if net is not None else None,
                   trades=len(g), wins=int((g.net > 0).sum()), losses=int((g.net <= 0).sum()),
                   status='scored' if net is not None else 'unknown_path')
        days.append(row)
        curve.append([day, equity if net is not None else None])
        if len(g) or net is None:
            sid = acct_id+':'+day
            store.save_session(sid, 'research', day, acct_id, dict(paper_only=True), row, ended=True)
            for _, x in g.iterrows():
                store.save_trade(dict(id=sid+':'+x.symbol, session_id=sid, day=day, strategy=getattr(x, 'strategy', cid), symbol=x.symbol, side=x.side,
                                      qty=int(x.qty), entry_ts=ts(day, int(x.entry_hm)) if 'entry_hm' in t else ts(day, next_hm(int(x.hm))), entry_price=float(x.entry),
                                      exit_ts=ts(day, int(x.exit_hm)) if 'exit_hm' in t else (ts(day, 1510) if x.reason == 'flatten' else None), exit_price=float(x.exit),
                                      gross=float(x.gross), costs=float(x.costs), net=float(x.net), reason=x.reason,
                                      fee_version='synthetic-disclosed-costs' if kind == 'synthetic' else CURRENT_FEE_SCHEDULE_VERSION,
                                      broker='synthetic' if kind == 'synthetic' else 'backtest'))
    known = [d for d in days if d['net'] is not None]
    peak, dd = 10000., 0.
    for d in known:
        peak = max(peak, d['end_equity']); dd = max(dd, peak-d['end_equity'])
    acct = dict(name='main', strategies=[cid], require_validated=False, start=10000., end=equity, net=round(float(t.net.sum()), 2),
                gross=round(float(t.gross.sum()), 2), costs=round(float(t.costs.sum()), 2), tax=0., after_tax=None, trades=len(t),
                max_dd=round(dd, 2), green_days=sum(d['net'] > 0 for d in known), red_days=sum(d['net'] < 0 for d in known),
                flat_days=sum(d['net'] == 0 for d in known), unknown_days=len(unknown),
                stats=dict(n=len(t), win=float((t.net > 0).mean()) if len(t) else None, net=float(t.net.sum())),
                per_strategy={}, days=days, curve=curve)
    obj = dict(id=cid, first_day=calendar[0], last_day=calendar[-1], days=len(days), capital=10000., accounts={'main': acct},
               weeks=[], kind=kind, label=label, research_status=status, unknown_days=len(unknown), note=note,
               decisions=decisions or [])
    store.save_campaign(cid, calendar[0], calendar[-1], dict(bar_minutes=5, label=label), obj)
    print(cid, len(calendar), 'days', len(t), 'trades', round(float(t.net.sum()), 2), 'net')


def record_cycle(cid, name, verdict, plain, result, trials, scoring_runs, score=None):
    """Add or replace one test on the Strategies page (data/research_summary.json), newest first.
    score: optional (row name, net % a trade, note) for the "What worked and what didn't" board."""
    import json
    p = ROOT/'data/research_summary.json'
    r = json.loads(p.read_text(encoding='utf-8'))
    c = dict(id=cid, name=name, verdict=verdict, plain=plain, result=result, summary=result, note=result,
             trials=trials, scoring_runs=scoring_runs)
    r['cycles'] = [c]+[x for x in r['cycles'] if x['id'] != cid]
    if score:
        r['scoreboard'] = [s for s in r['scoreboard'] if s['name'] != score[0]]+[dict(name=score[0], net_pct=score[1], note=score[2])]
        r['scoreboard'].sort(key=lambda s: -s['net_pct'])
    r['updated'] = datetime.now(IST).strftime('%Y-%m-%d')
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding='utf-8')
    tmp.replace(p)


if __name__ == '__main__':
    a = sys.argv[1:]
    publish(a[0], a[1], pd.read_csv(a[2]), a[3], a[4], a[5], a[6] if len(a) > 6 else '')
