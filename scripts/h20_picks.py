"""H20 preregistered 2/3/4-slot portfolios on saved H17 pools. No network."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.horizon_ladder import dump
from scripts.panic_final import nifty, tstat
from stalk.market.costs import round_trip_cost

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/h20'
BUDGETS = {2: 5000., 3: 3333., 4: 2500.}


def account(pools, k):
    rows = []
    selected = pools.sort_values(['day', 'gap', 'symbol'], kind='stable').groupby('day').head(k).copy()
    selected['rank'] = selected.groupby('day').cumcount() + 1
    for r in selected.itertuples():
        entry = r.open * 1.001
        qty = int(BUDGETS[k] // entry)
        if not qty:
            continue
        stopped = r.low <= entry * .97
        exit_px = (entry * .97 * .998 if stopped else r.close * .9992) * .999
        charges = round_trip_cost(qty, entry, exit_px)
        net = (exit_px - entry) * qty - charges
        rows.append(dict(day=r.day, symbol=r.symbol, rank=r.rank, qty=qty, entry=entry,
                         exit=exit_px, charges=charges, net=net, net_bps=net/(qty*entry)*10000,
                         reason='stop' if stopped else 'flatten'))
    return pd.DataFrame(rows, columns=['day', 'symbol', 'rank', 'qty', 'entry', 'exit', 'charges', 'net', 'net_bps', 'reason'])


def portfolio(trades, days):
    daily = trades.groupby('day').net.sum().reindex(days, fill_value=0.)
    equity = np.r_[10000., 10000. + daily.cumsum().to_numpy()]
    return daily, dict(mornings=len(daily), funded_mornings=trades.day.nunique(), trades=len(trades),
                      net=float(daily.sum()), net_per_morning=float(daily.mean()), day_t=tstat(daily),
                      max_drawdown=float((np.maximum.accumulate(equity) - equity).max()),
                      minimum_start_equity=float(equity[:-1].min()),
                      fixed_allocation_funded=bool((equity[:-1] >= 10000).all()),
                      charges=float(trades.charges.sum()))


def main():
    OUT.mkdir(exist_ok=True)
    if (OUT / 'started.json').exists():
        raise RuntimeError('H20 was already scored; preserve the single registered run.')
    paths = [ROOT/'RUNBOOK.md', ROOT/'data/h17/cleaned_pools.csv', ROOT/'data/h17/coverage.csv', Path(__file__)]
    dump(OUT/'started.json', {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
    (OUT/'preregistration.md').write_text((ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split('## H20:')[1], encoding='utf-8')
    pools, n = pd.read_csv(paths[1]), nifty()
    result = dict(basis='Reused H17 daily-price allocation screen; not fresh confirmation or executable 09:20 evidence.', versions={})
    lines = ['# H20: more picks on panic mornings', '', result['basis'], '',
             'Pre-registered in RUNBOOK.md before returns. Every qualifying NIFTY morning is retained, including empty and unfundable ones. Rs10,000 fixed slots: 2 x 5,000; 3 x 3,333 (Rs1 cash); 4 x 2,500. H17 stopped fills, whole shares and exact per-leg charges. No refills or rule changes.', '',
             'Net per morning must be at least the two-pick baseline, day t at least 2.5, and drawdown at most 1.25 times baseline. These are descriptive screens on reused history; forward implementation still needs H16 execution evidence and review.', '']
    for label, threshold in [('strict', -.005), ('loose', -.003)]:
        days = list(n.index[n.gap < threshold])
        p = pools[pools.day.isin(days)]
        data, summaries = {}, {}
        lines += [f'## {label.title()}', '', '| Picks | Morning count | Net/morning Rs | Day t | Drawdown Rs | Change/morning vs 2 | Screen |', '|---|---:|---:|---:|---:|---:|---|']
        for k in (2, 3, 4):
            trades = account(p, k)
            daily, s = portfolio(trades, days)
            if k == 2:
                old = pd.read_csv(ROOT/f'data/h17/{label}_cleaned_stop3_account.csv')
                merged = trades.merge(old, on=['day', 'symbol'], validate='one_to_one', suffixes=('', '_h17'))
                assert len(merged) == len(trades) == len(old)
                for col in ('qty', 'entry', 'exit', 'charges', 'net'):
                    np.testing.assert_allclose(merged[col], merged[col+'_h17'], rtol=1e-12, atol=1e-9)
                baseline, base = daily, s
            s['delta_per_morning'] = float((daily - baseline).mean())
            s['paired_day_t'] = tstat(daily-baseline) if k != 2 else None
            s['checks'] = dict(mean=s['net_per_morning'] >= base['net_per_morning'],
                               day_t=s['day_t'] >= 2.5, drawdown=s['max_drawdown'] <= 1.25*base['max_drawdown'])
            s['screen_pass'] = all(s['checks'].values())
            s['additional_ranks'] = {str(rank): dict(trades=len(r), expectancy_rupees=float(r.net.mean()),
                                                     expectancy_bps=float(r.net_bps.mean()))
                                     for rank in (3, 4) if len(r := trades[trades['rank'] == rank])}
            trades.to_csv(OUT/f'{label}_{k}_trades.csv', index=False)
            data[str(k)] = daily
            summaries[str(k)] = s
            lines.append(f"| {k} | {len(days)} | {s['net_per_morning']:.2f} | {s['day_t']:.3f} | {s['max_drawdown']:.2f} | {s['delta_per_morning']:+.2f} | {'baseline' if k==2 else 'pass' if s['screen_pass'] else 'fail'} |")
        pd.DataFrame(data).rename_axis('day').to_csv(OUT/f'{label}_mornings.csv')
        for k in ('3', '4'):
            for rank, r in summaries[k]['additional_ranks'].items():
                lines += ['', f"{k}-pick portfolio, rank {rank}: {r['trades']} funded trades, own expectancy Rs{r['expectancy_rupees']:.2f} ({r['expectancy_bps']:.2f} net bps)."]
        result['versions'][label] = summaries
    lines += ['', '## Lesson and next step', '', 'Additional ranks can earn positive amounts yet dilute the Rs10,000 portfolio because every original pick gets less capital. They add correlated panic exposure, not more trading days. No runner is changed. H16 timestamped quotes, MIS/circuit evidence and delayed executable fills remain the next prerequisite. Daily-bar stops, locked entries, calendar vintages and same-open rankings retain H17 limitations.']
    dump(OUT/'summary.json', result)
    (ROOT/'docs/research/2026-11-01-h20-picks.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
