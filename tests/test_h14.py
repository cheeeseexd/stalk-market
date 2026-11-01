"""H14 causality, fill, sizing and one-shot gate guards, on synthetic prices."""
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from scripts import h14_fade as h


class H14Tests(unittest.TestCase):
    def setUp(self):
        self.c = h.Candidate('2026-08-05', 'TEST', 'smallcap', 105., 100., 100 / 105 - 1, 'long')
        times = np.arange(h.stamp(self.c.day, 915), h.stamp(self.c.day, 1510) + 1, 300)
        self.bars = np.array([[ts, 100., 101., 99., 100., 1000.] for ts in times])

    def test_both_sides_one_top_two_portfolio_and_strict_threshold(self):
        cs = [replace(self.c, symbol=s, gap=g, side='long' if g < 0 else 'short')
              for s, g in [('CCC', .03), ('BBB', -.05), ('AAA', .05), ('DDD', .04)]]
        self.assertEqual([c.symbol for c in h.pick(cs, .03)], ['AAA', 'BBB'])
        self.assertEqual(h.pick(cs[:1], .03), [])

    def test_preentry_stop_extrema_do_not_leak(self):
        self.bars[0, 2:4] = [120., 80.]
        for side in ('long', 'short'):
            t, fail = h.walk(replace(self.c, side=side), self.bars)
            self.assertIsNone(fail)
            self.assertEqual(t['reason'], 'time')
            self.assertLess(t['net'], 0)
            self.assertLessEqual(t['qty'] * t['entry'], 5000)

    def test_later_entry_does_not_observe_earlier_stop(self):
        self.bars[1, 3] = 90.
        t, _ = h.walk(self.c, self.bars, 925)
        self.assertEqual(t['reason'], 'time')
        t, _ = h.walk(self.c, self.bars, 920)
        self.assertEqual(t['reason'], 'stop')

    def test_gap_through_stops_fill_at_adverse_open_both_directions(self):
        for side, opening, hi, lo in [('long', 95., 96., 94.), ('short', 105., 106., 104.)]:
            b = self.bars.copy()
            b[2, 1:5] = [opening, hi, lo, opening]
            t, fail = h.walk(replace(self.c, side=side), b)
            self.assertIsNone(fail)
            self.assertEqual(t['exit_hm'], 925)
            direction = 1 if side == 'long' else -1
            self.assertAlmostEqual(t['exit'], opening * (1 - direction * .001))

    def test_stop_uses_slipped_entry_and_exit_slip(self):
        self.bars[1, 3] = 97.
        t, _ = h.walk(self.c, self.bars)
        self.assertAlmostEqual(t['entry'], 100.1)
        self.assertAlmostEqual(t['exit'], 100.1 * .98 * .999)

    def test_1510_extrema_are_not_seen(self):
        self.bars[-1, 1:5] = [102., np.nan, np.nan, np.nan]
        t, fail = h.walk(self.c, self.bars)
        self.assertIsNone(fail)
        self.assertEqual(t['reason'], 'time')
        self.assertAlmostEqual(t['exit'], 102 * .999)

    def test_missing_candle_or_exit_is_unscorable(self):
        for b in (self.bars[:-1], np.delete(self.bars, 4, axis=0)):
            t, fail = h.walk(self.c, b)
            self.assertIsNone(t)
            self.assertEqual(fail, 'missing_or_duplicate_path')

    def test_no_replacement_for_zero_share_and_tier_costs(self):
        b = self.bars.copy()
        b[:, 1:5] *= 100
        self.assertEqual(h.walk(self.c, b)[1], 'zero_shares')
        small, _ = h.walk(self.c, self.bars)
        nxt, _ = h.walk(replace(self.c, tier='next50'), self.bars)
        self.assertGreater(nxt['net_return'], small['net_return'])

    def test_short_sell_tax_on_entry_and_buy_stamp_on_cover(self):
        sell, buy = 5000., 4000.
        taxed = (sell + buy) * (.0003 + .0000307 + .000001)
        expected = taxed * 1.18 + sell * .00025 + buy * .00003
        expected += (sell + buy) * (0.01 / 1e7) * 1.18
        self.assertAlmostEqual(h.fees('SELL', 10, 500, 400), expected)

    def test_event_future_date_only_usable_after_publication(self):
        cutoff = h.stamp(self.c.day, 908)
        ev = [('KNOWN', '2026-08-08', cutoff - 1), ('FUTURE', '2026-08-06', cutoff + 1),
              ('BOUNDARY', '2026-08-05', cutoff), ('FAR', '2026-08-09', cutoff - 1)]
        self.assertEqual(h.scheduled_near(ev, self.c.day, cutoff), {'KNOWN'})

    def test_primary_gate_no_variant_or_side_rescue(self):
        s = {'cells': {h.PRIMARY: {'pnl': -1., 't_day': 3}, 'winning_variant': {'pnl': 100, 't_day': 5}}}
        self.assertFalse(h.design_pass(s))
        s['cells'][h.PRIMARY] = {'pnl': 1, 't_day': 1.49}
        self.assertFalse(h.design_pass(s))
        s['cells'][h.PRIMARY]['t_day'] = 1.5
        self.assertTrue(h.design_pass(s))

    def test_holdout_gates_concentration_and_minimum_trades(self):
        s = {'unknown_days': 0, 'cells': {h.PRIMARY: dict(pnl=100, t_day=2., trades=39,
                                                      best_day_share=.26, unscorable=0)}}
        g = h.confirmation_gates(s)
        self.assertFalse(g['at_least_40_trades'])
        self.assertFalse(g['best_day_le_25pct'])
        s['cells'][h.PRIMARY].update(trades=40, best_day_share=.25)
        self.assertTrue(all(h.confirmation_gates(s).values()))

    def test_failed_design_never_calls_holdout_or_creates_marker(self):
        s = {'cells': {h.PRIMARY: {'pnl': -1, 't_day': -1}}}
        with patch.object(h, 'verify_inputs'), patch.object(h, 'exclusive') as ex, \
                patch.object(h, 'phase', return_value=s) as phase, patch.object(h, 'dump'), \
                patch.object(h, 'sha', return_value='hash'), patch('builtins.print'):
            h.score()
        phase.assert_called_once_with('design')
        ex.assert_called_once()
        self.assertEqual(ex.call_args.args[0].name, 'scoring_started.json')

    def test_constant_return_t_is_undefined(self):
        self.assertIsNone(h.tstat([.02, .02 + 1e-16, .02]))


if __name__ == '__main__':
    unittest.main()
