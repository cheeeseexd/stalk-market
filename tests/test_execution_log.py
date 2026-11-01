"""Prospective recorder tests: fake broker/public sources, isolated disk, no network."""
import asyncio
from copy import deepcopy
from datetime import timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from stalk.research.execution_log import (ExecutionLog, MISSources, SLOTS, WATCHLIST, candidates, latest_summary,
                                         levels, mis_today, quote_row, read_records, review_evidence, walk)
from stalk.research.paper_daily import PaperDaily, write_json
from tests.test_paper_daily import isolated, now, log, events

HTML = '<table><th>MIS/CO</th><tr data-scrip="S0" data-mis_margin="20"></tr><tr data-scrip="S1" data-mis_margin="100"></tr></table>'


def quote(t=None):
    return dict(timestamp=(t or now(920)).isoformat(), last_price=100,
                depth={'buy': [{'price': 99.9, 'quantity': 100}],
                       'sell': [{'price': 100.1, 'quantity': 10}, {'price': 100.2, 'quantity': 100}]},
                buy_quantity=1000, sell_quantity=2000, lower_circuit_limit=90, upper_circuit_limit=110)


def brief(root):
    write_json(root/'data/briefs/2026-11-02.json', dict(observed_at=now(910).isoformat(),
               candidates=[{'symbol': 'S0'}, {'symbol': 'S2'}]))


