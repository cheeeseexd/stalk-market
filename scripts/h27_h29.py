"""Frozen, offline H27-H29 diagnostics. No network or trading interface.

Each cycle audits inputs before its exclusive started.json gate. A failed
required-field audit records an unresolved attempt and never scores returns.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import itertools
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h22_h25 import metadata, past_features, estimate, sha
from scripts.h9_fetch import bhav_path
from scripts.panic_final import nifty
from scripts.panic_to_history import ETF
from stalk.config import DEFAULT_UNIVERSE
from stalk.market.costs import fill_cost, CURRENT_EXCHANGE_PCT
from stalk.research.execution_log import WATCHLIST

PROTECTED = ['stalk/sim/panic.py', 'stalk/research/validate.py', 'stalk/agent.py']
HOURS = [915, 1015, 1115, 1215, 1315, 1415]
SECTORS = {'banks': ['HDFCBANK','ICICIBANK','SBIN','AXISBANK'],
           'IT': ['INFY','TCS'], 'metals': ['TATASTEEL','HINDALCO']}


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False), encoding='utf-8')


def inventory(frame, source, required, rows):
    """Inventory every column; positive prices/nonfinite required numerics fail.

    Zero counts are separate: a zero beta or return is valid; zero prices are
    invalid, whereas zero traded value is a real illiquid observation.
    """
    for col in frame.columns:
        s = frame[col]
        numeric = pd.api.types.is_numeric_dtype(s)
        bad = s.isna() | (s.astype(str).str.strip() == '')
        if numeric:
            bad |= ~np.isfinite(s.to_numpy(dtype=float))
        zero = int((s == 0).sum())
        if col in required and col in ('o','h','l','c','open','high','low','close','prev_close','turn20','vol20','ts'):
            bad |= s <= 0
        rows.append(dict(source=source, column=col, rows=len(s), zeros=zero,
                         missing_or_invalid=int(bad.sum()), required=col in required,
                         missing_share=float(bad.mean()) if len(s) else 1.))


def audit_ok(rows):
    return all(r['missing_share'] <= .01 for r in rows if r['required'])


def freeze(cycle, audit, inputs):
    out = ROOT/'data'/cycle
    out.mkdir(exist_ok=True)
    if (out/'started.json').exists():
        raise RuntimeError('Attempt already started; no rescoring or guard reset')
    book = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8')
    registration = book.split('## '+cycle.upper()+':', 1)[1].split('\n## ', 1)[0]
    for rel in ['scripts/h22_h25.py','scripts/h21_hybrid.py','scripts/h17_rebuild.py',
                'scripts/panic_final.py','scripts/h9_fetch.py','stalk/config.py',
                'stalk/research/execution_log.py','stalk/market/costs.py']:
        inputs[rel]=sha(ROOT/rel)
    pd.DataFrame(audit).to_csv(out/'input_inventory.csv', index=False)
    with (out/'started.json').open('x', encoding='utf-8') as f:
        json.dump(dict(utc=datetime.now(timezone.utc).isoformat(), trials=1,
                       code=sha(__file__), audit_passed=audit_ok(audit),
                       protected={p:sha(ROOT/p) for p in PROTECTED}), f, indent=2)
    (out/'preregistration.md').write_text('## '+cycle.upper()+':'+registration, encoding='utf-8')
    shutil.copy2(__file__, out/'scored_code.py')
    dump(out/'input_manifest.json', inputs)
    return out


def finish(out, summary):
    dump(out/'summary.json', summary)
    dump(out/'output_manifest.json', {p.name:sha(p) for p in out.iterdir()
         if p.is_file() and p.name != 'output_manifest.json'})
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)


def unresolved(out, audit):
    finish(out, dict(verdict='Unresolved', trials=1, scoring_runs=0,
                     reason='Stopped before outcomes: required input exceeds 1% missing/invalid.',
                     failed_fields=[r for r in audit if r['required'] and r['missing_share'] > .01]))


def source(inputs, rel):
    p = ROOT/rel
    inputs[str(p.relative_to(ROOT))] = sha(p)
    return p


def hourly(frame):
    b = frame.copy()
    t = pd.to_datetime(b.ts, unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
    b['day'] = t.dt.strftime('%Y-%m-%d')
    b['hm'] = t.dt.hour*100+t.dt.minute
    if b.duplicated(['day','hm']).any():
        raise ValueError('Duplicate hourly labels')
    return b.set_index(['day','hm']).sort_index()


def match_five(pick, pool):
    """No tiers, gaps, outcomes, or future fields enter the distance."""
    fields = ['beta60','vol20','turn20']
    if not np.isfinite([pick[k] for k in fields]).all() or min(pick['vol20'],pick['turn20']) <= 0:
        return pool.iloc[:0].copy()
    c = pool[(pool.day == pick['day']) & (pool.symbol != pick['symbol'])].copy()
    c = c[np.isfinite(c[fields]).all(axis=1) & (c.vol20>0) & (c.turn20>0)]
    beta = abs(c.beta60-pick['beta60'])
    vol = abs(np.log(c.vol20/pick['vol20']))
    turn = abs(np.log(c.turn20/pick['turn20']))
    c['distance'] = (beta/.5)**2+(vol/np.log(2))**2+(turn/np.log(3))**2
    c = c[(beta<=.5)&(vol<=np.log(2))&(turn<=np.log(3))]
    return c.sort_values(['distance','symbol'], kind='stable').head(5)


def h27():
    inputs, audit = {}, []
    changes, actions, mechanical, events, event_days, tiers, funds = metadata(inputs)
    for rel,required in [('data/h17/actions.csv',{'symbol','day','kind','exclude'}),
                         ('data/h17/symbolchange.csv.raw',{'OLD SYMBOL','NEW SYMBOL','EFFECTIVE DATE'})]:
        f=pd.read_csv(ROOT/rel);f.columns=f.columns.str.strip()
        inventory(f,rel,required,audit)
    inventory(events,'events_metadata',{'symbol','day','purpose'},audit)
    # Tier metadata is inventoried but never required or used for eligibility.
    inventory(pd.read_csv(ROOT/'data/wide_meta.csv'), 'tier_metadata_unused', set(), audit)
    n = nifty()
    wide = pd.read_pickle(source(inputs,'data/wide_1d_10y.pkl'))
    ni = wide['NIFTY 50']
    inventory(ni, 'nifty_daily', {'ts','o','c'}, audit)
    dates = pd.to_datetime(ni.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata').dt.strftime('%Y-%m-%d')
    market = pd.Series(ni.c.to_numpy(),index=dates).sort_index().pct_change(fill_method=None)
    picks = {}
    for version in ['strict','loose']:
        raw = pd.read_csv(source(inputs,f'data/h17/{version}_cleaned_selected.csv'))
        inventory(raw,version+'_frozen',{'day','symbol','open','close'},audit)
        picks[version] = raw
    frames = {}
    for day in sorted(set(n.index)|set(n.prev_day)):
        p = bhav_path(day)
        if p.exists():
            frames[day] = pd.read_pickle(source(inputs,p.relative_to(ROOT)))
    # The same index-session clock as H17. No forward filling across missing files.
    ret = pd.DataFrame({d:f.close/f.prev_close-1 for d,f in frames.items()}).T.reindex(n.index)
    turn = pd.DataFrame({d:f.value for d,f in frames.items()}).T.reindex(n.index)
    for d,names in mechanical.items():
        if d in ret.index:
            ret.loc[d,ret.columns.intersection(names)] = np.nan
    pools, features, calendar, raw_inputs = [], [], [], []
    for day in n.index[n.gap<-.003]:
        prev_day = n.at[day,'prev_day']
        cp = ROOT/f'data/bhav/bm_{day}.pkl'
        available = day in frames and prev_day in frames and cp.exists()
        calendar.append(dict(day=day,required_input=1. if available else np.nan))
        if not available:
            continue
        near = pd.read_pickle(source(inputs,cp.relative_to(ROOT)))
        if not isinstance(near,(list,tuple,set)):
            raise ValueError('Unknown H17 results calendar')
        today, previous = frames[day],frames[prev_day]
        # EXACT H17 inner-join, prior-value/price, fixed universe, calendar,
        # ETF and action screens; retain locks as H17 did. No today's range veto.
        p = today[['open','prev_close']].join(previous[['value']].rename(columns={'value':'prev_value'}),how='inner')
        p = p[(p.prev_value>=1e8)&(p.prev_close>=50)&~p.index.isin(DEFAULT_UNIVERSE)&~p.index.isin(near)]
        p = p[~p.index.isin(funds)&~p.index.to_series().str.contains(ETF,regex=True)&~p.index.isin(actions.get(day,()))]
        # All raw bhav columns actually used by these eligible histories.
        raw_inputs.append(today.loc[p.index].assign(day=day))
        f = past_features(ret,turn,market,day)
        p = p.join(f)
        p['day'],p['symbol'] = day,p.index
        p['gap'] = p.open/p.prev_close-1
        p = p.reset_index(drop=True)
        pools.append(p)
        for version,selected in picks.items():
            q=selected[selected.day==day][['day','symbol']].merge(f,left_on='symbol',right_index=True,how='left')
            q['version']=version
            features.append(q)
    pool = pd.concat(pools,ignore_index=True)
    frozen_features = pd.concat(features,ignore_index=True)
    inventory(pd.DataFrame(calendar),'morning_sources',{'required_input'},audit)
    inventory(pd.concat(raw_inputs),'eligible_bhav_rows',{'open','prev_close','close','value'},audit)
    inventory(pool,'eligible_matching_features',{'day','symbol','open','prev_close','prev_value','gap','beta60','turn20','vol20'},audit)
    for version in picks:
        inventory(frozen_features[frozen_features.version==version],version+'_pick_features',{'beta60','turn20','vol20'},audit)
    # Explicitly inventory every prior-data column on the features' 60-session
    # support; absent listing histories stay absent and are not silently filled.
    used_days=set()
    for d in pool.day.unique():
        used_days.update(ret.index[ret.index<d][-60:])
    history=pd.concat([frames[d].assign(day=d) for d in sorted(used_days) if d in frames])
    inventory(history,'prior_bhav',{'close','prev_close','value'},audit)
    out=freeze('h27',audit,inputs)
    pool.to_csv(out/'eligible_features.csv',index=False)
    frozen_features.to_csv(out/'frozen_pick_features.csv',index=False)
    if not audit_ok(audit):
        unresolved(out,audit); return
    matches, coverage = [],[]
    for version,selected in picks.items():
        for d,g in selected.groupby('day'):
            p=pool[pool.day==d]
            # Reconcile exact historical rankings before matching.
            rank=p[p.gap<-.02].sort_values(['gap','symbol'],kind='stable')
            if list(rank.head(2).symbol)!=list(g.symbol):
                raise ValueError('Historical H17 eligibility/ranking mismatch')
            available=p[~p.symbol.isin(g.symbol)]
            for row in g.to_dict('records'):
                ff=frozen_features[(frozen_features.version==version)&(frozen_features.day==d)&(frozen_features.symbol==row['symbol'])].iloc[0]
                r={**row,**ff.to_dict()}
                for label,c in [('ordinary',available[available.gap>-.02]),('lower_gap',available[available.gap<=-.02])]:
                    matched=match_five(r,c)
                    complete=len(matched)==5
                    coverage.append(dict(version=version,day=d,symbol=r['symbol'],control_set=label,matched=complete,n=len(matched)))
                    if complete:
                        for z in matched.to_dict('records'):
                            matches.append(dict(version=version,day=d,pick=r['symbol'],control=z['symbol'],control_set=label,distance=z['distance']))
    links=pd.DataFrame(matches)
    links.to_csv(out/'matches.csv',index=False)
    pd.DataFrame(coverage).to_csv(out/'coverage.csv',index=False)
    # Only now access same-day official closes.
    observations=[]
    for r in links.to_dict('records'):
        b=frames[r['day']]
        a,z=b.loc[r['pick']],b.loc[r['control']]
        observations.append({**r,'pick_return':a.close/a.open-1,'control_return':z.close/z.open-1})
    obs=pd.DataFrame(observations)
    obs.to_csv(out/'paired_returns.csv',index=False)
    result={}
    for v in picks:
        result[v]={}
        for label in ['ordinary','lower_gap']:
            cov=pd.DataFrame(coverage).query('version==@v and control_set==@label')
            x=obs.query('version==@v and control_set==@label').copy() if len(obs) else pd.DataFrame()
            if len(x):
                x['excess']=x.pick_return-x.control_return
                day=x.groupby(['day','pick']).excess.mean().groupby('day').mean()
                stressed=x.pick_return.add(1).div(1.005).sub(1)-x.control_return
                stress_estimate=estimate(stressed.groupby([x.day,x.pick]).mean().groupby('day').mean())
            else: day=pd.Series(dtype=float)
            share=float(cov.matched.mean())
            item=dict(coverage=share,matched_picks=int(cov.matched.sum()),frozen_picks=len(cov),**estimate(day))
            item['pick_only_entry_worse_05']=stress_estimate if len(x) else estimate([])
            item['verdict']='Unresolved' if share<.8 or len(day)<40 else ('Passed' if item['mean_bps']>50 and (item['day_t'] or 0)>=2.5 else 'Failed')
            result[v][label]=item
        ds=sorted(picks[v].day.unique())
        ordinary=[]
        for d in ds:
            c=pool[(pool.day==d)&(pool.gap>-.02)&~pool.symbol.isin(picks[v].query('day==@d').symbol)]
            ordinary.append(np.mean([frames[d].at[s,'close']/frames[d].at[s,'open']-1 for s in c.symbol]))
        result[v]['raw_ordinary']=estimate(ordinary)
        result[v]['nifty']=estimate(n.loc[ds,'close']/n.loc[ds,'open']-1)
    finish(out,dict(verdict=result['strict']['ordinary']['verdict'],trials=1,scoring_runs=1,versions=result))


def hourly_path(b, day):
    return b.reindex(pd.MultiIndex.from_product([[day],HOURS],names=['day','hm']))


def execute_path(path, official_open, delayed=False, worse=0.):
    """Hourly-touch proxy; final 14:15 open is observed, final range unused."""
    g=path.copy()
    required=['ts','o','l']
    if not np.isfinite(g[required].to_numpy()).all() or (g[required]<=0).any().any():
        return None
    if not np.array_equal(np.diff(g.ts),np.full(5,3600)):
        return None
    start=1 if delayed else 0
    entry=(float(g.iloc[1].o) if delayed else official_open)*1.001*(1+worse)
    qty=int(5000//entry)
    if qty<1: return None
    stop=entry*.97
    exit_px=float(g.iloc[-1].o)
    stopped=False
    for i in range(start,6):
        row=g.iloc[i]
        if row.o<=stop:
            exit_px=float(row.o);stopped=True;break
        if i<5 and row.l<=stop:
            exit_px=stop;stopped=True;break
    exit_px*=.999*(.998 if stopped else 1.)
    gross=(exit_px-entry)*qty
    charges=fill_cost('BUY',qty,entry,exchange_pct=CURRENT_EXCHANGE_PCT).total+fill_cost('SELL',qty,exit_px,exchange_pct=CURRENT_EXCHANGE_PCT).total
    return dict(entry=entry,exit=exit_px,qty=qty,reason='stop' if stopped else 'flatten',gross=gross,charges=charges,net=gross-charges,net_return=(gross-charges)/(qty*entry))


def h28():
    inputs,audit={},[]
    bars=pd.read_pickle(source(inputs,'data/wide_60m.pkl'))
    b={s:hourly(f) for s,f in bars.items()}
    n=hourly(bars['NIFTY 50'])
    cache_days=sorted(n.index.get_level_values('day').unique())
    selections={v:pd.read_csv(source(inputs,f'data/h17/{v}_cleaned_selected.csv')) for v in ['strict','loose']}
    coverage,paths=[],{}
    for v,picks in selections.items():
        inventory(picks,v+'_frozen',{'day','symbol','open'},audit)
        for r in picks.itertuples():
            path=hourly_path(b.get(r.symbol,n.iloc[:0]),r.day)
            paths[(r.day,r.symbol)]=path
            ok=path[['ts','o','l']].notna().all().all()
            coverage.append(dict(version=v,day=r.day,symbol=r.symbol,in_cache_window=cache_days[0]<=r.day<=cache_days[-1],complete=bool(ok)))
        required=pd.concat([paths[(r.day,r.symbol)][['ts','o','l']].assign(symbol=r.symbol) for r in picks.itertuples()])
        inventory(required,v+'_required_frozen_paths',{'ts','o','l'},audit)
        recent=picks[picks.day.between(cache_days[0],cache_days[-1])]
        recent_paths=pd.concat([paths[(r.day,r.symbol)][['ts','o','l']] for r in recent.itertuples()])
        inventory(recent_paths,v+'_overlap_paths',{'ts','o','l'},audit)
    actual=pd.concat([f.assign(symbol=s) for s,f in bars.items()])
    inventory(actual,'hourly_cache_all_columns',{'ts','o','l'},audit)
    out=freeze('h28',audit,inputs)
    pd.DataFrame(coverage).to_csv(out/'coverage.csv',index=False)
    labels=pd.to_datetime(actual.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata').dt.strftime('%H:%M').value_counts().sort_index()
    dump(out/'timestamp_coverage.json',dict(first=cache_days[0],last=cache_days[-1],labels=labels.to_dict()))
    if not audit_ok(audit): unresolved(out,audit);return
    rows=[]
    for v,picks in selections.items():
        for r in picks.itertuples():
            for delayed in [False,True]:
                for stress in [0.,.005]:
                    x=execute_path(paths[(r.day,r.symbol)],r.open,delayed,stress)
                    if x: rows.append(dict(version=v,day=r.day,symbol=r.symbol,delayed=delayed,stress=stress,**x))
    trades=pd.DataFrame(rows)
    trades.to_csv(out/'trades.csv',index=False)
    result={}
    for v in selections:
        result[v]={}
        for delay in [False,True]:
            for stress in [0.,.005]:
                x=trades[(trades.version==v)&(trades.delayed==delay)&(trades.stress==stress)]
                e=estimate(x.groupby('day').net_return.mean())
                result[v][f'{delay}_{stress}']=dict(trades=len(x),net_bps=float(x.net_return.mean()*1e4),net_rupees=float(x.net.sum()),rupees_per_trade=float(x.net.mean()),**e)
        a=result[v]['True_0.005']
        result[v]['verdict']='Unresolved' if a['trades']<40 or a['n']<20 else ('Passed' if a['net_bps']>0 and (a['day_t'] or 0)>=2.5 else 'Failed')
        result[v]['lost_bps']=result[v]['False_0.0']['net_bps']-result[v]['True_0.0']['net_bps']
    finish(out,dict(verdict=result['strict']['verdict'],trials=1,scoring_runs=1,versions=result,proxy='3% hourly-touch stop; exact 14:15 bar OPEN exit; investigation only'))


def spread_signals(a,b,blocked_days=()):
    """Current completed open-to-open hourly spread vs 20 PRIOR sessions.

    Signal at each observed hour open (10:15..13:15). The prior moments are
    calculated on all six observed hourly log-price spreads in exactly the
    preceding 20 index sessions, excluding the current day entirely.
    """
    x=pd.concat([a.o.rename('a'),b.o.rename('b')],axis=1)
    x['spread']=np.log(x.a/x.b)
    days=sorted(x.index.get_level_values('day').unique())
    rows=[]
    for i,d in enumerate(days):
        if i<20: continue
        if set(days[i-20:i+1]) & set(blocked_days): continue
        prior=x.loc[days[i-20:i],'spread'].dropna()
        if len(prior)<120: continue
        mean,sd=prior.mean(),prior.std(ddof=1)
        if sd<=0: continue
        for hm in [1015,1115,1215,1315]:
            if (d,hm) not in x.index or (d,1415) not in x.index: continue
            s=x.at[(d,hm),'spread']; z=(s-mean)/sd
            if not np.isfinite(z) or abs(z)<=2: continue
            end=x.at[(d,1415),'spread']
            if not np.isfinite(end): continue
            # Half capital in each opposite leg; gross return per combined notional.
            reversal=-np.sign(z)*(x.at[(d,1415),'a']/x.at[(d,hm),'a']-x.at[(d,1415),'b']/x.at[(d,hm),'b'])/2
            rows.append(dict(day=d,hm=hm,z=float(z),gross_return=float(reversal),prior_last_day=days[i-1]))
    return pd.DataFrame(rows,columns=['day','hm','z','gross_return','prior_last_day'])


def clustered_estimate(frame):
    """Event-weighted mean with date-clustered sandwich standard error."""
    if frame.empty:return dict(events=0,days=0,mean_bps=None,ci95_bps=None)
    y=frame.gross_return; mean=float(y.mean()); scores=(y-mean).groupby(frame.day).sum()
    count=len(scores)
    se=float(np.sqrt(count/(count-1)*(scores**2).sum())/len(y)) if count>1 else None
    # Student t rather than normal critical value for small day clusters.
    from scipy.stats import t
    critical=float(t.ppf(.975,count-1)) if se is not None else None
    return dict(events=len(y),days=count,mean_bps=mean*1e4,ci95_bps=[(mean-critical*se)*1e4,(mean+critical*se)*1e4] if se is not None else None)


def h29():
    inputs,audit={},[]
    outdir=ROOT/'data/h29'
    if (outdir/'started.json').exists():
        raise RuntimeError('Attempt already started; do not replace the bank snapshot')
    outdir.mkdir(exist_ok=True)
    wide=pd.read_pickle(source(inputs,'data/wide_1d_10y.pkl'))
    bars=pd.read_pickle(source(inputs,'data/wide_60m.pkl'))
    # Existing local 60minute BANKNIFTY bars; no network or shorter substitution.
    with sqlite3.connect('file:'+str(ROOT/'data/stalk.db')+'?mode=ro',uri=True) as con:
        bank=pd.read_sql_query("SELECT ts,o,h,l,c,v FROM bars WHERE symbol=? AND interval=? ORDER BY ts",con,params=('NIFTY BANK','60minute'))
    bank.to_csv(outdir/'banknifty_hourly_input.csv',index=False)
    source(inputs,'data/h29/banknifty_hourly_input.csv')
    bars['NIFTY BANK']=bank
    hb={s:hourly(bars.get(s,pd.DataFrame(columns=['ts','o','h','l','c','v']))) for s in list(WATCHLIST)+['NIFTY 50','NIFTY BANK']}
    days=sorted(hb['NIFTY 50'].index.get_level_values('day').unique())
    expected=pd.MultiIndex.from_product([days,HOURS],names=['day','hm'])
    for s,f in hb.items():
        inventory(f.reindex(expected).reset_index(),s+'_hourly',{'ts','o','h','l','c'},audit)
    for s in WATCHLIST:
        inventory(wide[s],s+'_daily',{'ts','o','h','l','c'},audit)
    events=pd.concat([pd.read_pickle(source(inputs,'data/'+name)) for name in ['wide_events_2016.pkl','wide_events_old.pkl','wide_events.pkl']],ignore_index=True).drop_duplicates()
    ev=events[events.symbol.isin(WATCHLIST)]
    inventory(ev,'watchlist_results_calendar',{'symbol','day','purpose'},audit)
    actions=pd.read_csv(source(inputs,'data/h17/actions.csv'))
    inventory(actions,'actions_metadata',{'symbol','day','kind'},audit)
    out=freeze('h29',audit,inputs)
    dump(out/'banknifty_provenance.json',dict(source='local stalk.db, read-only SQL snapshot',symbol='NIFTY BANK',interval='60minute',rows=len(bank),columns=list(bank.columns)))
    if not audit_ok(audit): unresolved(out,audit);return
    observations=[]
    hr={s:np.log(f.o/f.groupby(level='day').o.shift()).where(f.groupby(level='day').ts.diff()==3600).rename(s) for s,f in hb.items()}
    for s in WATCHLIST:
        d=wide[s].copy()
        dt=pd.to_datetime(d.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
        d=d[(dt.dt.strftime('%Y-%m-%d')>='2016-11-01')&(dt.dt.strftime('%Y-%m-%d')<='2026-10-31')]
        f=hb[s]
        opens=f.o.unstack('hm').reindex(columns=HOURS,index=days)
        move=(opens[1415]/opens[1015]-1).abs()
        results=ev[(ev.symbol==s)&ev.purpose.fillna('').str.contains('result',case=False)]
        annual=results[['day']].drop_duplicates().assign(year=lambda x:x.day.str[:4]).groupby('year').size().reindex([str(y) for y in range(2017,2026)],fill_value=0)
        # 17.5bps planning hurdle: two times it is exactly 0.35%; actual spread
        # and impact remain unknown until the watchlist quotes are observed.
        observations.append(dict(symbol=s,daily_days=len(d),hourly_days=int(move.notna().sum()),
            median_daily_range_pct=float(((d.h-d.l)/d.o).median()*100),
            median_abs_otc_pct=float((d.c/d.o-1).abs().median()*100),
            median_post1015_bps=float(move.median()*1e4),
            share_post1015_gt035=float((move.dropna()>.0035).mean()),
            move_to_cost=float(move.median()/.00175),
            corr_nifty=float(hr[s].corr(hr['NIFTY 50'])),corr_banknifty=float(hr[s].corr(hr['NIFTY BANK'])),
            results_days_per_year=float(annual.mean()),results_year_counts=annual.to_dict()))
        pd.DataFrame(dict(day=days,abs_post1015_return=move.to_numpy())).to_csv(out/(s+'_post1015.csv'),index=False)
    table=pd.DataFrame(observations).sort_values(['move_to_cost','symbol'],ascending=[False,True]).reset_index(drop=True)
    table.insert(0,'rank',np.arange(1,len(table)+1))
    table.to_json(out/'ranked_table.json',orient='records',indent=2)
    table.to_csv(out/'ranked_table.csv',index=False)
    candidates=[]
    for sector,names in SECTORS.items():
        for a,b in itertools.combinations(sorted(names),2):
            candidates.append(dict(sector=sector,a=a,b=b,correlation=float(hr[a].corr(hr[b]))))
    ranking=pd.DataFrame(candidates).sort_values(['correlation','a','b'],ascending=[False,True,True])
    ranking.to_csv(out/'pair_correlation_ranking.csv',index=False)
    # Descriptive pair choice uses correlation, never the reversal outcome.
    ranking.head(3).to_csv(out/'frozen_pairs.csv',index=False)
    diagnostics=[]
    for r in ranking.head(3).to_dict('records'):
        mechanical=actions[(actions.symbol.isin([r['a'],r['b']])|actions.api_symbol.isin([r['a'],r['b']]))&~actions.kind.isin(['non_price','dividend'])]
        sig=spread_signals(hb[r['a']].reindex(expected),hb[r['b']].reindex(expected),set(mechanical.day))
        sig.to_csv(out/(r['a']+'_'+r['b']+'_signals.csv'),index=False)
        diagnostics.append({**r,**clustered_estimate(sig)})
    finish(out,dict(verdict='Descriptive',trials=1,scoring_runs=1,top4=table.symbol.head(4).tolist(),
         rows=table.drop(columns='results_year_counts').to_dict('records'),pairs=diagnostics,
         basis='Current 12-name observation list; daily 10y/hourly 3y survivor sample; 10:15 to actual 14:15 open, not market close; 17.5bps planning hurdle, quotes/impact unknown. Results counts are incomplete cached meeting calendars, not certified earnings dates; 2017-2025 full-year mean. Hourly correlation excludes overnight returns. Gross pair diagnostics only, no stops or strategy score.'))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('cycle',choices=['h27','h28','h29'])
    cycle=parser.parse_args().cycle
    if (ROOT/'data'/cycle/'started.json').exists():
        raise RuntimeError('Attempt already started; guard cannot be reset or reused')
    globals()[cycle]()
