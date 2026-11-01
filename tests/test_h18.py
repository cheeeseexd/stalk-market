"""H18 causal boundaries, contrarian selection and confirmation safeguards."""
import unittest
import numpy as np
import pandas as pd

from scripts.h18_daily import (PICK_COLUMNS, confirm_gates, design_pass, fees,
                              select, stats, TRADE_COLUMNS, walk)
from stalk.market.costs import fill_cost


class H18Tests(unittest.TestCase):
    def features(self):
        return pd.DataFrame([
            dict(day='2024-01-02', symbol='A', relative=-.04, second=.006, market=-.002),
            dict(day='2024-01-02', symbol='B', relative=.03, second=-.006, market=-.002),
            dict(day='2024-01-02', symbol='C', relative=-.025, second=-.001, market=-.002),
            dict(day='2024-01-02', symbol='D', relative=.02, second=-.005, market=-.002)])

    def pick(self, side='long'):
        p = dict.fromkeys(PICK_COLUMNS, 0)
        p.update(day='2024-01-02', symbol='A', side=side, tier='midcap', qty=49,
                 entry_open=100., phase='design', rule='failed')
        return p

    def bars(self):
        start = pd.Timestamp('2024-01-02 11:15', tz='Asia/Kolkata').timestamp()
        return pd.DataFrame({'ts': start + np.arange(4) * 3600, 'o': 100.,
                             'h': 101., 'l': 99., 'c': 100., 'v': 100.})

    def test_reversal_is_required_and_positions_capped_across_sides(self):
        p, c = select(self.features(), 'failed')
        self.assertEqual(p.symbol.tolist(), ['A', 'B'])
        self.assertEqual(c.symbol.tolist(), ['A', 'B', 'D'])
        self.assertEqual(p.side.tolist(), ['long', 'short'])

    def test_diagnostic_omits_only_reversal(self):
        _, candidates = select(self.features(), 'no_reversal')
        self.assertEqual(candidates.symbol.tolist(), ['A', 'B', 'C', 'D'])

    def test_market_filter_before_rank_including_boundary(self):
        p, _ = select(self.features(), 'with_market')
        self.assertEqual(p.symbol.tolist(), ['B', 'D'])
        f = self.features().assign(market=0.)
        self.assertTrue(select(f, 'with_market')[0].empty)
        self.assertTrue(select(f.assign(market=np.nan), 'with_market')[0].empty)

    def test_tie_is_symbol_and_future_columns_cannot_change_selection(self):
        f = self.features().assign(relative=-.03, second=.01, outcome=[5., -99., 40., 0.])
        p, _ = select(f.iloc[::-1], 'failed')
        self.assertEqual(p.symbol.tolist(), ['A', 'B'])
        self.assertEqual(select(f.assign(outcome=0), 'failed')[0].symbol.tolist(), ['A', 'B'])

    def test_1415_extrema_and_close_cannot_trigger_stop(self):
        b = self.bars()
        b.loc[3, ['h', 'l', 'c']] = [10000., .001, .001]
        t, error = walk(self.pick(), b)
        self.assertIsNone(error)
        self.assertEqual(t['reason'], 'time')
        self.assertAlmostEqual(t['exit'], 100 * .9994)

    def test_stop_gap_worse_than_level_long_and_short(self):
        for side, opening, low, high in [('long', 95., 94., 96.), ('short', 105., 104., 106.)]:
            b = self.bars()
            b.loc[1, ['o', 'h', 'l', 'c']] = [opening, high, low, opening]
            t, error = walk(self.pick(side), b)
            self.assertIsNone(error)
            self.assertEqual(t['reason'], 'stop')
            self.assertEqual(t['exit_hm'], 1215)
            self.assertAlmostEqual(t['exit'], opening * (1 - (.0006 if side == 'long' else -.0006)))

    def test_gap_at_exit_is_stop_gap(self):
        b = self.bars()
        b.loc[3, 'o'] = 95.
        t, _ = walk(self.pick(), b)
        self.assertEqual(t['reason'], 'stop_gap_at_exit')
        self.assertEqual(t['exit_hm'], 1415)

    def test_missing_bar_never_invents_fill(self):
        self.assertEqual(walk(self.pick(), self.bars().drop(index=1))[1], 'missing_or_duplicate_path')
        b = pd.concat([self.bars(), self.bars().iloc[[0]]])
        self.assertEqual(walk(self.pick(), b)[1], 'missing_or_duplicate_path')

    def test_short_fees_use_entry_sell_and_exit_buy(self):
        expected = (fill_cost('SELL', 20, 100).total + fill_cost('BUY', 20, 90).total
                    + 20 * 190 * (.000001 + .01 / 1e7) * 1.18)
        self.assertAlmostEqual(fees('short', 20, 100, 90), expected)
        self.assertNotEqual(fees('short', 20, 100, 90), fees('long', 20, 100, 90))

    def test_whole_share_allocation_and_entry_cost(self):
        t, _ = walk(self.pick(), self.bars())
        self.assertLessEqual(t['qty'] * t['entry'], 5000)
        self.assertEqual(t['qty'], 49)
        self.assertLess(t['net'], t['gross'])

    def test_primary_gates_do_not_accept_neighbour_or_low_power(self):
        self.assertFalse(design_pass({'net': -1., 't_day': 4.}))
        self.assertFalse(design_pass({'net': 1., 't_day': 1.49}))
        self.assertTrue(design_pass({'net': 1., 't_day': 1.5}))
        g = confirm_gates({'net': 1., 't_day': 2.5, 'trades': 99, 'best_day_share': .25})
        self.assertFalse(all(g.values()))
        self.assertFalse(g['trades_ge_100'])

    def test_empty_results_and_zero_days_are_explicit(self):
        t = pd.DataFrame(columns=TRADE_COLUMNS)
        s = stats(t, ['2024-01-02'])
        self.assertEqual(s['trades'], 0)
        self.assertIsNone(s['t_day'])
        self.assertIsNone(s['net_bps'])


if __name__ == '__main__':
    unittest.main()
