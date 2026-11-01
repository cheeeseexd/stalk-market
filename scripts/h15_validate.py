"""Reconcile H15 artifacts/History; do not rerun research or open H14 returns."""
from pathlib import Path
import hashlib
import json
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stalk.sim.panic_replay import load_cache, stamp
from stalk.sim.price_basis import resolve

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/h15'


def main():
    before = pd.read_csv(OUT / 'before/data/panic_weeks/recent_weeks.csv')
    after = pd.read_csv(ROOT / 'data/panic_weeks/recent_weeks.csv')
    assert len(before) == len(after) == 24
    assert set(before.history_id) == set(after.history_id)
    trades = pd.read_csv(ROOT / 'data/panic_weeks/recent_trades.csv')
    original = pd.read_csv(OUT / 'before_trades.csv')
    comparison = original.merge(trades, on=['rule', 'day', 'symbol'], how='outer', suffixes=('_before', '_after'), indicator=True)
    comparison.to_csv(OUT / 'trade_comparison.csv', index=False)
    both = comparison[comparison._merge == 'both']
    assert (both.net_before == both.net_after).all(), 'retained fills must not change'
    db = sqlite3.connect(ROOT / 'data/stalk.db')
    db.row_factory = sqlite3.Row
    old_db = sqlite3.connect(OUT / 'before/history.sqlite')
    old_db.row_factory = sqlite3.Row
    counts = {}
    for row in after.itertuples():
        saved = db.execute('select summary from campaigns where id=?', (row.history_id,)).fetchone()
        summary = json.loads(saved['summary'])['accounts']['panic_buy']
        actual = db.execute('select t.symbol,t.day,t.net from trades t join sessions s on s.id=t.session_id where s.account=?', (row.history_id + ':panic_buy',)).fetchall()
        expected = trades[(trades.rule == row.rule) & (trades.week == row.week)]
        assert summary['trades'] == row.trades == len(actual) == len(expected)
        assert abs(summary['net'] - row.net) < .005
        assert abs(sum(t['net'] for t in actual) - row.net) < .005
        assert {(t['day'], t['symbol']) for t in actual} == set(zip(expected.day, expected.symbol))
        counts[row.history_id] = len(actual)
    # All unrelated campaigns and their sessions/trades must remain byte-for-byte.
    other_ids = {r['id'] for r in old_db.execute('select id from campaigns')} - set(after.history_id)
    for cid in other_ids:
        old = old_db.execute('select * from campaigns where id=?', (cid,)).fetchone()
        new = db.execute('select * from campaigns where id=?', (cid,)).fetchone()
        assert tuple(old) == tuple(new)
    old_trades = {r['id']: tuple(r) for r in old_db.execute('select * from trades') if not any(r['session_id'].startswith(cid + ':') for cid in after.history_id)}
    new_trades = {r['id']: tuple(r) for r in db.execute('select * from trades') if not any(r['session_id'].startswith(cid + ':') for cid in after.history_id)}
    assert old_trades == new_trades
    db.close()
    old_db.close()
    grouped, _, _ = load_cache()
    days = sorted(grouped['NIFTY 50'])
    for row in trades.itertuples():
        rows = grouped[row.symbol][row.day]
        prev = grouped[row.symbol].get(days[days.index(row.day)-1])
        basis = resolve(row.day, row.symbol, rows[0][1], prev[-1][4] if prev else None)
        assert not basis['mismatch']
        assert basis['source'] == row.gap_source
        assert round(basis['open'] / basis['prev_close'] - 1, 4) == row.gap
        assert int(rows[0][0]) == stamp(row.day, 915)
    protected = json.loads((OUT / 'registration_audit.json').read_text())['protected']
    for path, digest in protected.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == digest
    assert not list((ROOT / 'data/h14').glob('holdout*trades*'))
    assert not (ROOT / 'data/h14/holdout_started.json').exists()
    # Hash frozen H14 files without interpreting their returns.
    h14 = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'data/h14').glob('*') if p.is_file()}
    manifest = {}
    for path in [ROOT / 'data/wide_5m.pkl', ROOT / 'data/wide_meta.csv', ROOT / 'data/wide_events.pkl', ROOT / 'data/price_basis_actions.json',
                 *sorted((ROOT / 'data/h9/announcements').glob('*.json'))]:
        manifest[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    a = pd.read_csv(OUT / 'stock_days.csv')
    for day in a.day.unique():
        from scripts.h9_fetch import bhav_path
        path = bhav_path(day)
        if path.exists():
            manifest[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    (OUT / 'input_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    changed = {}
    for rule, g in comparison.groupby('rule'):
        changed[rule] = {'removed': int((g._merge == 'left_only').sum()), 'added': int((g._merge == 'right_only').sum()),
                         'retained': int((g._merge == 'both').sum())}
    result = {'history_runs': len(counts), 'history_trades': sum(counts.values()), 'unchanged_other_campaigns': len(other_ids),
              'unchanged_other_trades': len(old_trades), 'protected_sources_unchanged': True,
              'all_traded_gaps_reconciled': True, 'fallback_trade_rows': int((trades.gap_source == 'yahoo_fallback').sum()),
              'input_hashes': len(manifest), 'h14_hashes': h14, 'trade_changes': changed}
    (OUT / 'artifact_validation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'h14_hashes'}, indent=2))


if __name__ == '__main__':
    main()
