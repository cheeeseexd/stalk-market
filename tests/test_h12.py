"""H12 safeguards: sector breadth, exclusions, matching, execution and holdout gates."""
import unittest
from unittest.mock import patch

import pandas as pd

from scripts import h12_sector as h12
from scripts.h9_news import fees
from stalk.config import DEFAULT_UNIVERSE


def candidate(symbol, gap, sector='Banks', day='2017-06-01'):
    return dict(day=day, symbol=symbol, gap=gap, sector=sector, sector_gap=-.02,
                open=100., high=104., low=98., close=102.)


class H12Tests(unittest.TestCase):
    def test_sector_uses_nifty_and_results_members_but_cannot_buy_them(self):
        nifty = DEFAULT_UNIVERSE[0]
        symbols = [nifty, 'RESULT', 'A', 'B', 'C', 'ETF']
        today = pd.DataFrame([dict(open=97., high=100., low=96., close=99., prev_close=100., value=0)
                              for _ in symbols], index=symbols)
        prev = pd.DataFrame({'value': [1e8] * len(symbols)}, index=symbols)
        mapping = {s: 'Banks' for s in symbols}
        pool, sectors, info, missing = h12.build_pool(today, prev, mapping, {'ETF'}, {'RESULT'}, '2017-06-01')
        self.assertEqual(sectors.loc['Banks', 'members'], 5)
        self.assertAlmostEqual(sectors.loc['Banks', 'sector_gap'], -.03)
        self.assertEqual(set(pool.symbol), {'A', 'B', 'C'})
        self.assertEqual(missing, [])
        self.assertEqual(info['etf_excluded'], 1)

    def test_liquidity_is_previous_day_and_four_members_do_not_make_a_sector(self):
        syms = list('ABCDE')
        today = pd.DataFrame([dict(open=97., high=100., low=96., close=99., prev_close=100., value=1e12)
                              for _ in syms], index=syms)
        previous = pd.DataFrame({'value': [1e8]*4+[1e8-1]}, index=syms)
        pool, sectors, _, _ = h12.build_pool(today, previous, dict.fromkeys(syms, 'Banks'), set(), set(), '2017-06-01')
        self.assertTrue(sectors.empty)
        self.assertTrue(pool.empty)

    def test_future_extremes_do_not_change_the_sector_median_and_locks_cannot_be_bought(self):
        syms = list('ABCDEF')
        today = pd.DataFrame([dict(open=97., high=100., low=96., close=99., prev_close=100., value=1)
                              for _ in syms], index=syms)
        previous = pd.DataFrame({'value': [1e8]*6}, index=syms)
        mapping = dict.fromkeys(syms[:5], 'Banks')
        _, before, _, missing = h12.build_pool(today, previous, mapping, set(), set(), '2017-06-01')
        today.loc['A', ['high', 'low', 'close']] = 97.
        pool, after, info, _ = h12.build_pool(today, previous, mapping, set(), set(), '2017-06-01')
        pd.testing.assert_frame_equal(before, after)
        self.assertNotIn('A', set(pool.symbol))
        self.assertEqual(missing, ['F'])
        self.assertEqual(info['all_day_locks_excluded'], 1)

    def test_worst_sector_cannot_be_replaced_by_a_sector_with_clean_names(self):
        sectors = pd.DataFrame({'sector_gap': [-.03, -.02], 'members': [5, 5]}, index=['A', 'B'])
        pool = pd.DataFrame([candidate('B1', -.04, 'B')])
        picks, chosen = h12.select_sector(pool, sectors, .015)
        self.assertEqual(chosen, 'A')
        self.assertTrue(picks.empty)
        sectors.loc['A', 'sector_gap'] = -.015
        picks, chosen = h12.select_sector(pool, sectors.iloc[:1], .015)
        self.assertIsNone(chosen)

    def test_unknown_results_window_is_not_clean_and_calendar_days_include_weekends(self):
        covered = set(pd.date_range('2017-06-02', '2017-06-08').strftime('%Y-%m-%d'))
        metadata = {'_covered': covered, 'events_by_filing_date': {'2017-06-04': ['A']}}
        self.assertEqual(h12.event_window('2017-06-05', metadata), (True, {'A'}))
        covered.remove('2017-06-03')
        self.assertFalse(h12.event_window('2017-06-05', metadata)[0])

    def test_control_matching_gap_caliper_sector_year_no_reuse_and_cap(self):
        treated = pd.DataFrame([candidate(f'T{i}', -.04, day=f'2017-06-{i+1:02}') for i in range(4)])
        calm = pd.DataFrame([candidate('C1', -.0401, day='2017-07-01'),
                             candidate('C2', -.041, day='2017-07-01'),
                             candidate('C3', -.04, day='2017-07-01'),
                             candidate('WRONGYEAR', -.04, day='2018-07-01'),
                             candidate('WRONGSECTOR', -.04, sector='Finance', day='2017-07-02'),
                             candidate('TOOFAR', -.043, day='2017-07-02')])
        left, right = h12.match_controls(treated, calm)
        self.assertEqual(len(left), 2)
        self.assertEqual(len(right[['day', 'symbol']].drop_duplicates()), 2)
        self.assertEqual(right.groupby('day').size().max(), 2)
        self.assertTrue(((left.gap.to_numpy()-right.gap.to_numpy()).__abs__() <= .0025).all())
        # No future OHLC/return field may affect matching.
        calm[['close', 'high', 'low']] = -10000
        _, changed = h12.match_controls(treated, calm)
        self.assertEqual(list(changed.symbol), list(right.symbol))

    def test_control_day_cannot_mix_sectors(self):
        treated = pd.DataFrame([candidate('T1', -.04, sector='A'), candidate('T2', -.04, sector='B')])
        calm = pd.DataFrame([candidate('C1', -.04, sector='A', day='2017-07-01'),
                             candidate('C2', -.04, sector='B', day='2017-07-01')])
        _, right = h12.match_controls(treated, calm)
        self.assertEqual(len(right), 1)

    def test_entry_stress_fees_exit_debit_and_integer_budget(self):
        row = pd.Series(candidate('A', -.04))
        trades = [h12.score(pd.DataFrame([row]), worse)[0].iloc[0] for worse in (0., .005, .01)]
        base = trades[0]
        self.assertAlmostEqual(base.entry, 100.1)
        self.assertAlmostEqual(base.exit, (102.-100.1*.0008)*.999)
        self.assertAlmostEqual(base.charges, fees('BUY', base.qty, base.entry, base.exit))
        self.assertGreater(base.net_return, trades[1].net_return)
        self.assertGreater(trades[1].net_return, trades[2].net_return)
        for t in trades:
            self.assertLessEqual(t.qty*t.entry, 5000.)
            self.assertEqual(t.qty, int(t.qty))

    def test_stop_and_zero_share_picks_are_not_silently_replaced(self):
        row = candidate('A', -.04)
        row['low'] = 90.
        trades, _ = h12.score(pd.DataFrame([row]), 0)
        t = trades.iloc[0]
        self.assertTrue(t.stop)
        self.assertAlmostEqual(t.exit, t.entry*.97*.999)
        row['open'] = 6000.
        trades, skipped = h12.score(pd.DataFrame([row]), 0)
        self.assertTrue(trades.empty)
        self.assertEqual(skipped, 1)

    def test_confirmation_gate_cannot_be_rescued_by_neighbour(self):
        p = {'pnl': -1., 't_trade': 3., 't_day': 3.}
        summary = {'cells': {h12.BASE: p, 'sector_1pct_0bp_worse': {'pnl': 10000}}}
        self.assertFalse(h12.primary_pass(summary))
        p.update(pnl=1., t_day=1.49)
        self.assertFalse(h12.primary_pass(summary))
        p['t_day'] = 1.5
        self.assertTrue(h12.primary_pass(summary))

    def test_constant_stop_returns_do_not_produce_a_huge_t_from_roundoff(self):
        self.assertIsNone(h12.tstat([-.03201335, -.03201335+1e-17, -.03201335-1e-17]))
        self.assertIsNone(h12.tstat([.01]))
        self.assertAlmostEqual(h12.tstat([.01, .02, .03]), 3.4641016151377544)

    def test_confirmation_needs_trade_day_power_concentration_frequency_and_neighbours(self):
        p = {'pnl': 1., 't_trade': 2.5, 't_day': 2.49, 'trades': 99,
             'positive_years': 2, 'best_day_share': .26}
        summary = {'years': h12.CONFIRM, 'complete_interior_data': True,
                   'cells': {h12.BASE: p, 'sector_1pct_0bp_worse': {'pnl': 1},
                             'sector_2pct_0bp_worse': {'pnl': -1}}}
        gates = h12.confirmation_gates(summary)
        self.assertTrue(gates['t_trade'])
        for key in ['t_day', 'at_least_100_trades', 'most_years_positive',
                    'best_day_at_most_25pct', 'neighbours_positive']:
            self.assertFalse(gates[key])

    def test_market_panic_and_unknown_news_do_not_load_stock_return_pools(self):
        n = pd.DataFrame({'prev_day': ['2017-05-31', '2017-06-01'], 'gap': [-.006, 0.]},
                         index=['2017-06-01', '2017-06-02'])
        # One known calendar date cannot establish either seven-day exclusion window.
        metadata = {'_covered': {'2017-06-01'}, 'events_by_filing_date': {}, 'mapping': {}, 'etfs': []}
        with patch.object(h12, 'build_pool') as build, patch.object(pd.DataFrame, 'to_csv'), \
                patch.object(h12, 'dump'), patch.object(pd, 'read_csv', return_value=pd.DataFrame({'day': []})), \
                patch('builtins.print'):
            result = h12.phase('synthetic', [2017], n, metadata)
        build.assert_not_called()
        self.assertEqual(result['cells'][h12.BASE]['trades'], 0)
        self.assertFalse(h12.primary_pass(result))


if __name__ == '__main__':
    unittest.main()
