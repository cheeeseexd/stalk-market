"""H19 decision causality, price bases, delayed entries and closed return gate."""
import unittest

import numpy as np
import pandas as pd

from scripts.h19_range import (classify, confirmation_allowed, entry_open,
                               quantity, reference_ok, select)


class H19Tests(unittest.TestCase):
    def test_reclaim_and_rejection_are_mirrored(self):
        long = classify(95, 98, 89, 94, 1000, 90, 110)
        short = classify(105, 111, 102, 106, 1000, 90, 110)
        self.assertEqual(long[0], 'long')
        self.assertAlmostEqual(long[1], 1 / 90)
        self.assertEqual(short[0], 'short')
        self.assertAlmostEqual(short[1], 1 / 110)

    def test_touch_and_boundary_close_do_not_qualify(self):
        self.assertEqual(classify(95, 98, 90, 94, 100, 90, 110)[2], 'no_sweep')
        self.assertEqual(classify(95, 98, 89, 90, 100, 90, 110)[2], 'close_not_inside')
        self.assertEqual(classify(105, 111, 102, 110, 100, 90, 110)[2], 'close_not_inside')

    def test_two_sided_sweep_is_ambiguous(self):
        self.assertEqual(classify(100, 111, 89, 100, 100, 90, 110)[2], 'both_boundaries')

    def test_invalid_prices_and_volume_are_unavailable(self):
        for candle in [(95, 94, 89, 96, 100), (95, 98, 89, 94, -1),
                       (95, 98, 89, np.nan, 100), (0, 98, 89, 94, 100)]:
            self.assertEqual(classify(*candle, 90, 110)[2], 'invalid_signal_or_reference')

    def test_reference_rejects_half_scale_and_incomplete_predecessor(self):
        self.assertTrue(reference_ok(90, 110, 90.1, 109.9, True))
        self.assertFalse(reference_ok(90, 110, 45, 55, True))
        self.assertFalse(reference_ok(90, 110, 90, 110, False))
        self.assertFalse(reference_ok(np.nan, 110, 90, 110, True))
        self.assertFalse(reference_ok(110, 90, 90, 110, True))

    def test_selection_caps_both_sides_and_breaks_ties_by_symbol(self):
        f = pd.DataFrame([dict(day='2024-01-02', symbol=s, sweep=v, side=side)
                          for s, v, side in [('C', .02, 'long'), ('B', .03, 'short'),
                                             ('A', .03, 'long')]])
        self.assertEqual(select(f).symbol.tolist(), ['A', 'B'])
        self.assertEqual(select(f.assign(future_net=[1e6, -1e6, 0])).symbol.tolist(), ['A', 'B'])

    def entry_bars(self):
        ts = pd.Timestamp('2024-01-02 11:15', tz='Asia/Kolkata').timestamp()
        return pd.DataFrame({'ts': [ts, ts + 300], 'o': [50., 100.],
                             'h': [1e9, 1e9], 'l': [.01, .01], 'c': [1., 1.]})

    def test_entry_waits_five_minutes_and_masks_future_ranges(self):
        b = self.entry_bars()
        self.assertEqual(entry_open(b, '2024-01-02'), (100., 'available'))
        self.assertEqual(entry_open(b.assign(h=-99, l=np.nan, c=np.inf), '2024-01-02'), (100., 'available'))

    def test_missing_entry_never_uses_decision_open(self):
        self.assertEqual(entry_open(self.entry_bars().iloc[:1], '2024-01-02'), (None, 'missing_entry'))

    def test_duplicate_or_invalid_entry_is_unavailable(self):
        b = self.entry_bars()
        self.assertEqual(entry_open(pd.concat([b, b.iloc[1:]]), '2024-01-02')[1], 'duplicate_entry')
        self.assertEqual(entry_open(b.assign(o=np.nan), '2024-01-02')[1], 'invalid_entry')

    def test_quantity_uses_adverse_side_slippage_and_no_leverage(self):
        self.assertEqual(quantity(5000, 'long', 'smallcap'), 0)
        self.assertEqual(quantity(5000, 'short', 'smallcap'), 1)
        self.assertEqual(quantity(None, 'long', 'midcap'), 0)
        self.assertLessEqual(quantity(100, 'long', 'next50') * 100.04, 5000)

    def test_reused_or_incomplete_design_cannot_unlock_confirmation(self):
        for fresh, complete in [(False, True), (True, False), (False, False)]:
            self.assertFalse(confirmation_allowed(1000, 10, fresh=fresh, complete=complete, trades=500, days=200))

    def test_design_gate_requires_rupees_day_t_and_independent_power(self):
        def gate(net=1, t=1.5, trades=100, days=40):
            return confirmation_allowed(net, t, fresh=True, complete=True, trades=trades, days=days)
        self.assertTrue(gate())
        for args in [dict(net=0), dict(net=-1), dict(t=1.499), dict(t=None),
                     dict(t=np.nan), dict(trades=99), dict(days=39)]:
            self.assertFalse(gate(**args))


if __name__ == '__main__':
    unittest.main()
