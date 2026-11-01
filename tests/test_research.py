"""Charges, causal features and signals, trade rules, research/engine parity, Time Machine."""
import unittest

import numpy as np
import pandas as pd

from stalk.config import Settings
from stalk.market.costs import fill_cost, round_trip_cost, round_trip_cost_pct
from stalk.research import timemachine
from stalk.research.features import build_features, index_frame
from stalk.research.strategies import STRATEGIES
from stalk.research.trade import Trade, size, walk
from stalk.research.validate import strategy_events
from stalk.sim.engine import DayEngine
from tests.helpers import make_bars, make_daily

USED = ["atr", "adr", "vwap", "rday", "c_prev", "ema9", "ema21", "prev_close", "gap_sigma", "rvol", "or_hi_30", "or_lo_30",
        "or_hi_15", "or_hi_45", "hi52", "idx_rday", "bank_rday", "rsi", "pdh", "pdl", "idx_cross4"]


def loose(**kw) -> Settings:
    """Settings with every account limit opened up, so the engine takes every first-of-day signal."""
    base = dict(capital=1e9, max_notional=1e9, risk_per_trade=1000.0, loss_halt=1e9, max_trades_per_day=1000,
                max_positions=1000, max_consecutive_losses=1000, lockout_after_losses=0, cooldown_min=0, cost_gate_mult=0.0,
                breadth_filter=False, sector_filter=False, avoid_events=False, rank_by_score=False, with_index=False,
                require_validated=False, lunch_start="12:00", lunch_end="12:00")
    base.update(kw)
    return Settings(**base)


class ChargesTests(unittest.TestCase):
    def test_zerodha_intraday_round_trip_on_one_lakh(self):
        # ₹1,00,000 buy and sell: brokerage 20+20, STT 25, exchange 5.94, SEBI 0.2, stamp 3, GST 18% of 46.14
        total = round_trip_cost(100, 1000.0, 1000.0)
        self.assertAlmostEqual(total, 40 + 25 + 5.94 + 0.2 + 3 + 0.18 * (40 + 5.94 + 0.2), places=2)
        self.assertAlmostEqual(round_trip_cost_pct(100000), total / 100000, places=8)

    def test_brokerage_uses_percentage_below_cap(self):
        c = fill_cost("BUY", 10, 500.0)  # ₹5,000 order -> 0.03% = ₹1.50 < ₹20
        self.assertAlmostEqual(c.brokerage, 1.5, places=6)
        self.assertEqual(c.stt, 0.0)
        self.assertGreater(fill_cost("SELL", 10, 500.0).stt, 0)


class CausalTests(unittest.TestCase):
    def setUp(self):
        self.stock = make_bars(days=30, seed=3)
        self.daily = make_daily(end="2026-06-01")
        self.idx = index_frame(make_bars(days=30, seed=9, price=24000, volume=0),
                               make_bars(days=30, seed=11, price=52000, volume=0))

    def test_features_use_only_past_bars(self):
        full = build_features(self.stock, self.daily, self.idx)
        cut = 1500
        part = build_features(self.stock.iloc[:cut], self.daily, self.idx)
        a, b = full[USED].iloc[:cut].to_numpy(dtype=float), part[USED].to_numpy(dtype=float)
        self.assertTrue(np.allclose(a, b, equal_nan=True))

    def test_signals_use_only_past_bars(self):
        full = build_features(self.stock, self.daily, self.idx)
        cut = 1500
        part = build_features(self.stock.iloc[:cut], self.daily, self.idx)
        for k, st in STRATEGIES.items():
            for v in st.grid:
                self.assertTrue(np.array_equal(st.signals(full, v)[:cut], st.signals(part, v)), f"{k}={v}")

    def test_opening_range_unknown_until_it_closes(self):
        f = build_features(self.stock, self.daily, self.idx)
        early = f[f["end_minute"] < 9 * 60 + 45]
        self.assertTrue(early["or_hi_30"].isna().all())
        self.assertTrue(f[f["end_minute"] >= 9 * 60 + 45]["or_hi_30"].notna().all())


class ResampleTests(unittest.TestCase):
    def test_fifteen_minute_bars_from_five(self):
        from stalk.research.features import bar_size, resample
        five = make_bars(days=3, seed=7)
        f = resample(five, 15)
        self.assertEqual(len(f), 3 * 25)
        self.assertEqual(bar_size(f), 15)
        first = five.iloc[:3]
        self.assertAlmostEqual(f["o"].iat[0], first["o"].iat[0])
        self.assertAlmostEqual(f["h"].iat[0], first["h"].max())
        self.assertAlmostEqual(f["c"].iat[0], first["c"].iat[2])
        self.assertAlmostEqual(f["v"].iat[0], first["v"].sum())

    def test_forming_bar_is_dropped(self):
        from stalk.research.features import resample
        five = make_bars(days=1, seed=7).iloc[:4]          # 09:15-09:35: one full 15-minute bar plus one 5-minute bar
        self.assertEqual(len(resample(five, 15)), 1)


