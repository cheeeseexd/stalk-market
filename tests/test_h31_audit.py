"""Astra audit fixes (2026-11-01): eligibility must be knowable at the open, and a stock that stops trading mid-day must
still produce a trade (exit at its last price) instead of silently dropping out of the results."""
import unittest

import numpy as np

from scripts import h30_capitulation as h
from scripts import h31_lab as lab
from tests.test_h30 import DAYS, crash, frame, ts, universe_frames


class CausalEligibilityTests(unittest.TestCase):
    def setUp(self):
        edits = crash(DAYS[16], at=930, after=97.5)
        self.frames, self.tiers = universe_frames(edits)
        f = self.frames['TGT']
        halt = ts(DAYS[16], 1200)
        self.frames['TGT'] = f[~((f.ts >= halt) & (f.ts < ts(DAYS[16], 1530)))]     # stops trading at 12:00

    def test_old_rule_used_the_future_and_new_rule_does_not(self):
        cal = sorted({d for d in DAYS})
        old = h.symbol_tables(self.frames['TGT'], cal)
        new = h.symbol_tables(self.frames['TGT'], cal, causal=True)
        i = cal.index(DAYS[16])
        self.assertFalse(old['eligible'][i])      # needed to know the day would run to 15:25
        self.assertTrue(new['eligible'][i])       # only needs the 09:15 bar

    def test_halted_trade_is_counted_at_its_last_price(self):
        B = h.build(self.frames, self.tiers, causal=True)
        i, k = B['calendar'].index(DAYS[16]), B['symbols'].index('TGT')
        r = lab.walk(B, i, 930, k, 'short', 0.03, None)
        self.assertIsNotNone(r)
        last = float(B['o'][i, h.GRID.index(1155), k])                       # the last bar before the halt
        self.assertAlmostEqual(r['exit']/(1+h.SLIP['midcap']), last, places=3)
        self.assertEqual(r['reason'], 'flatten')


if __name__ == '__main__':
    unittest.main()
