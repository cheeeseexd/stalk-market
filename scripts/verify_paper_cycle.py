"""Independent arithmetic/guard checks for the saved A/B/C cycle; no network."""
import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    audit = ROOT/'data/workflow_audit'
    before = json.loads((audit/'protected_before.json').read_text(encoding='utf-8'))
    for name, digest in before['files'].items():
        assert sha(ROOT/name) == digest, name
    tree = ast.parse((ROOT/'stalk/agent.py').read_text(encoding='utf-8'))
    ready = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'live_ready')
    assert ast.dump(ready) == before['live_ready_ast']
    old_manifest = json.loads((ROOT/'data/h17/input_manifest.json').read_text(encoding='utf-8'))
    for path, digest in old_manifest.items():
        assert sha(ROOT/path) == digest, path
    horizons = json.loads((ROOT/'data/horizons.json').read_text(encoding='utf-8'))
    summary = json.loads((ROOT/'data/h20/summary.json').read_text(encoding='utf-8'))
    ranks_checked, windows_checked = 0, 0
    pools = pd.read_csv(ROOT/'data/h17/cleaned_pools.csv')
    for label in ('strict', 'loose'):
        base = pd.read_csv(ROOT/f'data/h17/{label}_cleaned_stop3_account.csv')
        mornings = pd.read_csv(ROOT/f'data/h20/{label}_mornings.csv')
        for horizon, saved in horizons['versions'][label]['horizons'].items():
            rows = pd.read_csv(ROOT/f'data/h17/{label}_horizon_{horizon}.csv')
            # Verify every window directly against the original saved H17 rows.
            for r in rows.itertuples():
                active = base[(base.day >= r.start) & (base.day < r.end_exclusive)]
                assert len(active) == r.trades
                np.testing.assert_allclose(active.net.sum(), r.net, atol=1e-8)
            assert len(rows) == saved['windows']
            assert int((rows.trades > 0).sum()) == saved['windows_with_trades']
            np.testing.assert_allclose(rows.net.median(), saved['median_net'])
            windows_checked += len(rows)
        for k in (2, 3, 4):
            trades = pd.read_csv(ROOT/f'data/h20/{label}_{k}_trades.csv')
            s = summary['versions'][label][str(k)]
            np.testing.assert_allclose(trades.net.sum(), mornings[str(k)].sum(), atol=1e-8)
            np.testing.assert_allclose(mornings[str(k)].mean(), s['net_per_morning'])
            x = mornings[str(k)].to_numpy()
            np.testing.assert_allclose(x.mean()*np.sqrt(len(x))/x.std(ddof=1), s['day_t'])
            e = np.r_[10000, 10000+np.cumsum(x)]
            np.testing.assert_allclose(np.max(np.maximum.accumulate(e)-e), s['max_drawdown'])
            # Recalculate each fee without importing the scored accounting implementation.
            for r in trades.itertuples():
                source = pools[(pools.day == r.day) & (pools.symbol == r.symbol)].iloc[0]
                budget = {2: 5000, 3: 3333, 4: 2500}[k]
                entry = source.open*1.001
                qty = int(budget//entry)
                stopped = source.low <= entry*.97
                exit_px = (entry*.97*.998 if stopped else source.close*.9992)*.999
                buy, sell = qty*entry, qty*exit_px
                fees = (min(20, buy*.0003)+min(20, sell*.0003)+(buy+sell)*(.0000297+.000001))*1.18
                fees += sell*.00025+buy*.00003
                assert qty == r.qty
                np.testing.assert_allclose([entry, exit_px, fees, (sell-buy)-fees],
                                           [r.entry, r.exit, r.charges, r.net], atol=1e-8)
                ranked = pools[pools.day == r.day].sort_values(['gap', 'symbol']).symbol.tolist()
                assert ranked[r.rank-1] == r.symbol
                ranks_checked += 1
            if k == 2:
                np.testing.assert_allclose(trades.net.sum(), base.net.sum(), atol=1e-8)
            else:
                assert not s['screen_pass'] and not s['checks']['mean']
    files = ['scripts/horizon_ladder.py', 'scripts/h20_picks.py', 'scripts/verify_paper_cycle.py',
             'stalk/research/paper_daily.py', 'stalk/sim/panic_live.py', 'stalk/agent.py',
             'stalk/api/server.py', 'tests/test_paper_daily.py', 'RUNBOOK.md',
             'docs/research/QUEUE.md', 'docs/research/HANDOFF.md', 'data/research_summary.json',
             'data/horizons.json', 'data/h20/summary.json',
             'docs/research/2026-11-01-horizons.md', 'docs/research/2026-11-01-h20-picks.md',
             'docs/research/2026-11-01-paper-workflow.md']
    obj = dict(passed=True, protected_files_and_live_ready_unchanged=True,
               h17_input_hashes=len(old_manifest), windows_reconciled=windows_checked,
               h20_trade_rows_reconciled=ranks_checked, original_h17_code_unchanged=sha(ROOT/'scripts/h17_rebuild.py') ==
               json.loads((ROOT/'data/h17/deliverable_manifest.json').read_text())['scripts/h17_rebuild.py'],
               h20_extra_source_hashes={name: sha(ROOT/name) for name in ['data/wide_1d_10y.pkl',
                   'data/h17/strict_cleaned_stop3_account.csv', 'data/h17/loose_cleaned_stop3_account.csv']},
               final_files={name: sha(ROOT/name) for name in files})
    assert obj['original_h17_code_unchanged']
    (audit/'validation.json').write_text(json.dumps(obj, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in obj.items() if k not in ('final_files', 'h20_extra_source_hashes')}, indent=2))


if __name__ == '__main__':
    main()
