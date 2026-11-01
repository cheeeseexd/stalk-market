"""H18 frozen daily failed moves. Paper only: prepare power, then score once.

No broker/server/agent imports. Confirmation is gated by PRIMARY design only.
See data/h18/preregistration.md. All outputs are exclusive or hash-guarded.
"""
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stalk.config import DEFAULT_UNIVERSE
from stalk.market.costs import fill_cost

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/h18'
HOURS = (915, 1015, 1115, 1215, 1315, 1415)
RULES = ('failed', 'no_reversal', 'with_market')
STOPS = (.015, .025)
SLIP = {'next50': .0004, 'midcap': .0006, 'smallcap': .001}
PICK_COLUMNS = ['day', 'symbol', 'tier', 'industry', 'first', 'second', 'relative',
                'market', 'nifty_gap', 'side', 'entry_open', 'qty', 'rank', 'rule', 'phase']
TRADE_COLUMNS = PICK_COLUMNS + ['entry', 'exit', 'exit_hm', 'reason', 'gross', 'charges', 'net', 'net_return']


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(name, obj, exclusive=False):
    with (OUT / name).open('x' if exclusive else 'w', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, allow_nan=False)


def frame(bars):
    b = bars.copy().sort_values('ts')
    t = pd.to_datetime(b.ts, unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
    return b.assign(day=t.dt.strftime('%Y-%m-%d'), hm=t.dt.hour * 100 + t.dt.minute)


def fees(side, qty, entry, exit_price):
    first = 'BUY' if side == 'long' else 'SELL'
    last = 'SELL' if side == 'long' else 'BUY'
    # Frozen H14 schedule: NSE 0.00307% plus IPFT and GST, side-aware STT/stamp.
    return (fill_cost(first, qty, entry).total + fill_cost(last, qty, exit_price).total
            + qty * (entry + exit_price) * (.000001 + .01 / 1e7) * 1.18)


def select(features, rule):
    f = features.copy()
    long = f.relative <= -.02
    short = f.relative >= .02
    if rule != 'no_reversal':
        long &= f.second >= .005
        short &= f.second <= -.005
    if rule == 'with_market':
        long &= f.market > 0
        short &= f.market < 0
    f = f[long | short].copy()
    f['side'] = np.where(f.relative < 0, 'long', 'short')
    f['magnitude'] = f.relative.abs()
    f = f.sort_values(['day', 'magnitude', 'symbol'], ascending=[True, False, True])
    f['rank'] = f.groupby('day').cumcount() + 1
    return f[f['rank'] <= 2].copy(), f


def walk(pick, bars, stop_pct=.015, step=60):
    """Only pre-14:15 ranges; 14:15 open only. Gap-through stop is adverse."""
    b = frame(bars)
    b = b[(b.day == pick['day']) & (b.hm >= 1115) & (b.hm <= 1415)]
    start = pd.Timestamp(pick['day'] + ' 11:15', tz='Asia/Kolkata').timestamp()
    expected = np.arange(start, start + 3 * 3600 + 1, step * 60)
    if len(b) != len(expected) or not np.array_equal(b.ts.to_numpy(), expected):
        return None, 'missing_or_duplicate_path'
    pre = b.iloc[:-1]
    x = pre[['o', 'h', 'l', 'c']].to_numpy()
    if (not np.isfinite(x).all() or (x <= 0).any()
            or (pre.h < pre[['o', 'l', 'c']].max(axis=1)).any()
            or (pre.l > pre[['o', 'h', 'c']].min(axis=1)).any()
            or not np.isfinite(b.iloc[-1].o) or b.iloc[-1].o <= 0):
        return None, 'invalid_path'
    direction = 1 if pick['side'] == 'long' else -1
    slip = SLIP[pick['tier']]
    entry = float(b.iloc[0].o) * (1 + direction * slip)
    qty = int(5000 // entry)
    if qty < 1:
        return None, 'zero_shares'
    stop = entry * (1 - direction * stop_pct)
    exit_ref, hm, reason = float(b.iloc[-1].o), 1415, 'time'
    for r in pre.itertuples():
        hit = r.l <= stop if direction == 1 else r.h >= stop
        if hit:
            exit_ref = min(stop, r.o) if direction == 1 else max(stop, r.o)
            hm, reason = int(r.hm), 'stop'
            break
    else:
        if (exit_ref <= stop if direction == 1 else exit_ref >= stop):
            reason = 'stop_gap_at_exit'
    exit_price = exit_ref * (1 - direction * slip)
    gross = direction * (exit_price - entry) * qty
    charge = fees(pick['side'], qty, entry, exit_price)
    return {**{k: pick[k] for k in PICK_COLUMNS}, 'qty': qty, 'entry': entry,
            'exit': exit_price, 'exit_hm': hm, 'reason': reason, 'gross': gross,
            'charges': charge, 'net': gross - charge,
            'net_return': (gross - charge) / (qty * entry)}, None


def tstat(x):
    x = np.asarray(x, dtype=float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 1 and x.std(ddof=1) > 1e-12 else None


def stats(trades, days):
    daily = trades.groupby('day').net.sum()
    net = float(trades.net.sum())
    return {'trades': len(trades), 'days': len(daily), 'net': net,
            'net_bps': float(trades.net_return.mean() * 1e4) if len(trades) else None,
            'gross_bps': float((trades.gross / (trades.qty * trades.entry)).mean() * 1e4) if len(trades) else None,
            't_day': tstat(daily / 10000), 't_all_days': tstat(daily.reindex(days, fill_value=0.) / 10000),
            't_trade': tstat(trades.net_return), 'charges': float(trades.charges.sum()),
            'stops': int(trades.reason.str.startswith('stop').sum()),
            'best_day_share': float(daily.max() / net) if net > 0 else None,
            'annualized_days': 252 * len(daily) / len(days),
            'yearly_days': {str(k): int(v) for k, v in daily.groupby(daily.index.str[:4]).size().items()}}


def design_pass(s):
    return bool(s['net'] > 0 and s['t_day'] is not None and s['t_day'] >= 1.5)


def confirm_gates(s):
    return {'net_positive': s['net'] > 0, 'day_t_ge_2_5': s['t_day'] is not None and s['t_day'] >= 2.5,
            'trades_ge_100': s['trades'] >= 100,
            'best_day_le_25pct': s['best_day_share'] is not None and s['best_day_share'] <= .25}


def prepare():
    if (OUT / 'preparation_started.json').exists():
        raise RuntimeError('Preparation already attempted; preserve artifacts, no overwrite.')
    reg = json.loads((OUT / 'registration.json').read_text())
    assert reg['preregistration_sha256'] == sha(OUT / 'preregistration.md')
    dump('preparation_started.json', {'utc': now(), 'code': sha(__file__)}, True)
    inputs = {}

    def source(rel):
        p = ROOT / rel
        inputs[str(p.relative_to(ROOT))] = sha(p)
        return p

    bars = pd.read_pickle(source('data/wide_60m.pkl'))
    five = pd.read_pickle(source('data/wide_5m.pkl'))
    meta = pd.read_csv(source('data/wide_meta.csv')).set_index('symbol')
    events = pd.read_pickle(source('data/wide_events.pkl'))
    actions = pd.read_csv(source('data/h17/actions.csv'))
    source('data/h17/action_coverage.json')
    if not json.loads((ROOT / 'data/h17/action_coverage.json').read_text())['complete']:
        raise RuntimeError('Incomplete H17 action coverage')
    a = actions[actions.exclude.astype(str).str.lower() == 'true']
    blocked = set(zip(a.symbol, a.day)) | set(zip(a.api_symbol, a.day))
    for rel in ('data/h15/mismatches.csv', 'data/h15/action_days.csv'):
        flags = pd.read_csv(source(rel))
        blocked.update(zip(flags.symbol, flags.day))
    mask = (events.purpose.fillna('') + ' ' + events.detail.fillna('')).str.contains('result', case=False)
    for r in events[mask].itertuples():
        blocked.update((r.symbol, (pd.Timestamp(r.day) + pd.Timedelta(days=i)).strftime('%Y-%m-%d')) for i in range(-3, 4))
    index = frame(bars['NIFTY 50'])
    dates = sorted(index.day.unique())
    split = int(.6 * len(dates))
    design_dates, confirm_dates = dates[:split], dates[split:]
    phase_by_day = {d: 'design' if i < split else 'confirm' for i, d in enumerate(dates)}
    # Official opening references only: do not copy any same-day outcome fields.
    official = []
    coverage = []
    for day in dates:
        rel = f'data/bhav/{day}.pkl'
        if not (ROOT / rel).exists():
            rel = f'data/h9/bhav/{day}.pkl'
        if not (ROOT / rel).exists():
            coverage.append({'day': day, 'status': 'missing_official'})
            continue
        f = pd.read_pickle(source(rel))[['open', 'prev_close']].copy()
        f['day'] = day
        f['symbol'] = f.index
        official.append(f.reset_index(drop=True))
        coverage.append({'day': day, 'status': 'ok', 'rows': len(f)})
    official = pd.concat(official).rename(columns={'open': 'official_open', 'prev_close': 'official_prev'})
    ix = index.pivot(index='day', columns='hm', values='c').reindex(dates)
    io = index.pivot(index='day', columns='hm', values='o').reindex(dates)
    market = ix[1015] / io[915] - 1
    # Last close is a signal-time previous-session feature, never today's close.
    nifty_gap = io[915] / ix[1415].shift(1) - 1
    rows = []
    counts = Counter()
    for symbol in sorted(set(bars) & set(meta.index) - set(DEFAULT_UNIVERSE)):
        tier, industry = meta.loc[symbol, ['index', 'industry']]
        if tier not in SLIP or pd.isna(industry):
            counts['unmapped_symbol'] += 1
            continue
        b = frame(bars[symbol])
        if b.duplicated(['day', 'hm']).any():
            raise RuntimeError(f'Duplicate hourly timestamps: {symbol}')
        op = b.pivot(index='day', columns='hm', values='o').reindex(index=dates, columns=HOURS)
        cl = b.pivot(index='day', columns='hm', values='c').reindex(index=dates, columns=HOURS)
        vo = b.pivot(index='day', columns='hm', values='v').reindex(index=dates, columns=HOURS)
        complete = cl.notna().all(axis=1) & vo.notna().all(axis=1) & (vo >= 0).all(axis=1) & (cl > 0).all(axis=1)
        value = (cl * vo).sum(axis=1).where(complete).shift(1)
        f = pd.DataFrame({'day': dates, 'symbol': symbol, 'tier': tier, 'industry': industry,
                          'prev_value': value.to_numpy(), 'prev_close': cl[1415].shift(1).to_numpy(),
                          'open': op[915].to_numpy(), 'first': (cl[915] / op[915] - 1).to_numpy(),
                          'second': (cl[1015] / op[1015] - 1).to_numpy(), 'entry_open': op[1115].to_numpy(),
                          'market': market.to_numpy(), 'nifty_gap': nifty_gap.to_numpy()})
        rows.append(f)
    f = pd.concat(rows, ignore_index=True).merge(official, on=['day', 'symbol'], how='left', validate='many_to_one')
    f['known_block'] = [(s, d) in blocked for s, d in zip(f.symbol, f.day)]
    f['official_unknown'] = ~np.isfinite(f[['official_open', 'official_prev']]).all(axis=1) | (f[['official_open', 'official_prev']] <= 0).any(axis=1)
    f['hourly_mismatch'] = ((f.open / f.official_open - 1).abs() > .01) | ((f.prev_close / f.official_prev - 1).abs() > .01)
    f['liquid'] = (f.prev_value >= 1e8) & (f.prev_close >= 50)
    f['signal_valid'] = np.isfinite(f[['first', 'second', 'open']]).all(axis=1) & (f.open > 0)
    # Entry open is deliberately not an industry/relative-return eligibility filter.
    eligible = f.liquid & f.signal_valid & ~f.known_block & ~f.official_unknown & ~f.hourly_mismatch
    for col in ('known_block', 'official_unknown', 'hourly_mismatch', 'liquid', 'signal_valid'):
        counts[col] = int(f[col].sum())
    counts['stock_days'] = len(f)
    counts['eligible_stock_days'] = int(eligible.sum())
    f.to_csv(OUT / 'eligibility.csv', index=False)
    features = f[eligible].copy()
    features['industry_size'] = features.groupby(['day', 'industry']).symbol.transform('size')
    features['industry_median'] = features.groupby(['day', 'industry'])['first'].transform('median')
    features['relative'] = features['first'] - features.industry_median
    features['phase'] = features.day.map(phase_by_day)
    features.to_csv(OUT / 'features.csv', index=False)
    all_picks = []
    power = {}
    for rule in RULES:
        picks, candidates = select(features, rule)
        picks['rule'] = rule
        slipped = picks.entry_open * (1 + np.where(picks.side == 'long', 1, -1) * picks.tier.map(SLIP))
        picks['qty'] = np.floor(5000 / slipped.where(slipped > 0)).fillna(0).astype(int)
        all_picks.append(picks[PICK_COLUMNS])
        candidates.to_csv(OUT / f'{rule}_candidates.csv', index=False)
        for phase, ds in [('design', design_dates), ('confirm', confirm_dates)]:
            p = picks[picks.phase == phase]
            c = candidates[candidates.phase == phase]
            yearly = {}
            for year in sorted(set(d[:4] for d in ds)):
                yd = [d for d in ds if d.startswith(year)]
                py = p[p.day.str.startswith(year)]
                yearly[year] = {'sessions': len(yd), 'days': int(py.day.nunique()), 'picks': len(py),
                                'annualized_days': 252 * py.day.nunique() / len(yd)}
            power[f'{phase}_{rule}'] = {'sessions': len(ds), 'candidate_stocks': len(c),
                'candidate_days': int(c.day.nunique()), 'picks': len(p), 'days': int(p.day.nunique()),
                'long': int((p.side == 'long').sum()), 'short': int((p.side == 'short').sum()),
                'funded_at_entry': int((p.qty >= 1).sum()), 'missing_entry_open': int(p.entry_open.isna().sum()),
                'annualized_days': 252 * p.day.nunique() / len(ds), 'yearly': yearly,
                'nonpanic_days': int(p[p.nifty_gap >= -.005].day.nunique()),
                'panic_days': int(p[p.nifty_gap < -.005].day.nunique()),
                'unknown_gap_days': int(p[p.nifty_gap.isna()].day.nunique())}
    pd.concat(all_picks).to_csv(OUT / 'picks.csv', index=False)
    pd.DataFrame(coverage).to_csv(OUT / 'official_coverage.csv', index=False)
    five_dates = sorted(frame(five['NIFTY 50']).day.unique())
    obj = {'utc': now(), 'split': {'design': design_dates, 'confirm': confirm_dates},
           'power': power, 'coverage_counts': dict(counts), 'universe_symbols': len(rows),
           'industry_sizes': {str(k): int(v) for k, v in features.industry_size.value_counts().items()},
           'index_direction_unavailable_days': int(market.isna().sum()),
           'five_minute_design_overlap': sorted(set(design_dates) & set(five_dates)),
           'five_minute_range': [five_dates[0], five_dates[-1]],
           'manifest': inputs, 'code_sha256': sha(__file__), 'registration_sha256': sha(OUT / 'preregistration.md')}
    obj['prepared_artifacts'] = {p.name: sha(p) for p in OUT.glob('*.csv')}
    dump('power.json', obj, True)
    print(json.dumps({k: v for k, v in obj.items() if k not in ('manifest', 'prepared_artifacts', 'split', 'industry_sizes')}, indent=2))


def score_phase(phase, picks, bars, dates):
    results = {}
    for rule in RULES:
        p = picks[(picks.phase == phase) & (picks.rule == rule)]
        for stop in STOPS:
            label = f'{rule}_stop{stop*100:g}'
            trades, failures = [], []
            for row in p.to_dict('records'):
                trade, error = walk(row, bars[row['symbol']], stop)
                if error:
                    failures.append({'day': row['day'], 'symbol': row['symbol'], 'error': error})
                else:
                    trades.append(trade)
            t = pd.DataFrame(trades, columns=TRADE_COLUMNS)
            t.to_csv(OUT / f'{phase}_{label}_trades.csv', index=False)
            s = stats(t, dates)
            s['failures'] = failures
            s['sides'] = {side: stats(t[t.side == side], dates) for side in ('long', 'short')}
            s['yearly_net'] = {str(y): float(g.net.sum()) for y, g in t.groupby(t.day.str[:4])}
            results[label] = s
    dump(f'{phase}_results.json', results, True)
    return results


def score():
    power = json.loads((OUT / 'power.json').read_text())
    if sha(__file__) != power['code_sha256']:
        raise RuntimeError('Prepared scoring source changed; no silent revised score.')
    for rel, digest in power['manifest'].items():
        assert sha(ROOT / rel) == digest, rel
    for name, digest in power['prepared_artifacts'].items():
        assert sha(OUT / name) == digest, name
    assert sha(OUT / 'preregistration.md') == power['registration_sha256']
    dump('scoring_started.json', {'utc': now(), 'code_sha256': sha(__file__), 'power_sha256': sha(OUT / 'power.json')}, True)
    (OUT / 'scored_code.py').write_bytes(Path(__file__).read_bytes())
    bars = pd.read_pickle(ROOT / 'data/wide_60m.pkl')
    picks = pd.read_csv(OUT / 'picks.csv')
    design = score_phase('design', picks, bars, power['split']['design'])
    primary = design['failed_stop1.5']
    gate = design_pass(primary)
    summary = {'utc': now(), 'design_gate': gate, 'design': design, 'confirmation': 'NOT SCORED: primary design gate failed'}
    if gate:
        dump('confirmation_started.json', {'utc': now(), 'design_primary': primary}, True)
        confirm = score_phase('confirm', picks, bars, power['split']['confirm'])
        summary['confirmation'] = confirm
        summary['confirm_gates'] = confirm_gates(confirm['failed_stop1.5'])
        summary['numerical_confirm_pass'] = all(summary['confirm_gates'].values())
    # Only evaluate five-minute outcomes on authorized dates; never leak closed confirmation.
    authorized = power['split']['design'] + (power['split']['confirm'] if gate else [])
    overlap = set(authorized) & set(frame(pd.read_pickle(ROOT / 'data/wide_5m.pkl')['NIFTY 50']).day)
    if overlap:
        five = pd.read_pickle(ROOT / 'data/wide_5m.pkl')
        records = []
        for row in picks[(picks.rule == 'failed') & picks.day.isin(overlap)].to_dict('records'):
            for stop in STOPS:
                hourly, e1 = walk(row, bars[row['symbol']], stop)
                fine, e2 = walk(row, five.get(row['symbol'], pd.DataFrame(columns=['ts','o','h','l','c','v'])), stop, 5)
                records.append({'day': row['day'], 'symbol': row['symbol'], 'stop': stop,
                                'hourly_error': e1, 'five_error': e2,
                                'hourly_net': hourly['net'] if hourly else None,
                                'five_net': fine['net'] if fine else None})
        dump('timing_check.json', {'kind': 'timing/stop diagnostic only, not confirmation', 'records': records}, True)
    else:
        summary['timing_check'] = 'UNAVAILABLE: no design/five-minute overlap; reserved confirmation outcomes remain closed.'
    dump('summary.json', summary, True)
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'score'])
    args = parser.parse_args()
    prepare() if args.command == 'prepare' else score()
