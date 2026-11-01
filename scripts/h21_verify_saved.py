"""Arithmetic/selection checks of H21's frozen outputs, with no new trial."""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_fetch import bhav_path
from scripts.horizon_ladder import dump
from stalk.market.costs import round_trip_cost

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'data/h21'


def main():
    result = json.loads((OUT/'summary.json').read_text())
    pools = pd.read_csv(OUT/'everyday_pools.csv')
    n = pd.read_csv(OUT/'calendar.csv', index_col='day')
    coverage = pd.read_csv(OUT/'coverage.csv')
    ordinary = set(n.index[n.gap >= -.005])
    covered = set(coverage.loc[coverage.status == 'ok', 'day']) | set(n.index[n.gap < -.005])
    old = pd.read_csv(ROOT/'data/h17/strict_cleaned_selected.csv')
    unique = {}
    for variant in ('A', 'B', 'C'):
        p = pools[pools.day.isin(ordinary)]
        if variant == 'B':
            p = p[np.isfinite(p.std20) & (p.gap <= -2*p.std20)]
        if variant == 'C':
            p = p[p.day.isin(n.index[n.gap <= .002])]
        expected = []
        for day, g in p.groupby('day'):
            expected += [(day, symbol) for symbol in g.sort_values(['gap', 'symbol']).symbol.iloc[:2]]
        selected = pd.read_csv(OUT/f'{variant}_everyday_selected.csv')
        assert sorted(expected) == sorted(zip(selected.day, selected.symbol))
        t = pd.read_csv(OUT/f'{variant}_hybrid_trades.csv')
        e = t[t.leg == 'everyday']
        assert set(e.day) <= ordinary
        expected_funded = selected[(selected.open*1.001) <= 5000]
        assert set(zip(expected_funded.day, expected_funded.symbol)) == set(zip(e.day, e.symbol))
        combined = pd.concat([selected, old], ignore_index=True).set_index(['day', 'symbol'])
        for row in t.itertuples():
            key = (row.day, row.symbol)
            if key in unique:
                assert unique[key] == (row.entry, row.exit, row.net)
                continue
            opening = combined.loc[key, 'open']
            assert abs(row.entry-opening*1.001) < 1e-9
            b = pd.read_pickle(bhav_path(row.day)).loc[row.symbol]
            hit = b.low <= row.entry*.97
            expected_exit = row.entry*.97*.998*.999 if hit else b.close*.9992*.999
            assert row.reason == ('stop' if hit else 'flatten')
            assert abs(row.exit-expected_exit) < 1e-9
            assert row.qty == int(5000//row.entry)
            fee = round_trip_cost(row.qty, row.entry, expected_exit)
            assert abs(row.net-((expected_exit-row.entry)*row.qty-fee)) < 1e-8
            unique[key] = (row.entry, row.exit, row.net)
        for name, trades, days in [('hybrid', t, sorted(covered)),
                                  ('everyday', e, sorted(covered & ordinary))]:
            x = trades.groupby('day').net.sum().reindex(days, fill_value=0.).to_numpy()
            s = result['variants'][variant][name]
            assert abs(x.sum()-s['net']) < 1e-7
            if x.std(ddof=1) > 1e-12:
                assert abs(x.mean()*np.sqrt(len(x))/x.std(ddof=1)-s['day_t']) < 1e-10
            equity = np.r_[10000., 10000.+np.cumsum(x)]
            assert abs(np.max(np.maximum.accumulate(equity)-equity)-s['max_drawdown']) < 1e-7
        s = result['variants'][variant]
        expected_screen = (s['everyday']['net'] > 0 and s['everyday']['day_t'] is not None
                           and s['everyday']['day_t'] >= 2
                           and s['hybrid']['max_drawdown'] <= 1.5*result['panic_only']['max_drawdown'])
        assert (s['verdict'] == 'Passed') == expected_screen
        week = t[t.day.between('2024-12-09', '2024-12-13')]
        assert abs(week.net.sum()-s['week_net']) < 1e-8
        for day in s['week']:
            saved = week[week.day == day['day']]
            assert abs(day['nifty_gap']-n.at[day['day'], 'gap']) < 1e-12
            assert abs(saved.net.sum()-day['net']) < 1e-9
            assert set(saved.symbol) == {row['symbol'] for row in day['trades']}
    dump(OUT/'saved_arithmetic_validation.json', {'status': 'ok', 'unique_fills': len(unique),
        'checks': ['all three selections', 'unfunded slots', 'official OHLC stop/close fills',
                   'whole shares and fees', 'daily t and drawdown', 'screens', 'week slices'],
        'limit': 'Verification of frozen outputs only; no fresh evidence or opening-lock/calendar certification.'})
    print(f'Frozen H21 outputs reconciled: {len(unique)} distinct fills, all selections, screens and week slices.')


if __name__ == '__main__':
    main()
