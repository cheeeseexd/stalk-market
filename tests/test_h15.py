"""Price provenance, corporate-action causality and replaced History rows."""
import copy
import unittest
from unittest.mock import Mock, patch

from stalk.data.store import Store
from stalk.sim import price_basis
from stalk.sim.panic_live import PanicPaper, preopen
from stalk.sim.panic_replay import build_week, save_to_history
from tests import test_panic_replay as fixtures


class H15Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.PanicWeekTests.setUpClass()

    def setUp(self):
        self.grouped = copy.deepcopy(fixtures.PanicWeekTests.grouped)
        self.official = patch('stalk.sim.price_basis.official_day', return_value={}).start()
        self.actions = patch('stalk.sim.price_basis.known_actions', return_value=[]).start()
        self.addCleanup(patch.stopall)
        self.day = '2026-09-24'

    def build(self, **kwargs):
        return build_week(self.grouped, fixtures.PanicWeekTests.tiers, fixtures.PanicWeekTests.results, '2026-09-21', **kwargs)

    def test_two_to_one_mismatch_skipped_and_logged(self):
        self.official.return_value = {'A': {'open': 190, 'prev_close': 200}}
        frames = self.build()
        scan = next(f for f in frames if f['day'] == self.day and f['clock'] == '09:20')
        self.assertEqual(scan['funnel']['price_mismatches'], 1)
        self.assertEqual(frames[-1]['trades'], [])
        self.assertTrue(any('A: price data mismatch' in x['text'] for x in scan['log']))
        self.assertIsNone(next(f for f in frames if f['day'] == self.day and f['clock'] == '09:15')['funnel'])

    def test_official_previous_close_used_changes_eligibility(self):
        for b in self.grouped['A'][self.day]:
            b[1:5] = [98.6, 98.7, 98.5, 98.6]
        self.official.return_value = {'A': {'open': 98.6, 'prev_close': 100.8}}
        trades = self.build()[-1]['trades']
        self.assertEqual([t['symbol'] for t in trades], ['A'])
        self.assertEqual(trades[0]['gap'], round(98.6/100.8-1, 4))
        self.assertEqual(trades[0]['gap_source'], 'nse_bhavcopy')

    def test_official_open_used(self):
        r = price_basis.resolve(self.day, 'A', 98.1, 100, official={'A': {'open': 97.9, 'prev_close': 100}})
        self.assertFalse(r['mismatch'])
        self.assertEqual(r['open'], 97.9)

    def test_official_gap_does_not_require_yahoo_predecessor(self):
        del self.grouped['A']['2026-09-23']
        self.official.return_value = {'A': {'open': 95, 'prev_close': 100}}
        self.assertEqual([t['symbol'] for t in self.build()[-1]['trades']], ['A'])

    def test_previous_component_scale_mismatch_rejected(self):
        r = price_basis.resolve(self.day, 'A', 95, 50, official={'A': {'open': 95, 'prev_close': 100}})
        self.assertTrue(r['mismatch'])
        self.assertIn('previous last close', r['details'][0])

    def test_missing_official_is_marked_fallback(self):
        frames = self.build()
        self.assertEqual(frames[-1]['trades'][0]['gap_source'], 'yahoo_fallback')
        self.assertTrue(any('Yahoo fallback' in x['text'] for x in next(f for f in frames if f['day'] == self.day and f['clock'] == '09:20')['log']))

    def test_invalid_official_does_not_fall_back(self):
        r = price_basis.resolve(self.day, 'A', 95, 100, official={'A': {'open': 0, 'prev_close': 100}})
        self.assertEqual(r['source'], 'nse_bhavcopy')
        self.assertTrue(r['mismatch'])

    def test_one_percent_boundary_and_invalid_prices(self):
        self.assertFalse(price_basis.differs(101, 100))
        self.assertTrue(price_basis.differs(101.0001, 100))
        self.assertTrue(price_basis.differs(float('nan'), 100))

    def test_corporate_action_notice_must_already_be_published(self):
        notice = {'symbol': 'A', 'day': self.day, 'published': '23-Sep-2026 20:00:00'}
        self.actions.return_value = [notice]
        self.assertTrue(price_basis.resolve(self.day, 'A', 50, 100, official={'A': {'open': 50, 'prev_close': 100}})['mismatch'])
        notice['published'] = '24-Sep-2026 10:00:00'
        self.assertFalse(price_basis.resolve(self.day, 'A', 50, 100, official={'A': {'open': 50, 'prev_close': 100}})['mismatch'])

    def test_live_uses_nse_previous_close_and_rejects_bad_open(self):
        client = Mock()
        client.get.return_value.json.return_value = {'data': [{'metadata': {'symbol': 'A', 'previousClose': 100, 'iep': 95, 'lastPrice': 999}}]}
        self.assertEqual(preopen(client), {'A': (100., 95.)})
        runner = PanicPaper()
        runner._write = Mock()
        bad = copy.deepcopy(self.grouped['A'][self.day])
        bad[0][1] = 47.5
        with patch('stalk.sim.panic_live.universe', return_value={'A': 'midcap'}):
            self.assertEqual(runner.morning(self.day, self.grouped['NIFTY 50'], {'A': (100, 95)}, set(), lambda _: bad), [])
            self.assertTrue(any(c.kwargs.get('why') == 'price data mismatch' for c in runner._write.call_args_list))
            picks = runner.morning(self.day, self.grouped['NIFTY 50'], {'A': (100.8, 95)}, set(), lambda _: self.grouped['A'][self.day])
            self.assertEqual(picks[0].prev_close, 100.8)

    def test_live_missing_auction_does_not_substitute_last_trade(self):
        client = Mock()
        client.get.return_value.json.return_value = {'data': [{'metadata': {'symbol': 'A', 'previousClose': 100, 'lastPrice': 95}}]}
        self.assertEqual(preopen(client), {})

    def test_thresholds_are_explicit_and_distinct(self):
        for b in self.grouped['NIFTY 50'][self.day]:
            b[1:5] = [99.6, 99.7, 99.5, 99.6]
        self.assertEqual(self.build(index_gap=-.005)[-1]['trades'], [])
        self.assertEqual(len(self.build(index_gap=-.003)[-1]['trades']), 1)

    def test_replacing_history_deletes_removed_simulation_trades_only(self):
        frames = self.build()
        store = Store(':memory:')
        self.addCleanup(store._conn.close)
        cid = save_to_history(store, frames, '2026-09-21')
        other = save_to_history(store, frames, '2026-09-21', prefix='v')
        self.assertEqual(len(store.trades(account_prefix=cid + ':')), 1)
        empty = copy.deepcopy(frames)
        empty[-1]['trades'] = []
        self.assertEqual(save_to_history(store, empty, '2026-09-21'), cid)
        self.assertEqual(store.trades(account_prefix=cid + ':'), [])
        self.assertEqual(len(store.trades(account_prefix=other + ':')), 1)


if __name__ == '__main__':
    unittest.main()
