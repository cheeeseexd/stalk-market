"""Daily observer tests: fake sources only, isolated files, never boot real runners."""
import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid

import httpx
import pandas as pd

from scripts.horizon_ladder import windows, window_stats
from scripts.h20_picks import account, portfolio
from scripts.h17_rebuild import account as h17_account
from stalk.config import IST
from stalk.research.paper_daily import (PaperDaily, DailySources, completed_cue, current_preopen,
                                        latest, read_json, write_json)


@contextmanager
def isolated():
    root = Path(__file__).parent/('daily_test_'+uuid.uuid4().hex)
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def now(hm=910, day='2026-11-02'):
    return datetime.strptime(f'{day} {hm:04}', '%Y-%m-%d %H%M').replace(tzinfo=IST)


def fake_sources():
    rows = [dict(symbol=f'S{i}', gap=-.1+i*.005, indicative=90+i*.5, previous_close=100,
                 source_at='02-Nov-2026 09:09:00') for i in range(10)]
    return SimpleNamespace(globals=Mock(return_value={'sp500': {'change': -.01}}),
                           market=Mock(return_value=dict(nifty_gap=-.004, nifty_source_at='02-Nov-2026 09:09:00',
                                      candidates=rows, results_known=True, results_near=['S1'],
                                      tiers={f'S{i}': 'midcap' for i in range(10)})))


def log(root, filename, rows):
    p = root/'data'/filename
    p.parent.mkdir(exist_ok=True, parents=True)
    p.write_text(''.join(json.dumps(r)+'\n' for r in rows), encoding='utf-8')


def events(day='2026-11-02', net=100):
    return [dict(day=day, at=day+'T09:25:00+05:30', kind='morning', eligible=3,
                 ranking=[{'rank': 1, 'symbol': 'S0'}], picks=['S0']),
            dict(day=day, at=day+'T09:25:01+05:30', kind='entry', picks=[dict(symbol='S0', official_open=90,
                 entry_bar_open=91, entry_bar_at=day+'T09:20:00+05:30')],
                 quotes={'source': 'kite', 'quotes': {'S0': {'bid': 90.9, 'ask': 91.1}}}),
            dict(day=day, kind='exit', trade=dict(symbol='S0', qty=50, entry=91.1, net=net)),
            dict(day=day, kind='closed_all', net=net)]


