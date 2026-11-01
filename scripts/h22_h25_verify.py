"""Audit frozen H23/H22/H25 outputs; never invoke a scorer or change selections."""
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.h22_h25 import ROOT, added_days, sha, dump, tstat, ts
from stalk.market.costs import round_trip_cost


def main():
    proof={}
    starts=[json.loads((ROOT/'data'/c/'started.json').read_text())['utc'] for c in ['h23','h22','h25']]
    assert starts==sorted(starts) and len(set(starts))==3
    out=ROOT/'data/h23'
    summary=json.loads((out/'summary.json').read_text())
    picks=pd.read_csv(ROOT/'data/h17/strict_cleaned_selected.csv')
    pool=pd.read_csv(out/'morning_pools.csv')
    paired=pd.read_csv(out/'matched_prices.csv')
    returns=pd.read_csv(out/'paired_returns.csv')
    assert set(zip(paired.day,paired.pick))<=set(zip(picks.day,picks.symbol))
    assert not pool.symbol.isna().any()
    balance=[]
    for label,g in paired.groupby('control_set'):
        assert ((g.pick_beta60-g.control_beta60).abs()<=.35+1e-12).all()
        assert (np.abs(np.log(g.pick_turn20/g.control_turn20))<=np.log(2)+1e-12).all()
        assert (np.abs(np.log(g.pick_vol20/g.control_vol20))<=np.log(1.5)+1e-12).all()
        control=g.merge(pool,left_on=['day','control'],right_on=['day','symbol'],suffixes=('','_pool'))
        assert len(control)==len(g) and (control.tier==control.tier_pool).all()
        if label=='small_gap':
            assert g.control_gap.between(-.01,0).all() and (~control.event).all()
        else:
            assert (g.control_gap<=-.02).all() and control.event.all()
            assert ((g.control_gap-g.pick_gap).abs()<=.02+1e-12).all()
        for fill,h in returns[returns.control_set==label].groupby('fill'):
            np.testing.assert_allclose(h.pick_net_return-h.control_net_return,h.difference,atol=1e-12)
            day=h.groupby('day').difference.mean()
            s=summary['comparisons'][label][fill]
            assert abs(day.mean()*1e4-s['mean_bps'])<1e-8
            if len(day)>1: assert abs(tstat(day)-s['day_t'])<1e-10
        balance.append(dict(control_set=label,pairs=len(g),days=g.day.nunique(),
            beta_abs_distance_mean=float((g.pick_beta60-g.control_beta60).abs().mean()),
            turnover_ratio_median=float((g.pick_turn20/g.control_turn20).median()),
            volatility_ratio_median=float((g.pick_vol20/g.control_vol20).median())))
    pd.DataFrame(balance).to_csv(out/'balance_audit.csv',index=False)
    audit=pd.read_csv(out/'match_audit.csv')
    audit['reason']=np.select([audit.status=='matched',audit.tier.isna(),~np.isfinite(audit[['beta60','turn20','vol20']]).all(axis=1)],
        ['matched','unknown_tier','incomplete_prior_features'],default='no_control_within_frozen_calipers')
    audit.to_csv(out/'match_reason_audit.csv',index=False)
    proof['h23']=dict(frozen_picks=len(picks),small_gap_matches=int((audit.control_set.eq('small_gap')&audit.status.eq('matched')).sum()),
        reasons={label:g.reason.value_counts().to_dict() for label,g in audit.groupby('control_set')},
        matching='all saved pairs meet fixed calipers; no frozen pick replaced',day_statistics='reconciled')

    out=ROOT/'data/h22'
    summary=json.loads((out/'summary.json').read_text())
    n=pd.read_csv(out/'calendar.csv',index_col='day')
    pools=pd.read_csv(ROOT/'data/h21/everyday_pools.csv')
    coverage=pd.read_csv(ROOT/'data/h21/coverage.csv').set_index('day')
    campaigns={}
    for key,r in summary['variants'].items():
        added=set(added_days(n,r['k']))
        assert not added.intersection(n.index[n.gap<-.005])
        covered=sorted(d for d in added if coverage.at[d,'status']=='ok')
        selected=pd.read_csv(out/f'{key}_selected.csv')
        expected=pools[pools.day.isin(covered)].sort_values(['day','gap','symbol']).groupby('day').head(2)
        assert set(zip(expected.day,expected.symbol))==set(zip(selected.day,selected.symbol))
        candidate=selected.set_index(['day','symbol'])
        day_audit=n[['gap','sigma20']].copy()
        day_audit['added_trigger']=day_audit.index.isin(added)
        day_audit['price_status']=coverage.status
        for fill,worse in [('assumed',0.),('worse_05',.005)]:
            trades=pd.read_csv(out/f'{key}_{fill}_trades.csv')
            np.testing.assert_allclose(trades.gross-trades.charges,trades.net,atol=1e-9)
            np.testing.assert_allclose(trades.net/(trades.qty*trades.entry),trades.net_return,atol=1e-12)
            assert set(trades.reason)<=set(['stop','flatten']) and trades.groupby('day').size().max()<=2
            assert (trades.qty*trades.entry<=5000+1e-8).all() and set(trades.day)<=added
            for row in trades.itertuples():
                p=candidate.loc[(row.day,row.symbol)]
                assert abs(row.entry-p.open*1.001*(1+worse))<1e-8
                assert int(5000//row.entry)==row.qty
                assert abs(round_trip_cost(row.qty,row.entry,row.exit)-row.charges)<1e-8
            a=r['fills'][fill]
            assert abs(trades.net_return.mean()*1e4-a['net_bps'])<1e-8
            daily=trades.groupby('day').net_return.mean().reindex(covered,fill_value=0.)
            assert abs(tstat(daily)-a['day_t'])<1e-9
            day_audit[fill+'_net']=trades.groupby('day').net.sum().reindex(n.index,fill_value=0.)
            day_audit.loc[coverage.status!='ok',fill+'_net']=np.nan
        day_audit.to_csv(out/f'{key}_coverage.csv',index_label='day')
        cid='h22_volatility_'+key.replace('.','_')
        with sqlite3.connect(ROOT/'data/stalk.db') as con:
            campaign=json.loads(con.execute('SELECT summary FROM campaigns WHERE id=?',(cid,)).fetchone()[0])
            account=campaign['accounts']['panic_buy']
            trades=pd.read_csv(out/f'{key}_assumed_trades.csv')
            rows=con.execute('SELECT day,symbol,qty,entry_price,exit_price,gross,costs,net,reason,exit_ts FROM trades WHERE session_id LIKE ? ORDER BY day,symbol',(cid+':%',)).fetchall()
            stored=pd.DataFrame(rows,columns=['day','symbol','qty','entry','exit','gross','charges','net','reason','exit_ts'])
            expected=trades.sort_values(['day','symbol']).reset_index(drop=True)
            assert stored[['day','symbol','reason']].equals(expected[['day','symbol','reason']])
            for field in ['qty','entry','exit','gross','charges','net']:
                np.testing.assert_allclose(stored[field],expected[field],rtol=1e-12,atol=1e-9)
            assert stored[stored.reason=='stop'].exit_ts.isna().all()
            assert abs(account['net']-trades.net.sum())<1e-8
            sessions=pd.DataFrame(account['days']).set_index('day')
            assert sessions.index.tolist()==n.index.tolist()
            assert sessions.loc[coverage.status!='ok','net'].isna().all()
            campaigns[cid]=dict(trades=len(trades),sessions=len(sessions),unknown_days=int(sessions.net.isna().sum()),net=account['net'])
    with sqlite3.connect(out/'before_history.db') as before,sqlite3.connect(ROOT/'data/stalk.db') as after:
        preserved={}
        for table in ['campaigns','sessions','trades','logs','bars','events','news','trials']:
            old=before.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            new=after.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            assert new[:len(old)]==old,table
            preserved[table]=len(old)
    proof['h22']=dict(added_days_only=True,fills_and_fees='reconciled',campaigns=campaigns,preexisting_rows_unchanged=preserved)

    out=ROOT/'data/h25'
    summary=json.loads((out/'summary.json').read_text())
    completion=json.loads((out/'completion_started.json').read_text())
    assert completion['code']==sha(out/'completion_code.py')
    assert completion['registration']==sha(out/'completion_registration.md')
    assert all(sha(out/f)==v for f,v in completion['frozen_selections'].items())
    archived=out/'incomplete_path_attempt'
    assert all(sha(archived/f)==v for f,v in json.loads((archived/'manifest.json').read_text()).items())
    assert pd.read_csv(archived/'outcomes.csv').gross_return.isna().all()
    observations=pd.read_csv(out/'observations.csv')
    signals=pd.read_csv(out/'signal_features.csv')
    pairs=pd.read_csv(out/'matches.csv')
    assert (observations.market_morning<=-.007).all() and (observations.morning<=-.025).all()
    observed=observations[observations.gross_return.notna()]
    assert (observed.signal_ts==observed.entry_ts).all()  # Completed candle boundary = next bar start.
    assert all(row.signal_ts==ts(row.day,'12:15') for row in observed.itertuples())
    np.testing.assert_allclose(observed.close/observed.entry-1,observed.gross_return,atol=1e-12)
    np.testing.assert_allclose(observed.close/(observed.entry*1.005)-1,observed.worse_return,atol=1e-12)
    day=observed.groupby('day').gross_return.mean()
    assert abs(day.mean()*1e4-summary['gross']['mean_bps'])<1e-8
    assert abs(tstat(day)-summary['gross']['day_t'])<1e-9
    assert (pairs.control_day<pairs.day).all()
    index=signals.set_index(['symbol','day'])
    for r in pairs.itertuples():
        p=index.loc[(r.symbol,r.day)]; c=index.loc[(r.symbol,r.control_day)]
        assert abs(c.market_morning)<.0035
        assert abs(p.morning-c.morning)<=.01+1e-12 and abs(p.gap-c.gap)<=.01+1e-12
        assert abs(np.log(p.vol20/c.vol20))<=np.log(1.5)+1e-12
    proof['h25']=dict(signal_rows=len(signals),first_signal_day=signals.day.min(),last_signal_day=signals.day.max(),
        all_stress_observed=len(observed),matched=int(observed.control_return.notna().sum()),
        next_bar_entry='12:15 after completed 11:15-labelled candle',returns='gross arithmetic reconciled',ordinary_controls='earlier same-stock fixed-caliper matches',strategy_scored=False)
    dump(ROOT/'data/h22/saved_output_validation.json',proof)
    print(json.dumps(proof,indent=2))


if __name__=='__main__': main()
