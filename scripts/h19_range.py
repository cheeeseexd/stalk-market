"""H19 paper-only opportunity counts. Reused dates NEVER authorize returns."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/h19'
HOURS = (915, 1015, 1115, 1215, 1315, 1415)
SLIP = {'next50': .0004, 'midcap': .0006, 'smallcap': .001}
ELIGIBILITY_COLUMNS = ['day', 'symbol', 'tier', 'prev_value', 'prev_close',
                       'known_block', 'official_unknown', 'hourly_mismatch',
                       'liquid', 'nifty_gap']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(name, obj):
    with (OUT / name).open('x', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, allow_nan=False)


def frame(bars):
    b = bars.copy().sort_values('ts')
    t = pd.to_datetime(b.ts, unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
    return b.assign(day=t.dt.strftime('%Y-%m-%d'), hm=t.dt.hour * 100 + t.dt.minute)


def classify(o, h, l, c, v, prior_low, prior_high):
    """Only the completed decision candle and past official boundaries matter."""
    values = np.asarray([o, h, l, c, v, prior_low, prior_high], dtype=float)
    if (not np.isfinite(values).all() or min(o, h, l, c, prior_low) <= 0
            or v < 0 or prior_low >= prior_high or h < max(o, l, c)
            or l > min(o, h, c)):
        return None, None, 'invalid_signal_or_reference'
    below, above = l < prior_low, h > prior_high
    if below and above:
        return None, None, 'both_boundaries'
    if not prior_low < c < prior_high:
        return None, None, 'close_not_inside'
    if below:
        return 'long', (prior_low - l) / prior_low, 'candidate'
    if above:
        return 'short', (h - prior_high) / prior_high, 'candidate'
    return None, None, 'no_sweep'


def reference_ok(low, high, hourly_low, hourly_high, complete):
    vals = np.asarray([low, high, hourly_low, hourly_high], dtype=float)
    return bool(complete and np.isfinite(vals).all() and (vals > 0).all()
                and low < high and hourly_low <= hourly_high
                and abs(hourly_low / low - 1) <= .01
                and abs(hourly_high / high - 1) <= .01)


def select(candidates):
    c = candidates.sort_values(['day', 'sweep', 'symbol'], ascending=[True, False, True]).copy()
    c['rank'] = c.groupby('day').cumcount() + 1
    return c[c['rank'] <= 2].copy()


def entry_open(bars, day):
    """Read only exact 11:20 timestamp/open, never entry candle outcomes."""
    ts = pd.Timestamp(day + ' 11:20', tz='Asia/Kolkata').timestamp()
    b = bars.loc[bars.ts == ts, ['ts', 'o']]
    if len(b) != 1:
        return None, 'missing_entry' if b.empty else 'duplicate_entry'
    price = float(b.iloc[0].o)
    if not np.isfinite(price) or price <= 0:
        return None, 'invalid_entry'
    return price, 'available'


def quantity(price, side, tier):
    if price is None or not np.isfinite(price) or price <= 0:
        return 0
    slipped = price * (1 + (1 if side == 'long' else -1) * SLIP[tier])
    return int(5000 // slipped)


def confirmation_allowed(net, day_t, *, fresh, complete, trades, days):
    """Numerical success cannot turn reused or incomplete prices into evidence."""
    return bool(fresh and complete and trades >= 100 and days >= 40
                and np.isfinite(net) and net > 0 and day_t is not None
                and np.isfinite(day_t) and day_t >= 1.5)


def count(picks, dates):
    def subset(p):
        return {'picks': len(p), 'days': int(p.day.nunique()),
                'long': int((p.side == 'long').sum()), 'short': int((p.side == 'short').sum()),
                'days_per_252': 252 * p.day.nunique() / len(dates)}
    return {'sessions': len(dates), **subset(picks),
            'signal_close_affordability_PROXY': subset(picks[picks.proxy_qty > 0]),
            'exact_entry_available': subset(picks[picks.entry_status == 'available']),
            'exact_entry_fundable': subset(picks[picks.entry_qty > 0]),
            'entry_statuses': {str(k): int(v) for k, v in picks.entry_status.value_counts().items()},
            'nonpanic_proxy_days': int(picks[picks.nifty_gap >= -.005].day.nunique()),
            'panic_proxy_days': int(picks[picks.nifty_gap < -.005].day.nunique()),
            'unknown_gap_days': int(picks[picks.nifty_gap.isna()].day.nunique()),
            'yearly': {y: {'sessions': sum(d.startswith(y) for d in dates),
                            'picks': len(g), 'days': int(g.day.nunique()),
                            'long': int((g.side == 'long').sum()),
                            'short': int((g.side == 'short').sum())}
                       for y, g in picks.groupby(picks.day.str[:4])}}


def prepare():
    reg = json.loads((OUT / 'registration.json').read_text(encoding='utf-8'))
    assert reg['preregistration_sha256'] == sha(OUT / 'preregistration.md')
    assert (OUT / 'preregistration.md').read_text(encoding='utf-8') in (ROOT / 'RUNBOOK.md').read_text(encoding='utf-8')
    dump('preparation_started.json', {'utc': datetime.now(timezone.utc).isoformat(), 'code_sha256': sha(__file__)})
    (OUT / 'prepared_code.py').write_bytes(Path(__file__).read_bytes())
    manifest = {}

    def source(rel):
        path = ROOT / rel
        manifest[rel] = sha(path)
        return path

    old = json.loads(source('data/h18/power.json').read_text(encoding='utf-8'))
    for rel, digest in old['manifest'].items():
        assert sha(source(rel)) == digest, rel
    eligibility_path = source('data/h18/eligibility.csv')
    assert sha(eligibility_path) == old['prepared_artifacts']['eligibility.csv']
    elig = pd.read_csv(eligibility_path, usecols=ELIGIBILITY_COLUMNS)
    bars = pd.read_pickle(source('data/wide_60m.pkl'))
    five = pd.read_pickle(source('data/wide_5m.pkl'))
    dates = sorted(frame(bars['NIFTY 50']).day.unique())
    split = int(.6 * len(dates))
    phases = {d: 'design' if i < split else 'reserved' for i, d in enumerate(dates)}
    refs, coverage = [], []
    # Each reference is labelled for the FOLLOWING session. Never today's range.
    for prior, day in zip(dates[:-1], dates[1:]):
        rel = f'data/bhav/{prior}.pkl'
        if not (ROOT / rel).exists():
            rel = f'data/h9/bhav/{prior}.pkl'
        if not (ROOT / rel).exists():
            coverage.append({'day': day, 'prior_day': prior, 'status': 'missing'})
            continue
        f = pd.read_pickle(source(rel))[['high', 'low']].copy()
        f['symbol'], f['day'], f['prior_day'] = f.index, day, prior
        refs.append(f.reset_index(drop=True))
        coverage.append({'day': day, 'prior_day': prior, 'status': 'available'})
    refs = pd.concat(refs, ignore_index=True).rename(columns={'high': 'prior_high', 'low': 'prior_low'})
    rows = []
    duplicate_count = 0
    for symbol in sorted(elig.symbol.unique()):
        b = frame(bars[symbol])
        duplicate = b.duplicated(['day', 'hm'], keep=False)
        duplicate_count += int(duplicate.sum())
        b = b[~duplicate]
        piv = {k: b.pivot(index='day', columns='hm', values=k).reindex(index=dates, columns=HOURS)
               for k in ('o', 'h', 'l', 'c', 'v')}
        valid = pd.DataFrame(True, index=dates, columns=HOURS)
        for k in piv:
            valid &= np.isfinite(piv[k]) & (piv[k] >= 0 if k == 'v' else piv[k] > 0)
        valid &= (piv['h'] >= piv['o']) & (piv['h'] >= piv['c']) & (piv['h'] >= piv['l'])
        valid &= (piv['l'] <= piv['o']) & (piv['l'] <= piv['c'])
        f = pd.DataFrame({'day': dates, 'symbol': symbol,
                          'prior_hourly_low': piv['l'].min(axis=1).shift(1).to_numpy(),
                          'prior_hourly_high': piv['h'].max(axis=1).shift(1).to_numpy(),
                          'prior_complete': valid.all(axis=1).shift(1, fill_value=False).to_numpy()})
        for k in piv:
            f[k] = piv[k][1015].to_numpy()
        rows.append(f)
    f = elig.merge(pd.concat(rows), on=['day', 'symbol'], validate='one_to_one')
    f = f.merge(refs, on=['day', 'symbol'], how='left', validate='many_to_one')
    f['base_eligible'] = f.liquid & ~f.known_block & ~f.official_unknown & ~f.hourly_mismatch
    f['reference_valid'] = [reference_ok(r.prior_low, r.prior_high, r.prior_hourly_low,
                                       r.prior_hourly_high, r.prior_complete) for r in f.itertuples()]
    status = [classify(r.o, r.h, r.l, r.c, r.v, r.prior_low, r.prior_high)
              if r.base_eligible and r.reference_valid else (None, None, 'ineligible')
              for r in f.itertuples()]
    f['side'], f['sweep'], f['signal_status'] = zip(*status)
    f['phase'] = f.day.map(phases)
    f.to_csv(OUT / 'eligibility.csv', index=False, encoding='utf-8')
    candidates = f[f.signal_status == 'candidate'].copy()
    candidates.to_csv(OUT / 'candidates.csv', index=False, encoding='utf-8')
    picks = select(candidates)
    entry_rows = []
    empty = pd.DataFrame(columns=['ts', 'o'])
    for r in picks.itertuples():
        price, state = entry_open(five.get(r.symbol, empty), r.day)
        entry_rows.append((price, state, quantity(price, r.side, r.tier), quantity(r.c, r.side, r.tier)))
    picks['entry_open'], picks['entry_status'], picks['entry_qty'], picks['proxy_qty'] = zip(*entry_rows)
    picks.to_csv(OUT / 'picks.csv', index=False, encoding='utf-8')
    pd.DataFrame(coverage).to_csv(OUT / 'reference_coverage.csv', index=False, encoding='utf-8')
    power = {}
    for phase, ds in [('design', dates[:split]), ('reserved', dates[split:])]:
        p = picks[picks.phase == phase]
        c = candidates[candidates.phase == phase]
        power[phase] = {**count(p, ds), 'candidate_stock_days': len(c), 'candidate_days': int(c.day.nunique()),
                        'candidate_long': int((c.side == 'long').sum()),
                        'candidate_short': int((c.side == 'short').sum())}
    power['all'] = count(picks, dates)
    five_dates = sorted(frame(five['NIFTY 50'][['ts', 'o']]).day.unique())
    obj = {'utc': datetime.now(timezone.utc).isoformat(), 'power': power,
           'split': {'design': dates[:split], 'reserved': dates[split:]},
           'five_minute_range': [five_dates[0], five_dates[-1]],
           'five_minute_design_dates': sorted(set(dates[:split]) & set(five_dates)),
           'coverage': {'stock_days': len(f), 'base_eligible': int(f.base_eligible.sum()),
                        'valid_reference': int(f.reference_valid.sum()),
                        'joint_eligible': int((f.base_eligible & f.reference_valid).sum()),
                        'duplicate_hourly_rows': duplicate_count,
                        'signal_statuses': dict(Counter(f.signal_status)),
                        'exclusions_overlapping': {col: int(f[col].sum()) for col in
                                                  ('known_block', 'official_unknown', 'hourly_mismatch', 'liquid')}},
           'design_returns': 'NOT SCORED: reused dates are power only; no design five-minute entries',
           'confirmation_returns': 'NOT SCORED: no authorized passing design; fresh H16 evidence required',
           'complete_path_power': 'UNKNOWN: no post-entry outcomes inspected',
           'confirmation_hurdle': 2.5, 'manifest': manifest,
           'registration_sha256': sha(OUT / 'preregistration.md'), 'code_sha256': sha(__file__),
           'prepared_artifacts': {p.name: sha(p) for p in OUT.glob('*.csv')}}
    dump('power.json', obj)
    print(json.dumps({k: v for k, v in obj.items() if k not in ('manifest', 'prepared_artifacts', 'split')}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare'])
    parser.parse_args()
    prepare()