class DailyTests(unittest.TestCase):
    def setUp(self):
        # Every test fails if production HTTP is accidentally reached.
        self.network = patch.object(httpx.Client, 'send', side_effect=AssertionError('No network in tests'))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_brief_writes_top_eight_and_separate_gates(self):
        with isolated() as root:
            obj = PaperDaily(root, fake_sources()).brief(now())
            self.assertEqual(obj['would_qualify'], {'strict': False, 'loose': True})
            self.assertEqual(len(obj['candidates']), 8)
            self.assertFalse(obj['candidates'][1]['preliminary_eligible'])
            self.assertTrue(obj['candidates'][0]['preliminary_eligible'])
            self.assertIn('Nearby results meeting', obj['candidates'][1]['reasons'])
            self.assertEqual(read_json(root/'data/briefs/2026-11-02.json'), obj)

    def test_brief_once_per_day_across_restarts(self):
        with isolated() as root:
            sources = fake_sources()
            a = PaperDaily(root, sources).brief(now())
            b = PaperDaily(root, sources).brief(now(912))
            self.assertEqual(a, b)
            sources.market.assert_called_once()
            sources.globals.assert_called_once()

    def test_weekends_and_schedule_do_nothing(self):
        with isolated() as root:
            sources = fake_sources()
            obs = PaperDaily(root, sources)
            self.assertIsNone(obs.brief(now(909)))
            self.assertIsNone(obs.brief(now(day='2026-11-01')))
            self.assertIsNone(obs.review(now(1534)))
            self.assertIsNone(obs.review(now(1600, '2026-11-01')))
            sources.market.assert_not_called()
            self.assertFalse((root/'data').exists())

    def test_late_start_does_not_reconstruct_preopen(self):
        with isolated() as root:
            sources = fake_sources()
            obj = PaperDaily(root, sources).brief(now(925))
            self.assertEqual(obj['status'], 'missed')
            self.assertIsNone(obj['would_qualify']['strict'])
            sources.market.assert_not_called()

    def test_offline_context_and_unknown_calendar_are_not_eligibility(self):
        with isolated() as root:
            sources = fake_sources()
            sources.globals.side_effect = OSError('offline')
            sources.market.side_effect = OSError('offline')
            obj = PaperDaily(root, sources).brief(now())
            self.assertEqual(obj['globals'], {})
            self.assertEqual(obj['status'], 'partial')
            self.assertIsNone(obj['would_qualify']['loose'])

    def test_review_has_process_execution_outcome_and_journal(self):
        with isolated() as root:
            log(root, 'panic_paper.jsonl', events())
            write_json(root/'data/h20/summary.json', {'versions': {'strict': {'2': {'net_per_morning': 140}}}})
            obs = PaperDaily(root, fake_sources())
            obj = obs.review(now(1535))
            strict = obj['runners']['strict']
            self.assertEqual(strict['status'], 'complete')
            self.assertEqual(strict['outcome']['net'], 100)
            self.assertEqual(strict['outcome']['difference_from_expectation'], -40)
            self.assertAlmostEqual(strict['execution']['per_trade'][0]['spread'], .2)
            self.assertAlmostEqual(strict['execution']['per_trade'][0]['vs_0920_bps'], (91.1/91-1)*10000)
            self.assertTrue(any('retrospective' in d for d in strict['process']['deviations']))
            self.assertEqual(obj['runners']['loose']['status'], 'incomplete')
            self.assertIsNone(obj['runners']['loose']['outcome']['net'])
            journal = (root/'JOURNAL.md').read_text(encoding='utf-8')
            self.assertIn('## Paper days', journal)
            self.assertIn('### 2026-11-02', journal)
            obs.review(now(1540))
            PaperDaily(root, fake_sources()).review(now(1541))
            self.assertEqual(journal, (root/'JOURNAL.md').read_text(encoding='utf-8'))

    def test_missing_fill_stays_incomplete_and_duplicates_not_counted(self):
        with isolated() as root:
            rows = events()
            log(root, 'panic_paper.jsonl', rows[:2])
            log(root, 'panic_paper_loose.jsonl', rows+[rows[2]])
            obj = PaperDaily(root, fake_sources()).review(now(1535))
            self.assertEqual(obj['runners']['strict']['execution']['missed_fills'], ['S0'])
            self.assertIsNone(obj['runners']['strict']['outcome']['net'])
            self.assertEqual(obj['runners']['loose']['outcome']['net'], 100)

    def test_unobserved_day_and_malformed_logs_not_zero_profit(self):
        with isolated() as root:
            p = root/'data/panic_paper.jsonl'
            p.parent.mkdir()
            p.write_text('{bad\n', encoding='utf-8')
            obj = PaperDaily(root, fake_sources()).review(now(1535))
            self.assertIsNone(obj['runners']['strict']['outcome']['net'])
            self.assertTrue(obj['runners']['strict']['process']['deviations'])

    def test_explicit_quiet_day_is_complete_without_benchmark_difference(self):
        with isolated() as root:
            log(root, 'panic_paper.jsonl', [dict(day='2026-11-02', kind='skip', why='not a panic morning')])
            r = PaperDaily(root, fake_sources()).review(now(1535))['runners']['strict']
            self.assertEqual(r['outcome']['net'], 0)
            self.assertIsNone(r['outcome']['difference_from_expectation'])

    def test_panic_morning_with_no_fundable_picks_is_observed_zero(self):
        with isolated() as root:
            log(root, 'panic_paper.jsonl', [dict(day='2026-11-02', kind='morning', eligible=0,
                                               picks=[], ranking=[], at='2026-11-02T09:25:00+05:30')])
            r = PaperDaily(root, fake_sources()).review(now(1535))['runners']['strict']
            self.assertEqual(r['status'], 'complete')
            self.assertEqual(r['outcome']['net'], 0)

    def test_drawdown_deduplicates_and_journal_keeps_other_sections(self):
        with isolated() as root:
            (root/'JOURNAL.md').write_text('# Journal\n\nOriginal introduction.\n\n## Old run\n\nKeep this.\n', encoding='utf-8')
            log(root, 'panic_paper.jsonl', events('2026-10-29', -200)+events('2026-10-30', 100)+events(net=-50))
            obs = PaperDaily(root, fake_sources())
            r = obs.review(now(1535))['runners']['strict']
            self.assertEqual(r['outcome']['drawdown_to_date'], 200)
            obs.review(now(1535, '2026-10-30'))
            text = (root/'JOURNAL.md').read_text(encoding='utf-8')
            self.assertLess(text.index('### 2026-11-02'), text.index('### 2026-10-30'))
            self.assertIn('## Old run\n\nKeep this.', text)
            self.assertTrue(text.startswith('# Journal\n\nOriginal introduction.\n\n## Paper days'))

    def test_journal_recovery_after_json_was_saved(self):
        with isolated() as root:
            obs = PaperDaily(root, fake_sources())
            with patch.object(obs, '_journal', side_effect=OSError('disk busy')):
                with self.assertRaises(OSError):
                    obs.review(now(1535))
            self.assertTrue((root/'data/reviews/2026-11-02.json').exists())
            obs.review(now(1536))
            self.assertTrue((root/'JOURNAL.md').exists())

    def test_api_latest_and_ten_reviews_no_boot(self):
        from fastapi.testclient import TestClient
        from stalk.api import server
        with isolated() as root:
            for i in range(1, 13):
                day = f'2026-10-{i:02}'
                write_json(root/f'data/reviews/{day}.json', {'day': day})
            write_json(root/'data/briefs/2026-11-02.json', {'day': '2026-11-02'})
            with patch.object(server, 'BASE_DIR', str(root)), patch.object(server, 'get_agent', side_effect=AssertionError('No boot')):
                client = TestClient(server.app)
                self.assertEqual(client.get('/api/brief').json()['brief']['day'], '2026-11-02')
                reviews = client.get('/api/reviews').json()['reviews']
                self.assertEqual(len(reviews), 10)
                self.assertEqual(reviews[0]['day'], '2026-10-12')
                self.assertEqual(reviews[-1]['day'], '2026-10-03')

    def test_missing_api_files_are_empty(self):
        from stalk.api import server
        with isolated() as root, patch.object(server, 'BASE_DIR', str(root)):
            self.assertEqual(server.paper_brief(), {'brief': None})
            self.assertEqual(server.paper_reviews(), {'reviews': []})

    def test_preopen_timestamp_rejects_stale_and_after_open(self):
        self.assertTrue(current_preopen('02-Nov-2026 09:09:00', now()))
        for value in ('30-Oct-2026 09:09:00', '02-Nov-2026 09:16:00', None):
            self.assertFalse(current_preopen(value, now()))

    def source_market(self, root, indicative=99.4, stamp='02-Nov-2026 09:09:00', calendar=None):
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        payloads = [{}, {'lastUpdateTime': stamp, 'data': [{'metadata': {'symbol': 'S0', 'previousClose': 100, 'iep': 95}}]},
                    {'timestamp': stamp, 'data': [{'index': 'NIFTY 50', 'previousClose': 100,
                                                 'indicativeClose': indicative, 'open': 80, 'last': 70}]},
                    [] if calendar is None else calendar]
        client.get.side_effect = [SimpleNamespace(raise_for_status=lambda: None, json=lambda p=p: p) for p in payloads]
        with patch('stalk.research.paper_daily.httpx.Client', return_value=client), \
             patch('stalk.research.paper_daily.universe', return_value={'S0': 'midcap'}):
            return DailySources(root).market(now())

    def test_nse_indicative_only_no_regular_open_substitution(self):
        with isolated() as root:
            self.assertAlmostEqual(self.source_market(root)['nifty_gap'], -.006)
            self.assertIsNone(self.source_market(root, indicative=0)['nifty_gap'])
            stale = self.source_market(root, stamp='30-Oct-2026 09:09:00')
            self.assertIsNone(stale['nifty_gap'])
            self.assertEqual(stale['candidates'], [])

    def test_nse_malformed_calendar_is_unknown_not_no_results(self):
        with isolated() as root:
            with self.assertRaises(ValueError):
                self.source_market(root, calendar={'error': 'unavailable'})

    def test_yahoo_us_last_completed_session_and_future_bar(self):
        payload = {'chart': {'result': [{'meta': {'exchangeTimezoneName': 'America/New_York'},
                   'timestamp': [int(now(2000, '2026-10-28').timestamp()), int(now(2000, '2026-10-29').timestamp()), int(now(2000, '2026-10-30').timestamp())],
                   'indicators': {'quote': [{'close': [100, 101, 102]}]}}]}}
        cue = completed_cue(payload, now(910, '2026-10-31'), us_close=True)
        self.assertEqual(cue['session_date'], '2026-10-30')
        self.assertAlmostEqual(cue['change'], 102/101-1)
        before_close = completed_cue(payload, now(2100, '2026-10-30'), us_close=True)
        self.assertEqual(before_close['session_date'], '2026-10-29')

    def test_cached_globals_survive_offline_with_dates(self):
        with isolated() as root:
            write_json(root/'data/brief_cache/globals.json', {'sp500': {'session_date': '2026-10-30', 'change': .01}})
            result = DailySources(root).globals(now())
            self.assertTrue(result['sp500']['cached'])
            self.assertEqual(result['sp500']['session_date'], '2026-10-30')

    def test_observer_failure_is_logged_without_runner_call(self):
        from stalk.agent import Agent
        fake = SimpleNamespace(paper_daily=SimpleNamespace(brief=Mock(side_effect=OSError('disk busy'))))
        with patch('stalk.agent.asyncio.sleep', new=AsyncMock(side_effect=asyncio.CancelledError)), self.assertLogs('agent', level='ERROR'):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(Agent._daily_loop(fake, 'brief'))
        fake.paper_daily.brief.assert_called_once()

    def test_boot_starts_four_tasks_and_shutdown_cancels_them(self):
        from stalk.agent import Agent
        async def scenario():
            a = object.__new__(Agent)
            a.kite = SimpleNamespace(check=AsyncMock())
            a.live = SimpleNamespace(start=Mock())
            a._background_tasks = []
            async def forever():
                await asyncio.Event().wait()
            a._panic_loop = forever
            a._brief_loop = forever
            a._review_loop = forever
            a._execution_loop = forever
            a._orb_loop = forever
            a._consensus_loop = forever
            await a.boot()
            self.assertEqual(len(a._background_tasks), 6)
            tasks = list(a._background_tasks)
            await a.stop_background()
            self.assertTrue(all(t.done() for t in tasks))
            self.assertEqual(a._background_tasks, [])
        asyncio.run(scenario())


