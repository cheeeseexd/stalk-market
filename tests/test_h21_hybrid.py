"""H21 signal causality, filters, branch boundaries and one-run persistence."""
from collections import deque
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pandas as pd

from scripts.h21_hybrid import (checks, execute, known_results, marker, past_std,
                               pool, select, statistics, ts, update_history)
from stalk.market.costs import round_trip_cost
from scripts.h17_rebuild import historical_symbol


class H21Tests(unittest.TestCase):
    def opening(self):
        return pd.DataFrame(dict(open=[90., 95., 97., 98.], prev_close=[100.]*4,
            high=[96., 99., 99., 100.], low=[89., 94., 96., 97.],
            close=[95., 98., 98., 99.], value=[0.]*4), index=['SPLIT', 'A', 'B', 'EDGE'])

    def previous(self):
        return pd.DataFrame(dict(value=[1e8]*4, close=[100.]*4, prev_close=[99.]*4),
                            index=['SPLIT', 'A', 'B', 'EDGE'])

    def test_same_day_outcomes_turnover_and_full_day_lock_do_not_change_pool(self):
        before, _ = pool('2024-12-09', self.opening(), self.previous())
        after = self.opening()
        after[['high', 'low', 'close', 'value']] = [90., 90., 90., 1e12]
        pd.testing.assert_frame_equal(before, pool('2024-12-09', after, self.previous())[0])

    def test_future_price_changes_cannot_change_earlier_candidates(self):
        dates = pd.bdate_range('2024-10-01', periods=30).strftime('%Y-%m-%d')
        originals = {d: self.opening() for d in dates}
        later = {d: f.copy() for d, f in originals.items()}
        for d in dates[25:]:
            later[d].loc[:, ['open', 'prev_close', 'close', 'value']] *= 7

        def build(frames):
            h, result, previous = {}, {}, self.previous()
            for i, (day, frame) in enumerate(frames.items()):
                result[day] = pool(day, frame, previous, std20=past_std(h, i))[0]
                update_history(h, frame, i)
                previous = frame.assign(value=1e8)
            return result

        first, changed = build(originals), build(later)
        for d in dates[:25]:
            pd.testing.assert_frame_equal(first[d], changed[d])

    def test_sigma_uses_twenty_previous_returns_and_not_current_close(self):
        h = {'A': deque([(i, i/1000) for i in range(20)], maxlen=20)}
        self.assertAlmostEqual(past_std(h, 20)['A'], np.std(np.arange(20)/1000, ddof=1))
        self.assertNotIn('A', past_std(h, 21))
        update_history(h, pd.DataFrame({'close': [1000.], 'prev_close': [100.]}, index=['A']), 20)
        self.assertNotIn('A', past_std(h, 20))
        self.assertGreater(past_std(h, 21)['A'], .1)

    def test_missing_session_or_invalid_close_blocks_sigma(self):
        h = {'A': deque([(i, .01) for i in range(20)], maxlen=20)}
        update_history(h, pd.DataFrame({'close': [100.], 'prev_close': [100.]}, index=['A']), 22)
        self.assertNotIn('A', past_std(h, 23))
        h['A'] = deque([(i, np.nan if i == 5 else .01) for i in range(20)], maxlen=20)
        self.assertNotIn('A', past_std(h, 20))

    def test_announcements_after_open_do_not_remove_candidates(self):
        event = dict(symbol='A', day='2024-12-10', purpose='Financial Results', detail='')
        events = pd.DataFrame([{**event, 'announced_ts': ts('2024-12-09', '09:16')}])
        self.assertEqual(known_results(events, '2024-12-09'), set())
        events.announced_ts = ts('2024-12-09', '09:15')
        self.assertEqual(known_results(events, '2024-12-09'), set())
        events.announced_ts = ts('2024-12-09', '09:14')
        self.assertEqual(known_results(events, '2024-12-09'), {'A'})
        events.announced_ts = np.nan
        self.assertEqual(known_results(events, '2024-12-09'), set())

    def test_actions_funds_results_and_known_locks_filter_before_ranking(self):
        p, _ = pool('2024-12-09', self.opening(), self.previous(), actions={'SPLIT'}, results={'A'})
        self.assertEqual(p.iloc[0].symbol, 'B')
        p, _ = pool('2024-12-09', self.opening(), self.previous(), funds={'SPLIT'}, locks={'A': True})
        self.assertNotIn('SPLIT', set(p.symbol))
        self.assertNotIn('A', set(p.symbol))
        self.assertEqual(set(p.lock_status), {'unknown'})

    def test_official_only_delisted_names_are_kept_price_mismatches_are_not(self):
        refs = {'A': {'open': 47.5, 'prev_close': 50.}}
        p, audit = pool('2024-12-09', self.opening(), self.previous(), wide_refs=refs)
        self.assertNotIn('A', set(p.symbol))
        self.assertIn('SPLIT', set(p.symbol))
        self.assertEqual(p.set_index('symbol').loc['SPLIT', 'price_source'], 'official_only')
        self.assertEqual(audit['price_basis_exclusions'], 1)

    def test_documented_dated_identity_cleans_old_symbol_without_whole_symbol_veto(self):
        changes = [{'OLD SYMBOL': 'SPLIT', 'NEW SYMBOL': 'NEW', 'effective': '2025-01-01'}]
        action_symbol = historical_symbol('NEW', '2024-12-09', changes)[0]
        p, _ = pool('2024-12-09', self.opening(), self.previous(), actions={action_symbol})
        self.assertNotIn('SPLIT', set(p.symbol))
        self.assertEqual(historical_symbol('NEW', '2025-01-01', changes)[0], 'NEW')
        self.assertIn('SPLIT', set(pool('2024-12-10', self.opening(), self.previous())[0].symbol))

    def test_liquidity_uses_previous_value_not_today(self):
        prev = self.previous()
        prev.loc['A', 'value'] = 99999999
        p, _ = pool('2024-12-09', self.opening().assign(value=1e12), prev)
        self.assertNotIn('A', set(p.symbol))

    def test_branches_sigma_filter_and_market_boundaries(self):
        n = pd.DataFrame({'gap': [-.00501, -.005, .002, .00201]}, index=['d0', 'd1', 'd2', 'd3'])
        p = pd.DataFrame([dict(day=d, symbol=s, gap=g, std20=sigma)
            for d in n.index for s, g, sigma in [('A', -.06, .04), ('B', -.05, .02), ('C', -.04, .02)]])
        self.assertNotIn('d0', set(select(p, n, 'A').day))
        self.assertEqual(set(select(p, n, 'C').day), {'d1', 'd2'})
        self.assertEqual(set(select(p, n, 'B').symbol), {'B', 'C'})

    def test_stop_flatten_fills_costs_and_no_refill(self):
        opening = self.opening()
        picks = pd.DataFrame([dict(day='2024-12-09', symbol=s, open=o, gap=-.05)
                              for s, o in [('SPLIT', 90.), ('A', 95.), ('B', 6000.)]])
        opening.loc['SPLIT', 'low'] = 85.
        with patch('scripts.h21_hybrid.pd.read_pickle', return_value=opening):
            t = execute(picks)
        self.assertEqual(t.symbol.tolist(), ['SPLIT', 'A'])
        self.assertEqual(t.reason.tolist(), ['stop', 'flatten'])
        self.assertAlmostEqual(t.iloc[0]['exit'], 90*1.001*.97*.998*.999)
        self.assertAlmostEqual(t.iloc[1]['exit'], 98*.9992*.999)
        for r in t.itertuples():
            self.assertAlmostEqual(r.charges, round_trip_cost(r.qty, r.entry, r.exit))
            self.assertLessEqual(r.entry*r.qty, 5000)

    def test_guard_is_exclusive_and_preserves_first_attempt(self):
        path = Path(__file__).resolve().parent / ('.h21_guard_'+uuid4().hex+'.json')
        try:
            marker(path, {'first': True})
            original = path.read_bytes()
            with self.assertRaises(FileExistsError):
                marker(path, {'second': True})
            self.assertEqual(path.read_bytes(), original)
        finally:
            path.unlink(missing_ok=True)

    def test_screen_cannot_be_rescued_by_panic_profit(self):
        baseline = {'max_drawdown': 100.}
        hybrid = {'max_drawdown': 149., 'net': 1000.}
        self.assertFalse(all(checks({'net': -1., 'day_t': 3.}, hybrid, baseline).values()))
        self.assertFalse(all(checks({'net': 1., 'day_t': 1.99}, hybrid, baseline).values()))
        self.assertTrue(all(checks({'net': 1., 'day_t': 2.}, hybrid, baseline).values()))
        self.assertFalse(all(checks({'net': 1., 'day_t': 2.}, {'max_drawdown': 151.}, baseline).values()))

    def test_stats_zero_days_unknown_days_and_initial_drawdown(self):
        t = pd.DataFrame({'day': ['d1', 'd2'], 'net': [-100., 200.],
                          'net_return': [-.02, .04], 'charges': [5., 5.]})
        r = statistics(t, ['d1', 'd2', 'd3'], ['d1', 'd2', 'd3', 'd4'])
        self.assertEqual(r['max_drawdown'], 100.)
        self.assertEqual(r['days_traded_share'], .5)
        self.assertEqual(r['unknown_sessions'], 1)
        self.assertAlmostEqual(r['day_t'], np.mean([-100., 200., 0.])/(np.std([-100., 200., 0.], ddof=1)/np.sqrt(3)))


if __name__ == '__main__':
    unittest.main()
