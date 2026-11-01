"""H30 capitulation scorer on synthetic bars: signal logic, causality and execution. No real data."""
import unittest
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from stalk.config import IST
from scripts import h30_capitulation as h

DAYS = [(datetime(2021, 1, 1)+timedelta(days=i)).strftime('%Y-%m-%d') for i in range(20)]


def ts(day, hm):
    return int(datetime.fromisoformat(f'{day}T{hm//100:02d}:{hm%100:02d}:00').replace(tzinfo=IST).timestamp())


def frame(edits=None):
    """75 flat bars a day at ₹100, 20,000 shares a bar. edits: {(day, hm): (o, h, l, c, v)}."""
    rows = []
    for d in DAYS:
        for hm in h.GRID:
            o, hi, lo, c, v = (edits or {}).get((d, hm), (100, 100.2, 99.8, 100, 20000))
            rows.append([ts(d, hm), o, hi, lo, c, v])
    return pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v'])


def crash(day, at=945, depth=0.96, vol=100000, after=None):
    """The stock slides to `depth` by `at` on a volume spike and closes off the low; `after` sets later bars' price."""
    edits = {(day, at): (depth*100+0.6, depth*100+1.0, depth*100-0.5, depth*100+0.6, vol)}   # closes in the upper half
    for hm in h.GRID:
        if hm > at:
            p = after if after is not None else depth*100+0.6
            edits[(day, hm)] = (p, p+0.1, p-0.1, p, 20000)
    return edits


def universe_frames(target_edits):
    frames = {f'S{i:02d}': frame() for i in range(24)}
    frames['TGT'] = frame(target_edits)
    return frames, {s: 'midcap' for s in frames}


class SignalTests(unittest.TestCase):
    def test_capitulation_long_is_found_and_entered_next_bar(self):
        frames, tiers = universe_frames(crash(DAYS[16], after=97.5))
        B = h.build(frames, tiers)
        picks = h.signals(B, {})
        self.assertEqual([(p['day'], p['symbol'], p['side'], p['signal_hm']) for p in picks], [(DAYS[16], 'TGT', 'long', 945)])
        r = h.walk_all(B, picks[0]['i'], 945, 'long', 0.0)
        k = picks[0]['k']
        self.assertAlmostEqual(r['entry'][k], 97.5*(1+h.SLIP['midcap']), places=3)       # 09:50 open, not the signal close
        self.assertEqual(r['reason'][k], 'flatten')
        self.assertTrue(r['ok'][k])

    def test_no_signal_without_volume_spike_or_with_close_at_low(self):
        weak = crash(DAYS[16], vol=30000)
        at_low = {(DAYS[16], 945): (98, 98.5, 95.5, 95.6, 100000)}
        for edits in (weak, at_low):
            frames, tiers = universe_frames(edits)
            self.assertEqual(h.signals(h.build(frames, tiers), {}), [])

    def test_market_wide_fall_is_not_a_capitulation_signal(self):
        frames, tiers = universe_frames(crash(DAYS[16]))
        for s in list(frames):
            if s != 'TGT':
                frames[s] = frame({(DAYS[16], hm): (99, 99.1, 98.9, 99, 20000) for hm in h.GRID})   # everyone -1%
        self.assertEqual(h.signals(h.build(frames, tiers), {}), [])

    def test_results_day_exclusion(self):
        frames, tiers = universe_frames(crash(DAYS[16]))
        self.assertEqual(h.signals(h.build(frames, tiers), {DAYS[16]: {'TGT'}}), [])

    def test_needs_14_prior_complete_sessions(self):
        frames, tiers = universe_frames(crash(DAYS[5]))
        self.assertEqual(h.signals(h.build(frames, tiers), {}), [])

    def test_later_days_cannot_change_earlier_picks(self):
        frames, tiers = universe_frames(crash(DAYS[16]))
        before = [(p['day'], p['symbol'], p['signal_hm']) for p in h.signals(h.build(frames, tiers), {})]
        edits = crash(DAYS[16])
        edits.update(crash(DAYS[18], vol=500000))
        for hm in h.GRID:
            edits[(DAYS[17], hm)] = (130, 131, 129, 130, 900000)
        frames2, _ = universe_frames(edits)
        after = [(p['day'], p['symbol'], p['signal_hm']) for p in h.signals(h.build(frames2, tiers), {}) if p['day'] <= DAYS[16]]
        self.assertEqual(before, after)


