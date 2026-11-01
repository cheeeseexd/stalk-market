"""Risk gate, campaign bookkeeping, holdout, live-order safety, API and frontend contracts."""
import asyncio
import os
import re
import unittest
from pathlib import Path

import pandas as pd

os.environ["STALK_NO_BOOT"] = "1"

from stalk.config import HOLDOUT_START, Settings  # noqa: E402
from stalk.data.kite_mcp import KiteMCPError  # noqa: E402
from stalk.research.validate import validate_as_of  # noqa: E402
from stalk.sim import risk  # noqa: E402
from stalk.sim.broker import LiveKiteBroker, to_tick  # noqa: E402
from stalk.sim.campaign import campaign_days, run_campaign  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def gate(**kw):
    base = dict(minute_end=600, symbol_cooldown_until=None, qty=100, entry=100.0, dist=1.0, validated=True,
                holding_symbol=False)
    base.update(kw)
    return base


class RiskGateTests(unittest.TestCase):
    s = Settings()

    def code(self, st=None, **kw):
        return risk.check(self.s, st or risk.GateState(), **gate(**kw)).code

    def test_passes_a_clean_setup(self):
        self.assertEqual(self.code(), "pass")

    def test_each_rule_blocks_with_its_reason(self):
        self.assertEqual(self.code(risk.GateState(killed=True)), "killed")
        self.assertEqual(self.code(risk.GateState(halted=True)), "loss_halt")
        self.assertEqual(self.code(risk.GateState(stale=True)), "stale")
        self.assertEqual(self.code(risk.GateState(consecutive_losses=2)), "loss_streak")
        self.assertEqual(self.code(risk.GateState(trades_today=2)), "trade_cap")
        self.assertEqual(self.code(holding_symbol=True), "holding")
        self.assertEqual(self.code(risk.GateState(open_positions=1)), "max_positions")
        self.assertEqual(self.code(minute_end=9 * 60 + 25), "window")
        self.assertEqual(self.code(minute_end=12 * 60), "lunch")
        self.assertEqual(self.code(symbol_cooldown_until=700), "cooldown")
        self.assertEqual(self.code(validated=False), "not_validated")
        self.assertEqual(self.code(qty=0), "size")
        self.assertEqual(self.code(qty=1, dist=0.1), "costs")
        v = risk.check(self.s, risk.GateState(halted=True), **gate())
        self.assertIn("₹300", v.reason)


class FloorTests(unittest.TestCase):
    def test_equity_floor_turns_the_kill_switch_on(self):
        from stalk.sim.engine import DayEngine
        from stalk.research.trade import Trade
        s = Settings(slippage_bps=0.0)
        eng = DayEngine(s, "2026-06-01", "t", 5050.0, {}, [], {})
        t = Trade.open("AAA", "long", "orb_breakout", 100.0, 10, 1.0, 10, s)
        t.force_exit(2, 90.0, "stop", s)
        eng.positions["AAA"] = t
        eng._close(t)
        self.assertTrue(eng.gate.killed)
        self.assertEqual(risk.check(s, eng.gate, **gate()).code, "killed")


class TimeoutTests(unittest.TestCase):
    def test_timeout_blocks_with_reason(self):
        v = risk.check(Settings(), risk.GateState(locked=2), **gate())
        self.assertEqual(v.code, "timeout")
        self.assertIn("2 sessions left", v.reason)

    def test_three_losses_start_a_three_session_timeout(self):
        from stalk.sim.engine import DayEngine
        from stalk.research.trade import Trade
        eng = DayEngine(Settings(lockout_after_losses=3), "2026-06-01", "t", 10000.0, {}, [], {})
        eng.start_from({"streak": 2, "locked": 0})
        t = Trade.open("AAA", "long", "orb_breakout", 100.0, 1, 1.0, 10, Settings(slippage_bps=0.0))
        t.force_exit(2, 90.0, "stop", Settings(slippage_bps=0.0))
        eng.positions["AAA"] = t
        eng._close(t)
        self.assertEqual(eng.gate.locked, 4)                 # rest of today plus three sessions
        carry = eng.carry()
        self.assertEqual(carry, {"streak": 0, "locked": 3})
        for left in (2, 1, 0):
            nxt = DayEngine(Settings(lockout_after_losses=3), "2026-06-02", "t2", 10000.0, {}, [], {})
            nxt.start_from(carry)
            carry = nxt.carry()
            self.assertEqual(carry["locked"], left)