def make(root, t=None, payload=None, logged_in=True):
    t = t or now(920)
    kite = SimpleNamespace(logged_in=logged_in, call_tool=AsyncMock(return_value=payload if payload is not None else {'NSE:S0': quote(t)}))
    source = SimpleNamespace(fetch=Mock(return_value=HTML))
    return ExecutionLog(kite, root, mis_source=source, clock=lambda: t), kite, source


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        for client in (httpx.Client, httpx.AsyncClient):
            p = patch.object(client, 'send', side_effect=AssertionError('No network'))
            p.start()
            self.addCleanup(p.stop)

    def row(self, raw=None, mis=None):
        return quote_row('S0', quote() if raw is None else raw, now(920), now(920), now(920),
                         {'symbols': ['S0']} if mis is None else mis, [])

    def test_file_written_once_per_snapshot_across_restart(self):
        with isolated() as root:
            brief(root)
            recorder, kite, source = make(root)
            asyncio.run(recorder.tick(now(920)))
            path = root/'data/execution/2026-11-02.jsonl'
            before = path.read_bytes()
            again, _, _ = make(root)
            self.assertEqual(asyncio.run(again.tick(now(920))), [])
            self.assertEqual(path.read_bytes(), before)
            records = read_records(root, '2026-11-02')
            self.assertEqual([r['slot'] for r in records], ['09:15:30', '09:20:00'])
            row = records[1]['rows'][0]
            self.assertEqual(row['symbol'], 'S0')
            self.assertEqual(row['last_price'], 100)
            self.assertEqual(row['total_buy_quantity'], 1000)
            self.assertEqual(row['ask_size'], 10)
            kite.call_tool.assert_awaited_once_with('get_quotes', {'instruments': ['NSE:'+s for s in sorted({'S0','S2',*WATCHLIST})]})
            source.fetch.assert_called_once()

    def test_weekend_and_before_first_slot_no_io(self):
        with isolated() as root:
            recorder, kite, source = make(root)
            self.assertEqual(asyncio.run(recorder.tick(now(910))), [])
            self.assertEqual(asyncio.run(recorder.tick(now(920, '2026-11-01'))), [])
            self.assertFalse((root/'data/execution/2026-11-02.jsonl').exists())
            kite.call_tool.assert_not_called()
            source.fetch.assert_not_called()

    def test_half_minute_slot_and_all_seven_times(self):
        self.assertEqual(SLOTS, ('09:15:30', '09:20:00', '09:25:00', '11:15:00', '11:20:00', '15:05:00', '15:10:00'))
        with isolated() as root:
            t = now(915)+timedelta(seconds=30)
            recorder, _, _ = make(root, t)
            self.assertEqual(len(asyncio.run(recorder.tick(t))), 1)
            self.assertEqual(read_records(root, '2026-11-02')[0]['status'], 'observed')

    def test_start_after_market_marks_missed_without_backfill(self):
        with isolated() as root:
            brief(root)
            recorder, kite, source = make(root, now(1535))
            rows = asyncio.run(recorder.tick(now(1535)))
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(r['status'] == 'missed' and r['rows'] == [] for r in rows))
            kite.call_tool.assert_not_called()
            source.fetch.assert_not_called()

    def test_logged_out_is_null_quote_not_invented_zero(self):
        with isolated() as root:
            brief(root)
            recorder, kite, _ = make(root, logged_in=False)
            r = asyncio.run(recorder.tick(now(920)))[-1]
            row = r['rows'][0]
            for key in ('bid', 'ask', 'bid_size', 'last_price', 'spread_bps', 'lower_circuit', 'locked'):
                self.assertIsNone(row[key], key)
            self.assertIsNone(row['buy_estimate']['vwap'])
            self.assertFalse(row['quotes_available'])
            self.assertTrue(row['mis_eligible'])
            kite.call_tool.assert_not_called()

    def test_broker_failure_and_omitted_symbol_preserve_rows(self):
        with isolated() as root:
            brief(root)
            recorder, kite, _ = make(root)
            kite.call_tool.side_effect = RuntimeError('offline')
            r = asyncio.run(recorder.tick(now(920)))[-1]
            self.assertEqual(len(r['rows']), 2+len(WATCHLIST))
            self.assertIn('RuntimeError', r['reason'])
            self.assertTrue(all(not row['quotes_available'] for row in r['rows']))

    def test_mis_once_per_day_success_and_next_day_refetch(self):
        with isolated() as root:
            src = SimpleNamespace(fetch=Mock(return_value=HTML))
            one = mis_today(root, now(920), src)
            self.assertEqual(one, mis_today(root, now(1520), src))
            self.assertEqual(one['symbols'], ['S0', 'S1'])
            self.assertTrue((root/'data/execution/mis/2026-11-02.html').exists())
            mis_today(root, now(920, '2026-11-03'), src)
            self.assertEqual(src.fetch.call_count, 2)

    def test_mis_failed_attempt_cached_no_stale_success(self):
        with isolated() as root:
            src = SimpleNamespace(fetch=Mock(return_value=HTML))
            mis_today(root, now(920, '2026-10-30'), src)
            src.fetch.side_effect = OSError('offline')
            result = mis_today(root, now(920), src)
            self.assertIsNone(result['symbols'])
            self.assertEqual(result, mis_today(root, now(925), src))
            self.assertEqual(src.fetch.call_count, 2)

    def test_mis_empty_or_malformed_stays_unknown(self):
        for html in ('<html>login</html>', '<table>MIS</table>', '<tr data-scrip="S0" data-mis_margin="bad"></tr>'):
            with isolated() as root:
                result = mis_today(root, now(920), SimpleNamespace(fetch=lambda: html))
                self.assertIsNone(result['symbols'])

    def test_public_mis_fetch_uses_only_public_get(self):
        fake = Mock()
        fake.__enter__ = Mock(return_value=fake)
        fake.__exit__ = Mock(return_value=False)
        fake.get.return_value = SimpleNamespace(text=HTML, raise_for_status=lambda: None)
        with patch('stalk.research.execution_log.httpx.Client', return_value=fake):
            self.assertEqual(MISSources().fetch(), HTML)
        fake.get.assert_called_once_with('https://zerodha.com/margin-calculator/Equity/')

    def test_mis_false_distinct_from_unknown(self):
        self.assertFalse(self.row(mis={'symbols': ['OTHER']})['mis_eligible'])
        self.assertIsNone(self.row(mis={'symbols': None})['mis_eligible'])

    def test_depth_walk_quantity_vwap_spread_and_impact(self):
        r = self.row()
        self.assertAlmostEqual(r['spread_bps'], 20)
        buy = r['buy_estimate']
        self.assertEqual(buy['quantity'], 49)
        self.assertTrue(buy['complete'])
        self.assertAlmostEqual(buy['vwap'], (10*100.1+39*100.2)/49)
        self.assertAlmostEqual(buy['slippage_bps'], (buy['vwap']/100-1)*10000)
        self.assertAlmostEqual(buy['impact_bps'], (buy['vwap']/100.1-1)*10000)
        self.assertEqual(r['sell_estimate']['quantity'], 50)
        self.assertAlmostEqual(r['sell_estimate']['slippage_bps'], 10)

    def test_insufficient_depth_never_reports_partial_as_full_fill(self):
        q = quote()
        q['depth']['sell'] = [{'price': 100.1, 'quantity': 2}]
        estimate = self.row(q)['buy_estimate']
        self.assertFalse(estimate['complete'])
        self.assertEqual(estimate['filled_quantity'], 2)
        self.assertEqual(estimate['unfilled_quantity'], 47)
        self.assertIsNone(estimate['vwap'])
        self.assertIsNone(estimate['impact_bps'])

    def test_zero_affordable_shares_have_unknown_slippage(self):
        e = walk([{'price': 6000, 'quantity': 10}], 'buy', 6000)
        self.assertEqual(e['quantity'], 0)
        self.assertFalse(e['complete'])
        self.assertIsNone(e['vwap'])

    def test_depth_sorted_and_missing_quantities_not_zero(self):
        self.assertEqual(levels({'buy': [{'price': 99, 'quantity': 1}, {'price': 100, 'quantity': 2}]}, 'buy')[0]['price'], 100)
        self.assertIsNone(levels({'sell': [{'price': 100}]}, 'sell'))
        self.assertIsNone(levels({'sell': [{'price': 0, 'quantity': 5}]}, 'sell'))
        self.assertEqual(levels({'sell': [{'price': 0, 'quantity': 0}]}, 'sell'), [])

    def test_crossed_book_has_no_cost_estimate(self):
        q = quote()
        q['depth']['buy'][0]['price'] = 101
        r = self.row(q)
        self.assertIsNone(r['spread_bps'])
        self.assertIsNone(r['buy_estimate']['vwap'])

    def test_one_sided_depth_still_estimates_fill_without_midpoint_slippage(self):
        q = quote()
        q['depth']['buy'] = []
        r = self.row(q)
        self.assertTrue(r['buy_estimate']['complete'])
        self.assertIsNotNone(r['buy_estimate']['vwap'])
        self.assertIsNone(r['buy_estimate']['slippage_bps'])
        self.assertIsNone(r['spread_bps'])
        self.assertFalse(r['sell_estimate']['complete'])

    def test_timestamp_only_response_is_not_a_quote(self):
        r = self.row({'timestamp': now(920).isoformat()})
        self.assertFalse(r['quotes_available'])
        self.assertEqual(r['quote_status'], 'unavailable')

    def test_supplied_prices_survive_missing_sizes_without_invented_depth(self):
        q = quote()
        q['depth'] = {'buy': [{'price': 99.9}], 'sell': [{'price': 100.1}]}
        r = self.row(q)
        self.assertEqual(r['bid'], 99.9)
        self.assertEqual(r['ask'], 100.1)
        self.assertAlmostEqual(r['spread_bps'], 20)
        self.assertIsNone(r['bid_size'])
        self.assertIsNone(r['buy_estimate']['vwap'])
        self.assertEqual(r['depth'], {'buy': None, 'sell': None})

    def test_circuit_distances_lock_and_missing_band(self):
        r = self.row()
        self.assertEqual(r['distance_to_lower_bps'], 1000)
        self.assertEqual(r['distance_to_upper_bps'], 1000)
        self.assertFalse(r['locked'])
        q = quote()
        q['last_price'] = 90
        q['depth']['buy'] = [{'price': 0, 'quantity': 0}]
        self.assertTrue(self.row(q)['locked'])
        q = quote()
        q['last_price'] = 110
        q['depth']['sell'] = []
        self.assertTrue(self.row(q)['locked'])
        del q['depth']['sell']
        self.assertIsNone(self.row(q)['locked'])
        q.pop('lower_circuit_limit')
        q.pop('upper_circuit_limit')
        self.assertIsNone(self.row(q)['at_circuit'])

    def test_stale_missing_future_exchange_times_not_usable(self):
        for ts in (None, now(920, '2026-10-30').isoformat(), now(925).isoformat()):
            q = quote()
            q['timestamp'] = ts
            r = self.row(q)
            self.assertFalse(r['quotes_available'])
            self.assertIsNone(r['spread_bps'])
            self.assertIsNone(r['locked'])

    def test_late_response_keeps_times_without_claiming_slot(self):
        with isolated() as root:
            brief(root)
            recorder, _, _ = make(root)
            recorder.clock = Mock(side_effect=[now(920), now(920), now(921)])
            r = asyncio.run(recorder.tick(now(920)))[-1]
            self.assertEqual(r['status'], 'late')
            self.assertFalse(r['rows'][0]['quotes_available'])
            self.assertEqual(r['observed_at'], now(921).isoformat())

    def test_candidates_union_both_versions_top8_plus_picks_no_future(self):
        with isolated() as root:
            brief(root)
            event = events()[0]
            event['ranking'] = [{'symbol': f'R{i}'} for i in range(10)]
            event['picks'] = ['R9']
            log(root, 'panic_paper.jsonl', [event])
            loose = deepcopy(event)
            loose['picks'] = ['LATER']
            log(root, 'panic_paper_loose.jsonl', [loose])
            early, _ = candidates(root, now(920))
            self.assertEqual(set(early), {'S0', 'S2'})
            later, _ = candidates(root, now(925))
            self.assertIn('R9', later)
            self.assertIn('LATER', later)
            self.assertNotIn('R8', later)
            self.assertEqual(later['R9'][0]['observed_at'], event['at'])

    def test_h19_default_explicit_skip_at_decision_and_entry(self):
        with isolated() as root:
            recorder, _, _ = make(root, now(1115))
            decision = asyncio.run(recorder.tick(now(1115)))[-1]
            self.assertEqual(decision['h19']['status'], 'skipped')
            self.assertIn('pipeline unavailable', decision['h19']['reason'])
            recorder.clock = lambda: now(1120)
            entry = asyncio.run(recorder.tick(now(1120)))[-1]
            self.assertEqual(entry['h19'], decision['h19'])

    def test_h19_live_adapter_list_frozen_for_entry_and_exits(self):
        with isolated() as root:
            recorder, kite, _ = make(root, now(1115), payload={'NSE:SHORT': quote(now(1115))})
            recorder.h19_source = AsyncMock(return_value={'status': 'available', 'reason': None,
                                                         'candidates': [{'symbol': 'SHORT', 'side': 'short'}]})
            d = asyncio.run(recorder.tick(now(1115)))[-1]
            self.assertEqual(d['rows'][0]['symbol'], 'SHORT')
            for hm in (1120, 1505, 1510):
                recorder.clock = lambda hm=hm: now(hm)
                kite.call_tool.return_value = {'NSE:SHORT': quote(now(hm))}
                r = asyncio.run(recorder.tick(now(hm)))[-1]
                self.assertEqual(r['h19'], d['h19'])
                self.assertEqual(r['rows'][0]['symbol'], 'SHORT')
            recorder.h19_source.assert_awaited_once()

    def test_api_latest_summary_known_denominators_nulls_and_no_boot(self):
        from fastapi.testclient import TestClient
        from stalk.api import server
        with isolated() as root, patch.object(server, 'BASE_DIR', str(root)), patch.object(server, 'get_agent', side_effect=AssertionError('No boot')):
            client = TestClient(server.app)
            self.assertEqual(client.get('/api/execution').json(), {'execution': None, 'preopen': None})
            brief(root)
            recorder, _, _ = make(root)
            asyncio.run(recorder.tick(now(920)))
            s = client.get('/api/execution').json()['execution']
            self.assertEqual(s['day'], '2026-11-02')
            missed, observed = s['snapshots']
            self.assertIsNone(missed['symbols_seen'])
            self.assertIsNone(missed['median_spread_bps'])
            self.assertEqual(observed['share_with_quotes'], .5)
            self.assertEqual(observed['share_mis_eligible'], .5)
            self.assertEqual(observed['mis_known_count'], 2)
            self.assertIsNone(observed['any_locked_or_at_circuit'])

    def test_review_matches_later_picks_to_actual_0920_not_later_quotes(self):
        with isolated() as root:
            brief(root)
            recorder, _, _ = make(root)
            asyncio.run(recorder.tick(now(920)))
            e = events()
            e[0]['picks'] = ['S0', 'MISSING']
            log(root, 'panic_paper.jsonl', e)
            review = PaperDaily(root).review(now(1535))
            evidence = review['execution_evidence']['versions']['strict']
            self.assertAlmostEqual(evidence['median_spread_bps'], 20)
            self.assertGreater(evidence['median_buy_slippage_bps'], 10)
            self.assertEqual(evidence['missing_quote_picks'], ['MISSING'])
            self.assertIn('MISSING', evidence['unknown_mis_picks'])
            self.assertIsNone(evidence['per_pick'][1]['spread_bps'])
            saved = json.loads((root/'data/reviews/2026-11-02.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['execution_evidence'], review['execution_evidence'])

    def test_review_with_no_capture_or_picks_has_null_medians(self):
        with isolated() as root:
            v = review_evidence(root, '2026-11-02', {'strict': {'process': {'picks': ['S0']}}})['versions']['strict']
            self.assertIsNone(v['median_spread_bps'])
            self.assertIsNone(v['median_buy_slippage_bps'])
            self.assertEqual(v['missing_quote_picks'], ['S0'])

    def test_corrupt_evidence_is_never_overwritten(self):
        with isolated() as root:
            p = root/'data/execution/2026-11-02.jsonl'
            p.parent.mkdir(parents=True)
            p.write_text('{bad\n', encoding='utf-8')
            recorder, kite, _ = make(root)
            with self.assertRaises(ValueError):
                asyncio.run(recorder.tick(now(920)))
            self.assertEqual(p.read_text(encoding='utf-8'), '{bad\n')
            kite.call_tool.assert_not_called()

    def test_agent_recorder_loop_stops_on_cancellation_without_orders(self):
        from stalk.agent import Agent
        async def exercise():
            started = asyncio.Event()
            async def tick(t):
                started.set()
            a = SimpleNamespace(execution_log=SimpleNamespace(tick=AsyncMock(side_effect=tick)))
            task = asyncio.create_task(Agent._execution_loop(a))
            await started.wait()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.assertTrue(task.done())
            a.execution_log.tick.assert_awaited_once()
        asyncio.run(exercise())
