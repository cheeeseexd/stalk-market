"""Causal research features, fixed controls, added-day isolation and bar timing."""
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

import numpy as np
import pandas as pd

from scripts.h22_h25 import (added_days, execute_added, hourly_signal, match_one,
    morning_pool, ordinary_matches, past_features, sigma20, marker, ts)
from scripts.h25_complete import close_measurement


class ResearchCausalityTests(unittest.TestCase):
    def test_features_ignore_today_future_and_do_not_fill_missing_history(self):
        dates=pd.date_range('2024-01-01',periods=65).strftime('%Y-%m-%d')
        market=pd.Series(np.linspace(-.02,.02,65),index=dates)
        returns=pd.DataFrame({'A':market*1.3,'B':market*.8})
        turnover=returns*0+2e8
        f=past_features(returns,turnover,market,dates[60])
        self.assertAlmostEqual(f.at['A','beta60'],1.3)
        returns.loc[dates[60]:]=999
        turnover.loc[dates[60]:]=999
        pd.testing.assert_frame_equal(f,past_features(returns,turnover,market,dates[60]))
        self.assertTrue(past_features(returns,turnover,market,dates[20]).empty)
        turnover.at[dates[59],'A']=np.nan
        self.assertTrue(pd.isna(past_features(returns,turnover,market,dates[60]).at['A','turn20']))

    def test_pool_ignores_same_day_outcomes_and_turnover(self):
        opening=pd.DataFrame(dict(open=[95.,99.5],prev_close=[100.,100.],high=[98.,102.],low=[80.,90.],close=[90.,100.],value=[1.,1.]),index=['A','B'])
        previous=pd.DataFrame({'value':[2e8,2e8]},index=['A','B'])
        features=pd.DataFrame(dict(beta60=[1.,1.],turn20=[2e8,2e8],vol20=[.02,.02]),index=['A','B'])
        before=morning_pool('2024-01-01',opening,previous,features,tiers={'A':'smallcap','B':'smallcap'})
        opening[['high','low','close','value']]=0
        pd.testing.assert_frame_equal(before,morning_pool('2024-01-01',opening,previous,features,tiers={'A':'smallcap','B':'smallcap'}))
        p=morning_pool('2024-01-01',opening,previous,features,actions=['A'],funds=['B'])
        self.assertTrue(p.empty)

    def test_matching_has_fixed_calipers_ties_and_no_outcome_access(self):
        pick=dict(symbol='P',tier='smallcap',beta60=1.,turn20=2e8,vol20=.02,gap=-.06)
        candidates=pd.DataFrame([dict(symbol=s,tier=t,beta60=b,turn20=2e8,vol20=.02,gap=-.005,close=c)
            for s,t,b,c in [('B','smallcap',1.,999),('A','smallcap',1.,1),('C','midcap',1.,20),('D','smallcap',1.36,20)]])
        self.assertEqual(match_one(pick,candidates)['symbol'],'A')
        candidates['close']=-1e12
        self.assertEqual(match_one(pick,candidates)['symbol'],'A')
        self.assertIsNone(match_one(pick,candidates,event=True))
        pick['tier']=None
        self.assertIsNone(match_one(pick,candidates))

    def test_volatility_is_shifted_and_added_days_exclude_frozen_dates(self):
        x=pd.DataFrame({'c':100*np.cumprod(1+np.linspace(-.02,.02,25))})
        original=sigma20(x)
        x.loc[22:,'c']=1e12
        self.assertEqual(original.iloc[22],sigma20(x).iloc[22])
        n=pd.DataFrame({'gap':[-.006,-.005,-.004,0.], 'sigma20':[.01,.01,.01,np.nan]},index=['old','edge','added','unknown'])
        self.assertEqual(list(added_days(n,.25)),['edge','added'])
        self.assertNotIn('old',added_days(n,.5))

    def test_hourly_timestamp_is_start_and_future_bars_cannot_change_signal(self):
        bars=pd.DataFrame([dict(ts=ts('2024-12-09',h),o=100.,c=c) for h,c in [('09:15',99.),('10:15',98.),('11:15',97.),('12:15',110.)]])
        f=hourly_signal(bars)
        self.assertEqual(f.iloc[0].signal_ts,ts('2024-12-09','12:15'))
        self.assertAlmostEqual(f.iloc[0].morning,-.03)
        bars.loc[3,'c']=1e9
        pd.testing.assert_frame_equal(f,hourly_signal(bars))

    def test_ordinary_controls_are_prior_days_only_and_fixed_move_matched(self):
        pick=dict(day='2024-12-09',oldest_day='2024-01-01',morning=-.03,gap=-.01,vol20=.02)
        c=pd.DataFrame([dict(day=d,morning=m,gap=-.01,vol20=.02,close=999) for d,m in
            [('2024-12-10',-.03),('2023-12-31',-.03),('2024-12-08',-.03),('2024-12-07',0.)]])
        self.assertEqual(ordinary_matches(pick,c).day.tolist(),['2024-12-08'])

    def test_guard_cannot_be_reused(self):
        path=Path(__file__).parent/('guard_'+uuid4().hex+'.json')
        try:
            marker(path,{'first':True})
            with self.assertRaises(FileExistsError): marker(path,{'second':True})
        finally: path.unlink(missing_ok=True)

    def test_missing_closing_hour_uses_registered_official_close_without_earlier_entry(self):
        day='2024-12-09'
        bars=pd.DataFrame([dict(ts=ts(day,h),o=o,c=99999.) for h,o in
            [('09:15',100.),('10:15',99.),('11:15',98.),('12:15',97.)]])
        result=close_measurement(day,bars,101.)
        self.assertEqual(result['entry_ts'],ts(day,'12:15'))
        self.assertEqual(result['exit_ts'],ts(day,'15:30'))
        self.assertAlmostEqual(result['gross_return'],101/97-1)
        self.assertEqual(close_measurement(day,bars.iloc[:-1],101.)['status'],'missing_entry_path')

    def test_fill_stress_changes_quantity_and_uses_exact_charges(self):
        b=pd.DataFrame(dict(open=[100.],high=[110.],low=[99.],close=[108.]),index=['A'])
        p=pd.DataFrame([dict(day='2024-12-09',symbol='A',open=100.,gap=-.03)])
        with patch('scripts.h22_h25.pd.read_pickle',return_value=b):
            a=execute_added(p); w=execute_added(p,.005)
        self.assertLess(w.iloc[0].net_return,a.iloc[0].net_return)
        self.assertEqual(a.iloc[0].reason,'flatten')
        self.assertLessEqual(a.iloc[0].qty*a.iloc[0].entry,5000)


if __name__=='__main__': unittest.main()