class StopSizeTests(unittest.TestCase):
    def test_range_stop_uses_average_daily_range(self):
        from stalk.research.trade import stop_distance
        self.assertAlmostEqual(stop_distance(2.0, 20.0, Settings(stop_mode="range", stop_range=0.35)), 7.0)
        self.assertAlmostEqual(stop_distance(2.0, 20.0, Settings(stop_mode="atr", stop_atr=3.0)), 6.0)
        self.assertEqual(stop_distance(2.0, float("nan"), Settings(stop_mode="range")), 0.0)

    def test_adr_excludes_today(self):
        f = build_features(make_bars(days=20, seed=5), None, None)
        days = sorted(f["day"].unique())
        rng = f.groupby("day").apply(lambda g: g["h"].max() - g["l"].min())
        want = rng[days[5:15]].mean()
        self.assertAlmostEqual(f[f["day"] == days[15]]["adr"].iat[0], want, places=6)


class TradeRuleTests(unittest.TestCase):
    s = Settings(slippage_bps=0.0, stop_atr=1.5, breakeven_at_r=1.0)

    def run_trade(self, side, bars, atr=1.0):
        return walk([(i, *b, 600 + 5 * i) for i, b in enumerate(bars)], "X", side, "orb_breakout", 1.5 * atr, 10, self.s)

    def test_stop_fills_at_stop_or_worse_on_gap(self):
        t = self.run_trade("long", [(100, 100.5, 99.9, 100.2), (97, 97.5, 96, 97)])  # stop 98.5, gap to 97
        self.assertEqual(t.reason, "stop")
        self.assertAlmostEqual(t.exit, 97.0)

    def test_stop_checked_before_target_in_same_bar(self):
        t = self.run_trade("long", [(100, 104, 98, 101)])
        self.assertEqual(t.reason, "stop")

    def test_target_and_breakeven(self):
        t = self.run_trade("long", [(100, 103.1, 99.8, 103)])
        self.assertEqual(t.reason, "target")
        self.assertAlmostEqual(t.exit, 103.0)
        t = self.run_trade("long", [(100, 101.6, 99.8, 101.4), (101, 101.2, 99.9, 100)])
        self.assertEqual(t.reason, "breakeven")

    def test_flatten_at_close_of_last_bar(self):
        bars = [(i, 100, 100.2, 99.9, 100.1, 15 * 60 + 5 + 5 * i) for i in range(3)]
        t = walk(bars, "X", "short", "gap_fade", 1.5, 10, self.s)
        self.assertEqual(t.reason, "flatten")
        self.assertEqual(t.exit_ts, 1)

    def test_size_respects_risk_and_notional(self):
        s = Settings(leverage=5.0, max_notional=50000.0)
        self.assertEqual(size(100.0, 1.0, s, 10000), 100)            # ₹100 risk / ₹1 stop
        self.assertEqual(size(1000.0, 0.5, s, 10000), 50)            # capped by ₹50,000 notional
        self.assertEqual(size(1000.0, 0.5, s, 2000), 10)             # capped by equity × 5 leverage
        self.assertEqual(size(1000.0, 0.5, Settings(), 10000), 10)   # default: no leverage, position ≤ ₹10,000


class ParityTests(unittest.TestCase):
    """The engine, with its account limits opened up, takes exactly the trades research counts."""

    def test_engine_reproduces_research_trades(self):
        s = loose()
        stock = make_bars(days=30, seed=21, vol=0.003)
        f = {"AAA": build_features(stock, None, None), "BBB": build_features(make_bars(days=30, seed=22), None, None)}
        ev = strategy_events(f, "vwap_pullback", 0.005, s)
        self.assertGreater(len(ev), 10)
        got = []
        for day in sorted(ev["day"].unique()):
            eng = DayEngine(s, day, f"t:{day}", s.capital, f, ["vwap_pullback"], {}, use_tm=False)
            for ts in eng.timeline():
                eng.on_bar(ts)
            eng.finish()
            got += [(t["day"], t["symbol"], t["side"], round(t["r"], 3)) for t in eng.closed]
        want = [(r.day, r.symbol, r.side, round(r.r, 3)) for r in ev.itertuples()]
        self.assertEqual(sorted(got), sorted(want))


class TimeMachineTests(unittest.TestCase):
    def setUp(self):
        self.f = build_features(make_bars(days=70, seed=4), None, None)
        self.day = sorted(self.f["day"].unique())[65]

    def test_deterministic_and_sums_to_one(self):
        a = timemachine.odds(self.f, self.day, 600, "long", 1000.0, 3.0, 2.0, 910)
        b = timemachine.odds(self.f, self.day, 600, "long", 1000.0, 3.0, 2.0, 910)
        self.assertEqual(a, b)
        self.assertAlmostEqual(a["p_target"] + a["p_stop"] + a["p_open"], 1.0, places=2)
        self.assertEqual(a["sessions"], 59)

    def test_uses_only_earlier_sessions(self):
        a = timemachine.odds(self.f, self.day, 600, "short", 1000.0, 3.0, 2.0, 910)
        g = self.f.copy()
        later = g["day"] >= self.day
        g.loc[later, ["o", "h", "l", "c"]] *= 3.0
        self.assertEqual(a, timemachine.odds(g, self.day, 600, "short", 1000.0, 3.0, 2.0, 910))

    def test_needs_history(self):
        early = sorted(self.f["day"].unique())[5]
        self.assertIsNone(timemachine.odds(self.f, early, 600, "long", 1000.0, 3.0, 2.0, 910))


if __name__ == "__main__":
    unittest.main()