class ResearchAllocationTests(unittest.TestCase):
    def pool(self):
        return pd.DataFrame([dict(day='2020-01-01', symbol=str(i), gap=-.1+i*.01,
                                 open=100., high=110., low=99., close=105., locked=False) for i in range(5)])

    def test_two_pick_reconciles_h17_and_lower_slots_do_not_reinvest(self):
        p = self.pool()
        old, new = h17_account(p.head(2)), account(p, 2)
        self.assertAlmostEqual(old.net.sum(), new.net.sum())
        for k in (2, 3, 4):
            a = account(p, k)
            self.assertEqual(len(a), k)
            self.assertLessEqual(float((a.qty*a.entry).sum()), 10000)
            self.assertEqual(set(a.reason), {'flatten'})

    def test_unfundable_rank_is_not_replaced(self):
        p = self.pool()
        p.loc[0, 'open'] = 6000
        self.assertEqual(account(p, 3).symbol.tolist(), ['1', '2'])

    def test_store_day_end_reason_is_standard_without_changing_frozen_trade(self):
        from stalk.sim import panic
        from stalk.sim.panic_live import PanicPaper
        day = '2026-11-02'
        bars = [[int(now(hm, day).timestamp()), 100., 102., 99., 101., 1000.] for hm in (915, 920, 1510)]
        store = Mock()
        trade = PanicPaper(store=store).settle(day, panic.Candidate('S0', 105, 100), bars)
        self.assertEqual(trade.reason, 'close')
        self.assertEqual(store.save_trade.call_args.args[0]['reason'], 'flatten')

    def test_portfolio_includes_zero_morning_and_initial_drawdown(self):
        a = account(self.pool(), 2)
        a['net'] = -100
        daily, s = portfolio(a, ['2020-01-01', '2020-01-02'])
        self.assertEqual(s['net_per_morning'], -100)
        self.assertEqual(s['max_drawdown'], 200)
        self.assertEqual(daily.iloc[-1], 0)

    def test_calendar_full_windows_quiet_counts_and_boundary(self):
        t = pd.DataFrame({'day': ['2020-01-07', '2020-01-08'], 'net': [10., -20.]})
        w = windows(t, ['2020-01-07', '2020-01-08'], pd.DateOffset(weeks=1), 'D',
                    pd.Timestamp('2020-01-01'), pd.Timestamp('2020-01-15'))
        self.assertEqual(len(w), 8)
        self.assertEqual(w.iloc[0].net, 10)
        self.assertEqual(w.iloc[-1].net, -20)
        quiet = windows(t.iloc[:0], [], pd.DateOffset(weeks=1), 'D', pd.Timestamp('2020-01-01'), pd.Timestamp('2020-01-15'))
        s = window_stats(quiet)
        self.assertEqual(s['windows_with_trades'], 0)
        self.assertIsNone(s['share_positive_with_trades'])
        self.assertEqual(s['median_net'], 0)


if __name__ == '__main__':
    unittest.main()