class DailyCheckTests(unittest.TestCase):
    def _engine(self, exit_hhmm, stop=95.0, target=110.0):
        from datetime import datetime
        from stalk.config import IST
        from stalk.sim.engine import DayEngine
        eng = DayEngine(Settings(lockout_after_losses=3), "2026-06-01", "t", 10000.0, {}, [], {})
        exit_ts = int(datetime(2026, 6, 1, *exit_hhmm, tzinfo=IST).timestamp())
        eng.closed.append({"symbol": "AAA", "side": "long", "entry_price": 100.0, "stop_initial": stop, "target": target,
                           "reason": "flatten", "exit_ts": exit_ts, "net": 10.0})
        eng.realized = 10.0
        return eng

    def test_passes_a_clean_session(self):
        from stalk.sim.daybyday import check_day
        self.assertIn("flat before 15:20", check_day(self._engine((15, 0)), Settings(), 10000.0))

    def test_catches_late_exit_and_missing_stop(self):
        from stalk.sim.daybyday import CheckFailed, check_day
        with self.assertRaises(CheckFailed):
            check_day(self._engine((15, 20)), Settings(), 10000.0)
        with self.assertRaises(CheckFailed):
            check_day(self._engine((15, 0), stop=105.0), Settings(), 10000.0)


class LayerTests(unittest.TestCase):
    def test_event_window_uses_trading_days_and_announcement_time(self):
        from datetime import datetime
        from stalk.config import IST
        from stalk.data.store import Store
        st = Store(":memory:")
        before = int(datetime(2026, 10, 1, tzinfo=IST).timestamp())
        after = int(datetime(2026, 10, 20, tzinfo=IST).timestamp())
        with st._lock:
            st._conn.executemany("INSERT INTO events VALUES (?,?,?,?,?)", [
                ("AAA", "2026-10-19", "Financial Results", before, ""),   # Monday: next trading day after Friday the 16th
                ("BBB", "2026-10-21", "Financial Results", before, ""),   # 3 trading days away
                ("CCC", "2026-10-16", "Financial Results", after, ""),    # announced only after the morning
            ])
            st._conn.commit()
        open_ts = int(datetime(2026, 10, 16, 9, 15, tzinfo=IST).timestamp())
        self.assertEqual(st.event_symbols("2026-10-16", open_ts), {"AAA"})

    def test_settings_parse_text_booleans(self):
        self.assertFalse(Settings().patched({"breadth_filter": "false"}).breadth_filter)
        self.assertTrue(Settings().patched({"breadth_filter": "true"}).breadth_filter)

    def test_drex_veto_rule(self):
        from stalk.llm.drex import DrexAnswer, checklist_verdict
        ok = DrexAnswer(ok=True, answers={"with_market": {"noul": 0.9}, "no_event_risk": {"noul": 0.9}, "sector_supports": {"noul": 0.6},
                                          "room_to_target": {"noul": 0.6}, "fits_thesis": {"noul": 0.5}, "quality": {"score": 2.4}})
        self.assertFalse(checklist_verdict(ok)["veto"])
        bad = DrexAnswer(ok=True, answers={**ok.answers, "with_market": {"noul": 0.1}})
        v = checklist_verdict(bad)
        self.assertTrue(v["veto"])
        self.assertIn("against the market", v["reason"])
        self.assertFalse(checklist_verdict(DrexAnswer(ok=False, error="down"))["veto"])   # an outage never blocks by itself


