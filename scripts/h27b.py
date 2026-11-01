"""One guarded, offline H27b attribution run. No broker or order interface."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.h27_h29 import inventory, audit_ok, match_five, dump
from scripts.h22_h25 import estimate, metadata, past_features, sha
from scripts.h9_fetch import bhav_path
from scripts.panic_final import nifty

FEATURES = ['beta60', 'vol20', 'turn20']


def history_eligible(frame):
    """H27 minimum support is encoded in its frozen features; never see outcomes."""
    return np.isfinite(frame[FEATURES]).all(axis=1) & (frame.vol20 > 0) & (frame.turn20 > 0)


def exclusion_reasons(frame):
    return frame.apply(lambda r: ','.join(k for k in FEATURES
        if not np.isfinite(r[k]) or (k != 'beta60' and r[k] <= 0)), axis=1)


def verdict(coverage, mornings, mean_bps, day_t):
    if coverage < .8 or mornings < 40:
        return 'Unresolved'
    return 'Passed' if mean_bps > 50 and day_t is not None and day_t >= 2.5 else 'Failed'


def run():
    out = ROOT/'data/h27b'
    out.mkdir(exist_ok=True)
    # Exclusive guard precedes all mutable run artifacts and all outcome work.
    book = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8')
    registration = '## H27b:'+book.split('## H27b:', 1)[1].split('\n## ', 1)[0]
    with (out/'started.json').open('x', encoding='utf-8') as f:
        json.dump(dict(utc=datetime.now(timezone.utc).isoformat(), trials=1,
                       maximum_scored_runs=1, code_sha256=sha(__file__)), f, indent=2)
    (out/'preregistration.md').write_text(registration, encoding='utf-8')
    shutil.copy2(__file__, out/'scored_code.py')
    inputs, audit = {}, []
    # Prove that the inherited population/features still refer to H27's sources.
    for manifest in ['input_manifest.json', 'output_manifest.json']:
        path = ROOT/'data/h27'/manifest
        inputs[str(path.relative_to(ROOT))] = sha(path)
        for rel, expected in json.loads(path.read_text(encoding='utf-8')).items():
            p = ROOT/rel if manifest.startswith('input') else ROOT/'data/h27'/rel
            if sha(p) != expected:
                raise ValueError(f'H27 frozen hash mismatch: {p.name}')
            inputs[str(p.relative_to(ROOT))] = expected
    _, _, mechanical, _, _, _, _ = metadata(inputs)
    n = nifty()
    frames = {d: pd.read_pickle(bhav_path(d)) for d in sorted(set(n.index)|set(n.prev_day))
              if bhav_path(d).exists()}
    ret = pd.DataFrame({d: f.close/f.prev_close-1 for d,f in frames.items()}).T.reindex(n.index)
    turn = pd.DataFrame({d: f.value for d,f in frames.items()}).T.reindex(n.index)
    for d, symbols in mechanical.items():
        if d in ret.index:
            ret.loc[d, ret.columns.intersection(symbols)] = np.nan
    ni = pd.read_pickle(ROOT/'data/wide_1d_10y.pkl')['NIFTY 50']
    dates = pd.to_datetime(ni.ts, unit='s', utc=True).dt.tz_convert('Asia/Kolkata').dt.strftime('%Y-%m-%d')
    market = pd.Series(ni.c.to_numpy(), index=dates).sort_index().pct_change(fill_method=None)
    pool = pd.read_csv(ROOT/'data/h27/eligible_features.csv')
    frozen = pd.read_csv(ROOT/'data/h27/frozen_pick_features.csv')
    picks = {}
    population = {}
    for v in ['strict', 'loose']:
        p = ROOT/f'data/h17/{v}_cleaned_selected.csv'
        inputs[str(p.relative_to(ROOT))] = sha(p)
        raw = pd.read_csv(p, usecols=['day','symbol','open','close','prev_close','prev_value','gap'])
        raw = raw.merge(frozen[frozen.version == v], on=['day','symbol'], validate='one_to_one')
        # Reconcile ORIGINAL ranking before any eligibility exclusions.
        for d,g in raw.groupby('day', sort=False):
            rank = pool[(pool.day == d)&(pool.gap < -.02)].sort_values(['gap','symbol'], kind='stable')
            if rank.head(2).symbol.tolist() != g.symbol.tolist():
                raise ValueError(f'Frozen H17 ranking mismatch: {v} {d}')
        raw['history_eligible'] = history_eligible(raw)
        raw['exclusion_reason'] = exclusion_reasons(raw)
        raw.to_csv(out/f'{v}_eligibility.csv', index=False)
        picks[v] = raw[raw.history_eligible].copy()
        population[v] = dict(original_picks=len(raw), excluded_picks=int((~raw.history_eligible).sum()),
            retained_picks=len(picks[v]), retention=len(picks[v])/len(raw),
            original_mornings=int(raw.day.nunique()), retained_mornings=int(picks[v].day.nunique()),
            missing_features={k:int((~np.isfinite(raw[k]) | ((raw[k] <= 0) if k != 'beta60' else False)).sum()) for k in FEATURES})
    pool['history_eligible'] = history_eligible(pool)
    pool['exclusion_reason'] = exclusion_reasons(pool)
    pool.to_csv(out/'control_eligibility.csv', index=False)
    retained = pool[pool.history_eligible].copy()
    population['control_pool'] = dict(original=len(pool), retained=len(retained), excluded=len(pool)-len(retained))
    dump(out/'population.json', population)
    # Rebuild causal support and verify inherited features without using event-day returns.
    support = []
    for d,g in retained.groupby('day'):
        prior = ret.index[ret.index < d][-60:]
        r = ret.loc[prior, g.symbol]
        beta_n = r.notna().mul(market.reindex(prior).notna(), axis=0).sum()
        vol_n = r.iloc[-20:].count()
        turn_n = turn.reindex(prior[-20:])[g.symbol].count()
        recomputed = past_features(ret, turn, market, d).loc[g.symbol]
        np.testing.assert_allclose(g[FEATURES], recomputed[FEATURES], rtol=1e-10, atol=1e-12)
        for s in g.symbol:
            support.append(dict(day=d, symbol=s, prior_sessions=len(prior),
                prior_first=prior[0], prior_last=prior[-1], beta_pairs=int(beta_n[s]),
                vol_observations=int(vol_n[s]), turnover_observations=int(turn_n[s]),
                support_complete=len(prior)==60 and beta_n[s]>=45 and vol_n[s]>=15 and turn_n[s]==20))
    support = pd.DataFrame(support)
    support.to_csv(out/'prior_support.csv', index=False)
    if not support.support_complete.all() or not (support.prior_last < support.day).all():
        raise ValueError('Retained prior support failed')
    # Inventory every retained required field, including official endpoint availability.
    retained['close'] = [frames[d].at[s,'close'] if d in frames and s in frames[d].index else np.nan
                         for d,s in zip(retained.day,retained.symbol)]
    required = {'day','symbol','open','close','prev_close','prev_value','gap',*FEATURES}
    inventory(retained, 'retained_controls', required, audit)
    inventory(support, 'retained_prior_support', set(support.columns), audit)
    for v,p in picks.items():
        inventory(p, v+'_retained_picks', required|{'version'}, audit)
        inventory(n.reindex(sorted(p.day.unique())).reset_index(), v+'_retained_nifty', {'open','close'}, audit)
        p.merge(support, on=['day','symbol'], validate='one_to_one').to_csv(out/f'{v}_retained_support.csv',index=False)
    # H27 raw source/metadata audits passed; full-population missing features are
    # superseded ONLY by the explicitly registered history eligibility above.
    old = pd.read_csv(ROOT/'data/h27/input_inventory.csv').to_dict('records')
    audit.extend(r for r in old if r['source'] not in
                 ['eligible_matching_features','strict_pick_features','loose_pick_features','strict_frozen','loose_frozen'])
    pd.DataFrame(audit).to_csv(out/'input_inventory.csv', index=False)
    inputs['scripts/h27b.py'] = sha(__file__)
    inputs['scripts/h27_h29.py'] = sha(ROOT/'scripts/h27_h29.py')
    dump(out/'input_manifest.json', inputs)
    if not audit_ok(audit):
        summary = dict(verdict='Unresolved', trials=1, scoring_runs=0, population=population,
            reason='Required retained input exceeds1% missing/invalid.',
            failed_fields=[r for r in audit if r['required'] and r['missing_share'] > .01])
    else:
        links, coverage = [], []
        for v, selected in picks.items():
            for d,g in selected.groupby('day'):
                original_names = set(frozen[(frozen.version==v)&(frozen.day==d)].symbol)
                p = retained[(retained.day==d)&~retained.symbol.isin(original_names)]
                for row in g.to_dict('records'):
                    for label,c in [('ordinary',p[p.gap>-.02]),('lower_gap',p[p.gap<=-.02])]:
                        matched = match_five(row,c)
                        complete = len(matched)==5
                        coverage.append(dict(version=v,day=d,symbol=row['symbol'],control_set=label,
                                             matched=complete,n=len(matched)))
                        if complete:
                            for z in matched.to_dict('records'):
                                links.append(dict(version=v,day=d,pick=row['symbol'],control=z['symbol'],
                                                  control_set=label,distance=z['distance']))
        links = pd.DataFrame(links)
        cov = pd.DataFrame(coverage)
        links.to_csv(out/'matches.csv',index=False)
        cov.to_csv(out/'coverage.csv',index=False)
        # Frozen audit/populations/links are now on disk. First event-day return.
        dump(out/'before_returns_manifest.json', {p.name:sha(p) for p in out.iterdir() if p.is_file()})
        observations = []
        for r in links.to_dict('records'):
            b = frames[r['day']]
            a,z = b.loc[r['pick']],b.loc[r['control']]
            observations.append({**r,'pick_return':a.close/a.open-1,'control_return':z.close/z.open-1})
        obs = pd.DataFrame(observations)
        obs.to_csv(out/'paired_returns.csv',index=False)
        result, daily = {}, []
        for v,selected in picks.items():
            result[v] = {}
            for label in ['ordinary','lower_gap']:
                c = cov[(cov.version==v)&(cov.control_set==label)]
                x = obs[(obs.version==v)&(obs.control_set==label)].copy()
                per = x.groupby(['day','pick'])[['pick_return','control_return']].mean()
                per['excess'] = per.pick_return-per.control_return
                per['stressed_excess'] = (1+per.pick_return)/1.005-1-per.control_return
                days = per.groupby('day').mean()
                item = dict(coverage=float(c.matched.mean()),matched_picks=int(c.matched.sum()),
                    retained_picks=len(c), **estimate(days.excess))
                item.update(pick_only_entry_worse_05=estimate(days.stressed_excess),
                    pick_gross=estimate(days.pick_return), matched_control_gross=estimate(days.control_return))
                item['verdict'] = verdict(item['coverage'],item['n'],item['mean_bps'],item['day_t'])
                result[v][label] = item
                daily.extend(dict(version=v,control_set=label,day=d,**r) for d,r in days.to_dict('index').items())
            ds = sorted(selected.day.unique())
            ordinary = []
            matched_ds = set(cov[(cov.version==v)&(cov.control_set=='ordinary')&cov.matched].day)
            for d in ds:
                original_names = set(frozen[(frozen.version==v)&(frozen.day==d)].symbol)
                p = retained[(retained.day==d)&(retained.gap>-.02)&~retained.symbol.isin(original_names)]
                ordinary.append(dict(day=d,ordinary=float((p.close/p.open-1).mean()),
                                     nifty=float(n.at[d,'close']/n.at[d,'open']-1),ordinary_names=len(p),
                                     matched_morning=d in matched_ds))
            pd.DataFrame(ordinary).to_csv(out/f'{v}_market_mornings.csv',index=False)
            result[v]['raw_ordinary'] = estimate([r['ordinary'] for r in ordinary])
            result[v]['nifty'] = estimate([r['nifty'] for r in ordinary])
            result[v]['matched_morning_ordinary'] = estimate([r['ordinary'] for r in ordinary if r['matched_morning']])
            result[v]['matched_morning_nifty'] = estimate([r['nifty'] for r in ordinary if r['matched_morning']])
        pd.DataFrame(daily).to_csv(out/'daily_returns.csv',index=False)
        summary = dict(verdict=result['strict']['ordinary']['verdict'],trials=1,scoring_runs=1,
                       population=population,versions=result)
    dump(out/'summary.json', summary)
    dump(out/'output_manifest.json', {p.name:sha(p) for p in out.iterdir()
         if p.is_file() and p.name not in ['output_manifest.json','unchanged_before.json','research_summary_before.json']})
    print(json.dumps(summary,indent=2,allow_nan=False))


if __name__ == '__main__':
    run()
