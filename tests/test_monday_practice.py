import unittest
from unittest.mock import Mock, patch
import pandas as pd

from scripts.monday_practice import COLS, DAY, plan_for, simulate
from scripts.publish_run import publish


class MondayPracticeTests(unittest.TestCase):
    def test_weak_rebound_is_skipped_without_drex_veto(self):
        facts = dict(five_session_change=-.08, friday_close=100, first_candle=[915,99,101,98,100],
                     market_panic_verified=True, full_universe_rank_verified=True, events_verified=True)
        for opinion in (0, 1):
            plans = plan_for({**facts, 'drex': {'close_up': opinion}})
            self.assertEqual(plans['baseline']['side'], 'long')
            self.assertEqual(plans['checklist']['side'], 'none')
            self.assertIn('smaller', plans['checklist']['reason'])

    def test_missing_evidence_is_not_permission_and_shorts_unchanged(self):
        facts = dict(five_session_change=-.08, friday_close=100, first_candle=[915,95,96,94,95])
        self.assertEqual(plan_for(facts)['checklist']['side'], 'none')
        verified = dict(facts, market_panic_verified=True, full_universe_rank_verified=True, events_verified=True)
        self.assertEqual(plan_for(verified)['checklist']['side'], 'long')
        plans = plan_for(dict(facts, five_session_change=.08))
        self.assertEqual(plans['baseline']['side'], plans['checklist']['side'])

    def test_entry_bar_stop_and_gap_through_stop(self):
        bars = [[915,100,101,99,100], [920,100,101,96,97], [925,90,91,89,90], [1510,110,110,110,110]]
        plan = dict(side='long', stop_pct=.03)
        t = simulate('X', bars, plan, .004)
        self.assertEqual((t['exit_hm'], t['reason'], t['exit']), (920, 'stop', 97))
        self.assertEqual(t['costs'], 20)
        self.assertEqual(t['net'], -170)
        bars[1] = [920,100,101,99,100]
        self.assertEqual(simulate('X', bars, plan, .004)['exit'], 90)

    def test_worse_entry_resets_size_and_stop(self):
        bars = [[915,100,101,99,100], [920,100,101,96,97], [1510,110,110,110,110]]
        t = simulate('X', bars, dict(side='long', stop_pct=.03), .004, .005)
        self.assertEqual(t['qty'], 49)
        self.assertAlmostEqual(t['entry'], 100.5)
        self.assertAlmostEqual(t['exit'], 100.5*.97)

    def test_synthetic_zero_trade_run_never_reads_market_calendar(self):
        store = Mock()
        with patch('scripts.publish_run.Store', return_value=store), \
             patch('scripts.publish_run.pd.read_pickle', side_effect=AssertionError('must not read history')):
            publish('synthetic_test', 'Practice', pd.DataFrame(columns=COLS), DAY, DAY, 'Practice',
                    session_days=[DAY], kind='synthetic', decisions=[{'symbol':'X','action':'Skip','reason':'unknown'}])
        obj = store.save_campaign.call_args.args[-1]
        self.assertEqual(obj['kind'], 'synthetic')
        self.assertEqual(obj['accounts']['main']['net'], 0)
        self.assertEqual(obj['accounts']['main']['trades'], 0)
        self.assertEqual(obj['decisions'][0]['action'], 'Skip')

    def test_synthetic_exit_timestamp_is_actual_stop_time(self):
        store = Mock()
        t = simulate('X', [[915,100,101,99,100], [920,100,101,96,97], [1510,110,110,110,110]],
                     dict(side='long', stop_pct=.03), .004)
        with patch('scripts.publish_run.Store', return_value=store):
            publish('synthetic_test', 'Practice', pd.DataFrame([t]), DAY, DAY, 'Practice', session_days=[DAY], kind='synthetic')
        row = store.save_trade.call_args.args[0]
        self.assertEqual(row['entry_ts'], row['exit_ts'])
        self.assertEqual(row['broker'], 'synthetic')


if __name__ == '__main__':
    unittest.main()
