"""Verify saved H27b arithmetic/links and unchanged evidence; never run the scorer."""
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.h22_h25 import sha
from scripts.h27_h29 import match_five
from scripts.h9_fetch import bhav_path


def run():
    out = ROOT/'data/h27b'
    summary = json.loads((out/'summary.json').read_text())
    for manifest in ['before_returns_manifest.json','output_manifest.json']:
        for name, expected in json.loads((out/manifest).read_text()).items():
            assert sha(out/name)==expected,(manifest,name)
    baseline = json.loads((out/'unchanged_before.json').read_text())
    for rel, expected in baseline.items():
        assert sha(ROOT/rel)==expected,rel
    audit = pd.read_csv(out/'input_inventory.csv')
    assert (audit[audit.required].missing_share <= .01).all()
    pool = pd.read_csv(out/'control_eligibility.csv')
    retained = pool[pool.history_eligible]
    links = pd.read_csv(out/'matches.csv')
    obs = pd.read_csv(out/'paired_returns.csv')
    coverage = pd.read_csv(out/'coverage.csv')
    support = pd.read_csv(out/'prior_support.csv')
    assert support.support_complete.all()
    assert (support.prior_last < support.day).all()
    frames = {d:pd.read_pickle(bhav_path(d)) for d in obs.day.unique()}
    for r in obs.itertuples():
        b = frames[r.day]
        np.testing.assert_allclose(r.pick_return,b.at[r.pick,'close']/b.at[r.pick,'open']-1,atol=1e-14)
        np.testing.assert_allclose(r.control_return,b.at[r.control,'close']/b.at[r.control,'open']-1,atol=1e-14)
    for v in ['strict','loose']:
        original = pd.read_csv(out/f'{v}_eligibility.csv')
        selected = original[original.history_eligible]
        pop = summary['population'][v]
        assert len(selected)==pop['retained_picks'] and len(original)==pop['original_picks']
        assert selected[['beta60','vol20','turn20']].notna().all().all()
        for r in coverage[coverage.version==v].itertuples():
            p = selected[(selected.day==r.day)&(selected.symbol==r.symbol)].iloc[0]
            names = original[original.day==r.day].symbol
            c = retained[(retained.day==r.day)&~retained.symbol.isin(names)]
            c = c[c.gap > -.02] if r.control_set=='ordinary' else c[c.gap <= -.02]
            expected = match_five(p,c)
            assert len(expected)==r.n and r.matched==(r.n==5)
            saved = links[(links.version==v)&(links.day==r.day)&(links.pick==r.symbol)&(links.control_set==r.control_set)]
            assert saved.control.tolist()==(expected.symbol.tolist() if r.matched else [])
        for label in ['ordinary','lower_gap']:
            x = obs[(obs.version==v)&(obs.control_set==label)]
            per = x.groupby(['day','pick'])[['pick_return','control_return']].mean()
            daily = (per.pick_return-per.control_return).groupby('day').mean()
            item = summary['versions'][v][label]
            assert len(daily)==item['n']
            np.testing.assert_allclose(daily.mean()*1e4,item['mean_bps'],atol=1e-10)
            np.testing.assert_allclose(daily.mean()/(daily.std(ddof=1)/len(daily)**.5),item['day_t'],atol=1e-12)
            c = coverage[(coverage.version==v)&(coverage.control_set==label)]
            np.testing.assert_allclose(c.matched.mean(),item['coverage'])
        days = pd.read_csv(out/f'{v}_market_mornings.csv')
        for key, column in [('raw_ordinary','ordinary'),('nifty','nifty')]:
            item = summary['versions'][v][key]
            assert item['n']==len(days)==selected.day.nunique()
            np.testing.assert_allclose(days[column].mean()*1e4,item['mean_bps'],atol=1e-10)
    before = json.loads((out/'research_summary_before.json').read_text(encoding='utf-8'))
    after = json.loads((ROOT/'data/research_summary.json').read_text(encoding='utf-8'))
    assert before['insights']==after['insights']
    assert before['scoreboard']==after['scoreboard']
    assert after['cycles'][0]['id']=='H27b'
    assert after['cycles'][1:]==before['cycles']
    result = dict(saved_arithmetic_and_matches='passed',required_input_audit='passed',
        h27_and_protected_and_web_hashes='unchanged',one_guard=True,scoring_runs=summary['scoring_runs'],
        preserved_summary_insights_and_scoreboard=True,matched_links=len(links),
        strict_prior_support={k:[int(pd.read_csv(out/'strict_retained_support.csv')[k].min()),
                                int(pd.read_csv(out/'strict_retained_support.csv')[k].max())]
                              for k in ['beta_pairs','vol_observations','turnover_observations']})
    (out/'validation.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':run()
