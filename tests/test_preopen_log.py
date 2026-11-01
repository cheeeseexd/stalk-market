"""Auction capture with fake clients only; no network, orders or Agent boot."""
import asyncio
from datetime import timedelta
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx

from stalk.research.preopen_log import PreopenLog, SLOTS, latest_summary, read_records
from stalk.research.execution_log import ExecutionLog, WATCHLIST
from tests.test_paper_daily import isolated, now, write_json


def fake(root, t, logged=True):
    q = dict(timestamp=t.isoformat(),last_price=95,indicative_price=94,
        buy_quantity=0,sell_quantity=450,ohlc={'open':93},
        depth={'buy':[{'price':94,'quantity':10,'orders':3}], 'sell':[]})
    kite = SimpleNamespace(logged_in=logged,call_tool=AsyncMock(return_value={'data':{'NSE:BEL':q}}))
    source = SimpleNamespace(market=Mock(return_value=dict(stock_snapshot_current=True,
        tiers={'PANIC':'small','BEL':'large'},candidates=[
            dict(symbol='PANIC',gap=-.04,source_at=t.isoformat()),
            dict(symbol='BEL',gap=-.03,source_at=t.isoformat()),
            dict(symbol='FUTURE',gap=-.1,source_at=t.isoformat())])))
    return PreopenLog(kite,root,clock=lambda:t,candidate_source=source),kite,source


