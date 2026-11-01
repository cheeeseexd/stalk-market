"""H14: frozen, execution-aware no-news fade. Paper research, no broker imports.

Run --prepare (signal-time opportunity audit only), then --score exactly once.
The last six weeks are scored only if the primary design gate passes. See
data/h14/preregistration.md, including its pre-score metadata clarification.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_news import RESULT_CATEGORY, fees as h9_fees, is_material, publication, timestamp
from scripts.h9_fetch import bhav_path
from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST

ROOT = Path(BASE_DIR)
OUT = ROOT / 'data/h14'
H9 = ROOT / 'data/h9'
START, SPLIT, END = '2026-08-05', '2026-09-16', '2026-10-30'
THRESHOLDS, ENTRIES, STOPS = (.03, .02, .05), (920, 925, 930), (.02, .03)
SLIP = {'next50': .0004, 'midcap': .0006, 'smallcap': .0010}
PRIMARY = 'gap3_entry0920_stop2'
PICK_COLS = ['day', 'symbol', 'tier', 'prev_close', 'open', 'gap', 'side']
TRADE_COLS = [*PICK_COLS, 'qty', 'entry', 'exit', 'exit_hm', 'reason', 'gross',
              'charges', 'net', 'net_return', 'entry_slip_bps', 'entry_outside_bar',
              'entry_move_from_open_bps', 'entry_locked', 'entry_zero_volume', 'exit_locked']


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')


def exclusive(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, allow_nan=False)


def stamp(day, hm):
    return int(datetime.strptime(f'{day} {hm:04}', '%Y-%m-%d %H%M').replace(tzinfo=IST).timestamp())


def near(d1, d2):
    return abs((pd.Timestamp(d1) - pd.Timestamp(d2)).days) <= 3


def scheduled_near(events, day, cutoff):
    """Future meeting dates are usable only after their announcement timestamp."""
    return {symbol for symbol, event_day, announced in events
            if announced < cutoff and near(event_day, day)}


@dataclass(frozen=True)
class Candidate:
    day: str
    symbol: str
    tier: str
    prev_close: float
    open: float
    gap: float
    side: str


def pick(candidates, threshold):
    return sorted((c for c in candidates if abs(c.gap) > threshold),
                  key=lambda c: (-abs(c.gap), c.symbol))[:2]


def fees(side, qty, entry, exit_price):
    # H9 fixes shared NSE exchange rate locally; add published IPFT + GST.
    return h9_fees(side, qty, entry, exit_price) + qty * (entry + exit_price) * (0.01 / 1e7) * 1.18


def walk(c, bars, entry_hm=920, stop_pct=.02):
    """Require exact bars, causal post-entry stop checks and an exact 15:10 exit.

    bars: ndarray [ts,o,h,l,c,v]. No missing-candle/final-close fill invention.
    Returns (trade, failure). Prices remain unrounded for charge reconciliation.
    """
    if c.tier not in SLIP:
        return None, 'unknown_tier'
    bars = np.asarray(bars, dtype=float)
    if bars.ndim != 2 or bars.shape[1] != 6:
        return None, 'invalid_shape'
    begin, end = stamp(c.day, entry_hm), stamp(c.day, 1510)
    path = bars[(bars[:, 0] >= begin) & (bars[:, 0] <= end)]
    expected = np.arange(begin, end + 1, 300)
    if len(path) != len(expected) or not np.array_equal(path[:, 0], expected):
        return None, 'missing_or_duplicate_path'
    # Validate pre-exit OHLC, but at 15:10 only its OPEN is observable/used.
    p = path[:-1]
    if (not np.isfinite(p).all() or (p[:, 1:5] <= 0).any() or (p[:, 5] < 0).any()
            or (p[:, 2] < p[:, [1, 3, 4]].max(axis=1)).any()
            or (p[:, 3] > p[:, [1, 2, 4]].min(axis=1)).any()
            or not np.isfinite(path[-1, 1]) or path[-1, 1] <= 0):
        return None, 'invalid_path'
    direction = 1 if c.side == 'long' else -1
    slip = SLIP[c.tier]
    ref = float(path[0, 1])
    entry = ref * (1 + direction * slip)
    qty = int(5000 // entry)
    if qty < 1:
        return None, 'zero_shares'
    stop = entry * (1 - direction * stop_pct)
    exit_ref, exit_hm, reason, locked = float(path[-1, 1]), 1510, 'time', False
    for b in p:
        crossed = b[3] <= stop if direction == 1 else b[2] >= stop
        if crossed:
            exit_ref = min(stop, b[1]) if direction == 1 else max(stop, b[1])
            exit_hm = int(datetime.fromtimestamp(b[0], IST).strftime('%H%M'))
            reason, locked = 'stop', bool(b[2] == b[3] or b[5] == 0)
            break
    fill_out = exit_ref * (1 - direction * slip)
    charge = fees('BUY' if direction == 1 else 'SELL', qty, entry, fill_out)
    gross = direction * (fill_out - entry) * qty
    return {**asdict(c), 'qty': qty, 'entry': entry, 'exit': fill_out,
            'exit_hm': exit_hm, 'reason': reason, 'gross': gross, 'charges': charge,
            'net': gross - charge, 'net_return': (gross - charge) / (qty * entry),
            'entry_slip_bps': slip * 1e4,
            'entry_outside_bar': bool(entry < path[0, 3] or entry > path[0, 2]),
            'entry_move_from_open_bps': direction * (ref / c.open - 1) * 1e4,
            'entry_locked': bool(path[0, 2] == path[0, 3]),
            'entry_zero_volume': bool(path[0, 5] == 0),
            'exit_locked': locked}, None


def tstat(x):
    x = np.asarray(x, dtype=float)
    if len(x) < 2 or x.std(ddof=1) <= 1e-12:
        return None
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def stats(t, ordinary_days):
    daily = t.groupby('day').net.sum()
    net = float(t.net.sum())
    best = float(daily.max()) if len(daily) else 0.
    return {'trades': len(t), 'days': int(t.day.nunique()), 'net_bps': float(t.net_return.mean() * 1e4) if len(t) else None,
            'pnl': net, 't_trade': tstat(t.net_return), 't_day': tstat(daily / 10000),
            't_all_days': tstat(daily.reindex(ordinary_days, fill_value=0.) / 10000),
            'best_day': best, 'best_day_share': best / net if net > 0 else None,
            'stops': int((t.reason == 'stop').sum()), 'charges': float(t.charges.sum()),
            'gross_bps': float((t.gross / (t.qty * t.entry)).mean() * 1e4) if len(t) else None,
            'adverse_entry_move_bps': float(t.entry_move_from_open_bps.mean()) if len(t) else None,
            'outside_bar': int(t.entry_outside_bar.sum()), 'locked_stops': int(t.exit_locked.sum()),
            'locked_entries': int(t.entry_locked.sum()), 'zero_volume_entries': int(t.entry_zero_volume.sum())}


def design_pass(s):
    p = s['cells'][PRIMARY]
    return bool(p['pnl'] > 0 and p['t_day'] is not None and p['t_day'] >= 1.5)


def confirmation_gates(s):
    p = s['cells'][PRIMARY]
    return {'positive': p['pnl'] > 0, 'day_t_ge_2': p['t_day'] is not None and p['t_day'] >= 2.,
            'at_least_40_trades': p['trades'] >= 40,
            'best_day_le_25pct': p['best_day_share'] is not None and p['best_day_share'] <= .25,
            'complete_selected_paths': s['cells'][PRIMARY]['unscorable'] == 0,
            'complete_signal_coverage': s['unknown_days'] == 0}


def prepare():
    OUT.mkdir(exist_ok=True)
    if (OUT / 'opportunities.json').exists() or (OUT / 'scoring_started.json').exists():
        raise SystemExit('Preparation already frozen; do not overwrite it.')
    inputs = {}

    def readpath(p):
        inputs[str(p.relative_to(ROOT))] = sha(p)
        return p

    reg = OUT / 'preregistration.md'
    readpath(reg)
    readpath(Path(__file__).resolve())
    for helper in ('scripts/h9_news.py', 'scripts/h9_fetch.py', 'stalk/research/news.py',
                   'stalk/market/costs.py', 'stalk/config.py'):
        readpath(ROOT / helper)
    frames = pd.read_pickle(readpath(ROOT / 'data/wide_5m.pkl'))
    meta = pd.read_csv(readpath(ROOT / 'data/wide_meta.csv'))
    events = pd.read_pickle(readpath(ROOT / 'data/wide_events.pkl'))
    inv = json.loads(readpath(H9 / 'news_audit.json').read_text(encoding='utf-8'))
    excluded = set(inv['excluded_symbols']) | set(DEFAULT_UNIVERSE)
    tiers = {r.symbol: r.index for r in meta.itertuples(index=False)
             if r.symbol not in excluded and r.symbol in frames and r.index in SLIP}
    n = pd.read_csv(readpath(H9 / 'index_calendar.csv'), index_col=0)
    n = n.loc[START:END]
    days = list(n.index)
    if not days or days[0] != START or days[-1] != END:
        raise ValueError('Index calendar does not cover registered boundaries')
    first = {}
    audit = Counter()
    for symbol in ['NIFTY 50', *tiers]:
        f = frames[symbol]
        ts = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
        mask = (ts.dt.hour == 9) & (ts.dt.minute == 15)
        # Only 09:15 OHLC/volume is extracted; never derive hold-out later returns.
        z = f.loc[mask, ['ts', 'o', 'h', 'l', 'v']].copy()
        z['day'] = ts.loc[mask].dt.strftime('%Y-%m-%d').values
        duplicates = set(z.loc[z.day.duplicated(keep=False), 'day'])
        first[symbol] = {r.day: r for r in z.itertuples(index=False) if r.day not in duplicates}
        audit['duplicate_first_bars'] += len(duplicates)
    del frames
    event_rows = []
    invalid_events = []
    ev = events[(events.purpose.fillna('') + ' ' + events.detail.fillna('')).str.contains('result', case=False)]
    for r in ev.itertuples(index=False):
        try:
            ts = float(r.announced_ts)
            if not np.isfinite(ts) or ts <= 0:
                raise ValueError('invalid event timestamp')
            event_rows.append((r.symbol, r.day, ts))
        except (TypeError, ValueError):
            invalid_events.append({'symbol': r.symbol, 'day': r.day})
    covered, unknown, rows, response_hashes = set(), set(), [], {}
    for path in sorted((H9 / 'announcements').glob('*.json')):
        if path.stem[:10] > END or path.stem[11:] < '2026-07-31':
            continue
        obj = json.loads(readpath(path).read_text(encoding='utf-8'))
        m = obj['meta']
        dates = set(pd.date_range(m['start'], m['end']).strftime('%Y-%m-%d'))
        if m['status'] != 'ok':
            unknown.update(dates)
            continue
        if obj['rows'] and m['sha256'] in response_hashes:
            unknown.update(dates | response_hashes[m['sha256']])
        response_hashes[m['sha256']] = dates
        covered.update(dates)
        audit['announcement_windows'] += 1
        audit['raw_announcements'] += len(obj['rows'])
        seen = set()
        for row in obj['rows']:
            sym, ann, pub = str(row.get('symbol') or '').strip(), timestamp(row.get('an_dt')), publication(row)
            if not sym or ann is None or pub is None or ann.date().isoformat() not in dates:
                unknown.update(dates)
                audit['invalid_announcement_rows'] += 1
                continue
            cat, txt = str(row.get('desc') or ''), str(row.get('attchmntText') or '')
            key = (sym, pub, cat, txt)
            if key in seen:
                continue
            seen.add(key)
            if sym in tiers and (RESULT_CATEGORY.search(cat) or is_material(cat, txt)):
                rows.append((sym, pub, bool(RESULT_CATEGORY.search(cat)), is_material(cat, txt)))
    good = covered - unknown
    allpicks = {f'{g:g}': [] for g in THRESHOLDS}
    allcandidates, daily = [], []
    for day, nr in n.iterrows():
        record = {'day': day, 'phase': 'design' if day < SPLIT else 'holdout', 'status': 'ok'}
        prev = nr.prev_day
        needed = set(pd.date_range(prev, day).strftime('%Y-%m-%d'))
        if not needed <= good or any(near(day, e['day']) for e in invalid_events):
            record['status'] = 'unknown_news_or_events'
            daily.append(record)
            continue
        nf = first['NIFTY 50'].get(day)
        prev_nclose = float(n.loc[prev, 'close']) if prev in n.index else float(nr.open / (1 + nr.gap))
        if nf is None or not np.isfinite(prev_nclose) or prev_nclose <= 0:
            record['status'] = 'unknown_index'
            daily.append(record)
            continue
        record['nifty_gap'] = float(nf.o / prev_nclose - 1)
        if record['nifty_gap'] <= -.005:
            record['status'] = 'panic_excluded'
            daily.append(record)
            continue
        bp = bhav_path(prev)
        if not bp.exists():
            record['status'] = 'unknown_prev_close'
            daily.append(record)
            continue
        previous = pd.read_pickle(readpath(bp))
        low = datetime.fromisoformat(prev).replace(hour=15, minute=30, tzinfo=IST)
        cutoff = datetime.fromisoformat(day).replace(hour=9, minute=8, tzinfo=IST)
        decision = datetime.fromisoformat(day).replace(hour=9, minute=20, tzinfo=IST)
        result_names = scheduled_near(event_rows, day, cutoff.timestamp())
        fresh, stale = set(), set()
        for sym, pub, res, material in rows:
            if low < pub < cutoff and (res or material):
                fresh.add(sym)
            if cutoff <= pub < decision and (res or material):
                stale.add(sym)
            if res and pub < cutoff and 0 <= (pd.Timestamp(day).date() - pub.date()).days <= 3:
                result_names.add(sym)
        candidates = []
        counts = Counter()
        for symbol, tier in tiers.items():
            f = first[symbol].get(day)
            if f is None or symbol not in previous.index:
                counts['missing_first_or_predecessor'] += 1
                continue
            close = float(previous.loc[symbol, 'close'])
            vals = np.array([f.o, f.h, f.l, f.v, close], dtype=float)
            if (not np.isfinite(vals).all() or min(f.o, f.h, f.l, close) <= 0
                    or f.h < max(f.o, f.l) or f.l > min(f.o, f.h) or f.v < 0):
                counts['invalid_signal'] += 1
                continue
            if f.v == 0:
                counts['zero_opening_volume'] += 1
            if f.h == f.l:
                counts['locked_first_bar'] += 1
                continue
            if symbol in fresh:
                counts['fresh_news'] += 1
                continue
            if symbol in result_names:
                counts['results_near'] += 1
                continue
            gap = float(f.o / close - 1)
            c = Candidate(day, symbol, tier, close, float(f.o), gap, 'long' if gap < 0 else 'short')
            candidates.append(c)
            allcandidates.append({**asdict(c), 'cutoff_stale': symbol in stale})
        for g in THRESHOLDS:
            chosen = pick(candidates, g)
            allpicks[f'{g:g}'].extend(asdict(c) for c in chosen)
            record[f'eligible_{g:g}'] = sum(abs(c.gap) > g for c in candidates)
            record[f'picks_{g:g}'] = len(chosen)
        record.update(counts)
        daily.append(record)
    d = pd.DataFrame(daily)
    d.to_csv(OUT / 'opportunity_days.csv', index=False)
    pd.DataFrame(allcandidates).to_csv(OUT / 'signal_candidates.csv', index=False)
    summary = {'created_utc': now(), 'universe': len(tiers), 'audit': dict(audit), 'phases': {}}
    for phase in ('design', 'holdout'):
        ds = d[d.phase == phase]
        s = {'sessions': len(ds), 'ordinary_covered_days': int((ds.status == 'ok').sum()),
             'panic_days': int((ds.status == 'panic_excluded').sum()),
             'unknown_days': int((~ds.status.isin(['ok', 'panic_excluded'])).sum()), 'thresholds': {}}
        for g in THRESHOLDS:
            ps = [p for p in allpicks[f'{g:g}'] if (p['day'] < SPLIT) == (phase == 'design')]
            pd.DataFrame(ps, columns=PICK_COLS).to_csv(OUT / f'{phase}_gap{g*100:g}_picks.csv', index=False)
            s['thresholds'][f'{g:g}'] = {'candidate_stock_days': int(ds.get(f'eligible_{g:g}', pd.Series(dtype=float)).sum()),
                'candidate_days': int((ds.get(f'eligible_{g:g}', pd.Series(dtype=float)) > 0).sum()),
                'selected_trades': len(ps), 'selected_days': len({p['day'] for p in ps}),
                'long': sum(p['side'] == 'long' for p in ps), 'short': sum(p['side'] == 'short' for p in ps)}
        summary['phases'][phase] = s
    # Hash prepared selections and day audit too, so neither half can be edited later.
    for p in sorted(OUT.glob('*_picks.csv')):
        readpath(p)
    readpath(OUT / 'opportunity_days.csv')
    readpath(OUT / 'signal_candidates.csv')
    dump(OUT / 'input_manifest.json', {'frozen_utc': now(), 'sha256': inputs})
    (OUT / 'code_frozen.py').write_bytes(Path(__file__).read_bytes())
    dump(OUT / 'opportunities.json', summary)
    print(json.dumps(summary, indent=2), flush=True)


def verify_inputs():
    manifest = json.loads((OUT / 'input_manifest.json').read_text(encoding='utf-8'))
    for name, digest in manifest['sha256'].items():
        if sha(ROOT / name) != digest:
            raise SystemExit(f'Frozen input/code changed: {name}')


def phase(name):
    verify_inputs()
    opportunities = json.loads((OUT / 'opportunities.json').read_text(encoding='utf-8'))['phases'][name]
    d = pd.read_csv(OUT / 'opportunity_days.csv')
    ordinary = d.loc[(d.phase == name) & (d.status == 'ok'), 'day'].tolist()
    frames = pd.read_pickle(ROOT / 'data/wide_5m.pkl')
    paths = {}
    summary = {'created_utc': now(), 'phase': name, 'unknown_days': opportunities['unknown_days'], 'cells': {}}
    for g in THRESHOLDS:
        picks = pd.read_csv(OUT / f'{name}_gap{g*100:g}_picks.csv')
        for entry in ENTRIES:
            for stop in STOPS:
                key = f'gap{g*100:g}_entry{entry:04}_stop{stop*100:g}'
                trades, failures = [], []
                for r in picks.itertuples(index=False):
                    c = Candidate(**{k: getattr(r, k) for k in PICK_COLS})
                    pair = (c.symbol, c.day)
                    if pair not in paths:
                        f = frames[c.symbol]
                        # Slice this opened phase's selected day only. No hold-out
                        # path or P&L is constructed during the design phase.
                        z = f.loc[(f.ts >= stamp(c.day, 915)) & (f.ts <= stamp(c.day, 1510)), ['ts', 'o', 'h', 'l', 'c', 'v']]
                        paths[pair] = z.sort_values('ts').to_numpy(dtype=float)
                    trade, fail = walk(c, paths[pair], entry, stop)
                    if trade is not None:
                        trades.append(trade)
                    else:
                        failures.append({'day': c.day, 'symbol': c.symbol, 'reason': fail})
                t = pd.DataFrame(trades, columns=TRADE_COLS)
                t.to_csv(OUT / f'{name}_{key}_trades.csv', index=False)
                s = stats(t, ordinary)
                s['sides'] = {side: stats(t[t.side == side], ordinary) for side in ('long', 'short')}
                s['unscorable'], s['failures'] = len(failures), failures
                summary['cells'][key] = s
                print(name, key, json.dumps({k: s[k] for k in ('trades', 'days', 'net_bps', 'pnl', 't_day', 'unscorable')}), flush=True)
    dump(OUT / f'{name}_summary.json', summary)
    return summary


def score():
    verify_inputs()
    exclusive(OUT / 'scoring_started.json', {'started_utc': now(), 'code_sha256': sha(__file__)})
    design = phase('design')
    passed = design_pass(design)
    result = {'created_utc': now(), 'design_pass': passed, 'holdout_scored': False,
              'verdict': 'DESIGN_REJECTED', 'holdout_gates': None}
    if passed:
        verify_inputs()
        exclusive(OUT / 'holdout_started.json', {'started_utc': now(), 'prediction': 'Frozen primary may fail after executable fills; no tuning permitted.'})
        holdout = phase('holdout')
        gates = confirmation_gates(holdout)
        result.update(holdout_scored=True, holdout_gates=gates,
                      verdict='PAPER_SPEC_ELIGIBLE' if all(gates.values()) else 'HOLDOUT_REJECTED')
    dump(OUT / 'result.json', result)
    print(json.dumps(result, indent=2), flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument('--prepare', action='store_true')
    mode.add_argument('--score', action='store_true')
    args = p.parse_args()
    prepare() if args.prepare else score()


if __name__ == '__main__':
    main()
