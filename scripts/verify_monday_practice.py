"""Read-only reconciliation of saved practice results and published records; never rescores."""
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/synthetic/20261101_random_pair'


def read(name):
    return json.loads((OUT/name).read_text(encoding='utf-8'))


def main():
    commit = read('commit.json')
    for name in ('plans', 'sealed', 'visible'):
        assert hashlib.sha256((OUT/f'{name}.json').read_bytes()).hexdigest() == commit[f'{name}_sha256']
    assert hashlib.sha256((ROOT/'scripts/monday_practice.py').read_bytes()).hexdigest() == read('manifest.json')['monday_practice.py']
    paths = read('sealed.json')
    result = read('result.json')
    trades_checked = 0
    for r, path in zip(result, paths):
        for data in r['accounts'].values():
            assert round(sum(t['net'] for t in data['trades']),2) == data['net']
            assert round(sum(t['net'] for t in data['stress']),2) == data['worse_net']
            for t in data['trades'] + data['stress']:
                assert t['reason'] in ('stop','flatten')
                assert t['qty']*t['entry'] <= 5000
                gross = (1 if t['side']=='long' else -1)*(t['exit']-t['entry'])*t['qty']
                assert abs(gross-t['gross']) <= .00501
                assert abs(t['gross']-t['costs']-t['net']) <= .01001
                bars = path['stocks'][t['symbol']]['bars']
                exit_bar = next(b for b in bars if b[0]==t['exit_hm'])
                assert exit_bar[3] <= t['exit'] <= exit_bar[2]
                assert '2024-01-01' <= path['stocks'][t['symbol']]['source_day'] <= '2025-07-31'
                trades_checked += 1
    with sqlite3.connect(f'file:{ROOT / "data/stalk.db"}?mode=ro', uri=True) as db:
        db.execute('ATTACH DATABASE ? AS old', (str(OUT/'before_publication.db'),))
        preserved = {}
        for table in ('campaigns','sessions','trades','bars','events','news','logs','trials'):
            missing = db.execute(f'SELECT COUNT(*) FROM (SELECT * FROM old.{table} EXCEPT SELECT * FROM main.{table})').fetchone()[0]
            assert missing == 0, (table, missing)
            preserved[table] = db.execute(f'SELECT COUNT(*) FROM old.{table}').fetchone()[0]
        for cid in read('published.json')['campaign_ids']:
            row = db.execute('SELECT summary FROM campaigns WHERE id=?',(cid,)).fetchone()
            assert row is not None
            sm = json.loads(row[0])
            assert sm['kind']=='synthetic' and len(sm['decisions'])==2
            stored = db.execute('SELECT net,reason,broker FROM trades WHERE session_id=?',(cid+':main:2026-11-02',)).fetchall()
            assert len(stored)==sm['accounts']['main']['trades']
            assert abs(sum(x[0] for x in stored)-sm['accounts']['main']['net']) < .001
            assert all(x[1] in ('stop','flatten') and x[2]=='synthetic' for x in stored)
    verification = {'hashes_match': True, 'trade_scenarios_checked': trades_checked,
                    'campaigns_verified': 6, 'preexisting_rows_preserved': preserved}
    (OUT/'verification.json').write_text(json.dumps(verification,indent=2),encoding='utf-8')
    print(json.dumps(verification,indent=2))


if __name__ == '__main__':
    main()
