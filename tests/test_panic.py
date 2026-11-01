"""The frozen panic-buy rule (stalk/sim/panic.py) and its paper runner's settlement."""
import asyncio
import json
import os
import shutil
import uuid
from contextlib import contextmanager
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from datetime import datetime

from stalk.config import IST
from stalk.sim import panic
from stalk.sim.panic_live import PanicPaper


@contextmanager
def journal_directory():
    # Default mkdir permissions also work in restricted Windows workspaces.
    path = os.path.join(os.path.dirname(__file__), "panic_test_" + uuid.uuid4().hex)
    os.mkdir(path)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def bars_for(day, rows):
    """rows: [(HHMM, o, h, l, c)] → [[ts, o, h, l, c, v]]"""
    out = []
    for hm, o, h, l, c in rows:
        ts = int(datetime.strptime(f"{day} {hm:04d}", "%Y-%m-%d %H%M").replace(tzinfo=IST).timestamp())
        out.append([ts, o, h, l, c, 1000])
    return out


class PanicRuleTests(unittest.TestCase):
    def test_market_gate(self):
        self.assertTrue(panic.market_qualifies(99.4, 100))      # −0.6%
        self.assertFalse(panic.market_qualifies(99.6, 100))     # −0.4%

    def test_pick_biggest_eligible(self):
        cands = [panic.Candidate("A", 100, 97), panic.Candidate("B", 100, 95), panic.Candidate("C", 100, 99),
                 panic.Candidate("D", 100, 90, results_near=True), panic.Candidate("E", 100, 92, locked=True),
                 panic.Candidate("F", 100, 96)]
        self.assertEqual([c.symbol for c in panic.pick(cands)], ["B", "F"])

    def test_stop_and_close(self):
        day = "2026-11-02"
        c = panic.Candidate("X", 100, 96, tier="midcap")
        # enters at the 09:20 open (96), never reaches the 3% stop (93.12), sells at the 15:10 open (98)
        t = panic.walk(day, c, *self._split(bars_for(day, [(915, 96, 96.5, 95, 96), (920, 96, 97, 95, 96.5), (1505, 97, 98, 97, 98), (1510, 98, 98, 98, 98)])))
        self.assertEqual(t.reason, "close")
        self.assertAlmostEqual(t.exit, 98 * (1 - panic.SLIP["midcap"]), places=2)
        self.assertEqual(t.qty, panic.shares(96))
        # a later bar trades through the stop: out at the stop
        t = panic.walk(day, c, *self._split(bars_for(day, [(915, 96, 96, 95, 96), (920, 96, 96, 95, 95.5), (1000, 95, 95, 92, 92.5), (1510, 99, 99, 99, 99)])))
        self.assertEqual(t.reason, "stop")
        self.assertAlmostEqual(t.exit, 96 * 0.97 * (1 - panic.SLIP["midcap"]), places=2)
        self.assertLess(t.net, 0)

    def test_gap_through_stop_fills_at_open(self):
        day = "2026-11-02"
        c = panic.Candidate("X", 100, 96)
        t = panic.walk(day, c, *self._split(bars_for(day, [(915, 96, 96, 95, 96), (920, 96, 96, 95, 95.5), (1000, 90, 91, 89, 90)])))
        self.assertAlmostEqual(t.exit, 90 * (1 - panic.SLIP["midcap"]), places=2)

    def test_settle_waits_while_open(self):
        day = "2026-11-02"
        p = PanicPaper(store=None, log_path="nul")
        c = panic.Candidate("X", 100, 96)
        self.assertIsNone(p.settle(day, c, bars_for(day, [(915, 96, 96, 95, 96), (920, 96, 97, 95.5, 96.5), (1000, 96.5, 97, 96, 96.8)])))

    @staticmethod
    def _split(bars):
        hms = [datetime.fromtimestamp(b[0], IST).hour * 100 + datetime.fromtimestamp(b[0], IST).minute for b in bars]
        return bars, hms


