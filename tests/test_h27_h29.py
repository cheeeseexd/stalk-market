"""Prior-only features/matching, exact hourly boundaries, and quote observation."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from scripts.h27_h29 import (HOURS, audit_ok, execute_path, freeze, hourly,
    inventory, match_five, past_features, spread_signals, clustered_estimate)
from scripts.panic_to_history import ts
from stalk.research.execution_log import WATCHLIST, SLOTS, summarize
from tests.test_execution_log import make, brief, quote
from tests.test_paper_daily import isolated, now


class DiagnosticTests(unittest.TestCase):
    def test_features_ignore_today_and_future(self):
        dates=pd.bdate_range('2023-01-01',periods=85).strftime('%Y-%m-%d')
        m=pd.Series(np.sin(np.arange(85))*.01,index=dates)
        r=pd.DataFrame({'A':m*1.2,'B':m*.8})
        turn=r*0+2e8
        before=past_features(r,turn,m,dates[60])
        r.loc[dates[60]:]=999;turn.loc[dates[60]:]=999;m.loc[dates[60]:]=999
        pd.testing.assert_frame_equal(before,past_features(r,turn,m,dates[60]))
        self.assertAlmostEqual(before.at['A','beta60'],1.2)

    def test_five_matches_no_tier_and_no_outcome_access(self):
        p=dict(day='2024-01-03',symbol='P',beta60=1.,vol20=.02,turn20=2e8)
        c=pd.DataFrame([dict(p,symbol=s,close=i,tier=None) for i,s in enumerate('FEDCBA')])
        a=match_five(p,c)
        self.assertEqual(a.symbol.tolist(),list('ABCDE'))
        c['close']=99999;c['tier']='wrong'
        self.assertEqual(match_five(p,c).symbol.tolist(),a.symbol.tolist())
        c.loc[c.symbol=='A','day']='2024-01-04'
        c.loc[c.symbol=='B','beta60']=1.501
        self.assertEqual(match_five(p,c).symbol.tolist(),list('CDEF'))

    def test_audit_no_fill_and_strict_one_percent_boundary(self):
        rows=[]
        inventory(pd.DataFrame({'o':[100.]*99+[np.nan],'v':[0.]*100}),'x',{'o'},rows)
        self.assertTrue(audit_ok(rows));self.assertEqual(rows[1]['zeros'],100)
        rows=[]
        inventory(pd.DataFrame({'o':[100.]*98+[np.nan,0.]}),'x',{'o'},rows)
        self.assertFalse(audit_ok(rows));self.assertEqual(rows[0]['missing_or_invalid'],2)

    def path(self):
        d='2024-12-09'
        return hourly(pd.DataFrame([dict(ts=ts(d,f'{h//100:02}:{h%100:02}'),o=100.,l=99.,h=102.,c=101.,v=0.) for h in HOURS]))

    def test_exact_1415_open_and_final_range_unused(self):
        b=self.path();b.iloc[-1,b.columns.get_loc('o')]=105
        a=execute_path(b,100)
        b.iloc[-1,b.columns.get_loc('l')]=1
        self.assertEqual(a,execute_path(b,100))
        self.assertAlmostEqual(a['exit'],105*.999)
        self.assertLess(execute_path(b,100,worse=.005)['net_return'],a['net_return'])

    def test_delayed_does_not_use_first_hour_stop_and_missing_no_substitution(self):
        b=self.path();b.iloc[0,b.columns.get_loc('l')]=90
        self.assertEqual(execute_path(b,100)['reason'],'stop')
        self.assertEqual(execute_path(b,100,True)['reason'],'flatten')
        b.iloc[3,b.columns.get_loc('o')]=np.nan
        self.assertIsNone(execute_path(b,100,True))

    def test_pair_history_excludes_entire_current_day(self):
        ds=pd.bdate_range('2024-01-01',periods=23).strftime('%Y-%m-%d')
        idx=pd.MultiIndex.from_product([ds,HOURS],names=['day','hm'])
        a=pd.DataFrame({'o':100*np.exp(np.sin(np.arange(len(idx)))*.001)},index=idx)
        b=pd.DataFrame({'o':100.},index=idx)
        a.loc[(ds[20],1015),'o']=110
        old=spread_signals(a,b)
        a.loc[(ds[20],1315),'o']=1e6
        new=spread_signals(a,b)
        left=old[(old.day==ds[20])&(old.hm==1015)].iloc[0]
        right=new[(new.day==ds[20])&(new.hm==1015)].iloc[0]
        self.assertEqual(left.z,right.z)
        self.assertLess(left.prior_last_day,left.day)
        self.assertLess(left.gross_return, .1)

    def test_day_cluster_ci_does_not_treat_same_day_events_as_independent(self):
        f=pd.DataFrame({'day':['a','a','b','b'],'gross_return':[.01,.01,-.01,-.01]})
        r=clustered_estimate(f)
        self.assertEqual(r['days'],2)
        self.assertEqual(r['mean_bps'],0)
        self.assertGreater(r['ci95_bps'][1],100)

    def test_guard_rejects_second_attempt_before_mutating_artifacts(self):
        with isolated() as root:
            (root/'data/h27').mkdir(parents=True)
            (root/'data/h27/started.json').write_text('original')
            with patch('scripts.h27_h29.ROOT',root):
                with self.assertRaises(RuntimeError):freeze('h27',[],{})
            self.assertEqual((root/'data/h27/started.json').read_text(),'original')


class WatchlistTests(unittest.TestCase):
    def test_all_seven_slots_watchlist_once_and_unknown_null(self):
        with isolated() as root:
            for slot in SLOTS:
                t=now().replace(hour=int(slot[:2]),minute=int(slot[3:5]),second=int(slot[6:]))
                recorder,kite,_=make(root,t,payload={})
                r=asyncio.run(recorder.tick(t))[-1]
                self.assertEqual({x['symbol'] for x in r['rows']},set(WATCHLIST))
                for row in r['rows']:
                    self.assertEqual(row['labels'][0]['source'],'watchlist')
                    self.assertIsNone(row['spread_bps'])
                    self.assertIsNone(row['locked'])
                self.assertEqual(summarize(r)['watchlist_count'],12)
                self.assertIsNone(summarize(r)['share_with_quotes'])
                self.assertEqual(kite.call_tool.call_args.args[0],'get_quotes')
                self.assertEqual(asyncio.run(recorder.tick(t)),[])

    def test_existing_candidate_labels_and_aggregates_preserved(self):
        with isolated() as root:
            brief(root)
            recorder,_,_=make(root)
            r=asyncio.run(recorder.tick(now(920)))[-1]
            self.assertEqual(summarize(r)['share_with_quotes'],.5)
            self.assertEqual(r['rows'][0]['symbol'],'S0')
            self.assertEqual(summarize(r)['symbols_seen'],2)

    def test_watchlist_overlap_deduplicates_and_keeps_both_labels(self):
        with isolated() as root:
            (root/'data/briefs').mkdir(parents=True)
            (root/'data/briefs/2026-11-02.json').write_text(json.dumps(dict(observed_at=now(910).isoformat(),candidates=[dict(symbol='SBIN')])))
            recorder,kite,_=make(root,payload={'NSE:SBIN':quote()})
            r=asyncio.run(recorder.tick(now(920)))[-1]
            self.assertEqual(len(r['rows']),12)
            row=next(x for x in r['rows'] if x['symbol']=='SBIN')
            self.assertEqual({x['source'] for x in row['labels']},{'brief_top8','watchlist'})
            self.assertEqual(summarize(r)['share_with_quotes'],1.)

    def test_before_monday_does_not_add_watchlist(self):
        with isolated() as root:
            recorder,kite,_=make(root,now(920,'2026-10-30'))
            r=asyncio.run(recorder.tick(now(920,'2026-10-30')))[-1]
            self.assertEqual(r['rows'],[])
            kite.call_tool.assert_not_called()


if __name__=='__main__':unittest.main()
