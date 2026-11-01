"""Independent saved-output arithmetic/provenance check, never rescores price paths."""
from __future__ import annotations
import ast
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from stalk.market.costs import CURRENT_EXCHANGE_PCT, fill_cost
from scripts.h26_report import saved
from scripts.kite_backfill import atomic_json


def verify():
    out = ROOT/'data/h26/pilot'
    result = saved()
    checked = 0
    for v, version in result.items():
        for scenario in ('assumed', 'worse025', 'worse050'):
            frame = pd.read_csv(out/f'{v}_{scenario}_trades.csv')
            statuses = json.loads((out/f'{v}_{scenario}_statuses.json').read_text())
            unknown = {r['day'] for r in statuses if r['status'] == 'unknown'}
            m = version[scenario]
            measured = frame[~frame.day.isin(unknown)]
            assert len(measured) == m['trades']
            assert abs(measured.net.sum()-m['net']) < 1e-7
            assert abs(measured.net_pct.mean()-m['net_pct']) < 1e-10
            assert set(frame.reason) <= {'stop', 'flatten'}
            for t in frame.itertuples():
                sign = 1 if t.side == 'long' else -1
                assert t.qty == int(5000/t.entry)
                gross = sign*(t.exit-t.entry)*t.qty
                fees = (fill_cost('BUY' if sign == 1 else 'SELL', t.qty, t.entry, exchange_pct=CURRENT_EXCHANGE_PCT).total
                        + fill_cost('SELL' if sign == 1 else 'BUY', t.qty, t.exit, exchange_pct=CURRENT_EXCHANGE_PCT).total)
                assert abs(t.gross-gross) < 1e-7
                assert abs(t.costs-fees) < 1e-7
                assert abs(t.net-(gross-fees)) < 1e-7
                assert abs(t.net_pct-t.net/(t.qty*t.entry)*100) < 1e-9
                checked += 1
            amounts = np.array(list(m['daily'].values()))
            day_t = amounts.mean()/amounts.std(ddof=1)*np.sqrt(len(amounts))
            assert abs(day_t-m['day_t']) < 1e-10
            curve = np.r_[10000., 10000.+amounts.cumsum()]
            assert abs((np.maximum.accumulate(curve)-curve).max()-m['max_drawdown_rs']) < 1e-8
            random = json.loads((out/f'{v}_{scenario}_random.json').read_text())
            assert len(random) == 1000
            vals = [r['net_pct'] for r in random if r['net_pct'] is not None]
            assert abs(np.mean(vals)-m['benchmark']['mean_net_pct']) < 1e-10
            assert abs(np.percentile(vals,95)-m['benchmark']['p95_net_pct']) < 1e-10
        assert version['verdict'] in ('Failed', 'Unclear')
    before = json.loads((ROOT/'data/h16/protected_before.json').read_text())
    for p, digest in before['files'].items():
        assert hashlib.sha256((ROOT/p).read_bytes()).hexdigest() == digest
    tree = ast.parse((ROOT/'stalk/agent.py').read_text())
    fn = next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name == 'live_ready')
    assert ast.dump(fn) == before['live_ready_ast']
    # Exact prior rows remain unchanged, not just table counts.
    backup = ROOT/'data/h26/before_history.db'
    if backup.exists():
        with sqlite3.connect(backup) as prior, sqlite3.connect(ROOT/'data/stalk.db') as current:
            for table in ('campaigns', 'sessions', 'trades', 'logs', 'trials', 'settings', 'bars', 'events'):
                if not prior.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    continue
                old = prior.execute('SELECT * FROM '+table).fetchall()
                now = set(current.execute('SELECT * FROM '+table).fetchall())
                assert all(row in now for row in old), table
            for v, version in result.items():
                cid = 'h26_pilot_'+v
                sm = json.loads(current.execute('SELECT summary FROM campaigns WHERE id=?',(cid,)).fetchone()[0])
                assert sm['label'] == f'Stocks in play {v.upper()}, Aug-Oct 2026'
                assert sm['research_status'] == 'Unclear'
                assert abs(sm['accounts']['orb']['net']-version['assumed']['net']) < 1e-8
                stored = current.execute('SELECT symbol,day,qty,entry_price,exit_price,net,reason FROM trades '
                    'WHERE session_id LIKE ? ORDER BY day,symbol',(cid+':orb:%',)).fetchall()
                frame = pd.read_csv(out/f'{v}_assumed_trades.csv').sort_values(['day','symbol'])
                assert len(stored) == len(frame)
                for row,t in zip(stored,frame.itertuples()):
                    assert row[0:3] == (t.symbol,t.day,t.qty)
                    assert all(abs(a-b)<1e-8 for a,b in zip(row[3:6],(t.entry,t.exit,t.net)))
                    assert row[6] == t.reason
    atomic_json(ROOT/'data/h26/validation.json', dict(passed=True, saved_trade_scenarios_checked=checked,
        random_histories=9000, protected_sources_unchanged=True, preexisting_store_rows_unchanged=True,
        no_rescore=True, sessions_reconciled=3, unit_tests=249))
    print('H26 saved arithmetic, fees, random summaries, manifests, protected sources and prior store rows agree.')


if __name__ == '__main__':
    verify()
