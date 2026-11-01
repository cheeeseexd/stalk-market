"""H26 frozen scorer; NEVER import to score. CLI --pilot or --full, one guard/window.

Full history is deliberately not scored until the user has downloaded Kite bars.
The latest 12 months before the pilot are held back and opened only after design.
"""
from __future__ import annotations
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd
from stalk.config import IST
from stalk.research.orb import (VERSIONS, STRESSES, prepare, select, random_picks, walk,
                                 metrics, tstat, event_exclusions, universe)
from scripts.kite_backfill import atomic_json


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def registration():
    return (ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split('## H26:', 1)[1].split('\n## ', 1)[0]


def source_files():
    return ['stalk/research/orb.py', 'stalk/market/costs.py', 'stalk/sim/panic.py',
            'scripts/h26_full.py', 'data/wide_meta.csv', 'data/wide_events.pkl',
            'data/h9/instrument_inventory.json']


def preselect(data, features, tiers, days):
    selections = {}
    for day in days:
        excluded, calendar = event_exclusions(ROOT, day)
        picks, pool = select(day, data, features, tiers, excluded)
        selections[day] = dict(picks=picks, pool=pool, calendar=calendar)
    return selections


def score(selections, data, out, label, versions=None):
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out/'selections.json', selections)  # frozen before any returns
    days = list(selections)
    # Cache each eligible stock/side execution once, independent of random repetitions.
    cache = {}
    def outcome(p, v, stress):
        key = (p['day'], p['symbol'], p['side'], v, stress)
        if key not in cache:
            cache[key] = walk(p, data[p['symbol']][p['day']], VERSIONS[v], STRESSES[stress])
        return cache[key]
    result = {}
    random_histories = {d: [] for d in days}
    rng = np.random.default_rng(2601)
    for _ in range(1000):
        for d, selection in selections.items():
            random_histories[d].append(random_picks(selection['pool'], rng))
    atomic_json(out/'random_picks.json', random_histories)
    for v in (versions if versions is not None else VERSIONS):
        result[v] = {}
        for stress in STRESSES:
            trades, unknown, states = [], set(), []
            for d, sel in selections.items():
                for p in sel['picks']:
                    r = outcome(p, v, stress)
                    states.append(dict(day=d, symbol=p['symbol'], status=r['status']))
                    if r['status'] == 'unknown':
                        unknown.add(d)
                    if r['trade']:
                        trades.append(r['trade'])
            # Exclude whole unknown days from observed comparisons, retaining raw trades separately.
            measured = [t for t in trades if t['day'] not in unknown]
            obs = metrics(measured, days, unknown)
            draws = []
            for i in range(1000):
                rt, ru = [], set(unknown)
                for d in days:
                    for p in random_histories[d][i]:
                        r = outcome(p, v, stress)
                        if r['status'] == 'unknown':
                            ru.add(d)
                        if r['trade']:
                            rt.append(r['trade'])
                m = metrics([t for t in rt if t['day'] not in ru], days, ru)
                common = set(obs['daily']) & set(m['daily'])
                m['paired_excess_rs'] = float(np.mean([obs['daily'][d]-m['daily'][d] for d in common])) if common else None
                m.pop('daily')
                draws.append(m)
            vals = [d['net_pct'] for d in draws if d['net_pct'] is not None]
            benchmark = dict(draws=1000, mean_net_pct=float(np.mean(vals)) if vals else None,
                p95_net_pct=float(np.percentile(vals, 95)) if vals else None,
                observed_percentile=float(np.mean(np.array(vals) < obs['net_pct'])*100) if vals and obs['net_pct'] is not None else None,
                p_ge_observed=float((1+sum(x >= obs['net_pct'] for x in vals))/(1+len(vals))) if vals and obs['net_pct'] is not None else None,
                mean_paired_excess_rs=float(np.mean([d['paired_excess_rs'] for d in draws if d['paired_excess_rs'] is not None])) if vals else None)
            result[v][stress] = dict(obs, benchmark=benchmark)
            pd.DataFrame(trades).to_csv(out/f'{v}_{stress}_trades.csv', index=False)
            atomic_json(out/f'{v}_{stress}_statuses.json', states)
            atomic_json(out/f'{v}_{stress}_random.json', draws)
        a = result[v]['assumed']
        qualifies = (a['trades'] >= 40 and a['independent_days'] >= 20 and a['day_t'] is not None
                     and a['day_t'] >= (1 if label == 'pilot' else 1.5)
                     and all(result[v][s]['net_pct'] is not None and result[v][s]['net_pct'] > 0 for s in STRESSES)
                     and a['benchmark']['observed_percentile'] is not None and a['benchmark']['observed_percentile'] >= 95)
        result[v]['verdict'] = 'Unclear' if qualifies else 'Failed'
        result[v]['design_gate'] = bool(qualifies)
    atomic_json(out/'summary.json', result)
    return result


