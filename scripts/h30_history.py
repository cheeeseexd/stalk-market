"""Publish the saved H30 design-window trades as one Sessions run. Reads data/h30 only; never re-scores anything."""
from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from stalk.config import IST
from stalk.data.store import Store
from stalk.market.costs import CURRENT_FEE_SCHEDULE_VERSION
from stalk.research.orb import normalize
from scripts.h30_capitulation import DESIGN_START, DESIGN_END

OUT = ROOT/'data/h30'
CID = 'h30_design'


def ts(day, hm):
    return int(datetime.fromisoformat(f'{day}T{hm//100:02d}:{hm%100:02d}:00').replace(tzinfo=IST).timestamp())


def next_hm(hm):
    return hm+5 if hm % 100 < 55 else hm+45


def main():
    summary = json.loads((OUT/'summary.json').read_text(encoding='utf-8'))
    a = summary['design']['assumed']
    trades = pd.read_csv(OUT/'design_assumed_trades.csv').to_dict('records')
    picked = {p['day'] for p in json.loads((OUT/'selections.json').read_text()) if DESIGN_START <= p['day'] <= DESIGN_END}
    scored = {(t['day'], t['symbol']) for t in trades}
    sel = [p for p in json.loads((OUT/'selections.json').read_text()) if p['day'] in picked]
    unknown = {p['day'] for p in sel if (p['day'], p['symbol']) not in scored}       # a pick whose path had a missing bar
    trades = [t for t in trades if t['day'] not in unknown]                            # same rule the scorer used
    # The trading calendar: NSE sessions in the design window, from the most liquid stock's bars
    ref = normalize(pd.read_pickle(ROOT/'data/kite_5m/RELIANCE.pkl'))
    calendar = sorted(d for d in ref.day.unique() if DESIGN_START <= d <= DESIGN_END)
    store = Store()
    account_id = CID+':capitulation'
    days, curve, equity = [], [], 10000.
    for day in calendar:
        g = [t for t in trades if t['day'] == day]
        net = None if day in unknown else round(sum(t['net'] for t in g), 2)
        start = equity
        if net is not None:
            equity += net
        row = dict(day=day, start_equity=start, end_equity=equity if net is not None else None, net=net,
                   gross=round(sum(t['gross'] for t in g), 2) if net is not None else None,
                   costs=round(sum(t['costs'] for t in g), 2) if net is not None else None,
                   trades=len(g), wins=sum(t['net'] > 0 for t in g), losses=sum(t['net'] <= 0 for t in g),
                   status='scored' if net is not None else 'unknown_path')
        days.append(row)
        curve.append([day, equity if net is not None else None])
        if not g and net is not None:
            continue
        sid = account_id+':'+day
        store.save_session(sid, 'capitulation', day, account_id, dict(paper_only=True), row, ended=True)
        for t in g:
            entry = ts(day, next_hm(int(t['signal_hm'])))
            store.save_trade(dict(id=sid+':'+t['symbol'], session_id=sid, day=day, strategy='capitulation', symbol=t['symbol'],
                                  side=t['side'], qty=int(t['qty']), entry_ts=entry, entry_price=t['entry'],
                                  exit_ts=ts(day, 1510) if t['reason'] == 'flatten' else None, exit_price=t['exit'],
                                  gross=t['gross'], costs=t['costs'], net=t['net'], reason=t['reason'],
                                  fee_version=CURRENT_FEE_SCHEDULE_VERSION, broker='backtest'))
    acct = dict(name='capitulation', strategies=['capitulation'], require_validated=False, start=10000., end=equity,
                net=a['net'], gross=round(sum(t['gross'] for t in trades), 2), costs=round(sum(t['costs'] for t in trades), 2),
                tax=0., after_tax=None, trades=a['trades'], max_dd=a['max_drawdown_rs'],
                green_days=sum(d['net'] is not None and d['net'] > 0 for d in days),
                red_days=sum(d['net'] is not None and d['net'] < 0 for d in days),
                flat_days=sum(d['net'] == 0 for d in days), unknown_days=len(unknown),
                stats=dict(n=a['trades'], win=a['win_rate'], net=a['net'], t=a['day_t']),
                per_strategy={}, days=days, curve=curve)
    label = 'Capitulation reversal (H30), 2021-2025'
    obj = dict(id=CID, first_day=calendar[0], last_day=calendar[-1], days=len(days), capital=10000.,
               accounts={'capitulation': acct}, weeks=[], kind='capitulation', label=label, research_status=summary['verdict'],
               unknown_days=len(unknown), note='Design window only; the final year stayed sealed because the design gate failed. '
               'Kite 5-minute bars, current stock list, next-bar fills, ₹5,000 slots. No real orders.')
    store.save_campaign(CID, calendar[0], calendar[-1], dict(bar_minutes=5, label=label), obj)
    print(CID, len(calendar), 'days', a['trades'], 'trades', round(equity - 10000, 2), 'net; unknown days', len(unknown))


if __name__ == '__main__':
    main()