class NewsTests(unittest.TestCase):
    def test_classifier(self):
        from stalk.research.news import classify
        self.assertEqual(classify("Trading Window", "Closure of trading window"), (False, 0))
        self.assertEqual(classify("Bagging/Receiving of orders/contracts", "Order from NHAI"), (True, 1))
        self.assertEqual(classify("Action(s) taken or orders passed", "GST demand"), (True, -1))
        self.assertEqual(classify("Press Release", "Company wins a large contract"), (True, 1))
        self.assertEqual(classify("General Updates", "Receipt of tax demand notice"), (True, -1))

    def test_only_news_published_before_the_bar_is_seen(self):
        from datetime import datetime
        from stalk.config import IST
        from stalk.data.store import Store
        from stalk.research.news import news_view, session_news
        st = Store(":memory:")
        t = lambda d, h, m: int(datetime(2026, 10, d, h, m, tzinfo=IST).timestamp())
        with st._lock:
            st._conn.executemany("INSERT INTO news (symbol, ts, category, text, seq, drex_p) VALUES (?,?,?,?,?,?)", [
                ("AAA", t(15, 16, 0), "Press Release", "AAA wins a large order", "1", None),       # after Thursday's close: counts on Friday
                ("AAA", t(16, 11, 0), "Action(s) taken or orders passed", "Penalty", "2", None),  # Friday 11:00
                ("AAA", t(14, 12, 0), "Press Release", "Old order win", "3", None),                # before the window
            ])
            st._conn.commit()
        news = session_news(st, "2026-10-16")
        self.assertEqual(news_view(news["AAA"], t(16, 10, 0))[0], 1)       # at 10:00 only the evening order is known
        self.assertEqual(news_view(news["AAA"], t(16, 11, 30))[0], 0)      # by 11:30 the penalty offsets it


class CampaignTests(unittest.TestCase):
    def test_days_stop_before_the_holdout_and_start_on_a_monday(self):
        days = pd.bdate_range("2026-07-01", "2026-10-30").strftime("%Y-%m-%d").tolist()
        got = campaign_days(days, None, None, False)
        self.assertLess(max(got), HOLDOUT_START)
        self.assertEqual(pd.Timestamp(got[0]).weekday(), 0)
        self.assertGreaterEqual(days.index(got[0]), 25)
        self.assertGreaterEqual(max(campaign_days(days, None, None, True)), HOLDOUT_START)

    def test_validation_ignores_later_and_holdout_days(self):
        ev = pd.DataFrame({"day": ["2026-08-01"] * 50 + ["2026-09-01"] * 50 + ["2026-10-20"] * 50,
                           "r": [0.5, -0.2] * 25 + [-1.0] * 50 + [5.0] * 50, "net": [1.0] * 150})
        events = {15: ev, 30: ev, 45: ev}
        a = validate_as_of(events, "orb_breakout", "2026-08-15")
        self.assertEqual(a["main"]["n"], 50)
        self.assertTrue(a["validated"])
        late = validate_as_of(events, "orb_breakout", "2026-10-30")
        self.assertEqual(late["main"]["n"], 100)          # the holdout's +5R trades are not visible
        self.assertFalse(late["validated"])

    def test_campaign_carries_capital_day_to_day(self):
        from tests.helpers import make_bars
        from stalk.data.store import Store
        st = Store(":memory:")
        for i, sym in enumerate(["AAA", "BBB"]):
            b = make_bars(days=40, seed=30 + i, vol=0.003)
            st.upsert_bars(sym, "5minute", b.itertuples(index=False, name=None), "test")
        idx = make_bars(days=40, seed=50, price=24000, volume=0)
        st.upsert_bars("NIFTY 50", "5minute", idx.itertuples(index=False, name=None), "test")
        s = Settings(universe=["AAA", "BBB"], cost_gate_mult=0.0, strategies=["vwap_pullback"])
        res = run_campaign(st, s, accounts={"x": {"strategies": ["vwap_pullback"], "require_validated": False}})
        days = res["accounts"]["x"]["days"]
        self.assertGreater(sum(d["trades"] for d in days), 0)
        for a, b in zip(days, days[1:]):
            self.assertAlmostEqual(a["end_equity"], b["start_equity"], places=2)
        self.assertAlmostEqual(days[-1]["end_equity"], s.capital + res["accounts"]["x"]["net"], places=1)


class FakeKite:
    def __init__(self, modify_fails=False):
        self.calls = []
        self.modify_fails = modify_fails

    async def place_order(self, symbol, side, qty, order_type="MARKET", price=None, trigger_price=None, tag=""):
        self.calls.append(("place", side, order_type, trigger_price))
        return f"o{len(self.calls)}"

    async def call_tool(self, name, args=None):
        self.calls.append((name, args.get("order_type")))
        if self.modify_fails:
            raise KiteMCPError("modify rejected")

    async def cancel_order(self, oid):
        self.calls.append(("cancel", oid))

    async def order_history(self, oid):
        return [{"status": "COMPLETE", "average_price": 100.0, "filled_quantity": 5, "transaction_type": "SELL"}]