class ShortTests(unittest.TestCase):
    def test_buying_climax_gives_a_short_with_a_stop_above(self):
        edits = {(DAYS[16], 1000): (103.4, 104.5, 103.0, 103.4, 100000)}      # +3.4% on 5x volume, closes in the lower half
        for hm in h.GRID:
            if hm > 1000:
                edits[(DAYS[16], hm)] = (103.0, 103.1, 102.9, 103.0, 20000)
        edits[(DAYS[16], 1200)] = (103.0, 105.0, 102.9, 104.8, 20000)        # squeezes through the 1.5% stop
        frames, tiers = universe_frames(edits)
        B = h.build(frames, tiers)
        picks = h.signals(B, {})
        self.assertEqual([(p['side'], p['signal_hm']) for p in picks], [('short', 1000)])
        r = h.walk_all(B, picks[0]['i'], 1000, 'short', 0.0)
        k = picks[0]['k']
        e = 103.0*(1-h.SLIP['midcap'])
        self.assertAlmostEqual(r['entry'][k], e, places=4)
        self.assertEqual(r['reason'][k], 'stop')
        self.assertLess(r['net'][k], 0)


class ExecutionTests(unittest.TestCase):
    def test_long_stop_and_gap_through(self):
        frames, tiers = universe_frames(crash(DAYS[16], after=90))        # next bar opens far below the 1.5% stop
        B = h.build(frames, tiers)
        p = h.signals(B, {})[0]
        r = h.walk_all(B, p['i'], p['signal_hm'], 'long', 0.0)
        k = p['k']
        self.assertEqual(r['reason'][k], 'flatten')                          # entry is at 90, it never fell 1.5% below that
        edits = crash(DAYS[16], after=96.3)
        edits[(DAYS[16], 1100)] = (96.3, 96.4, 94.0, 94.5, 20000)            # touches the stop intrabar
        frames, tiers = universe_frames(edits)
        B = h.build(frames, tiers)
        p = h.signals(B, {})[0]
        r = h.walk_all(B, p['i'], p['signal_hm'], 'long', 0.0)
        e = r['entry'][p['k']]
        self.assertEqual(r['reason'][p['k']], 'stop')
        self.assertAlmostEqual(r['exit'][p['k']], e*(1-h.STOP)*(1-h.SLIP['midcap']), places=4)

    def test_worse_fill_stress_raises_long_entry(self):
        frames, tiers = universe_frames(crash(DAYS[16]))
        B = h.build(frames, tiers)
        p = h.signals(B, {})[0]
        a = h.walk_all(B, p['i'], p['signal_hm'], 'long', 0.0)['entry'][p['k']]
        w = h.walk_all(B, p['i'], p['signal_hm'], 'long', 0.005)['entry'][p['k']]
        self.assertAlmostEqual(w/a, 1.005, places=6)

    def test_missing_path_bar_is_unknown_not_zero(self):
        frames, tiers = universe_frames(crash(DAYS[16]))
        f = frames['TGT']
        frames['TGT'] = f[f.ts != ts(DAYS[16], 1200)]
        B = h.build(frames, tiers)
        picks = h.signals(B, {})
        if picks:                                                            # the day may simply become ineligible
            r = h.walk_all(B, picks[0]['i'], picks[0]['signal_hm'], 'long', 0.0)
            self.assertFalse(r['ok'][picks[0]['k']])
            self.assertTrue(np.isnan(r['net'][picks[0]['k']]))


if __name__ == '__main__':
    unittest.main()
