"""Three separate, frozen offline research cycles; no broker or agent interface."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_fetch import bhav_path
from scripts.h17_rebuild import historical_symbol
from scripts.h21_hybrid import marker, tstat, dated_wide, known_results, table_digest
from scripts.panic_final import nifty
from scripts.panic_to_history import ETF, ts
from stalk.config import DEFAULT_UNIVERSE
from stalk.market.costs import round_trip_cost, FEE_SCHEDULE_VERSION
from stalk.sim.price_basis import differs

ROOT = Path(__file__).resolve().parents[1]
PROTECTED = ['stalk/sim/panic.py', 'stalk/research/validate.py', 'stalk/agent.py']
KS = (.25, .50)  # Both are reported; .25 is the primary, never choose after returns.


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, allow_nan=False), encoding='utf-8')


def freeze(cycle, trials):
    out = ROOT/'data'/cycle
    out.mkdir(exist_ok=True)
    registration = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split(f'## {cycle.upper()}:', 1)[1]
    marker(out/'started.json', dict(utc=datetime.now(timezone.utc).isoformat(),
        trials=trials, code=sha(__file__), protected={p: sha(ROOT/p) for p in PROTECTED}))
    (out/'preregistration.md').write_text(f'## {cycle.upper()}:'+registration, encoding='utf-8')
    shutil.copy2(__file__, out/'scored_code.py')
    return out, {}


def source(inputs, rel):
    path = ROOT/rel
    inputs[str(path.relative_to(ROOT))] = sha(path)
    return path


def finish(out, inputs, summary):
    dump(out/'input_manifest.json', inputs)
    dump(out/'summary.json', summary)
    dump(out/'output_manifest.json', {p.name: sha(p) for p in sorted(out.iterdir())
        if p.suffix in ('.csv', '.json', '.py', '.md') and p.name != 'output_manifest.json'})
    print(json.dumps(summary, indent=2, allow_nan=False), flush=True)


def metadata(inputs):
    changes = pd.read_csv(source(inputs, 'data/h17/symbolchange.csv.raw'))
    changes.columns = changes.columns.str.strip()
    for c in ('OLD SYMBOL', 'NEW SYMBOL'):
        changes[c] = changes[c].str.strip()
    changes['effective'] = pd.to_datetime(changes['EFFECTIVE DATE'], format='%d-%b-%Y').dt.strftime('%Y-%m-%d')
    extra = json.loads(source(inputs, 'data/h21/dated_aliases.json').read_text())
    for x in extra:
        source(inputs, x['source'])
    changes = sorted(changes.to_dict('records')+extra, key=lambda r:r['effective'], reverse=True)
    actions = pd.read_csv(source(inputs, 'data/h17/actions.csv'))
    actions['symbol'] = [historical_symbol(r.api_symbol, r.day, changes)[0] for r in actions.itertuples()]
    assert json.loads(source(inputs, 'data/h17/action_coverage.json').read_text())['complete']
    blocked = actions[actions.exclude.astype(str).str.lower() == 'true']
    action_days = {d: set(g.symbol) for d,g in blocked.groupby('day')}
    # Mechanical split/basis returns cannot be historical volatility observations.
    mechanical = actions[~actions.kind.isin(['non_price', 'dividend'])]
    mechanical_days = {d: set(g.symbol) for d,g in mechanical.groupby('day')}
    events = pd.concat([pd.read_pickle(source(inputs, 'data/'+f)) for f in
        ['wide_events_2016.pkl', 'wide_events_old.pkl', 'wide_events.pkl']], ignore_index=True).drop_duplicates()
    events['symbol'] = [historical_symbol(r.symbol, r.day, changes)[0] for r in events.itertuples()]
    event_days = {}
    for day,g in events.groupby('day'):
        for offset in range(-3,4):
            d = (pd.Timestamp(day)+pd.Timedelta(days=offset)).strftime('%Y-%m-%d')
            event_days.setdefault(d, []).append(g)
    tiers = pd.read_csv(source(inputs,'data/wide_meta.csv')).set_index('symbol')['index'].to_dict()
    funds = set(json.loads(source(inputs,'data/h9/instrument_inventory.json').read_text())['etf_or_fund_symbols'])
    return changes, action_days, mechanical_days, events, event_days, tiers, funds


def past_features(returns, turnover, market, day):
    """Strictly earlier sessions, no forward-fill; missing observations stay missing."""
    prior = returns.index[returns.index < day][-60:]
    if len(prior) < 60:
        return pd.DataFrame(columns=['beta60','turn20','vol20'])
    r = returns.loc[prior]
    m = market.reindex(prior)
    count = r.notna().mul(m.notna(), axis=0).sum()
    # Pairwise variance: identical stock/index dates, not variance on extra dates.
    mm = pd.DataFrame(np.broadcast_to(m.to_numpy()[:,None], r.shape), index=r.index, columns=r.columns).where(r.notna())
    cov = (r*mm).sum() - r.where(mm.notna()).sum()*mm.sum()/count
    var = (mm*mm).sum() - mm.sum()**2/count
    beta = (cov/var).where((count >= 45) & (var > 0))
    vol = r.iloc[-20:].std(ddof=1).where(r.iloc[-20:].count() >= 15)
    turn = turnover.reindex(prior[-20:]).mean().where(turnover.reindex(prior[-20:]).count() == 20)
    return pd.DataFrame(dict(beta60=beta, turn20=turn, vol20=vol))


def morning_pool(day, opening, previous, features, *, actions=(), funds=(), tiers=None, results=()):
    """Only today's open/reference; previous turnover and lagged features."""
    p = opening[['open','prev_close']].join(previous[['value']].rename(columns={'value':'prev_value'}))
    p = p.join(features)
    p = p[(p.prev_value >= 1e8) & (p.prev_close >= 50) & (p.open > 0)
          & np.isfinite(p[['open','prev_close']]).all(axis=1)]
    p = p[~p.index.isin(set(DEFAULT_UNIVERSE)|set(actions)|set(funds))
          & ~p.index.to_series().str.contains(ETF, regex=True)]
    p['gap'] = p.open/p.prev_close-1
    p['tier'] = p.index.map(tiers or {})
    p['event'] = p.index.isin(results)
    p['day'], p['symbol'] = day,p.index
    return p.reset_index(drop=True)


