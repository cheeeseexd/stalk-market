"""H9 research guards: timestamp causality, direction-aware costs and confirmation gate."""
from datetime import datetime
import unittest
from unittest.mock import patch

import pandas as pd

from scripts.h9_news import (confirmation_gates, design_pass, execute, fees, fresh_day,
                             is_material, publication)
from stalk.config import IST
from scripts import h9_news


class H9ResearchTests(unittest.TestCase):
    def test_portfolio_caps_both_sides_together_and_unknown_news_is_not_control(self):
        n = pd.DataFrame({'prev_day': ['2016-12-30', '2017-01-02'], 'gap': [0., 0.]},
                         index=['2017-01-02', '2017-01-03'])
        pool = pd.DataFrame([dict(day='2017-01-02', symbol=s, gap=g, abs_gap=abs(g),
                                  open=100., high=102., low=98., close=100.)
                             for s, g in [('AAA', .06), ('BBB', -.05), ('CCC', .04)]])
        with patch.object(h9_news, 'eligible_pool', return_value=(pool, {'status': 'ok'})) as build, \
                patch.object(pd.DataFrame, 'to_csv'), patch.object(h9_news, 'dump'), patch('builtins.print'):
            result = h9_news.phase('synthetic', [2017], n,
                                  {'2017-01-02': {'AAA', 'BBB', 'CCC'}}, {'2017-01-02': set()}, set(),
                                  {'2017-01-02': True, '2017-01-03': False})
        build.assert_called_once()
        primary = result['cells']['news_3pct_0bp_worse']
        self.assertEqual(primary['trades'], 2)
        self.assertEqual(primary['sides']['long']['trades'], 1)
        self.assertEqual(primary['sides']['short']['trades'], 1)
        self.assertEqual(result['cells']['no_news_fade_3pct_0bp_worse']['trades'], 0)
        self.assertFalse(result['complete_ordinary_coverage'])

    def test_freshness_uses_actual_previous_session_and_strict_boundaries(self):
        days = ['2026-09-21', '2026-09-22']
        cutoffs = [datetime(2026, 9, d, 9, 8, tzinfo=IST) for d in (21, 22)]
        lower = [datetime(2026, 9, d, 15, 30, tzinfo=IST) for d in (18, 21)]
        self.assertEqual(fresh_day(datetime(2026, 9, 19, 12, tzinfo=IST), days, cutoffs, lower), days[0])
        for ts in (lower[0], cutoffs[0], datetime(2026, 9, 21, 10, tzinfo=IST)):
            self.assertIsNone(fresh_day(ts, days, cutoffs, lower))
        self.assertEqual(fresh_day(datetime(2026, 9, 21, 16, tzinfo=IST), days, cutoffs, lower), days[1])

    def test_later_dissemination_overrides_earlier_receipt(self):
        row = {'an_dt': '21-Sep-2026 09:07:00', 'exchdisstime': '21-Sep-2026 09:09:00'}
        self.assertEqual(publication(row).minute, 9)
        row['exchdisstime'] = 'malformed'
        self.assertIsNone(publication(row))
        row['exchdisstime'] = '-'
        self.assertEqual(publication(row).minute, 7)

    def test_results_are_not_material_h9_news_even_if_classifier_says_material(self):
        for category in ('Financial Result Updates', 'Outcome of Board Meeting', 'Integrated Filing- Financial'):
            self.assertFalse(is_material(category, 'major contract wins'))
        self.assertFalse(is_material('Trading Window', 'major contract wins'))
        self.assertTrue(is_material('Bagging/Receiving of orders/contracts', 'binding contract'))

    def test_short_fees_put_stt_on_entry_and_stamp_on_cover(self):
        # Independently calculate schedule with different entry/exit turnovers.
        sell, buy = 5000., 4000.
        turnover = sell + buy
        brokerage = .0003 * turnover
        exchange = .0000307 * turnover
        sebi = .000001 * turnover
        expected = brokerage + exchange + sebi + .00025 * sell + .00003 * buy
        expected += .18 * (brokerage + exchange + sebi)
        self.assertAlmostEqual(fees('SELL', 10, 500, 400), expected)

    def test_equal_prices_lose_for_both_directions_and_stress_is_adverse(self):
        row = pd.Series(dict(day='2017-01-02', symbol='TEST', gap=.04, open=100., high=102., low=98., close=100.))
        for side in (1, -1):
            base, stress = execute(row, side), execute(row, side, .005)
            self.assertLess(base['net'], 0)
            self.assertLess(stress['net_return'], base['net_return'])
            self.assertLessEqual(base['qty'] * base['entry'], 5000.)
            self.assertFalse(base['stop'])

    def test_daily_low_and_high_trigger_long_and_short_stops(self):
        row = pd.Series(dict(day='2017-01-02', symbol='TEST', gap=-.04, open=100., high=104., low=96., close=100.))
        for side in (1, -1):
            trade = execute(row, side)
            self.assertTrue(trade['stop'])
            self.assertLess(trade['net_return'], -.03)

    def test_combined_design_cannot_be_rescued_by_another_cell(self):
        summary = {'cells': {'news_3pct_0bp_worse': {'pnl': -1, 't_trade': 2, 't_day': 2},
                             'news_2pct_0bp_worse': {'pnl': 1000, 't_trade': 10, 't_day': 10}}}
        self.assertFalse(design_pass(summary))
        summary['cells']['news_3pct_0bp_worse'].update(pnl=100, t_day=1.49)
        self.assertFalse(design_pass(summary))

    def test_confirmation_requires_day_t_concentration_and_plateau(self):
        summary = {'years': [2022, 2023, 2024, 2025, 2026], 'complete_ordinary_coverage': True,
                   'cells': {'news_3pct_0bp_worse': {'pnl': 1000, 't_trade': 3., 't_day': 2.49,
                              'trades': 100, 'positive_years': 3, 'best_day_share': .26},
                             'news_2pct_0bp_worse': {'pnl': 1}, 'news_5pct_0bp_worse': {'pnl': -1}}}
        gates = confirmation_gates(summary)
        self.assertFalse(gates['t_day'])
        self.assertFalse(gates['best_day_at_most_25pct'])
        self.assertFalse(gates['neighbours_positive'])
        self.assertTrue(gates['at_least_100_trades'])


if __name__ == '__main__':
    unittest.main()