def run(pilot):
    name = 'pilot' if pilot else 'full'
    out = ROOT/'data/h26'/name
    out.mkdir(parents=True, exist_ok=True)
    # Guard before reading OHLC data; failed attempts remain spent and visible.
    with (out/'started.json').open('x', encoding='utf-8') as f:
        json.dump(dict(at=datetime.now(IST).isoformat(), window=name, trials=3), f)
    reg = registration()
    inputs = source_files()+(['data/wide_5m.pkl'] if pilot else
                            [str(p.relative_to(ROOT)).replace('\\', '/') for p in sorted((ROOT/'data/kite_5m').glob('*.pkl'))])
    manifest = {p: sha(ROOT/p) for p in inputs}
    atomic_json(out/'input_manifest.json', manifest)
    (out/'preregistration.md').write_text(reg, encoding='utf-8')
    if not pilot:
        frozen = json.loads((ROOT/'data/h26/pilot/input_manifest.json').read_text())
        for p in ('stalk/research/orb.py', 'stalk/market/costs.py', 'scripts/h26_full.py'):
            if manifest[p] != frozen[p]:
                raise ValueError('Frozen scoring source changed: '+p)
        if reg != (ROOT/'data/h26/pilot/preregistration.md').read_text(encoding='utf-8'):
            raise ValueError('H26 registration changed')
    for p in ('stalk/research/orb.py', 'scripts/h26_full.py'):
        shutil.copyfile(ROOT/p, out/Path(p).name)
    tiers = universe()
    frames = pd.read_pickle(ROOT/'data/wide_5m.pkl') if pilot else {
        p.stem: pd.read_pickle(p) for p in (ROOT/'data/kite_5m').glob('*.pkl') if p.stem in tiers}
    if not pilot and set(tiers)-set(frames):
        raise ValueError('Backfill missing symbols; inspect coverage report before scoring')
    data, features = prepare(frames, tiers)
    all_days = sorted({d for groups in data.values() for d in groups})
    valid = [d for d in all_days if any(d in f.index and not f.loc[d].isna().any() for f in features.values())]
    if pilot:
        days = [d for d in valid if '2026-08-01' <= d <= '2026-10-31']
    else:
        days = [d for d in valid if '2015-01-01' <= d <= '2026-07-31']
        if not days or (datetime.fromisoformat(days[-1])-datetime.fromisoformat(days[0])).days < 730:
            raise ValueError('At least two years required')
    atomic_json(out/'coverage.json', dict(calendar=all_days, scored_calendar=days,
        symbols=len(data), requested_symbols=len(tiers), warmup_or_unavailable_days=sorted(set(all_days)-set(valid))))
    selection = preselect(data, features, tiers, days)
    if pilot:
        result = score(selection, data, out, name)
    else:
        design = {d: s for d, s in selection.items() if d <= '2025-07-31'}
        final = {d: s for d, s in selection.items() if d >= '2025-08-01'}
        if len(final) < 200 or len(design) < 252:
            raise ValueError('Incomplete fixed design/final calendar')
        result = score(design, data, out/'design', 'design')
        allowed = [v for v in VERSIONS if result[v]['design_gate']]
        # score() freezes all registered settings together; do not expose failed final variants.
        if allowed:
            with (out/'final_started.json').open('x') as f:
                json.dump(dict(versions=allowed, trials=3, dates=list(final)), f)
            final_result = score(final, data, out/'final', 'final', allowed)
            for v in final_result:
                if v not in allowed:
                    final_result[v]['verdict'] = 'Failed (design gate)'
                else:
                    a = final_result[v]['assumed']
                    final_result[v]['verdict'] = ('Passed' if final_result[v]['design_gate'] and a['day_t'] >= 2.5 else 'Failed')
            atomic_json(out/'final/summary.json', final_result)
        else:
            atomic_json(out/'final_unscored.json', dict(reason='No design variant passed', versions=allowed))
    atomic_json(out/'output_manifest.json', {str(p.relative_to(out)): sha(p) for p in out.rglob('*')
                if p.is_file() and p.name != 'output_manifest.json'})
    print(json.dumps({v: {s: r[s]['net_pct'] for s in STRESSES} for v, r in result.items()}, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--pilot', action='store_true')
    mode.add_argument('--full', action='store_true')
    args = p.parse_args()
    run(args.pilot)