class PreopenTests(unittest.TestCase):
    def setUp(self):
        for client in (httpx.Client,httpx.AsyncClient):
            p = patch.object(client,'send',side_effect=AssertionError('No network'))
            p.start()
            self.addCleanup(p.stop)

    def test_all_slots_data_restart_dedup_and_open(self):
        self.assertEqual(SLOTS,('09:01:00','09:04:00','09:07:00','09:08:30','09:15:00'))
        with isolated() as root:
            for slot in SLOTS:
                t = now().replace(hour=int(slot[:2]),minute=int(slot[3:5]),second=int(slot[6:]))
                rec,kite,source = fake(root,t)
                r = asyncio.run(rec.tick(t))[-1]
                self.assertEqual(r['status'],'observed')
                symbols = {row['symbol'] for row in r['rows']}
                self.assertEqual(symbols,{*WATCHLIST,'NIFTY 50','PANIC'})
                kite.call_tool.assert_awaited_once_with('get_quotes',{'instruments':['NSE:'+s for s in sorted(symbols)]})
                bel = next(x for x in r['rows'] if x['symbol']=='BEL')
                self.assertEqual(bel['total_buy_quantity'],0)
                self.assertEqual(bel['total_sell_quantity'],450)
                self.assertEqual(bel['indicative_price'],94)
                self.assertEqual(bel['last_price'],95)
                self.assertEqual(bel['depth']['buy'][0]['orders'],3)
                self.assertEqual(bel['depth']['sell'],[])
                self.assertEqual(bel['open_price'],93 if slot=='09:15:00' else None)
                unknown = next(x for x in r['rows'] if x['symbol']=='PANIC')
                self.assertIsNone(unknown['last_price'])
                self.assertIsNone(unknown['depth'])
                self.assertIsNone(unknown['total_buy_quantity'])
                if slot=='09:15:00':source.market.assert_not_called()
                else:source.market.assert_called_once()
                before = (root/'data/execution/preopen_2026-11-02.jsonl').read_bytes()
                self.assertEqual(asyncio.run(fake(root,t)[0].tick(t)),[])
                self.assertEqual((root/'data/execution/preopen_2026-11-02.jsonl').read_bytes(),before)
            self.assertEqual(len(latest_summary(root)['snapshots']),5)

    def test_logged_out_no_file_no_source_no_quotes_even_after_close(self):
        with isolated() as root:
            for hm in (901,904,907,908,915,1535):
                rec,kite,source = fake(root,now(hm),logged=False)
                self.assertEqual(asyncio.run(rec.tick(now(hm))),[])
                kite.call_tool.assert_not_called()
                source.market.assert_not_called()
            self.assertFalse((root/'data').exists())

    def test_before_start_weekend_and_before_first_slot(self):
        with isolated() as root:
            for t in (now(900),now(901,'2026-11-01'),now(901,'2026-10-30')):
                rec,kite,source = fake(root,t)
                self.assertEqual(asyncio.run(rec.tick(t)),[])
                kite.call_tool.assert_not_called()
                source.market.assert_not_called()
            self.assertFalse((root/'data').exists())

    def test_missed_slots_never_reconstructed_and_future_brief_not_used(self):
        with isolated() as root:
            t = now(907)
            write_json(root/'data/briefs/2026-11-02.json',dict(observed_at=now(910).isoformat(),candidates=[dict(symbol='LATER')]))
            rec,kite,_ = fake(root,t)
            rows = asyncio.run(rec.tick(t))
            self.assertEqual([r['status'] for r in rows],['missed','missed','observed'])
            self.assertTrue(all(r['rows']==[] for r in rows[:2]))
            self.assertNotIn('NSE:LATER',kite.call_tool.call_args.args[1]['instruments'])

    def test_source_failure_still_quotes_watchlist_and_index(self):
        with isolated() as root:
            rec,kite,source = fake(root,now(901))
            source.market.side_effect = RuntimeError('unavailable')
            r = asyncio.run(rec.tick(now(901)))[0]
            self.assertEqual(len(r['rows']),13)
            self.assertIn('unknown',r['candidate_notes'][-1])
            kite.call_tool.assert_awaited_once()

    def test_stale_candidate_source_and_stale_quote_do_not_claim_open(self):
        with isolated() as root:
            rec,kite,source = fake(root,now(901))
            source.market.return_value['stock_snapshot_current'] = False
            r = asyncio.run(rec.tick(now(901)))[0]
            self.assertNotIn('PANIC',{x['symbol'] for x in r['rows']})
            rec,kite,_ = fake(root,now(915))
            kite.call_tool.return_value['data']['NSE:BEL']['timestamp'] = now(915,'2026-10-30').isoformat()
            r = asyncio.run(rec.tick(now(915)))[-1]
            bel = next(x for x in r['rows'] if x['symbol']=='BEL')
            self.assertIsNone(bel['open_price'])
            self.assertEqual(bel['ohlc_open_as_returned'],93)
            self.assertEqual(bel['quote_status'],'stale')

    def test_quote_failure_and_absent_indicative_stay_null(self):
        with isolated() as root:
            rec,kite,_ = fake(root,now(901))
            kite.call_tool.side_effect = RuntimeError('offline')
            r = asyncio.run(rec.tick(now(901)))[0]
            self.assertIn('RuntimeError',r['reason'])
            self.assertTrue(all(x['indicative_price'] is None and x['depth'] is None for x in r['rows']))

    def test_late_response_and_slow_discovery_are_explicit(self):
        with isolated() as root:
            rec,kite,_ = fake(root,now(901))
            rec.clock = Mock(side_effect=[now(901),now(901),now(901),now(902)])
            r = asyncio.run(rec.tick(now(901)))[0]
            self.assertEqual(r['status'],'late')
            self.assertEqual(next(x for x in r['rows'] if x['symbol']=='BEL')['quote_status'],'late')
        with isolated() as root:
            rec,kite,_ = fake(root,now(901))
            rec.clock = Mock(side_effect=[now(901),now(902),now(902)])
            r = asyncio.run(rec.tick(now(901)))[0]
            self.assertEqual(r['status'],'missed')
            kite.call_tool.assert_not_called()

    def test_existing_execution_tick_drives_preopen_without_h16_requests(self):
        with isolated() as root:
            rec,kite,source = fake(root,now(901))
            execution = ExecutionLog(kite,root,clock=lambda:now(901))
            execution.preopen = rec
            self.assertEqual(asyncio.run(execution.tick(now(901))),[])
            self.assertEqual(len(read_records(root,'2026-11-02')),1)
            self.assertFalse((root/'data/execution/2026-11-02.jsonl').exists())
            kite.call_tool.assert_awaited_once()

    def test_api_adds_preopen_without_boot_or_changing_execution(self):
        from fastapi.testclient import TestClient
        from stalk.api import server
        with isolated() as root, patch.object(server,'BASE_DIR',str(root)), patch.object(server,'get_agent',side_effect=AssertionError('No boot')):
            client = TestClient(server.app)
            self.assertEqual(client.get('/api/execution').json(),dict(execution=None,preopen=None))
            rec,_,_ = fake(root,now(901))
            asyncio.run(rec.tick(now(901)))
            obj = client.get('/api/execution').json()
            self.assertIsNone(obj['execution'])
            self.assertEqual(obj['preopen']['snapshots'][0]['slot'],'09:01:00')

    def test_corrupt_journal_is_not_overwritten(self):
        with isolated() as root:
            p = root/'data/execution/preopen_2026-11-02.jsonl'
            p.parent.mkdir(parents=True)
            p.write_text('{broken\n',encoding='utf-8')
            rec,kite,_ = fake(root,now(901))
            with self.assertRaises(ValueError):asyncio.run(rec.tick(now(901)))
            self.assertEqual(p.read_text(encoding='utf-8'),'{broken\n')
            kite.call_tool.assert_not_called()