class LiveSafetyTests(unittest.TestCase):
    def test_entry_places_stop_at_the_broker_on_a_tick(self):
        k = FakeKite()
        asyncio.run(LiveKiteBroker(k).enter("SBIN", "long", 5, 812.337))
        self.assertEqual(k.calls[0][:3], ("place", "BUY", "MARKET"))
        self.assertEqual(k.calls[1][:3], ("place", "SELL", "SL-M"))
        self.assertAlmostEqual(k.calls[1][3], 812.35)
        self.assertAlmostEqual(to_tick(100.02), 100.0)

    def test_exit_converts_the_stop_and_never_cancels_it_first(self):
        k = FakeKite()
        asyncio.run(LiveKiteBroker(k).exit("SBIN", "long", 5, "stop1"))
        self.assertEqual(k.calls, [("modify_order", "MARKET")])

    def test_exit_falls_back_to_market_then_cancels_stop(self):
        k = FakeKite(modify_fails=True)
        asyncio.run(LiveKiteBroker(k).exit("SBIN", "long", 5, "stop1"))
        kinds = [c[0] for c in k.calls]
        self.assertEqual(kinds, ["modify_order", "place", "cancel"])

    def test_live_needs_every_guard_including_static_ip(self):
        from stalk.agent import Agent
        from stalk.data.store import Store

        class K:
            logged_in, tools, url, user_name = True, ["place_order"], "x", "u"

            def status(self):
                return {}

        a = Agent(store=Store(":memory:"), settings=Settings(mode="live"), kite=K())
        os.environ["ALLOW_LIVE_ORDERS"] = "1"
        try:
            a.armed = True
            a.validation._cache = {"k": {"orb_breakout": {"validated": True}}}
            self.assertFalse(a.live_ready()["ready"])
            self.assertFalse(a.live_ready()["checks"]["static_ip_confirmed"])
            a.settings = Settings(mode="live", static_ip_confirmed=True)
            self.assertTrue(a.live_ready()["ready"])
            a.kill()
            self.assertFalse(a.live_ready()["ready"])
            with self.assertRaises(ValueError):
                a.arm("yes")
        finally:
            os.environ.pop("ALLOW_LIVE_ORDERS", None)


class ApiTests(unittest.TestCase):
    def test_state_contract_and_dashboard(self):
        from fastapi.testclient import TestClient
        from stalk.agent import Agent
        from stalk.api import server
        from stalk.data.store import Store
        server.agent = Agent(store=Store(":memory:"), settings=Settings())
        with TestClient(server.app) as c:
            st = c.get("/api/state").json()
            for k in ("phase", "settings", "kite", "data", "live_ready", "live", "replay", "job", "holdout"):
                self.assertIn(k, st)
            self.assertEqual(c.get("/").status_code, 200)
            self.assertEqual(c.post("/api/settings", json={"risk_per_trade": 5000}).status_code, 400)
            self.assertEqual(c.post("/api/live/arm", json={"confirm": "yes"}).status_code, 400)
            self.assertEqual(c.post("/api/replay", json={"day": HOLDOUT_START}).status_code, 400)
        server.agent = None


class FrontendContractTests(unittest.TestCase):
    def setUp(self):
        self.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
        self.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8") + (ROOT / "web" / "lib.js").read_text(encoding="utf-8")

    def test_single_font_family(self):
        self.assertEqual(set(re.findall(r"font-family:\s*([^;]+);", self.css)), {"var(--font)"})
        self.assertEqual(len(re.findall(r"--font:\s", self.css)), 1)
        self.assertNotIn("font-family", self.html.replace("family=Inter", ""))

    def test_rupees_only(self):
        self.assertNotRegex(self.js + self.html, r"\$\d|USD")
        self.assertIn("en-IN", self.js)
        self.assertIn("INR", self.js)

    def test_control_heights_are_tokens(self):
        self.assertIn("--h-control: 36px", self.css)
        self.assertIn("--h-chip: 24px", self.css)
        for selector, token in ((".btn {", "--h-control"), (".seg {", "--h-control"), (".chip {", "--h-chip")):
            block = self.css.split(selector, 1)[1].split("}", 1)[0]
            self.assertIn(f"height: var({token})", block, selector)

    def test_colours_only_defined_as_tokens(self):
        for line in self.css.splitlines():
            if re.search(r"#[0-9a-fA-F]{3,8}\b", line):
                self.assertRegex(line.strip(), r"^--[\w-]+:\s*#", f"raw colour outside a token: {line.strip()}")


if __name__ == "__main__":
    unittest.main()
