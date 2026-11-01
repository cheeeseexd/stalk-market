"""Unavailable research days must not crash or become zero-return Sessions."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from stalk.api import server


class H21SessionTests(unittest.TestCase):
    def response(self, nets):
        campaign = dict(id='h21_hybrid_a_10y', created_at=1,
            first_day='2016-11-02', last_day='2026-10-30',
            summary=dict(label='Hybrid A, 10 years', kind='panic_buy',
                accounts={'panic_buy': dict(days=[{'net': n, 'wins': 0, 'losses': 0} for n in nets],
                    net=sum(n for n in nets if n is not None), stats={})}))
        store = SimpleNamespace(campaigns=lambda limit: [campaign])
        with patch.object(server, 'get_agent', return_value=SimpleNamespace(store=store)):
            return json.loads(server.sessions().body)[0]

    def test_missing_days_do_not_crash_sessions_or_count_as_trading_days(self):
        r = self.response([None, 0., -50., 100.])
        self.assertEqual(r['label'], 'Hybrid A, 10 years')
        self.assertEqual(r['sessions'], 4)
        self.assertEqual(r['trading_days'], 2)
        self.assertEqual(r['unknown_days'], 1)
        self.assertEqual(r['best_day'], 100.)
        self.assertEqual(r['worst_day'], -50.)

    def test_all_unavailable_days_keep_extrema_unknown(self):
        r = self.response([None, None])
        self.assertIsNone(r['best_day'])
        self.assertIsNone(r['worst_day'])
        self.assertEqual(r['unknown_days'], 2)
        self.assertEqual(r['trading_days'], 0)


if __name__ == '__main__':
    unittest.main()