def match_one(pick, candidates, event=False):
    """Fixed ex-ante calipers; no outcome column is accessed."""
    fields = ['beta60','turn20','vol20']
    if pd.isna(pick['tier']) or not np.isfinite([pick[x] for x in fields]).all() or min(pick['turn20'],pick['vol20']) <= 0:
        return None
    c = candidates[candidates.tier == pick['tier']].copy()
    c = c[(c.symbol != pick['symbol']) & np.isfinite(c[fields]).all(axis=1) & (c.turn20 > 0) & (c.vol20 > 0)]
    b = (c.beta60-pick['beta60']).abs()
    u = np.abs(np.log(c.turn20/pick['turn20']))
    v = np.abs(np.log(c.vol20/pick['vol20']))
    ok = (b <= .35) & (u <= np.log(2)) & (v <= np.log(1.5))
    distance = (b/.35)**2+(u/np.log(2))**2+(v/np.log(1.5))**2
    if event:
        g = (c.gap-pick['gap']).abs()
        ok &= g <= .02
        distance += (g/.02)**2
    c['distance'] = distance
    c = c[ok].sort_values(['distance','symbol'],kind='stable')
    return c.iloc[0].to_dict() if len(c) else None


def estimate(values):
    x = np.asarray(values,dtype=float)
    x = x[np.isfinite(x)]
    se = float(x.std(ddof=1)/np.sqrt(len(x))) if len(x)>1 else None
    return dict(n=len(x), mean_bps=float(x.mean()*1e4) if len(x) else None, day_t=tstat(x),
        ci95_bps=[float((x.mean()-1.96*se)*1e4),float((x.mean()+1.96*se)*1e4)] if se is not None else None)


def otc(open_px,close_px,worse=0.):
    entry = open_px*1.001*(1+worse)
    exit_px = close_px*.9992*.999
    return exit_px/entry-1-round_trip_cost(5000/entry,entry,exit_px)/5000


