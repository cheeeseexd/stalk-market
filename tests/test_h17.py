"""H17 research guards: ex-date timing, displaced rankings and historical units."""
from datetime import datetime
import json
from types import SimpleNamespace
import unittest
from unittest.mock import mock_open, patch

import numpy as np
import pandas as pd

from scripts.h17_rebuild import account, account_stats, clean_pool, historical_symbol, publication, purpose_kind, random_control
from stalk.config import IST
from stalk.market.costs import round_trip_cost


class H17Tests(unittest.TestCase):
    def picks(self):
        return pd.DataFrame([
            dict(day='2020-01-01', symbol='SPLIT', gap=-.5, open=100., high=110., low=99., close=105., locked=False),
            dict(day='2020-01-01', symbol='B', gap=-.05, open=200., high=220., low=199., close=210., locked=False),
            dict(day='2020-01-01', symbol='C', gap=-.03, open=300., high=330., low=299., close=315., locked=False),
            dict(day='2020-01-01', symbol='BOUNDARY', gap=-.02, open=500., high=550., low=499., close=525., locked=False)])

    def test_action_excluded_before_two_pick_ranking(self):
        clean, excluded = clean_pool(self.picks(), '2020-01-01', {('SPLIT', '2020-01-01'): 'split'})
        self.assertEqual(clean.head(2).symbol.tolist(), ['B', 'C'])
        self.assertEqual(excluded.symbol.tolist(), ['SPLIT'])

    def test_exdate_only_no_whole_symbol_or_record_day_veto(self):
        clean, _ = clean_pool(self.picks(), '2020-01-01', {('SPLIT', '2020-01-02'): 'record day'})
        self.assertEqual(clean.head(2).symbol.tolist(), ['SPLIT', 'B'])

    def test_current_symbol_reversed_through_dated_rename_chain(self):
        changes = [{'OLD SYMBOL': 'B', 'NEW SYMBOL': 'C', 'effective': '2024-01-01'},
                   {'OLD SYMBOL': 'A', 'NEW SYMBOL': 'B', 'effective': '2020-01-01'}]
        self.assertEqual(historical_symbol('C', '2019-01-01', changes)[0], 'A')
        self.assertEqual(historical_symbol('C', '2021-01-01', changes)[0], 'B')
        self.assertEqual(historical_symbol('C', '2024-01-01', changes)[0], 'C')

    def test_locked_names_kept(self):
        p = self.picks()
        p.loc[1, 'locked'] = True
        clean, _ = clean_pool(p, '2020-01-01', {})
        self.assertIn('B', clean.head(2).symbol.tolist())

    def test_all_required_action_types(self):
        for text, kind in [('Face Value Split From Rs10 To Rs2', 'split'), ('Bonus 1:1', 'bonus'),
            ('Rights 2:5', 'rights'), ('Consolidation of shares', 'consolidation'),
            ('Scheme of Arrangement / Demerger', 'scheme'), ('Special Dividend Rs 10', 'dividend'),
            ('Capital Reduction', 'scheme'), ('Buyback', 'non_price')]:
            self.assertEqual(purpose_kind(text), kind)
        self.assertEqual(purpose_kind('Unclassified exchange basis event'), 'unknown_conservative')

    def test_unknown_publication_is_explicit(self):
        self.assertEqual(publication(None), (None, 'unknown_assume_advance'))
        self.assertEqual(publication('01-Jan-2020'), (None, 'unknown_assume_advance'))
        self.assertEqual(publication('01-Jan-2020 9:bad'), (None, 'invalid'))

    def test_publication_exact_open_is_not_before_cutoff(self):
        early, _ = publication('01-Jan-2020 09:14:59')
        exact, _ = publication('01-Jan-2020 09:15:00')
        cutoff = datetime(2020, 1, 1, 9, 15, tzinfo=IST)
        self.assertLess(early, cutoff)
        self.assertFalse(exact < cutoff)

    def test_actual_contemporaneous_whole_share_units_and_fees(self):
        a = account(self.picks().head(1), stop=False).iloc[0]
        entry = 100 * 1.001
        qty = int(5000 // entry)
        exit_px = 105 * .9992 * .999
        self.assertEqual(a.qty, qty)
        self.assertAlmostEqual(a.net, (exit_px-entry)*qty-round_trip_cost(qty, entry, exit_px))
        self.assertLessEqual(a.qty*a.entry, 5000)

    def test_stop_uses_slipped_entry_and_daily_low(self):
        p = self.picks().head(1)
        p.loc[0, 'low'] = 97.05  # above 97% official open, below 97% slipped entry
        a = account(p).iloc[0]
        self.assertTrue(a.stopped)
        self.assertAlmostEqual(a.exit, a.entry*.97*.998*.999)
        self.assertFalse(account(p, stop=False).iloc[0].stopped)

    def test_no_refill_for_unaffordable_ranked_pick(self):
        p = self.picks()
        p.loc[0, 'open'] = 6000.
        self.assertEqual(account(p.head(2)).symbol.tolist(), ['B'])

    def test_initial_capital_counts_in_drawdown_and_zero_days(self):
        a = account(self.picks().head(1))
        a['net'] = -100.
        stats = account_stats(a, ['2020-01-01', '2020-01-02'])
        self.assertEqual(stats['max_drawdown'], 100.)
        self.assertEqual(stats['market_mornings'], 2)
        self.assertEqual(stats['mornings'], 1)

    def test_random_preserves_without_replacement_counts(self):
        p = pd.DataFrame({'stop3': [.01, -.02]})
        draws = random_control([p, pd.DataFrame({'stop3': [.03]})], 'stop3')
        np.testing.assert_allclose(draws, (.01-.02+.03)/3)
        np.testing.assert_array_equal(draws, random_control([p, pd.DataFrame({'stop3': [.03]})], 'stop3'))

    def test_api_publishes_distinct_cleaned_evidence_without_runner_start(self):
        from stalk.api import server
        evidence = {'strict': {'status': 'Execution check', 'net_pct': 1.2},
                    'loose': {'status': 'Execution check', 'net_pct': .7}}
        with patch.object(server, 'get_agent', return_value=SimpleNamespace(panic_error=None, panic_loose_error=None)), \
             patch('builtins.open', mock_open(read_data=json.dumps(evidence))), \
             patch.object(server.os.path, 'exists', return_value=False):
            result = server.panic_test()
        self.assertEqual(result['strict']['evidence']['net_pct'], 1.2)
        self.assertEqual(result['loose']['evidence']['net_pct'], .7)
        self.assertEqual(result['strict']['trades'], [])

    def test_api_missing_cleaned_artifact_is_under_review(self):
        from stalk.api import server
        with patch.object(server, 'get_agent', return_value=SimpleNamespace(panic_error=None, panic_loose_error=None)), \
             patch('builtins.open', side_effect=FileNotFoundError), \
             patch.object(server.os.path, 'exists', return_value=False):
            result = server.panic_test()
        self.assertEqual(result['strict']['evidence']['status'], 'Under review')
        self.assertNotIn('net_pct', result['strict']['evidence'])


if __name__ == '__main__':
    unittest.main()
