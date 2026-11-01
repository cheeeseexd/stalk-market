"""Complete the single H25 attempt from frozen matches; no signal/pool rerun."""
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.h22_h25 import ROOT, dump, sha, marker, estimate, source, finish, ts
from scripts.h9_fetch import bhav_path


def close_measurement(day,bars,official_close):
    """Completed signal followed by12:15 entry; official15:30 close proxy."""
    times=pd.to_datetime(bars.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
    b=bars[times.dt.strftime('%Y-%m-%d')==day].set_index('ts')
    expected=[ts(day,x) for x in ['09:15','10:15','11:15','12:15']]
    if b.index.duplicated().any() or not set(expected).issubset(b.index):
        return dict(gross_return=np.nan,worse_return=np.nan,status='missing_entry_path')
    entry=float(b.at[expected[-1],'o'])
    if not np.isfinite([entry,official_close]).all() or min(entry,official_close)<=0:
        return dict(gross_return=np.nan,worse_return=np.nan,status='invalid_entry_or_close')
    return dict(entry=entry,close=float(official_close),entry_ts=expected[-1],exit_ts=ts(day,'15:30'),
        gross_return=official_close/entry-1,worse_return=official_close/(entry*1.005)-1,status='ok')


def main():
    out=ROOT/'data/h25'
    assert (out/'started.json').exists() and not (out/'summary.json').exists()
    original=pd.read_csv(out/'outcomes.csv')
    assert original.gross_return.isna().all() and set(original.status)=={'missing_path'}
    frozen={f:sha(out/f) for f in ['signal_features.csv','stress_candidates.csv','matches.csv']}
    marker(out/'completion_started.json',dict(code=sha(__file__),frozen_selections=frozen,
        registration=sha(ROOT/'RUNBOOK.md'),reason='No15:15 bar in cache; no finite returns in incomplete attempt; official close completes same intended exit time'))
    archive=out/'incomplete_path_attempt';archive.mkdir()
    for f in ['signal_features.csv','stress_candidates.csv','matches.csv','outcomes.csv','observations.csv','scored_code.py','preregistration.md']:
        shutil.copy2(out/f,archive/f)
    dump(archive/'manifest.json',{p.name:sha(p) for p in archive.iterdir()})
    shutil.copy2(__file__,out/'completion_code.py')
    shutil.copy2(ROOT/'RUNBOOK.md',out/'completion_registration.md')
    # All input hashes are saved before any official closing price is read.
    inputs=json.loads((ROOT/'data/h23/input_manifest.json').read_text())
    source(inputs,'data/wide_60m.pkl')
    for rel in ['scripts/h25_complete.py','stalk/market/costs.py','stalk/sim/price_basis.py','scripts/h21_hybrid.py']:
        source(inputs,rel)
    features=pd.read_csv(out/'signal_features.csv')
    candidates=pd.read_csv(out/'stress_candidates.csv')
    matches=pd.read_csv(out/'matches.csv')
    need=pd.concat([candidates[['symbol','day']],matches[['symbol','control_day']].rename(columns={'control_day':'day'})]).drop_duplicates()
    for d in sorted(set(features.day)|set(need.day)):
        path=bhav_path(d)
        if path.exists(): source(inputs,path.relative_to(ROOT))
    dump(out/'completion_input_manifest_before_returns.json',inputs)
    bars=pd.read_pickle(ROOT/'data/wide_60m.pkl')
    rows=[]; official={}
    for r in need.itertuples():
        if r.day not in official:
            path=bhav_path(r.day)
            official[r.day]=pd.read_pickle(path) if path.exists() else pd.DataFrame()
        frame=official[r.day]
        close=float(frame.at[r.symbol,'close']) if r.symbol in frame.index else np.nan
        rows.append(dict(day=r.day,symbol=r.symbol,**close_measurement(r.day,bars[r.symbol],close)))
    outcomes=pd.DataFrame(rows)
    outcomes.to_csv(out/'outcomes.csv',index=False)
    observations=candidates.merge(outcomes,on=['day','symbol'],how='left')
    controls=matches.merge(outcomes[['day','symbol','gross_return']],left_on=['control_day','symbol'],right_on=['day','symbol'],suffixes=('','_control'))
    control_mean=controls.groupby(['day','symbol']).gross_return.mean().rename('control_return')
    observations=observations.join(control_mean,on=['day','symbol'])
    observations['difference']=observations.gross_return-observations.control_return
    observations.to_csv(out/'observations.csv',index=False)
    complete=observations[observations.gross_return.notna()]
    paired=complete[complete.control_return.notna()]
    day=complete.groupby('day').gross_return.mean()
    market=bars['NIFTY 50'].copy()
    time=pd.to_datetime(market.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
    market['day']=time.dt.strftime('%Y-%m-%d');market['hm']=time.dt.hour*100+time.dt.minute
    m=market.pivot(index='day',columns='hm',values='c')
    op=market[market.hm==915].set_index('day').o
    market_move=m[1115]/op-1
    summary=dict(trials=1,scoring_runs=1,descriptive_only=True,stress_market_days=int((market_move<=-.007).sum()),
        candidates=len(candidates),observed=len(complete),days=complete.day.nunique(),matched=len(paired),matched_days=paired.day.nunique(),
        gross=estimate(day),worse_05=estimate(complete.groupby('day').worse_return.mean()),
        paired_excess=estimate(paired.groupby('day').difference.mean()),trade_weighted_gross_bps=float(complete.gross_return.mean()*1e4) if len(complete) else None,
        missing_outcomes=len(candidates)-len(complete),verdict='Unclear',
        basis='Frozen H25 signals/matches;12:15 hourly next-bar open to official15:30 daily close. Original cache lacks15:15 candles; no returns existed before preregistered source completion. Gross descriptive only; no strategy scored.')
    if len(day)>1:
        rng=np.random.default_rng(2525)
        draws=rng.choice(day.to_numpy(),size=(2000,len(day)),replace=True).mean(axis=1)*1e4
        summary['day_bootstrap_ci95_bps']=np.percentile(draws,[2.5,97.5]).tolist()
    summary['graduates_to_scored_test']=len(complete)>=40 and len(day)>=20 and (summary['gross']['mean_bps'] or -1)>=60
    assert all(sha(out/f)==v for f,v in frozen.items())
    finish(out,inputs,summary)


if __name__=='__main__': main()