def h23():
    out,inputs = freeze('h23',1)
    changes,actions,mechanical,events,event_days,tiers,funds = metadata(inputs)
    n = nifty()
    wide = pd.read_pickle(source(inputs,'data/wide_1d_10y.pkl'))
    ni = dated_wide(wide['NIFTY 50'])
    market = ni.c.pct_change(fill_method=None)
    selected = pd.read_csv(source(inputs,'data/h17/strict_cleaned_selected.csv'))
    dates = sorted(set(n.index)|set(n.prev_day))
    frames,coverage = {},[]
    for day in dates:
        path = bhav_path(day)
        if path.exists():
            frames[day] = pd.read_pickle(source(inputs,path.relative_to(ROOT)))
    ret = pd.DataFrame({d:f.close/f.prev_close-1 for d,f in frames.items()}).T.reindex(dates)
    turn = pd.DataFrame({d:f.value for d,f in frames.items()}).T.reindex(dates)
    for d,names in mechanical.items():
        if d in ret.index:
            ret.loc[d,ret.columns.intersection(names)] = np.nan
    pairs,audit,pools = [],[],[]
    for day in n.index[n.gap < -.005]:
        today,previous = frames.get(day),frames.get(n.at[day,'prev_day'])
        if today is None or previous is None:
            coverage.append(dict(day=day,status='unknown_price')); continue
        f = past_features(ret,turn,market,day)
        scheduled = pd.concat(event_days[day]) if day in event_days else events.iloc[:0]
        known = known_results(scheduled,day)
        p = morning_pool(day,today,previous,f,actions=actions.get(day,()),funds=funds,tiers=tiers,results=known)
        # Frozen pick identities remain unchanged, even if new metadata would veto them.
        picks = selected[selected.day == day]
        small = p[p.gap.between(-.01,0) & ~p.event]
        event = p[(p.gap <= -.02) & p.event]
        pools.append(p)
        coverage.append(dict(day=day,status='ok',frozen_picks=len(picks),small_pool=len(small),event_pool=len(event)))
        for r in picks.to_dict('records'):
            ff = f.loc[r['symbol']].to_dict() if r['symbol'] in f.index else dict(beta60=np.nan,turn20=np.nan,vol20=np.nan)
            pick = {**r,**ff,'tier':tiers.get(r['symbol'])}
            for label,candidates in [('small_gap',small),('event_gap',event)]:
                control = match_one(pick,candidates,event=label=='event_gap')
                audit.append(dict(day=day,pick=r['symbol'],control_set=label,tier=pick['tier'],
                    status='matched' if control else 'unmatched',known_pick_event=r['symbol'] in known,**ff))
                if control:
                    pairs.append(dict(day=day,pick=r['symbol'],control=control['symbol'],control_set=label,
                        pick_open=r['open'],pick_close=r['close'],control_open=control['open'],
                        control_close=float(today.at[control['symbol'],'close']),pick_gap=r['gap'],control_gap=control['gap'],
                        tier=pick['tier'],distance=control['distance'],
                        **{'pick_'+k:pick[k] for k in ff},**{'control_'+k:control[k] for k in ff}))
    pd.concat(pools).to_csv(out/'morning_pools.csv',index=False)
    pd.DataFrame(audit).to_csv(out/'match_audit.csv',index=False)
    pd.DataFrame(coverage).to_csv(out/'coverage.csv',index=False)
    paired = pd.DataFrame(pairs)
    # Selection and matching are saved before accessing paired outcomes in aggregate.
    paired.to_csv(out/'matched_prices.csv',index=False)
    summary = dict(trials=1,scoring_runs=1,frozen_picks=len(selected),strict_mornings=int((n.gap<-.005).sum()),comparisons={})
    rows = []
    for label,g in paired.groupby('control_set'):
        item = dict(pairs=len(g),days=g.day.nunique(),unique_controls=len(g[['day','control']].drop_duplicates()))
        for tag,worse in [('assumed',0.),('worse_05',.005)]:
            a = g.apply(lambda r:otc(r.pick_open,r.pick_close,worse),axis=1)
            b = g.apply(lambda r:otc(r.control_open,r.control_close,worse),axis=1)
            diff = a-b
            days = diff.groupby(g.day).mean()
            item[tag] = dict(**estimate(days),trade_weighted_diff_bps=float(diff.mean()*1e4),
                pick_bps=float(a.mean()*1e4),control_bps=float(b.mean()*1e4),
                pick_only_worse_diff_bps=float((a-g.apply(lambda r:otc(r.control_open,r.control_close,0),axis=1)).mean()*1e4))
            for idx in g.index:
                rows.append(dict(day=g.at[idx,'day'],pick=g.at[idx,'pick'],control=g.at[idx,'control'],
                    control_set=label,fill=tag,pick_net_return=a.at[idx],control_net_return=b.at[idx],difference=diff.at[idx]))
        raw = g.pick_close/g.pick_open-g.control_close/g.control_open
        item['raw_gross'] = estimate(raw.groupby(g.day).mean())
        summary['comparisons'][label]=item
    pd.DataFrame(rows).to_csv(out/'paired_returns.csv',index=False)
    primary = summary['comparisons'].get('small_gap',{})
    s = primary.get('assumed',{})
    power = primary.get('pairs',0)>=40 and primary.get('days',0)>=20
    supported = power and s.get('mean_bps',-1)>=50 and (s.get('day_t') or -1)>=2
    summary['verdict'] = 'Passed' if supported else ('Failed' if power else 'Unclear')
    summary['interpretation'] = ('Stock-specific excess recovery is supported within the matched subset.' if supported else
        'Market rebound is consistent with the evidence, but a failed excess-return test does not prove it explains the edge.')
    summary['limitations'] = 'Current tier labels only; unknown tiers unmatched; incomplete timestamped results calendars; frozen H17 retrospective exclusions and assumed opening fills; matched subset only.'
    finish(out,inputs,summary)


