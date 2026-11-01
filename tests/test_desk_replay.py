"""The Simulation page's desk week must trade exactly what the ML ranker (setting B, with its 8x-predicted-move target) and panic
buy picked, nothing more, and exit the same way the backtest does."""
import os
import unittest

import pandas as pd

from stalk.config import BASE_DIR

DATA = os.path.join(BASE_DIR, "data")
NEEDED = [os.path.join(DATA, "h31_explore", "h34_preds_dev2.pkl"), os.path.join(DATA, "h31_explore", "trades_h34_cfg_B_target.csv"),
          os.path.join(DATA, "kite_5m", "ADANIENSOL.pkl")]


@unittest.skipUnless(all(os.path.exists(p) for p in NEEDED), "needs the stored research data")
class DeskWeekTests(unittest.TestCase):
    def test_week_matches_the_stored_setting_b_trades(self):
        from stalk.sim import desk_replay as d
        frames = d.build_week("2023-03-13")
        self.assertTrue(all(f["mode"] == "desk" for f in frames))
        sim = pd.DataFrame(frames[-1]["trades"])
        ref = pd.read_csv(NEEDED[1])
        ref = ref[(ref.day >= "2023-03-13") & (ref.day <= "2023-03-17")]
        self.assertEqual(sorted(zip(sim.day, sim.symbol, sim.side)), sorted(zip(ref.day, ref.symbol, ref.side)))
        merged = sim.merge(ref, on=["day", "symbol"], suffixes=("", "_ref"))
        self.assertLess((merged.net - merged.net_ref).abs().max(), 5.0)      # same rule; rounding and intrabar order only
        self.assertTrue(all(len(f["positions"]) <= 2 for f in frames))       # ₹10,000 in two ₹5,000 slots
        self.assertEqual(sorted(merged.reason.replace({'close': 'flatten'})), sorted(merged.reason_ref))


if __name__ == "__main__":
    unittest.main()
