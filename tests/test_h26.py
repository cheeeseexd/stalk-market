from __future__ import annotations
import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.kite_backfill import backfill, chunks
from tests.test_paper_daily import isolated
from stalk.config import IST
from stalk.data.kite_mcp import KiteAuthRequired
from stalk.research.orb import (COLS, prepare, select, walk, random_picks, metrics, VERSIONS)
from stalk.sim.orb_live import ORBPaper, summary, records, BarSources


def stamp(day, hm):
    return int(datetime.fromisoformat(day).replace(tzinfo=IST, hour=hm//100, minute=hm%100).timestamp())


def history():
    frames = {}
    days = pd.bdate_range('2026-10-12', periods=16).strftime('%Y-%m-%d').tolist()
    for s in ('A', 'B', 'C'):
        rows = []
        for i, day in enumerate(days):
            for minute in range(9*60+15, 15*60+30, 5):
                ts = stamp(day, minute//60*100+minute%60)
                rows.append([ts, 100, 103, 99, 101, 100000 if i < 15 else 300000])
        frames[s] = pd.DataFrame(rows, columns=COLS)
    return frames, {s: 'midcap' for s in frames}, days[-1]


class CausalORBTests(unittest.TestCase):
    def test_h26_current_fees_leave_legacy_schedule_available(self):
        from stalk.market.costs import fill_cost, CURRENT_EXCHANGE_PCT
        self.assertAlmostEqual(fill_cost('SELL', 10, 500, exchange_pct=CURRENT_EXCHANGE_PCT).exchange, .1535)
        self.assertAlmostEqual(fill_cost('SELL', 10, 500).exchange, .1485)

    def test_run_guard_refuses_before_any_data_read(self):
        from scripts import h26_full
        with isolated() as tmp:
            p = tmp/'data/h26/pilot'
            p.mkdir(parents=True)
            (p/'started.json').write_text('{}')
            with patch.object(h26_full, 'ROOT', tmp), patch.object(h26_full.pd, 'read_pickle') as read:
                with self.assertRaises(FileExistsError):
                    h26_full.run(True)
                read.assert_not_called()

    def test_same_day_and_future_bars_cannot_change_picks(self):
        frames, tiers, day = history()
        d, f = prepare(frames, tiers)
        before = select(day, d, f, tiers)
        for frame in frames.values():
            later = frame.ts > stamp(day, 915)
            frame.loc[later, ['o', 'h', 'l', 'c', 'v']] *= 50
        d, f = prepare(frames, tiers)
        self.assertEqual(before, select(day, d, f, tiers))
        self.assertEqual([p['symbol'] for p in before[0]], ['A', 'B'])
        self.assertAlmostEqual(before[0][0]['rv'], 3)

    def test_rv_uses_exactly_prior14_opening_volumes(self):
        frames, tiers, day = history()
        frame = frames['A']
        frame.loc[frame.ts == frame.ts.min(), 'v'] = 1e12  # outside prior14
        d, f = prepare(frames, tiers)
        picks, pool = select(day, d, f, tiers)
        self.assertEqual(next(p for p in pool if p['symbol'] == 'A')['rv'], 3)
        # Last session's AFTER-open volume changes turnover but not RV.
        frame.loc[frame.ts == stamp('2026-10-30', 920), 'v'] = 2e12
        d, f = prepare(frames, tiers)
        self.assertEqual(next(p for p in select(day, d, f, tiers)[1] if p['symbol'] == 'A')['rv'], 3)

    def test_missing_prior_session_not_replaced_by_older_session(self):
        frames, tiers, day = history()
        frames['A'] = frames['A'][~frames['A'].ts.between(stamp('2026-10-20', 915), stamp('2026-10-20', 1525))]
        d, f = prepare(frames, tiers)
        self.assertNotIn('A', [p['symbol'] for p in select(day, d, f, tiers)[1]])

    def test_exclusions_doji_lock_rv_and_atr(self):
        frames, tiers, day = history()
        frames['A'].loc[frames['A'].ts == stamp(day, 915), 'c'] = 100
        frames['B'].loc[frames['B'].ts == stamp(day, 915), ['h', 'l', 'c']] = 100
        d, f = prepare(frames, tiers)
        self.assertEqual(select(day, d, f, tiers, {'C'})[0], [])

    def path(self, side='long'):
        day = '2026-11-02'
        p = dict(day=day, symbol='A', tier='midcap', side=side, high=101, low=99, atr=4, rv=3)
        rows = [[stamp(day, hm), 100, 102, 99.9, 101, 100] for hm in
                [m//60*100+m%60 for m in range(560, 915, 5)]]
        return p, pd.DataFrame(rows, columns=COLS)

    def test_stop_entry_conservative_same_bar_and_costs_both_directions(self):
        for side in ('long', 'short'):
            p, raw = self.path(side)
            if side == 'short':
                raw[['o', 'h', 'l', 'c']] = [100, 100.1, 98, 99]
            from stalk.research.orb import normalize
            r = walk(p, normalize(raw), .1)
            self.assertEqual(r['trade']['reason'], 'stop')
            self.assertLess(r['trade']['net'], 0)
            self.assertGreater(r['trade']['costs'], 0)
            self.assertLessEqual(r['trade']['qty']*r['trade']['entry'], 5000)

    def test_gaps_missing_bars_and_flatten_time(self):
        from stalk.research.orb import normalize
        p, raw = self.path()
        raw[['o', 'h', 'l', 'c']] = [102, 103, 102, 102.5]
        r = walk(p, normalize(raw), .5)
        self.assertAlmostEqual(r['trade']['entry'], 102*1.0006)
        self.assertEqual(r['trade']['exit_ts'], stamp(p['day'], 1510))
        self.assertEqual(r['trade']['reason'], 'flatten')
        missing = raw[raw.ts != stamp(p['day'], 1000)]
        self.assertEqual(walk(p, normalize(missing), .5)['status'], 'unknown')
        self.assertEqual(walk(p, normalize(raw[raw.ts < stamp(p['day'], 1510)]), .5)['status'], 'unknown')

    def test_adverse_fill_recomputes_stop_and_size(self):
        from stalk.research.orb import normalize
        p, raw = self.path()
        a, b = [walk(p, normalize(raw), .1, x)['trade'] for x in (0, .0025)]
        self.assertGreater(b['entry'], a['entry'])
        self.assertGreater(b['stop'], a['stop'])
        self.assertLessEqual(b['qty'], a['qty'])

    def test_random_control_is_reproducible_distinct_and_uses_pool(self):
        frames, tiers, day = history()
        d, f = prepare(frames, tiers)
        pool = select(day, d, f, tiers)[1]
        a = random_picks(pool, np.random.default_rng(2601))
        self.assertEqual(a, random_picks(pool, np.random.default_rng(2601)))
        self.assertEqual(len({p['symbol'] for p in a}), 2)
        self.assertTrue(all(p['side'] in ('long', 'short') for p in a))


class FakeKite:
    def __init__(self, logged=True, fail=False):
        self.logged_in, self.fail = logged, fail
        self.calls = []

    async def check(self):
        return self.logged_in

    async def historical(self, symbol, interval, lo, hi):
        self.calls.append((symbol, interval, lo, hi))
        if self.fail:
            raise OSError('offline')
        return [[stamp(lo[:10], 915), 100, 101, 99, 100, 1000]]


class BackfillTests(unittest.TestCase):
    def test_chunk_limits(self):
        c = list(chunks('2015-01-01', '2016-01-01'))
        self.assertEqual(c[0][0], '2015-01-01')
        self.assertEqual(c[-1][1], '2016-01-01')
        for lo, hi in c:
            self.assertLessEqual((datetime.fromisoformat(hi)-datetime.fromisoformat(lo)).days, 98)

    def test_logged_out_refused_before_any_history(self):
        async def scenario():
            k = FakeKite(False)
            with self.assertRaises(KiteAuthRequired):
                await backfill(k, ['A'], '2020-01-01', '2020-01-02', 'unused')
            self.assertEqual(k.calls, [])
        asyncio.run(scenario())

    def test_resume_atomic_dedup_and_coverage(self):
        async def scenario():
            with isolated() as tmp:
                k = FakeKite()
                pauses = []
                async def sleep(x): pauses.append(x)
                await backfill(k, ['A'], '2020-01-01', '2020-07-01', tmp, sleeper=sleep)
                n = len(k.calls)
                await backfill(k, ['A'], '2020-01-01', '2020-07-01', tmp, sleeper=sleep)
                self.assertEqual(n, len(k.calls))
                self.assertEqual(n, len(pd.read_pickle(Path(tmp)/'A.pkl')))
                self.assertTrue(all(x >= .4 for x in pauses))
                self.assertIn('A', json.loads((Path(tmp)/'coverage.json').read_text())['symbols'])
        asyncio.run(scenario())

    def test_failed_chunk_not_checkpointed(self):
        async def scenario():
            with isolated() as tmp:
                async def sleep(x): pass
                k = FakeKite(fail=True)
                report = await backfill(k, ['A'], '2020-01-01', '2020-01-02', tmp, sleeper=sleep)
                self.assertEqual(len(k.calls), 3)
                self.assertTrue(report['A']['errors'])
                self.assertFalse((Path(tmp)/'checkpoint.json').exists())
        asyncio.run(scenario())

    def test_invalid_opening_volume_is_counted_not_hidden(self):
        class Bad(FakeKite):
            async def historical(self, symbol, interval, lo, hi):
                return [[stamp(lo[:10],915),100,102,99,101,0], [stamp(lo[:10],920),0,0,0,0,0]]
        async def scenario():
            with isolated() as tmp:
                async def sleep(x): pass
                result = await backfill(Bad(), ['A'], '2020-01-01','2020-01-02',tmp,sleeper=sleep)
                self.assertFalse(result['A']['errors'])
                self.assertEqual(result['A']['opening_nonflat_zero_volume'], 1)
                chunk = json.loads((tmp/'checkpoint.json').read_text())['A']['2020-01-01:2020-01-02']
                self.assertEqual((chunk['dropped'], chunk['zero_volume_open']), (1, 1))
        asyncio.run(scenario())

    def test_empty_success_is_checkpointed(self):
        class Empty(FakeKite):
            async def historical(self, *args):
                self.calls.append(args)
                return []
        async def scenario():
            with isolated() as tmp:
                async def sleep(x): pass
                k = Empty()
                await backfill(k, ['A'], '2015-01-01', '2015-01-02', tmp, sleeper=sleep)
                await backfill(k, ['A'], '2015-01-01', '2015-01-02', tmp, sleeper=sleep)
                self.assertEqual(len(k.calls), 1)
        asyncio.run(scenario())


class RunnerTests(unittest.TestCase):
    def test_only_results_known_before_decision_exclude_live_picks(self):
        from stalk.data.store import Store
        frames, tiers, day = history()
        now = datetime.fromisoformat(day).replace(tzinfo=IST, hour=9, minute=20, second=10)
        class Source:
            async def bars(self, symbol, now, provider=None):
                return frames[symbol], 'fake'
        async def scenario():
            with isolated() as tmp:
                store = Store(':memory:')
                for symbol, purpose, announced in [('A','Financial Results',stamp(day,919)),
                                                    ('B','Financial Results',stamp(day,921)),
                                                    ('C','Routine meeting',stamp(day,919))]:
                    store._exec('INSERT INTO events(symbol,day,purpose,announced_ts,detail) VALUES(?,?,?,?,?)',
                                (symbol,day,purpose,announced,''))
                run = ORBPaper(store=store,root=tmp,sources=Source(),tiers=tiers,clock=lambda:now)
                try:
                    await run.tick(now)
                    picks = next(r['picks'] for r in records(tmp) if r['kind'] == 'picks')
                    self.assertEqual([p['symbol'] for p in picks], ['B','C'])
                finally:
                    store._conn.close()
        asyncio.run(scenario())

    def test_empty_api_unknowns_and_no_boot(self):
        from fastapi.testclient import TestClient
        import stalk.api.server as server
        with isolated() as tmp, patch.object(server, 'BASE_DIR', tmp):
            value = TestClient(server.app).get('/api/orb').json()
            self.assertIsNone(value['versions']['v1']['net'])
            self.assertIsNone(value['benchmark']['v1']['trades'])

    def test_runner_restart_side_by_side_and_unknown_review(self):
        frames, tiers, day = history()
        current = [datetime.fromisoformat(day).replace(tzinfo=IST, hour=9, minute=20, second=10)]
        class Source:
            async def bars(self, symbol, now, provider=None):
                return frames[symbol], 'fake completed bars'
        async def scenario():
            with isolated() as tmp:
                run = ORBPaper(root=tmp, sources=Source(), tiers=tiers, clock=lambda: current[0])
                await run.tick(current[0])
                self.assertEqual(len([r for r in records(tmp) if r['kind'] == 'picks']), 1)
                current[0] = current[0].replace(hour=15, minute=16)
                # Restart derives state exclusively from durable events.
                run = ORBPaper(root=tmp, sources=Source(), tiers=tiers, clock=lambda: current[0])
                await run.tick(current[0])
                events = records(tmp)
                await run.tick(current[0])
                self.assertEqual(events, records(tmp))
                for group in ('strategy', 'benchmark'):
                    for v in VERSIONS:
                        self.assertTrue(any(r['kind'] == 'day_end' and r.get('version') == v and r.get('group') == group for r in events))
                self.assertTrue(all(r['reason'] in ('stop', 'flatten') for r in events if r['kind'] == 'exit'))
                self.assertIsNotNone(summary(tmp)['versions']['v1']['net'])
        asyncio.run(scenario())

    def test_late_missed_day_and_failed_source_not_zero(self):
        async def scenario():
            with isolated() as tmp:
                now = datetime(2026, 11, 2, 9, 31, tzinfo=IST)
                r = ORBPaper(root=tmp, tiers={'A': 'midcap'}, clock=lambda: now)
                await r.tick(now)
                self.assertEqual(records(tmp)[0]['kind'], 'skip')
                self.assertIsNone(summary(tmp)['versions']['v1']['net'])
        asyncio.run(scenario())

    def test_missing_source_cannot_rank_partial_universe(self):
        frames, tiers, day = history()
        now = datetime.fromisoformat(day).replace(tzinfo=IST, hour=9, minute=20, second=10)
        class Source:
            async def bars(self, symbol, now, provider=None):
                if symbol == 'A': raise OSError('missing')
                return frames[symbol], 'fake'
        async def scenario():
            with isolated() as tmp:
                r = ORBPaper(root=tmp, sources=Source(), tiers=tiers, clock=lambda: now)
                await r.tick(now)
                self.assertFalse(any(e['kind'] == 'picks' for e in records(tmp)))
                self.assertIsNone(summary(tmp)['versions']['v1']['net'])
        asyncio.run(scenario())

    def test_kite_fallback_label_and_provider_pin(self):
        class Broken(FakeKite):
            async def historical(self, *a): raise OSError('unreachable')
        async def scenario():
            source = BarSources(Broken())
            with patch('stalk.sim.orb_live.fetch', return_value=[]):
                _, label = await source.bars('A', datetime(2026,11,2,tzinfo=IST))
                self.assertEqual(label, 'Yahoo fallback')
                with self.assertRaises(OSError):
                    await source.bars('A', datetime(2026,11,2,tzinfo=IST), 'Kite')
        asyncio.run(scenario())