def sigma20(index):
    return index.c.pct_change(fill_method=None).rolling(20,min_periods=20).std(ddof=1).shift(1)


def added_days(index,k):
    return index.index[(index.gap >= -.005) & index.sigma20.notna() & (index.gap <= -k*index.sigma20)]


def execute_added(selected,worse=0.):
    columns=['day','symbol','gap','qty','entry','exit','gross','charges','net','net_return','reason']
    rows=[]
    for day,g in selected.groupby('day'):
        b = pd.read_pickle(bhav_path(day))
        for r in g.itertuples():
            p=b.loc[r.symbol]
            values=p[['open','high','low','close']].to_numpy(dtype=float)
            if not np.isfinite(values).all() or min(values)<=0 or p.high<max(p.open,p.close,p.low) or p.low>min(p.open,p.close,p.high):
                raise ValueError('Invalid outcome OHLC; guard retained')
            entry=r.open*1.001*(1+worse)
            qty=int(5000//entry)
            if qty<1: continue
            stopped=p.low <= entry*.97
            exit_px=(entry*.97*.998 if stopped else p.close*.9992)*.999
            gross=(exit_px-entry)*qty
            charges=round_trip_cost(qty,entry,exit_px)
            rows.append(dict(day=day,symbol=r.symbol,gap=r.gap,qty=qty,entry=entry,exit=exit_px,
                gross=gross,charges=charges,net=gross-charges,net_return=(gross-charges)/(qty*entry),reason='stop' if stopped else 'flatten'))
    return pd.DataFrame(rows,columns=columns)


def added_stats(trades,covered):
    daily = trades.groupby('day').net_return.mean().reindex(covered,fill_value=0.)
    return dict(trades=len(trades),funded_days=trades.day.nunique(),added_mornings_per_year=trades.day.nunique()/10,
        eligible_mornings_per_year=len(covered)/10,net_bps=float(trades.net_return.mean()*1e4) if len(trades) else None,
        net_rupees_per_trade=float(trades.net.mean()) if len(trades) else None,net_rupees=float(trades.net.sum()),
        day_t=tstat(daily),active_day_t=tstat(trades.groupby('day').net_return.mean()),
        yearly_funded_days=trades.groupby(trades.day.str[:4]).day.nunique().to_dict())


def h22():
    out,inputs=freeze('h22',2)
    source(inputs,'data/h23/summary.json')
    wide=pd.read_pickle(source(inputs,'data/wide_1d_10y.pkl'))
    n=nifty()
    n['sigma20']=sigma20(dated_wide(wide['NIFTY 50'])).reindex(n.index)
    pools=pd.read_csv(source(inputs,'data/h21/everyday_pools.csv'))
    coverage=pd.read_csv(source(inputs,'data/h21/coverage.csv')).set_index('day')
    # Preserve H21's hashes and official-only universe rather than rebuilding a favourable pool.
    manifest=json.loads(source(inputs,'data/h21/input_manifest.json').read_text())
    for rel,digest in manifest.items():
        if rel in ['RUNBOOK.md','scripts/h21_hybrid.py']: continue
        path=source(inputs,rel)
        if sha(path)!=digest: raise ValueError('H21 frozen input changed: '+rel)
    source(inputs,'stalk/market/costs.py')
    n.to_csv(out/'calendar.csv',index_label='day')
    summary=dict(trials=2,scoring_runs=1,primary_k=.25,h23_verdict=json.loads((ROOT/'data/h23/summary.json').read_text())['verdict'],variants={})
    for k in KS:
        key=f'k{k:.2f}'
        added=list(added_days(n,k))
        covered=[d for d in added if coverage.at[d,'status']=='ok']
        picks=pools[pools.day.isin(covered)].sort_values(['day','gap','symbol'],kind='stable').groupby('day').head(2)
        picks.to_csv(out/f'{key}_selected.csv',index=False)
        r=dict(k=k,added_trigger_mornings=len(added),added_trigger_mornings_per_year=len(added)/10,
            unknown_trigger_mornings=len(added)-len(covered),selected=len(picks),fills={})
        for tag,worse in [('assumed',0.),('worse_05',.005)]:
            trades=execute_added(picks,worse)
            trades.to_csv(out/f'{key}_{tag}_trades.csv',index=False)
            r['fills'][tag]=added_stats(trades,covered)
        a,b=r['fills']['assumed'],r['fills']['worse_05']
        power=a['trades']>=40 and a['funded_days']>=20
        gates=dict(positive_net=(a['net_bps'] or 0)>0,day_t=(a['day_t'] or -1)>=2,
            worse_nonnegative=b['net_bps'] is not None and b['net_bps']>=0,frequency=a['added_mornings_per_year']>=10)
        r['gates']=gates
        r['verdict']='Passed' if power and all(gates.values()) else ('Failed' if power else 'Unclear')
        summary['variants'][key]=r
    summary['verdict']=summary['variants']['k0.25']['verdict']
    summary['basis']='Only added days scored; prior-20 NIFTY daily volatility; H21 signal-only pools; exact H17 3% stop/fees; current calendars incomplete, opening locks unknown; reused history, no fresh confirmation.'
    finish(out,inputs,summary)


def hourly_signal(bars):
    """11:15 is the bar START; signal becomes known at 12:15."""
    b=bars.sort_values('ts').copy()
    time=pd.to_datetime(b.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
    b['day']=time.dt.strftime('%Y-%m-%d')
    b['hm']=time.dt.hour*100+time.dt.minute
    if b.duplicated(['day','hm']).any(): raise ValueError('Duplicate hourly timestamp')
    op=b[b.hm==915].set_index('day').o
    signal=b[b.hm==1115].set_index('day')
    f=pd.DataFrame(dict(open=op,signal_close=signal.c,signal_ts=signal.ts+3600))
    f['morning']=f.signal_close/f.open-1
    return f


def ordinary_matches(pick,candidates):
    """Same stock, previous 252 sessions only; signal-time move and gap calipers."""
    c=candidates[(candidates.day < pick['day']) & (candidates.day >= pick['oldest_day'])].copy()
    move=(c.morning-pick['morning']).abs()
    gap=(c.gap-pick['gap']).abs()
    vol=np.abs(np.log(c.vol20/pick['vol20']))
    c['distance']=(move/.01)**2+(gap/.01)**2+(vol/np.log(1.5))**2
    c=c[(move<=.01)&(gap<=.01)&(vol<=np.log(1.5))]
    return c.sort_values(['distance','day'],kind='stable').head(3)


def h25():
    out,inputs=freeze('h25',1)
    changes,actions,mechanical,events,event_days,tiers,funds=metadata(inputs)
    bars=pd.read_pickle(source(inputs,'data/wide_60m.pkl'))
    wide=pd.read_pickle(source(inputs,'data/wide_1d_10y.pkl'))
    market=hourly_signal(bars['NIFTY 50'])
    market=market.loc['2023-11-01':'2026-10-31']
    dates=list(market.index.sort_values())
    known_by_day={}
    official={}
    for d in dates:
        e=pd.concat(event_days[d]) if d in event_days else events.iloc[:0]
        known_by_day[d]=known_results(e,d)
        path=bhav_path(d)
        if path.exists(): official[d]=pd.read_pickle(source(inputs,path.relative_to(ROOT)))[['open','prev_close']]
    all_features=[]
    for symbol,b in bars.items():
        if tiers.get(symbol) not in ['midcap','smallcap'] or symbol in funds or symbol not in wide: continue
        f=hourly_signal(b).reindex(dates)
        # Prior daily returns must be known before the intraday signal.
        daily=dated_wide(wide[symbol])
        r=daily.c.pct_change(fill_method=None)
        for d,names in mechanical.items():
            if symbol in names and d in r.index: r.at[d]=np.nan
        f['vol20']=r.rolling(20,min_periods=15).std(ddof=1).shift(1).reindex(f.index)
        f['market_morning']=market.morning
        for d in dates:
            if d not in official or symbol not in official[d].index or symbol in actions.get(d,()) or symbol in known_by_day[d]: continue
            px=official[d].loc[symbol]
            if differs(f.at[d,'open'],px.open): continue
            if not np.isfinite(f.loc[d,['morning','vol20']].to_numpy(dtype=float)).all() or f.at[d,'vol20']<=0: continue
            # Opening gap is computed only on the official basis after excluding actions.
            all_features.append(dict(day=d,symbol=symbol,tier=tiers[symbol],open=f.at[d,'open'],
                signal_close=f.at[d,'signal_close'],signal_ts=int(f.at[d,'signal_ts']),morning=f.at[d,'morning'],
                market_morning=f.at[d,'market_morning'],gap=px.open/px.prev_close-1,vol20=f.at[d,'vol20']))
    features=pd.DataFrame(all_features)
    features.to_csv(out/'signal_features.csv',index=False)
    treated=features[(features.market_morning<=-.007)&(features.morning<=-.025)].copy()
    matches=[]
    for symbol,g in treated.groupby('symbol'):
        ordinary=features[(features.symbol==symbol)&(features.market_morning.abs()<.0035)]
        for r in g.to_dict('records'):
            pos=dates.index(r['day'])
            r['oldest_day']=dates[max(0,pos-252)]
            controls=ordinary_matches(r,ordinary)
            for c in controls.to_dict('records'):
                matches.append(dict(day=r['day'],symbol=symbol,control_day=c['day'],distance=c['distance']))
    treated.to_csv(out/'stress_candidates.csv',index=False)
    pairs=pd.DataFrame(matches,columns=['day','symbol','control_day','distance'])
    pairs.to_csv(out/'matches.csv',index=False)
    # Outcomes read only after every candidate and matched date has been frozen.
    outcomes=[]
    for symbol,days in pd.concat([treated[['symbol','day']],pairs[['symbol','control_day']].rename(columns={'control_day':'day'})]).drop_duplicates().groupby('symbol'):
        b=bars[symbol].copy()
        time=pd.to_datetime(b.ts,unit='s',utc=True).dt.tz_convert('Asia/Kolkata')
        b['day']=time.dt.strftime('%Y-%m-%d'); b['hm']=time.dt.hour*100+time.dt.minute
        for d in days.day:
            g=b[b.day==d].set_index('hm')
            required=[915,1015,1115,1215,1315,1415,1515]
            if not set(required).issubset(g.index):
                outcomes.append(dict(day=d,symbol=symbol,gross_return=np.nan,status='missing_path'));continue
            times=g.loc[required,'ts'].to_numpy()
            vals=g.loc[[1215,1515],['o','c']].to_numpy(dtype=float)
            if not np.array_equal(np.diff(times),np.full(6,3600)) or not np.isfinite(vals).all() or vals.min()<=0:
                outcomes.append(dict(day=d,symbol=symbol,gross_return=np.nan,status='invalid_path'));continue
            entry=float(g.at[1215,'o']); close=float(g.at[1515,'c'])
            outcomes.append(dict(day=d,symbol=symbol,entry=entry,close=close,entry_ts=int(g.at[1215,'ts']),
                gross_return=close/entry-1,worse_return=close/(entry*1.005)-1,status='ok'))
    outcomes=pd.DataFrame(outcomes)
    outcomes.to_csv(out/'outcomes.csv',index=False)
    observations=treated.merge(outcomes,on=['day','symbol'],how='left')
    controls=pairs.merge(outcomes[['day','symbol','gross_return']],left_on=['control_day','symbol'],right_on=['day','symbol'],suffixes=('','_control'))
    control_mean=controls.groupby(['day','symbol']).gross_return.mean().rename('control_return')
    observations=observations.join(control_mean,on=['day','symbol'])
    observations['difference']=observations.gross_return-observations.control_return
    observations.to_csv(out/'observations.csv',index=False)
    complete=observations[observations.gross_return.notna()]
    paired=complete[complete.control_return.notna()]
    day=complete.groupby('day').gross_return.mean()
    summary=dict(trials=1,scoring_runs=1,descriptive_only=True,stress_market_days=int((market.morning<=-.007).sum()),
        candidates=len(treated),observed=len(complete),days=complete.day.nunique(),matched=len(paired),matched_days=paired.day.nunique(),
        gross=estimate(day),worse_05=estimate(complete.groupby('day').worse_return.mean()),
        paired_excess=estimate(paired.groupby('day').difference.mean()),
        trade_weighted_gross_bps=float(complete.gross_return.mean()*1e4) if len(complete) else None,
        missing_outcomes=len(treated)-len(complete))
    rng=np.random.default_rng(2525)
    if len(day)>1:
        draws=rng.choice(day.to_numpy(),size=(2000,len(day)),replace=True).mean(axis=1)*1e4
        summary['day_bootstrap_ci95_bps']=np.percentile(draws,[2.5,97.5]).tolist()
    summary['graduates_to_scored_test']=len(complete)>=40 and len(day)>=20 and (summary['gross']['mean_bps'] or -1)>=60
    summary['verdict']='Unclear'  # A descriptive estimate never becomes a strategy verdict.
    summary['basis']='11:15-labelled hour closes 12:15 IST; next 12:15 open to final 15:15-15:30 bar close; gross only, no stops/fees/strategy score; same-stock earlier ordinary controls. Current constituent tiers, incomplete event vintages, hourly proxy.'
    finish(out,inputs,summary)


def history():
    """Save only frozen H22 outputs; preserve every pre-existing database row."""
    from stalk.data.store import Store
    out=ROOT/'data/h22'
    summary=json.loads((out/'summary.json').read_text())
    outputs=json.loads((out/'output_manifest.json').read_text())
    assert all(sha(out/p)==v for p,v in outputs.items())
    backup=out/'before_history.db'
    if backup.exists(): raise RuntimeError('History already started; do not replace pre-existing rows')
    with sqlite3.connect(ROOT/'data/stalk.db') as con,sqlite3.connect(backup) as dest: con.backup(dest)
    n=pd.read_csv(out/'calendar.csv',index_col='day')
    coverage=pd.read_csv(ROOT/'data/h21/coverage.csv').set_index('day')
    st=Store(); ids=[]
    try:
        for key,r in summary['variants'].items():
            cid='h22_volatility_'+key.replace('.','_')
            if st._conn.execute('SELECT id FROM campaigns WHERE id=?',(cid,)).fetchone(): raise RuntimeError('Existing H22 campaign')
            acct=cid+':panic_buy'; ids.append(cid)
            trades=pd.read_csv(out/f'{key}_assumed_trades.csv')
            added=set(added_days(n,r['k']))
            days,curve,equity,peak,dd=[],[],10000.,10000.,0.
            for day in n.index:
                g=trades[trades.day==day]
                covered=coverage.at[day,'status']=='ok'
                start=equity; equity+=float(g.net.sum()); peak=max(peak,equity); dd=max(dd,peak-equity)
                d=dict(day=day,start_equity=round(start,2),end_equity=round(equity,2),
                    gross=round(float(g.gross.sum()),2) if covered else None,costs=round(float(g.charges.sum()),2) if covered else None,
                    net=round(float(g.net.sum()),2) if covered else None,trades=len(g),wins=int((g.net>0).sum()),losses=int((g.net<=0).sum()),
                    status='daily_proxy' if covered else 'unknown_price',nifty_gap=float(n.at[day,'gap']),
                    added_trigger=day in added,leg='added' if day in added else 'inactive')
                days.append(d);curve.append([day,round(equity,2)])
                st.save_session(acct+':'+day,'panic_buy',day,acct,dict(daily_proxy=True,k=r['k'],added_days_only=True),d,ended=True)
                for t in g.itertuples():
                    st.save_trade(dict(id=acct+':'+day+':'+t.symbol,session_id=acct+':'+day,day=day,strategy='panic_buy',symbol=t.symbol,
                        side='long',qty=int(t.qty),entry_ts=ts(day,'09:15'),entry_price=t.entry,
                        exit_ts=None if t.reason=='stop' else ts(day,'15:30'),exit_price=t.exit,stop_initial=t.entry*.97,
                        gross=t.gross,costs=t.charges,net=t.net,reason=t.reason,fee_version=FEE_SCHEDULE_VERSION,broker='backtest'))
            net=float(trades.net.sum());tax=float(trades.groupby(trades.day.str[:4]).net.sum().clip(lower=0).sum()*.3)
            account=dict(name='panic_buy',strategies=['panic_buy'],require_validated=False,start=10000.,end=round(equity,2),
                gross=float(trades.gross.sum()),costs=float(trades.charges.sum()),net=net,tax=tax,after_tax=net-tax,trades=len(trades),
                green_days=sum(d['net'] is not None and d['net']>0 for d in days),red_days=sum(d['net'] is not None and d['net']<0 for d in days),
                flat_days=sum(d['net']==0 for d in days),unknown_days=sum(d['net'] is None for d in days),max_dd=dd,
                stats=dict(n=len(trades),win=float((trades.net>0).mean()) if len(trades) else None,net=net,t=r['fills']['assumed']['day_t']),
                per_strategy={},days=days,curve=curve)
            label='Panic, volatility-scaled, 10 years'+f" (k={r['k']:.2f}, added days only)"
            note=summary['basis']+' Original panic profits excluded; fixed Rs5,000 slots may need replenishment; unknown net remains null; stop times unknown; official-close proxy.'
            campaign=dict(id=cid,first_day=n.index[0],last_day=n.index[-1],days=len(n),capital=10000.,accounts={'panic_buy':account},weeks=[],
                kind='panic_buy',label=label,note=note,research_status=r['verdict'],daily_proxy=True,k=r['k'],unknown_days=account['unknown_days'])
            st.save_campaign(cid,n.index[0],n.index[-1],dict(label=label,daily_proxy=True,bar_minutes=5,k=r['k']),campaign)
    finally: st._conn.close()
    with sqlite3.connect(backup) as old,sqlite3.connect(ROOT/'data/stalk.db') as new:
        counts={}
        for table in ['campaigns','sessions','trades','logs']:
            rows=old.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            now=new.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            assert now[:len(rows)]==rows,table+' existing rows changed'
            counts[table]=len(rows)
    dump(out/'history_validation.json',dict(campaigns=ids,preexisting_rows_unchanged=counts))
    print('Saved two H22 Sessions campaigns; all pre-existing rows unchanged.')


def validate():
    evidence={}
    for cycle in ['h23','h22','h25']:
        out=ROOT/'data'/cycle
        start=json.loads((out/'started.json').read_text())
        assert sha(out/'scored_code.py')==start['code']
        assert all(sha(ROOT/p)==v for p,v in start['protected'].items())
        inputs=json.loads((out/'input_manifest.json').read_text())
        assert all(sha(ROOT/p)==v for p,v in inputs.items())
        outputs=json.loads((out/'output_manifest.json').read_text())
        assert all(sha(out/p)==v for p,v in outputs.items())
        evidence[cycle]=dict(inputs=len(inputs),outputs=len(outputs),protected='unchanged',frozen_hashes='ok')
    dump(ROOT/'data/h22/final_validation.json',evidence)
    print(json.dumps(evidence,indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['h23','h22','h25','history','validate'])
    globals()[p.parse_args().mode]()
