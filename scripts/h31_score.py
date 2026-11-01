"""H31 volume-climax gap fade (RUNBOOK "## H31:"), scored once per sample. Paper research only.

    python scripts/h31_score.py --sample A     # replication 2015-01..2020-12 (after the Kite 2015-2020 download)
    python scripts/h31_score.py --sample B     # sealed year 2025-08..2026-07, only after A has been scored
Each sample is guarded by data/h31/started_<sample>.json: a failed attempt stays spent. The rule is trial 10 of
scripts/h31_lab.py verbatim; this file adds only sample windows, results-day exclusions, worse fills and the bootstrap.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd

from scripts import h30_capitulation as h
from scripts import h31_lab as lab
from stalk.config import IST
from stalk.research.orb import normalize, universe

OUT = ROOT/'data/h31'
RULE = dict(lab.BASE, **lab.VARIANTS['short_vol10_stop3'])     # RUNBOOK H31 amendment: 3% stop (user)
SAMPLES = {'A': ('2015-01-01', '2020-12-31'), 'B': ('2025-08-01', '2026-07-31')}
HURDLE_GROSS = 0.00212


def build_range(start, end):
    """H30's arrays over [start - warm-up, end] from data/kite_5m (same tables, same eligibility)."""
    tiers = universe()
    frames = {p.stem: pd.read_pickle(p) for p in sorted((ROOT/'data/kite_5m').glob('*.pkl')) if p.stem in tiers}
    warm = (pd.Timestamp(start)-pd.Timedelta(days=45)).strftime('%Y-%m-%d')
    symbols = sorted(set(frames) & set(tiers))
    calendar = set()
    for s in symbols:                                   # one stock at a time: holding all normalised frames needs GBs
        calendar |= {d for d in normalize(frames[s]).day.unique() if warm <= d <= end}
    calendar = sorted(calendar)
    D, S = len(calendar), len(symbols)
    B = dict(symbols=symbols, calendar=calendar, tiers=[tiers[s] for s in symbols],
             pc=np.full((D, S), np.nan), eligible=np.zeros((D, S), bool),
             vavg=np.full((D, len(h.SIGNAL), S), np.nan, np.float32),
             **{k: np.full((D, len(h.GRID), S), np.nan, np.float32) for k in 'ohl'},
             **{k: np.full((D, len(h.SIGNAL), S), np.nan, np.float32) for k in 'cv'})
    for j, s in enumerate(symbols):
        f = frames[s]
        f = f[(pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST).dt.strftime('%Y-%m-%d') >= warm)]
        if f.empty:
            continue
        t = h.symbol_tables(f, calendar, causal=True)
        B['pc'][:, j], B['eligible'][:, j], B['vavg'][:, :, j] = t['pc'], t['eligible'], t['vavg']
        for k in 'ohlcv':
            B[k][:, :, j] = t[k]
    return B


def load_full():
    meta = json.loads((lab.OUT/'B_meta.json').read_text())
    return dict(meta, **{k: np.load(lab.OUT/f'B_{k}.npy', mmap_mode='r') for k in lab.ARRAYS})


def bootstrap_lower(m, draws=5000, seed=31):
    """Day-block bootstrap of net % a trade: resample whole days, 2.5th percentile of the mean."""
    g = [x.net_pct.to_numpy() for _, x in m.groupby('day')]
    rng = np.random.default_rng(seed)
    means = []
    for _ in range(draws):
        pick = rng.integers(len(g), size=len(g))
        v = np.concatenate([g[i] for i in pick])
        means.append(v.mean())
    return float(np.percentile(means, 2.5))


def score(B, start, end):
    p = dict(RULE, start=start, end=end, excluded=h.results_exclusions([d for d in B['calendar'] if start <= d <= end]))
    picks = lab.signals(B, p)
    rows = []
    for q in picks:
        r = lab.walk(B, q['i'], q['hm'], q['k'], q['side'], p['stop'], p['target'])
        w = lab.walk(B, q['i'], q['hm'], q['k'], q['side'], p['stop'], p['target'], stress=0.0025)
        rows.append(dict(q, **(r or {}), known=r is not None, net_pct_worse025=w['net_pct'] if w else np.nan,
                         control_pct=lab.control(B, q, p) if r else np.nan))
    t = pd.DataFrame(rows)
    if t.empty:
        return dict(trades=0, verdict='Failed', reason='no signals'), t
    unknown = set(t.loc[~t.known, 'day'])
    m = t[t.known & ~t.day.isin(unknown)].copy()
    days = m.groupby('day').net.sum()
    res = dict(trades=len(m), days=int(days.size), unknown_days=len(unknown),
               years=round((pd.Timestamp(end)-pd.Timestamp(start)).days/365.25, 2),
               net_pct=float(m.net_pct.mean()), gross_pct=float(m.gross_pct.mean()),
               worse025_pct=float(m.net_pct_worse025.mean()), boot_lower95_net_pct=bootstrap_lower(m),
               vs_control_pct=float((m.gross_pct-m.control_pct).mean()), controlled=int(m.control_pct.notna().sum()),
               win=float((m.net > 0).mean()), avg_win=float(m.loc[m.net > 0, 'net'].mean()),
               avg_loss=float(m.loc[m.net <= 0, 'net'].mean()), net_rs=float(m.net.sum()),
               day_t=float(days.mean()/days.std()*np.sqrt(len(days))), stops=float((m.reason == 'stop').mean()),
               near_circuit=int((m.ret >= 0.09).sum()),
               top_stock_share=float(m.groupby('symbol').net.sum().max()/m.net.sum()) if m.net.sum() > 0 else None,
               per_year={y: dict(trades=int(len(g)), net_pct=float(g.net_pct.mean()), net_rs=float(g.net.sum()))
                         for y, g in m.groupby(m.day.str[:4])})
    gates = dict(trades=res['trades'] >= 40 and res['days'] >= 20, gross=res['gross_pct'] >= HURDLE_GROSS,
                 bootstrap=res['boot_lower95_net_pct'] > 0, control=res['vs_control_pct'] > 0, worse=res['worse025_pct'] > 0)
    res['gates'] = gates
    res['verdict'] = 'Passed' if all(gates.values()) else 'Failed'
    res['secondary'] = secondary(B, m)
    return res, m


