"""The week simulation (stalk/sim/panic_replay.py): no look-ahead, parity with the frozen rule, quiet stretches skipped."""
import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

from stalk.config import IST
from stalk.sim import panic
from stalk.sim.panic_replay import WeekSim, build_week, stamp, weeks_available


class PanicWeekTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.days = ["2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
        cls.grouped = {"NIFTY 50": {}, "A": {}, "LOCK": {}, "RESULT": {}}
        for day in cls.days:
            panic_day = day == "2026-09-24"
            for symbol in cls.grouped:
                price = (99 if symbol == "NIFTY 50" else 95) if panic_day else 100
                rows = []
                for ts in range(stamp(day, 915), stamp(day, 1525) + 1, 300):
                    low, high = price - .1, price + .1
                    if panic_day and symbol == "A" and ts == stamp(day, 930):
                        low = 90
                    if symbol == "LOCK" and ts == stamp(day, 915):
                        high = low = price
                    rows.append([ts, price, high, low, price, 1000])
                cls.grouped[symbol][day] = rows
        cls.tiers = {s: "midcap" for s in ("A", "LOCK", "RESULT")}
        cls.results = pd.DataFrame([{"day": "2026-09-24", "symbol": "RESULT"}])
        cls.frames = build_week(cls.grouped, cls.tiers, cls.results, "2026-09-21")

    def frame(self, clock, day="2026-09-24"):
        return next(f for f in self.frames if f["day"] == day and f["clock"] == clock)

    def test_no_future_stop_or_return(self):
        self.assertIsNone(self.frame("09:15")["funnel"])
        self.assertEqual(len(self.frame("09:20")["positions"]), 1)
        self.assertFalse(self.frame("09:30")["trades"])            # the 09:30 candle that hits the stop hasn't completed yet
        self.assertEqual(self.frame("09:35")["trades"][0]["reason"], "stop")

    def test_parity_with_frozen_walk(self):
        c = panic.Candidate("A", 100, 95, "midcap")
        rows = [b for b in self.grouped["A"]["2026-09-24"] if b[0] <= stamp("2026-09-24", 935)]
        hms = [int(datetime.fromtimestamp(b[0], IST).strftime("%H%M")) for b in rows]
        expected = panic.as_dict(panic.walk("2026-09-24", c, rows, hms))
        actual = self.frames[-1]["trades"][0]
        self.assertEqual(expected, {k: actual[k] for k in expected})
        self.assertEqual(self.frames[-1]["realized"], actual["net"])

    def test_funnel_and_skipped_days(self):
        f = self.frame("09:20")["funnel"]
        self.assertEqual((f["reviewed"], f["fell"], f["results"], f["locked"], f["eligible"], f["picked"]), (3, 3, 1, 1, 1, 1))
        states = {d["day"]: d["state"] for d in self.frames[-1]["days"]}
        self.assertEqual(states["2026-09-21"], "skip")
        self.assertEqual(states["2026-09-24"], "done")
        self.assertTrue(self.frame("09:15", "2026-09-21")["quiet"])
        self.assertTrue(any(e["kind"] == "stop" for e in self.frames[-1]["log"]))

    def test_weeks_available(self):
        self.assertEqual(weeks_available(self.grouped), ["2026-09-21"])

    def test_quiet_days_are_skipped_after_a_pause(self):
        with patch("stalk.sim.panic_replay.time.monotonic", return_value=100.0):
            sim = WeekSim(self.frames, "2026-09-21")
            sim.control("play")
        with patch("stalk.sim.panic_replay.time.monotonic", return_value=104.0):
            self.assertEqual(sim.state()["day"], "2026-09-22")      # Monday had no trade: shown for 3 s, then Tuesday
        with self.assertRaises(ValueError):
            sim.control("speed-up")

    def test_jump_to_day_and_copy_isolation(self):
        sim = WeekSim(self.frames, "2026-09-21")
        sim.control("day", "2026-09-24")
        state = sim.state()
        self.assertEqual((state["day"], state["clock"]), ("2026-09-24", "09:15"))
        state["log"].clear()
        sim.control("day", "2026-09-25")
        self.assertTrue(sim.state()["log"])

    def test_bars_endpoint_has_no_lookahead(self):
        from fastapi.testclient import TestClient
        from stalk.api import server
        sim = WeekSim(self.frames, "2026-09-21")
        client = TestClient(server.app)
        with patch.object(server, "week_replay", sim), patch("stalk.sim.panic_replay.load_cache", return_value=(self.grouped, self.tiers, self.results)):
            sim.control("day", "2026-09-24")
            data = client.get("/api/sim/bars").json()
            self.assertEqual(data["symbol"], "NIFTY 50")
            self.assertEqual(data["markers"], [])
            self.assertIsNone(data["entry"])
            for clock in ("09:20", "09:30", "09:35", "15:10"):
                sim.idx = next(i for i, f in enumerate(sim.frames) if f["day"] == "2026-09-24" and f["clock"] == clock)
                data = client.get("/api/sim/bars?symbol=A").json()
                ts = self.frame(clock)["ts"]
                self.assertTrue(all(b[0] <= ts for b in data["bars"]))
                self.assertEqual(data["bars"][-1], [ts, 95, 95, 95, 95, 0])
                self.assertTrue(all(m["ts"] <= ts for m in data["markers"]))
                self.assertEqual(len(data["markers"]), 1 if clock < "09:35" else 2)
                self.assertEqual(data["stop"], 92.15)
                index = client.get("/api/sim/bars?symbol=NIFTY%2050").json()
                self.assertEqual(index["symbol"], "NIFTY 50")
                self.assertEqual(index["markers"], [])
            sim.control("day", "2026-09-25")
            data = client.get("/api/sim/bars").json()
            self.assertEqual(data["symbol"], "NIFTY 50")
            self.assertEqual(data["markers"], [])
            self.assertIn("No trade today", data["note"])
            self.assertEqual(client.get("/api/sim/bars?symbol=UNKNOWN").status_code, 400)
            # The original cached candle was never modified by masking.
            self.assertEqual(self.grouped["A"]["2026-09-24"][3][3], 90)
        with patch.object(server, "week_replay", None):
            self.assertEqual(client.get("/api/sim/bars").status_code, 404)

    def test_history_variant_is_separate(self):
        from stalk.data.store import Store
        from stalk.sim.panic_replay import save_to_history
        store = Store(":memory:")
        try:
            a = save_to_history(store, self.frames, "2026-09-21")
            b = save_to_history(store, self.frames, "2026-09-21", label_suffix=" −0.3%", prefix="v")
            self.assertNotEqual(a, b)
            self.assertEqual(len(store.campaigns(10)), 2)
            self.assertTrue(store.campaign(b)["summary"]["label"].startswith("Panic buy −0.3%, week of"))
        finally:
            store._conn.close()


if __name__ == "__main__":
    unittest.main()