class PanicVariantTests(unittest.TestCase):
    def test_entry_journals_use_each_variants_actual_reference(self):
        from stalk.sim.panic_auction import PanicAuctionPaper
        day = "2026-11-02"
        stock = bars_for(day, [(915, 94, 96, 93, 95), (920, 96, 97, 95, 96)])
        candidate = panic.Candidate("X", 100, 94)
        with journal_directory() as tmp:
            for cls, hm, price in ((PanicPaper, 920, 96), (PanicAuctionPaper, 915, 94)):
                with self.subTest(variant=cls.__name__):
                    runner = cls()
                    runner.log_path = os.path.join(tmp, cls.__name__ + ".jsonl")
                    with patch("stalk.sim.panic_live.httpx.Client"), \
                         patch("stalk.sim.panic_live.nse_client"), \
                         patch("stalk.sim.panic_live.preopen", return_value={"X": (100, 94)}), \
                         patch("stalk.sim.panic_live.results_near", return_value=set()), \
                         patch("stalk.sim.panic_live.five_min", return_value=stock), \
                         patch("stalk.sim.panic_live.announcements", return_value=[]), \
                         patch.object(runner, "morning", return_value=[candidate]), \
                         patch.object(runner, "settle", return_value=None):
                        asyncio.run(runner.tick(datetime(2026, 11, 2, 9, 25, tzinfo=IST)))
                    with open(runner.log_path, encoding="utf-8") as f:
                        row = json.loads(f.readline())
                    self.assertEqual(row["kind"], "entry")
                    self.assertEqual(row["entry_hm"], hm)
                    self.assertEqual(row["picks"][0]["entry_bar_open"], price)
                    stamp = datetime.fromisoformat(row["picks"][0]["entry_bar_at"])
                    self.assertEqual(stamp.hour * 100 + stamp.minute, hm)
                    self.assertIn("not a realised fill", row["entry_basis"])
                    if hm == 915:
                        self.assertIn("selected after the open", row["entry_basis"])
                        self.assertIn("09:07", row["entry_basis"])

    def test_default_gate_and_optional_threshold(self):
        self.assertEqual(panic.INDEX_GAP, -0.005)
        self.assertFalse(panic.market_qualifies(99.6, 100))
        self.assertTrue(panic.market_qualifies(99.6, 100, threshold=-0.003))
        for op in (99, 99.4, 99.6, 100):
            self.assertEqual(panic.market_qualifies(op, 100), panic.market_qualifies(op, 100, -0.005))

    def test_runner_gate_and_journal_isolation(self):
        from stalk.sim.panic_live import LOG, LOOSE_LOG
        self.assertEqual(PanicPaper().log_path, LOG)
        self.assertEqual(PanicPaper().name, "panic")
        self.assertEqual(PanicPaper().index_gap, panic.INDEX_GAP)
        self.assertEqual(PanicPaper(name="panic03").log_path, LOOSE_LOG)
        day = "2026-11-02"
        nifty = {"2026-10-30": bars_for("2026-10-30", [(1510, 100, 100, 100, 100)]),
                 day: bars_for(day, [(915, 99.6, 99.7, 99.5, 99.6)])}
        stock = bars_for(day, [(915, 96, 97, 95, 96), (920, 96, 97, 95, 96.5), (1510, 98, 98, 97, 98)])
        with journal_directory() as tmp, patch("stalk.sim.panic_live.universe", return_value={"X": "midcap"}):
            strict = PanicPaper(log_path=os.path.join(tmp, "strict.jsonl"))
            loose = PanicPaper(index_gap=-0.003, name="panic03", log_path=os.path.join(tmp, "loose.jsonl"))
            strict.day = loose.day = day
            self.assertEqual(strict.morning(day, nifty, {"X": (100, 96)}, set(), lambda _: stock), [])
            self.assertEqual([c.symbol for c in loose.morning(day, nifty, {"X": (100, 96)}, set(), lambda _: stock)], ["X"])
            self.assertTrue(strict._done_today(day))
            self.assertFalse(loose._done_today(day))
            loose._write("closed_all", net=0)
            self.assertTrue(loose._done_today(day))
            with open(strict.log_path, encoding="utf-8") as f:
                self.assertEqual([json.loads(ln)["kind"] for ln in f], ["skip"])

    def test_full_liquid_universe_is_ranked(self):
        """Owner's yes (2026-11-01): rank from every liquid EQ stock (the 10-year evidence's universe), not only the stored list;
        NIFTY 50 names stay out; without a liquid list the stored list still works."""
        from stalk.config import DEFAULT_UNIVERSE
        day = "2026-11-02"
        nifty = {"2026-10-30": bars_for("2026-10-30", [(1510, 100, 100, 100, 100)]),
                 day: bars_for(day, [(915, 99.4, 99.5, 99.3, 99.4)])}
        def stock(o):
            return bars_for(day, [(915, o, o+1, o-1, o), (920, o, o+1, o-1, o+0.5), (1510, o+2, o+2, o+1, o+2)])
        big = sorted(DEFAULT_UNIVERSE)[0]
        gaps = {"LISTED": (100, 97), "SMALLCO": (100, 92), big: (100, 90)}
        with journal_directory() as tmp, patch("stalk.sim.panic_live.universe", return_value={"LISTED": "midcap"}):
            r = PanicPaper(log_path=os.path.join(tmp, "s.jsonl"))
            r.day = day
            picks = r.morning(day, nifty, gaps, set(), lambda s: stock(gaps[s][1]), liquid_for=lambda prev: {"SMALLCO", big})
            self.assertEqual([c.symbol for c in picks], ["SMALLCO", "LISTED"])
            self.assertEqual(picks[0].tier, "smallcap")
            r2 = PanicPaper(log_path=os.path.join(tmp, "s2.jsonl"))
            r2.day = day
            self.assertEqual([c.symbol for c in r2.morning(day, nifty, gaps, set(), lambda s: stock(gaps[s][1]))], ["LISTED"])

    def test_live_morning_survives_bad_data(self):
        """Pre-mortem 2026-11-01: a late NIFTY 09:15 bar retries instead of skipping the day, and one ticker whose bars fail is
        skipped instead of stopping the ranking."""
        day = "2026-11-02"
        prev = {"2026-10-30": bars_for("2026-10-30", [(1510, 100, 100, 100, 100)])}
        late = {**prev, day: bars_for(day, [(920, 99.4, 99.5, 99.3, 99.4)])}
        nifty = {**prev, day: bars_for(day, [(915, 99.4, 99.5, 99.3, 99.4)])}
        good = bars_for(day, [(915, 92, 93, 91, 92), (920, 92, 93, 91, 92.5), (1510, 94, 94, 93, 94)])
        def bars(s):
            if s == "BROKEN":
                raise RuntimeError("404")
            return good
        with journal_directory() as tmp, patch("stalk.sim.panic_live.universe", return_value={"BROKEN": "smallcap", "OK": "smallcap"}):
            r = PanicPaper(log_path=os.path.join(tmp, "s.jsonl"))
            r.day = day
            with self.assertRaises(RuntimeError):
                r.morning(day, late, {}, set(), bars, final=False)
            self.assertFalse(r._done_today(day))
            picks = r.morning(day, nifty, {"BROKEN": (100, 88), "OK": (100, 92)}, set(), bars)
            self.assertEqual([c.symbol for c in picks], ["OK"])

    def test_consensus_entry_is_after_the_decision(self):
        from datetime import datetime
        from scripts.consensus_desk import next_bar_hm
        at = lambda t: datetime.strptime(f"2026-11-02 {t}", "%Y-%m-%d %H:%M:%S")
        self.assertEqual([next_bar_hm(at(t)) for t in ("09:31:10", "09:43:20", "09:45:00", "09:59:59")], [935, 945, 950, 1000])

    def test_trade_ids_and_execution_are_separate(self):
        day = "2026-11-02"
        stock = bars_for(day, [(915, 96, 97, 95, 96), (920, 96, 97, 95, 96.5), (1510, 98, 98, 97, 98)])
        store = Mock()
        strict = PanicPaper(store=store)
        loose = PanicPaper(store=store, index_gap=-0.003, name="panic03")
        candidate = panic.Candidate("X", 100, 96)
        self.assertEqual(strict.settle(day, candidate, stock), loose.settle(day, candidate, stock))
        rows = [call.args[0] for call in store.save_trade.call_args_list]
        self.assertEqual([r["id"] for r in rows], [f"panic:{day}:X", f"panic03:{day}:X"])
        self.assertEqual([r["session_id"] for r in rows], [f"panic:{day}", f"panic03:{day}"])
        self.assertEqual([r["strategy"] for r in rows], ["panic_buy", "panic_buy_loose"])
        self.assertTrue(all(r["broker"] == "paper" for r in rows))

    def test_api_summaries_are_independent(self):
        from fastapi.testclient import TestClient
        from stalk.api.server import app
        with journal_directory() as tmp:
            strict_path, loose_path = [os.path.join(tmp, name) for name in ("strict.jsonl", "loose.jsonl")]
            for path, rows in ((strict_path, [{"day": "2026-11-02", "kind": "skip"}]),
                               (loose_path, [{"day": "2026-11-02", "kind": "morning"},
                                             {"day": "2026-11-02", "kind": "exit", "trade": {"net": 12.5}}])):
                with open(path, "w", encoding="utf-8") as f:
                    f.write("".join(json.dumps(row) + "\n" for row in rows))
            with patch("stalk.sim.panic_live.LOG", strict_path), patch("stalk.sim.panic_live.LOOSE_LOG", loose_path), \
                 patch("stalk.api.server.get_agent", return_value=SimpleNamespace(panic_error="strict failed", panic_loose_error=None)):
                response = TestClient(app).get("/api/panic")
            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertEqual(set(data), {"strict", "loose"})
            self.assertEqual(data["strict"]["checked"], 1)
            self.assertEqual(data["strict"]["fired"], [])
            self.assertEqual(data["strict"]["net"], 0)
            self.assertEqual(data["strict"]["error"], "strict failed")
            self.assertEqual(len(data["loose"]["fired"]), 1)
            self.assertEqual(data["loose"]["net"], 12.5)
            self.assertIsNone(data["loose"]["error"])
            self.assertEqual(data["strict"]["review_after"], 5)
            self.assertEqual(data["loose"]["review_after"], 5)

    def test_loop_failure_does_not_block_other_version(self):
        from stalk.agent import Agent
        for failing in ("strict", "loose"):
            strict = SimpleNamespace(name="panic", tick=AsyncMock(side_effect=RuntimeError("strict failed") if failing == "strict" else None))
            loose = SimpleNamespace(name="panic03", tick=AsyncMock(side_effect=RuntimeError("loose failed") if failing == "loose" else None))
            agent = SimpleNamespace(panic=strict, panic_loose=loose, panic_error="old", panic_loose_error="old")
            with patch("stalk.agent.asyncio.sleep", new=AsyncMock(side_effect=asyncio.CancelledError)), \
                 self.assertLogs("agent", level="ERROR"):
                with self.assertRaises(asyncio.CancelledError):
                    asyncio.run(Agent._panic_loop(agent))
            strict.tick.assert_awaited_once()
            loose.tick.assert_awaited_once()
            self.assertEqual(agent.panic_error, "strict failed" if failing == "strict" else None)
            self.assertEqual(agent.panic_loose_error, "loose failed" if failing == "loose" else None)


if __name__ == "__main__":
    unittest.main()