def secondary(B, m):
    """RUNBOOK H31 secondary checks (reported, not gated): s1 skip a second trade when the first is in profit at its
    entry; s2 moves >= 5% only; s3 no smallcaps."""
    from stalk.research.orb import universe
    tiers = universe()
    ci, si = {d: i for i, d in enumerate(B['calendar'])}, {s: k for k, s in enumerate(B['symbols'])}
    keep = []
    for d, g in m.sort_values(['day', 'hm', 'symbol']).groupby('day'):
        r = g.to_dict('records')
        keep.append(r[0])
        if len(r) > 1:
            a, b = r[0], r[1]
            px = float(B['o'][ci[d], h.GRID.index(b['hm'])+1, si[a['symbol']]])
            if not (a['hm'] < b['hm'] and a['entry'] - px > 0):      # shorts: first in profit -> skip the second
                keep.append(b)
    out = {}
    for name, x in (('s1_skip_second_if_first_winning', pd.DataFrame(keep)), ('s2_moves_5pct_plus', m[m.ret >= 0.05]),
                    ('s3_no_smallcaps', m[m.symbol.map(tiers) != 'smallcap'])):
        out[name] = dict(trades=int(len(x)), net_rs=float(x.net.sum()) if len(x) else 0.0,
                         rs_a_trade=float(x.net.mean()) if len(x) else None, net_pct=float(x.net_pct.mean()) if len(x) else None)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sample', choices=list(SAMPLES), required=True)
    s = ap.parse_args().sample
    OUT.mkdir(parents=True, exist_ok=True)
    if s == 'B' and not (OUT/'summary_A.json').exists():
        raise SystemExit('Sample B opens only after sample A is scored (RUNBOOK H31).')
    with (OUT/f'started_{s}.json').open('x', encoding='utf-8') as fh:     # one scored run per sample
        json.dump(dict(at=datetime.now(IST).isoformat()), fh)
    reg = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split('## H31:', 1)[1].split('\n## ', 1)[0]
    (OUT/'preregistration.md').write_text(reg, encoding='utf-8')
    (OUT/f'code_sha_{s}.json').write_text(json.dumps({p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
        ('scripts/h31_score.py', 'scripts/h31_lab.py', 'scripts/h30_capitulation.py', 'stalk/market/costs.py')}, indent=1))
    start, end = SAMPLES[s]
    B = build_range(start, end) if s == 'A' else load_full()
    res, trades = score(B, start, end)
    res.update(sample=s, start=start, end=end, symbols=len(B['symbols']))
    trades.to_csv(OUT/f'trades_{s}.csv', index=False)
    (OUT/f'summary_{s}.json').write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({k: v for k, v in res.items() if k != 'per_year'}, indent=1, default=float))
    dashboard(s, res, trades)


def dashboard(s, res, trades):
    """Every scored run goes on Sessions and Strategies automatically (user's standing request)."""
    from scripts.publish_run import publish, record_cycle
    start, end = SAMPLES[s]
    name = {'A': '2015-2020 replication', 'B': 'sealed year'}[s]
    pct = lambda x: f"{x*100:+.2f}%"
    publish(f'h31_{s.lower()}', f'Volume-climax gap fade (H31), {name}', trades, start, end, res['verdict'],
            'Scored once as registered in RUNBOOK H31. 3% stop, flatten 15:10, max 2 a day, Rs5,000 slots.')
    done = {x: json.loads((OUT/f'summary_{x}.json').read_text()) for x in SAMPLES if (OUT/f'summary_{x}.json').exists()}
    both = len(done) == 2
    verdict = 'Failed' if any(v['verdict'] == 'Failed' for v in done.values()) else ('Passed' if both else 'Pending')
    parts = [f"{ {'A': '2015-2020', 'B': 'sealed year'}[x]}: {v['trades']} trades, {pct(v['net_pct'])} a trade after charges, "
             f"win {v['win']*100:.0f}%, {v['verdict'].lower()}" for x, v in done.items()]
    plain = 'Short stocks up 3%+ by 09:30 on 10x normal volume, 3% stop. ' + '; '.join(parts) + '.'
    record_cycle('H31', 'Volume-climax gap fade, short', verdict, plain,
                 '; '.join(f"{x}: " + json.dumps({k: v[k] for k in ('trades', 'net_pct', 'gross_pct', 'boot_lower95_net_pct', 'vs_control_pct', 'gates')}, default=float)
                           for x, v in done.items()),
                 12, len(done), score=('Volume-climax fade (H31)', round(res['net_pct']*100, 2),
                                       f"{verdict} · {name} · about {round(res['trades']/max(res['years'], 0.1))} trades a year"))


if __name__ == '__main__':
    main()
