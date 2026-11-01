"""H17: registered action-ex-date exclusions, frozen daily proxy; paper research only.

python scripts/h17_rebuild.py fetch
python scripts/h17_rebuild.py score
python scripts/h17_rebuild.py history
python scripts/h17_rebuild.py validate
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.panic_bhav import COST, UA
from scripts.panic_final import nifty, tstat
from scripts.h9_fetch import bhav_path
from scripts.panic_to_history import ETF, ts
from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST
from stalk.market.costs import round_trip_cost

ROOT = Path(BASE_DIR)
OUT = ROOT / 'data/h17'
URL = 'https://www.nseindia.com/api/corporates-corporateActions'
PROTECTED = ['stalk/sim/panic.py', 'stalk/agent.py', 'stalk/research/validate.py']
SOURCES = ['scripts/panic_bhav.py', 'scripts/panic_bhav_stress.py', 'scripts/panic_final.py',
           'scripts/panic_account.py', 'scripts/panic_to_history.py', 'scripts/h15_audit.py']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(name, obj):
    path = OUT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str, allow_nan=False), encoding='utf-8')


def snapshot():
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'registration.json').exists():
        return
    before = OUT / 'before'
    before.mkdir()
    for item in SOURCES + ['RUNBOOK.md', 'stalk/api/server.py', 'PLAYBOOK.md',
                           'data/panic_journal.csv', 'data/panic_bhav_trades.pkl',
                           'data/panic_final', 'data/panic_weeks']:
        src, dest = ROOT / item, before / item
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    with sqlite3.connect(ROOT / 'data/stalk.db') as source, sqlite3.connect(before / 'stalk.db') as dest:
        source.backup(dest)
    dump('registration.json', {'created_utc': datetime.now().astimezone().isoformat(),
         'protected': {p: sha(ROOT / p) for p in PROTECTED},
         'sources': {p: sha(ROOT / p) for p in SOURCES},
         'preregistration_sha256': sha(before / 'RUNBOOK.md'), 'scored': False})


def months():
    for start in pd.date_range('2016-11-01', '2026-10-01', freq='MS'):
        yield start, start + pd.offsets.MonthEnd(0)


def fetch_chunk(item):
    start, end = item
    name = f'actions/{start:%Y-%m-%d}_{end:%Y-%m-%d}'
    path = OUT / (name + '.json')
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))['meta']
    params = {'index': 'equities', 'from_date': start.strftime('%d-%m-%Y'), 'to_date': end.strftime('%d-%m-%Y')}
    meta = {'start': str(start.date()), 'end': str(end.date()), 'url': URL, 'params': params}
    with httpx.Client(headers=UA, timeout=35, follow_redirects=True) as c:
        for attempt in range(3):
            try:
                r = c.get(URL, params=params)
                (OUT / 'actions').mkdir(exist_ok=True)
                (OUT / (name + '.raw')).write_bytes(r.content)
                meta.update(http_status=r.status_code, sha256=hashlib.sha256(r.content).hexdigest(),
                            retrieved_utc=datetime.now().astimezone().isoformat())
                r.raise_for_status()
                rows = r.json()
                if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                    raise ValueError('Expected all-equity list of action records')
                for row in rows:
                    ex = pd.to_datetime(row.get('exDate'), format='%d-%b-%Y', errors='raise')
                    if not start <= ex <= end or not row.get('symbol'):
                        raise ValueError('Missing symbol or ex-date outside requested window')
                meta.update(status='ok', rows=len(rows))
                dump(name + '.json', {'meta': meta, 'rows': rows})
                return meta
            except (httpx.HTTPError, ValueError, TypeError) as e:
                meta.update(status='unknown', error=str(e))
                time.sleep(.5 * (attempt + 1))
    dump(name + '.error.json', meta)
    return meta


def purpose_kind(subject):
    text = str(subject).lower()
    for kind, pattern in [
        ('split', r'split|sub.?divi'), ('bonus', r'bonus'), ('rights', r'rights?'),
        ('consolidation', r'consolidat'),
        ('scheme', r'scheme|arrangement|demerger|de-merger|merger|amalgam|capital.*reduct|reduc.*capital|reconstruction'),
        ('dividend', r'dividend|distribution|return of capital'),
        ('other_basis', r'conversion|convert|redemption|forfeit|cancell|capital.*repay|repay.*capital'),
        ('non_price', r'buy.?back|annual general|extra.?ordinary general|\bagm\b|\begm\b|interest|general meeting')]:
        if re.search(pattern, text):
            return kind
    return 'unknown_conservative'


def publication(value):
    if value is None or str(value).strip() in ('', '-', 'NA', 'null'):
        return None, 'unknown_assume_advance'
    text = str(value).strip()
    # Date-only fields cannot establish the pre-09:15 cutoff.
    if ':' not in text:
        for fmt in ('%d-%b-%Y', '%d-%m-%Y', '%Y-%m-%d'):
            try:
                datetime.strptime(text, fmt)
                return None, 'unknown_assume_advance'
            except ValueError:
                pass
        return None, 'invalid'
    for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S',
                '%d-%b-%Y %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=IST), 'known'
        except ValueError:
            pass
    return None, 'invalid'


def symbol_changes():
    path = OUT / 'symbolchange.csv.raw'
    if not path.exists():
        with httpx.Client(headers=UA, timeout=35, follow_redirects=True) as c:
            r = c.get('https://nsearchives.nseindia.com/content/equities/symbolchange.csv')
            r.raise_for_status()
            path.write_bytes(r.content)
    frame = pd.read_csv(path)
    frame.columns = frame.columns.str.strip()
    for col in ('OLD SYMBOL', 'NEW SYMBOL'):
        frame[col] = frame[col].str.strip()
    frame['effective'] = pd.to_datetime(frame['EFFECTIVE DATE'], format='%d-%b-%Y', errors='raise').dt.strftime('%Y-%m-%d')
    frame = frame.drop_duplicates(['OLD SYMBOL', 'NEW SYMBOL', 'effective'])
    no_op = int((frame['OLD SYMBOL'] == frame['NEW SYMBOL']).sum())
    frame = frame[frame['OLD SYMBOL'] != frame['NEW SYMBOL']]
    if frame.duplicated(['NEW SYMBOL', 'effective']).any():
        raise ValueError('Ambiguous symbol-change predecessor')
    dump('symbol_change_source.json', {'url': 'https://nsearchives.nseindia.com/content/equities/symbolchange.csv',
        'sha256': sha(path), 'rows': len(frame), 'ignored_self_renames': no_op,
        'mapping': 'Reverse dated rename chains to ex-date symbol; no price inference.'})
    return frame.sort_values('effective', ascending=False).to_dict('records')


def historical_symbol(symbol, day, changes):
    links = []
    for row in changes:
        if row['NEW SYMBOL'] == symbol and day < row['effective']:
            links.append(f"{row['OLD SYMBOL']}->{symbol}@{row['effective']}")
            symbol = row['OLD SYMBOL']
    return symbol, ' | '.join(links)


def normalize():
    records, coverage = [], []
    renames = symbol_changes()
    for start, end in months():
        path = OUT / f'actions/{start:%Y-%m-%d}_{end:%Y-%m-%d}.json'
        if not path.exists():
            coverage.append({'start': str(start.date()), 'status': 'missing'})
            continue
        obj = json.loads(path.read_text(encoding='utf-8'))
        coverage.append(obj['meta'])
        assert obj['meta']['sha256'] == sha(path.with_suffix('.raw'))
        for r in obj['rows']:
            day = datetime.strptime(r['exDate'], '%d-%b-%Y').strftime('%Y-%m-%d')
            kind = purpose_kind(r.get('subject'))
            published, timing = publication(r.get('caBroadcastDate'))
            cutoff = datetime.strptime(day + ' 09:15', '%Y-%m-%d %H:%M').replace(tzinfo=IST)
            available = timing == 'unknown_assume_advance' or (published is not None and published < cutoff)
            symbol, links = historical_symbol(r['symbol'].strip(), day, renames)
            records.append({'symbol': symbol, 'api_symbol': r['symbol'].strip(), 'rename_links': links, 'day': day, 'kind': kind,
                'subject': r.get('subject'), 'series': r.get('series'), 'isin': r.get('isin'),
                'record_date': r.get('recDate'), 'publication_raw': r.get('caBroadcastDate'),
                'publication': published.isoformat() if published else None, 'timing': timing,
                'exclude': kind != 'non_price' and available, 'source': str(path.relative_to(ROOT))})
    frame = pd.DataFrame(records).drop_duplicates(['symbol', 'day', 'subject', 'series', 'publication_raw'])
    frame.to_csv(OUT / 'actions.csv', index=False)
    frame.groupby(['kind', 'subject'], dropna=False).size().rename('rows').to_csv(OUT / 'purposes.csv')
    complete = len(coverage) == 120 and all(x['status'] == 'ok' for x in coverage) and not (frame.timing == 'invalid').any()
    summary = {'chunks': len(coverage), 'ok_chunks': sum(x['status'] == 'ok' for x in coverage),
        'rows': len(frame), 'symbols': frame.symbol.nunique(), 'kinds': frame.kind.value_counts().to_dict(),
        'timing': frame.timing.value_counts().to_dict(), 'excluded_symbol_days': len(frame[frame.exclude][['symbol', 'day']].drop_duplicates()),
        'known_late_rows': int(((frame.timing == 'known') & ~frame.exclude & (frame.kind != 'non_price')).sum()),
        'renamed_rows': int((frame.symbol != frame.api_symbol).sum()),
        'complete': bool(complete), 'coverage': coverage,
        'limitation': 'Endpoint coverage, not independent certification of all historical notices or their original vintages.'}
    dump('action_coverage.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'coverage'}, indent=2), flush=True)
    return frame, summary


def fetch():
    snapshot()
    with ThreadPoolExecutor(max_workers=3) as workers:
        for i, meta in enumerate(workers.map(fetch_chunk, months()), 1):
            print(f"{i}/120 {meta['start']} {meta['status']} {meta.get('rows', '-')} rows", flush=True)
    normalize()


def get_pool(day, prev_day, inputs):
    paths = [bhav_path(d) for d in (day, prev_day)]
    calendar = ROOT / f'data/bhav/bm_{day}.pkl'
    if not all(p.exists() for p in paths) or not calendar.exists():
        return None, 'missing_input'
    inputs.update(paths + [calendar])
    today, prev = [pd.read_pickle(p) for p in paths]
    near = pd.read_pickle(calendar)
    if today.empty or prev.empty:
        return None, 'empty_bhav'
    if not isinstance(near, (list, tuple, set)):
        return None, 'unknown_results'
    p = today.join(prev[['value']].rename(columns={'value': 'prev_value'}), how='inner')
    p = p[(p.prev_value >= 1e8) & (p.prev_close >= 50) & ~p.index.isin(DEFAULT_UNIVERSE) & ~p.index.isin(near)].copy()
    valid = np.isfinite(p[['open', 'high', 'low', 'close', 'prev_close']]).all(axis=1)
    valid &= (p[['open', 'high', 'low', 'close', 'prev_close']] > 0).all(axis=1)
    if not valid.all():
        raise ValueError(f'{day}: invalid eligible OHLC; do not silently score')
    p = p.rename_axis('symbol').reset_index()
    p['day'] = day
    p['gap'] = p.open / p.prev_close - 1
    p['locked'] = (p.open == p.high) & (p.high == p.low)
    p['headline'] = p.close / p.open - 1 - COST
    p['no_stop'] = p.headline - .0008
    p['stop3'] = np.where(p.low <= p.open * .97, -.032, p.close / p.open - 1 - .0008) - COST
    return p.sort_values(['gap', 'symbol'], kind='stable'), 'ok'


def account(picks, stop=True):
    rows = []
    for r in picks.itertuples():
        entry = r.open * 1.001
        qty = int(5000 // entry)
        if qty <= 0:
            continue
        stopped = bool(stop and r.low <= entry * .97)
        exit_px = (entry * .97 * .998 if stopped else r.close * .9992) * .999
        gross = (exit_px - entry) * qty
        charges = round_trip_cost(qty, entry, exit_px)
        rows.append({'day': r.day, 'symbol': r.symbol, 'gap': r.gap, 'qty': qty,
            'entry': entry, 'exit': exit_px, 'gross': gross, 'charges': charges,
            'net': gross - charges, 'net_return': (gross - charges) / (qty * entry),
            'stopped': stopped, 'locked': r.locked, 'entry_outside_range': entry > r.high})
    return pd.DataFrame(rows, columns=['day', 'symbol', 'gap', 'qty', 'entry', 'exit', 'gross',
        'charges', 'net', 'net_return', 'stopped', 'locked', 'entry_outside_range'])


def clean_pool(stock, day, action_map):
    """Action-day removal is causal metadata filtering BEFORE rank truncation."""
    excluded = pd.Series([(s, day) in action_map for s in stock.symbol], index=stock.index)
    return stock[~excluded & (stock.gap < -.02)], stock[excluded].copy()


def random_control(pools, col):
    rng = np.random.default_rng(7004)
    totals, count = np.zeros(2000), 0
    for p in pools:
        x = p[col].to_numpy()
        k = min(2, len(x))
        if k:
            draws = np.array([rng.choice(len(x), size=k, replace=False) for _ in range(2000)])
            totals += x[draws].sum(axis=1)
            count += k
    return totals / count


def streak(values):
    longest = current = 0
    for x in values:
        current = current + 1 if x < 0 else 0
        longest = max(longest, current)
    return longest


def account_stats(trades, market_days):
    active = trades.groupby('day').net.sum()
    daily = active.reindex(market_days, fill_value=0)
    equity = np.r_[10000., 10000 + daily.cumsum().to_numpy()]
    weekly = trades.assign(week=pd.to_datetime(trades.day).dt.strftime('%G-W%V')).groupby('week').net.sum()
    annual = trades.groupby(trades.day.str[:4]).net.sum().reindex([str(y) for y in range(2016, 2027)], fill_value=0)
    return {'trades': len(trades), 'mornings': len(active), 'market_mornings': len(daily),
        'net': float(trades.net.sum()), 'annual_mean': float(trades.net.sum() / 10),
        'yearly': annual.to_dict(), 'max_drawdown': float((np.maximum.accumulate(equity) - equity).max()),
        'losing_streak_active': streak(active), 'losing_streak_with_zeros': streak(daily),
        'weekly_win_share': float((weekly > 0).mean()), 'active_weeks': len(weekly),
        'positive_weeks': int((weekly > 0).sum()), 'calendar_weeks': 522,
        'weekly_win_share_all': float((weekly > 0).sum() / 522),
        'best_day': float(daily.max()), 'worst_day': float(daily.min()),
        'green_share_active': float((active > 0).mean()), 'average_active': float(active.mean()),
        'min_start_equity': float(equity[:-1].min()), 'fixed_allocations_fully_funded': bool((equity[:-1] >= 10000).all()),
        'locked_trades': int(trades.locked.sum()), 'entries_above_daily_high': int(trades.entry_outside_range.sum()),
        'charges': float(trades.charges.sum())}


def diff_rows(old, new, action_map, name):
    joined = old.merge(new, on=['day', 'symbol'], how='outer', suffixes=('_old', '_new'), indicator=True)
    changes = joined[joined._merge != 'both'].copy()
    changes['change'] = changes._merge.map({'left_only': 'removed', 'right_only': 'added', 'both': 'retained'}).astype(str)
    changes['action'] = [action_map.get((r.symbol, r.day), '') for r in changes.itertuples()]
    changes.to_csv(OUT / name, index=False)
    return changes


def score():
    snapshot()
    if (OUT / 'scoring_started.json').exists():
        raise RuntimeError('Score already started; retain original artifacts, do not silently rerun')
    actions, ca = normalize()
    if not ca['complete']:
        raise RuntimeError('Incomplete action coverage; cannot score unconditional reconstruction')
    action_map = actions[actions.exclude].groupby(['symbol', 'day']).subject.agg(' | '.join).to_dict()
    funds = set(json.loads((ROOT / 'data/h9/instrument_inventory.json').read_text())['etf_or_fund_symbols'])
    inputs = {ROOT / 'data/h9/instrument_inventory.json', ROOT / 'data/wide_1d_10y.pkl', OUT / 'symbolchange.csv.raw'}
    n = nifty()
    wanted = n[n.gap < -.003]
    pools, coverage, removals = {}, [], []
    for day, row in wanted.iterrows():
        p, status = get_pool(day, row.prev_day, inputs)
        coverage.append({'day': day, 'prev_day': row.prev_day, 'nifty_gap': row.gap, 'status': status})
        if p is None:
            continue
        fund_mask = p.symbol.isin(funds) | p.symbol.str.contains(ETF, regex=True)
        stock = p[~fund_mask].copy()
        cleaned, excluded = clean_pool(stock, day, action_map)
        excluded['subject'] = [action_map[(s, day)] for s in excluded.symbol]
        removals.append(excluded)
        pools[day] = {'original': p[p.gap < -.02], 'stock_baseline': stock[stock.gap < -.02],
                      'cleaned': cleaned}
    pd.DataFrame(coverage).to_csv(OUT / 'coverage.csv', index=False)
    pd.concat(removals, ignore_index=True).to_csv(OUT / 'excluded_pool_rows.csv', index=False)
    if any(x['status'] != 'ok' for x in coverage):
        raise RuntimeError('Missing price/calendar inputs; resolve before scoring')
    # Save full candidate identities/rankings and freeze hashes BEFORE first return aggregation.
    for mode in ('original', 'stock_baseline', 'cleaned'):
        pd.concat([p[mode] for p in pools.values()], ignore_index=True).to_csv(OUT / f'{mode}_pools.csv', index=False)
    inputs.update((OUT / 'actions').glob('*.raw'))
    dump('input_manifest.json', {str(p.relative_to(ROOT)): sha(p) for p in sorted(inputs)})
    dump('scoring_started.json', {'created_utc': datetime.now().astimezone().isoformat(),
         'code_sha256': sha(Path(__file__)), 'input_manifest_sha256': sha(OUT / 'input_manifest.json')})
    shutil.copy2(Path(__file__), OUT / 'scored_code.py')
    shutil.copy2(ROOT / 'RUNBOOK.md', OUT / 'preregistration.md')
    results = {}
    for label, threshold in [('strict', -.005), ('loose', -.003)]:
        days = list(n.index[n.gap < threshold])
        results[label] = {}
        selected = {}
        for mode in ('original', 'stock_baseline', 'cleaned'):
            ps = [pools[d][mode] for d in days]
            picks = pd.concat([p.head(2) for p in ps], ignore_index=True)
            selected[mode] = picks
            picks.to_csv(OUT / f'{label}_{mode}_selected.csv', index=False)
            item = {'trades': len(picks), 'mornings': picks.day.nunique(), 'market_mornings': len(days),
                    'headline_bps': float(picks.headline.mean() * 1e4)}
            for col in ('no_stop', 'stop3'):
                x = picks[col]
                random = random_control(ps, col)
                np.save(OUT / f'{label}_{mode}_{col}_random.npy', random)
                year_means = picks.groupby(picks.day.str[:4])[col].mean().reindex([str(y) for y in range(2016, 2027)], fill_value=0)
                item[col] = {'net_bps': float(x.mean() * 1e4), 't_trade': tstat(x),
                    't_day': tstat(picks.groupby('day')[col].mean()), 'years_positive': int((year_means > 0).sum()),
                    'yearly_bps': (year_means * 1e4).to_dict(), 'win': float((x > 0).mean()),
                    'random_percentile': float((random < x.mean()).mean() * 100),
                    'random_p5_p50_p95_bps': (np.percentile(random, [5, 50, 95]) * 1e4).tolist(),
                    'random_upper_p': float((1 + (random >= x.mean()).sum()) / 2001)}
                a = account(picks, stop=col == 'stop3')
                a.to_csv(OUT / f'{label}_{mode}_{col}_account.csv', index=False)
                item[col]['account'] = account_stats(a, days)
            results[label][mode] = item
        for source in ('original', 'stock_baseline'):
            diff_rows(selected[source], selected['cleaned'], action_map, f'{label}_{source}_selection_changes.csv')
            # Original dashboard filtered ETFs AFTER picking using its narrower historical regex.
            old = account(selected[source])
            if source == 'original':
                old = old[~old.symbol.str.contains(ETF, regex=True)]
            old.to_csv(OUT / f'{label}_{source}_history_account.csv', index=False)
            diff_rows(old, account(selected['cleaned']), action_map, f'{label}_{source}_account_changes.csv')
        original_history = pd.read_csv(OUT / f'{label}_original_history_account.csv')
        results[label]['original_history'] = account_stats(original_history, days)
    # Historical strict selections and both legacy account totals must reproduce, no inferred correction.
    old = pd.read_csv(ROOT / 'data/panic_final/selected.csv')
    rebuilt = pd.read_csv(OUT / 'strict_original_selected.csv')
    check = old.merge(rebuilt, on=['day', 'symbol'], how='outer', suffixes=('_saved', '_rebuilt'), indicator=True)
    assert len(check) == len(old) and (check._merge == 'both').all()
    assert np.allclose(check['net'], check.stop3)
    legacy = json.loads((ROOT / 'data/panic_weeks/summary.json').read_text())
    for label, key in [('strict', 'frozen'), ('loose', 'loose')]:
        saved = pd.read_csv(ROOT / ('data/panic_journal.csv' if label == 'strict' else 'data/panic_weeks/loose_trades.csv'))
        saved = saved[~saved.symbol.str.contains(ETF, regex=True)]
        new = pd.read_csv(OUT / f'{label}_original_history_account.csv')
        paired = saved.merge(new, on=['day', 'symbol'], how='outer', suffixes=('_saved', '_rebuilt'), indicator=True)
        assert (paired._merge == 'both').all() and (paired.net_saved-paired.net_rebuilt).abs().max() < .0051
        assert abs(new.net.sum() - legacy[key]['net']) < .1
    strict = results['strict']['cleaned']
    gates = {}
    for col in ('no_stop', 'stop3'):
        s = strict[col]
        gates[col] = {'positive_net': s['net_bps'] > 0, 'day_t_at_least_2_5': s['t_day'] >= 2.5,
                      'eight_positive_years': s['years_positive'] >= 8,
                      'beats_random_p95': s['net_bps'] > s['random_p5_p50_p95_bps'][2]}
    dump('summary.json', {'status': 'PASS' if all(gates['stop3'].values()) else 'FAIL',
        'action_coverage_complete': ca['complete'], 'covered_mornings': len(wanted),
        'gates': gates, 'results': results})
    print(json.dumps({'gates': gates, 'strict_cleaned': strict, 'loose_cleaned': results['loose']['cleaned']}, indent=2), flush=True)


def finalize_saved():
    """Finish summaries from saved outputs after a verifier-only column-name fix.

    No candidate reconstruction, account execution or random resampling here.
    """
    scored = json.loads((OUT / 'scoring_started.json').read_text())
    assert sha(OUT / 'scored_code.py') == scored['code_sha256']
    artifact_paths = sorted(OUT.glob('*_selected.csv')) + sorted(OUT.glob('*_account.csv')) + sorted(OUT.glob('*_random.npy'))
    dump('finalization_code.json', {'code_sha256': sha(Path(__file__)),
         'reason': 'Original H7d net column is unsuffixed when rebuilt frame has stop3, not net; fix verifier only.',
         'frozen_output_hashes': {p.name: sha(p) for p in artifact_paths}})
    n = nifty()
    results = {}
    for label, threshold in [('strict', -.005), ('loose', -.003)]:
        days = list(n.index[n.gap < threshold])
        results[label] = {}
        for mode in ('original', 'stock_baseline', 'cleaned'):
            picks = pd.read_csv(OUT / f'{label}_{mode}_selected.csv')
            item = {'trades': len(picks), 'mornings': picks.day.nunique(), 'market_mornings': len(days),
                    'headline_bps': float(picks.headline.mean()*1e4)}
            for col in ('no_stop', 'stop3'):
                x = picks[col]
                random = np.load(OUT / f'{label}_{mode}_{col}_random.npy')
                years = picks.groupby(picks.day.str[:4])[col].mean().reindex([str(y) for y in range(2016, 2027)], fill_value=0)
                a = pd.read_csv(OUT / f'{label}_{mode}_{col}_account.csv')
                item[col] = {'net_bps': float(x.mean()*1e4), 't_trade': tstat(x),
                    't_day': tstat(picks.groupby('day')[col].mean()), 'years_positive': int((years>0).sum()),
                    'yearly_bps': (years*1e4).to_dict(), 'win': float((x>0).mean()),
                    'random_percentile': float((random<x.mean()).mean()*100),
                    'random_p5_p50_p95_bps': (np.percentile(random, [5,50,95])*1e4).tolist(),
                    'random_upper_p': float((1+(random>=x.mean()).sum())/2001),
                    'account': account_stats(a, days)}
            results[label][mode] = item
        original = pd.read_csv(OUT / f'{label}_original_history_account.csv')
        results[label]['original_history'] = account_stats(original, days)
        saved = pd.read_csv(ROOT / ('data/panic_journal.csv' if label == 'strict' else 'data/panic_weeks/loose_trades.csv'))
        saved = saved[~saved.symbol.str.contains(ETF, regex=True)]
        joined = saved.merge(original, on=['day','symbol'], how='outer', suffixes=('_saved','_rebuilt'), indicator=True)
        assert (joined._merge == 'both').all() and (joined.net_saved-joined.net_rebuilt).abs().max()<.0051
    old = pd.read_csv(ROOT / 'data/panic_final/selected.csv')
    rebuilt = pd.read_csv(OUT / 'strict_original_selected.csv')
    check = old.merge(rebuilt, on=['day','symbol'], how='outer', indicator=True)
    assert len(check)==len(old) and (check._merge=='both').all() and np.allclose(check['net'], check.stop3)
    gates = {}
    for col in ('no_stop','stop3'):
        b = results['strict']['cleaned'][col]
        gates[col] = {'positive_net': b['net_bps']>0, 'day_t_at_least_2_5': b['t_day']>=2.5,
                      'eight_positive_years': b['years_positive']>=8,
                      'beats_random_p95': b['net_bps']>b['random_p5_p50_p95_bps'][2]}
    dump('summary.json', {'status': 'PASS' if all(gates['stop3'].values()) else 'FAIL',
        'action_coverage_complete': True, 'covered_mornings': int((n.gap<-.003).sum()), 'gates': gates, 'results': results})
    print('Saved-output finalization complete; no outcomes rerun or resampled', flush=True)


def history():
    from stalk.data.store import Store
    result = json.loads((OUT / 'summary.json').read_text())
    st = Store()
    with sqlite3.connect(OUT / 'before/stalk.db') as original:
        changed = [cid for cid, raw in original.execute('SELECT id,summary FROM campaigns')
                   if json.loads(raw).get('label', '').startswith('Panic buy, 10 years')]
    try:
        for campaign in st.campaigns(10000):
            s, settings = campaign['summary'], campaign['settings']
            label = s.get('label', '')
            if label.startswith('Panic buy, 10 years') and 'splits removed' not in label and 'original, unadjusted' not in label:
                s['label'] = label + ' (original, unadjusted)'
                settings['label'] = s['label']
                s['note'] = s.get('note', '') + '; Original unadjusted corporate-action basis; retained for audit, superseded by H17.'
                st._exec('UPDATE campaigns SET settings=?, summary=? WHERE id=?', (json.dumps(settings), json.dumps(s), campaign['id']))
                if campaign['id'] not in changed:
                    changed.append(campaign['id'])
        for rule in ('strict', 'loose'):
            cid = 'h17_panic_10y_' + rule
            acct = cid + ':panic_buy'
            t = pd.read_csv(OUT / f'{rule}_cleaned_stop3_account.csv')
            st.delete_sessions(acct)
            days, curve, equity, peak, dd = [], [], 10000., 10000., 0.
            for day, g in t.groupby('day', sort=True):
                start = equity
                equity += g.net.sum()
                peak = max(peak, equity)
                dd = max(dd, peak-equity)
                d = {'day': day, 'start_equity': round(start, 2), 'end_equity': round(equity, 2),
                     'gross': round(g.gross.sum(), 2), 'costs': round(g.charges.sum(), 2), 'net': round(g.net.sum(), 2),
                     'trades': len(g), 'wins': int((g.net > 0).sum()), 'losses': int((g.net <= 0).sum())}
                days.append(d)
                curve.append([day, round(equity, 2)])
                st.save_session(acct + ':' + day, 'panic_buy', day, acct, {'daily_proxy': True}, d, ended=True)
                for r in g.itertuples():
                    st.save_trade({'id': acct + ':' + day + ':' + r.symbol, 'session_id': acct + ':' + day,
                        'day': day, 'strategy': 'panic_buy', 'symbol': r.symbol, 'side': 'long', 'qty': int(r.qty),
                        'entry_ts': ts(day, '09:15'), 'entry_price': r.entry, 'exit_ts': None if r.stopped else ts(day, '15:30'),
                        'exit_price': r.exit, 'stop_initial': r.entry*.97, 'gross': r.gross, 'costs': r.charges, 'net': r.net,
                        'reason': 'daily-low stop proxy; time unknown' if r.stopped else 'official-close minus 8bps proxy',
                        'fee_version': 'zerodha-mis-2026-09', 'broker': 'backtest'})
            a = result['results'][rule]['cleaned']['stop3']['account']
            yearly = t.groupby(t.day.str[:4]).net.sum()
            tax = float(yearly.clip(lower=0).sum() * .3)
            account_summary = {'name': 'panic_buy', 'strategies': ['panic_buy'], 'require_validated': False,
                'start': 10000., 'end': round(equity, 2), 'gross': round(t.gross.sum(), 2), 'costs': round(t.charges.sum(), 2),
                'net': round(t.net.sum(), 2), 'tax': round(tax, 2), 'after_tax': round(t.net.sum()-tax, 2), 'trades': len(t),
                'green_days': int(sum(d['net'] > 0 for d in days)), 'red_days': int(sum(d['net'] < 0 for d in days)),
                'flat_days': int(sum(d['net'] == 0 for d in days)), 'max_dd': round(dd, 2),
                'stats': {'n': len(t), 'win': round(float((t.net > 0).mean()), 3), 'net': round(t.net.sum(), 2),
                          't': round(tstat(t.groupby('day').net.sum()), 2)},
                'per_strategy': {}, 'days': days, 'curve': curve}
            label = 'Panic buy, 10 years (splits removed)' + (' -0.3%' if rule == 'loose' else '')
            summary = {'id': cid, 'first_day': days[0]['day'], 'last_day': days[-1]['day'], 'days': len(days),
                'capital': 10000., 'accounts': {'panic_buy': account_summary}, 'weeks': [], 'kind': 'panic_buy',
                'label': label, 'note': 'H17: all price-effect action ex-dates and ETFs excluded BEFORE ranking. Official daily proxy, not measured 09:20 fills; stop time unknown. Reused history; Claude review pending.',
                'research_status': result['status'], 'daily_proxy': True}
            st.save_campaign(cid, days[0]['day'], days[-1]['day'], {'bar_minutes': 5, 'label': label, 'daily_proxy': True}, summary)
        dump('history_update.json', {'archived_campaigns': changed, 'new_campaigns': ['h17_panic_10y_strict', 'h17_panic_10y_loose']})
    finally:
        st._conn.close()


def validate():
    reg = json.loads((OUT / 'registration.json').read_text())
    assert all(sha(ROOT / p) == value for p, value in reg['protected'].items())
    manifest = json.loads((OUT / 'input_manifest.json').read_text())
    assert all(sha(ROOT / p) == value for p, value in manifest.items())
    scored = json.loads((OUT / 'scoring_started.json').read_text())
    assert scored['code_sha256'] == sha(OUT / 'scored_code.py'), 'Frozen scorer snapshot changed'
    if scored['code_sha256'] != sha(Path(__file__)):
        fix = json.loads((OUT / 'finalization_code.json').read_text())
        assert fix['code_sha256'] == sha(Path(__file__))
        assert all(sha(OUT / p) == digest for p, digest in fix['frozen_output_hashes'].items())
    result = json.loads((OUT / 'summary.json').read_text())
    ca = pd.read_csv(OUT / 'actions.csv')
    keys = set(zip(ca[ca.exclude].symbol, ca[ca.exclude].day))
    for rule in ('strict', 'loose'):
        pool = pd.read_csv(OUT / 'cleaned_pools.csv')
        selected = pd.read_csv(OUT / f'{rule}_cleaned_selected.csv')
        n = nifty()
        days = n.index[n.gap < (-.005 if rule == 'strict' else -.003)]
        ranked = pool[pool.day.isin(days)].sort_values(['day', 'gap', 'symbol']).groupby('day').head(2)
        assert set(zip(ranked.day, ranked.symbol)) == set(zip(selected.day, selected.symbol))
        assert not any((r.symbol, r.day) in keys for r in selected.itertuples())
        for col, stop in [('no_stop', False), ('stop3', True)]:
            a = pd.read_csv(OUT / f'{rule}_cleaned_{col}_account.csv')
            recomputed = account(selected, stop)
            assert np.allclose(a.net, recomputed.net)
            assert ((a.qty * a.entry) <= 5000).all()
            assert a.groupby('day').size().max() <= 2
            assert abs(a.net.sum() - result['results'][rule]['cleaned'][col]['account']['net']) < 1e-8
    with sqlite3.connect(ROOT / 'data/stalk.db') as now, sqlite3.connect(OUT / 'before/stalk.db') as old:
        archived = json.loads((OUT / 'history_update.json').read_text())['archived_campaigns']
        before_campaigns = old.execute('SELECT * FROM campaigns ORDER BY id').fetchall()
        for row in before_campaigns:
            new = now.execute('SELECT * FROM campaigns WHERE id=?', (row[0],)).fetchone()
            if row[0] not in archived:
                assert new == row
        for table in ('sessions', 'trades', 'logs'):
            original = old.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            current = now.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            assert current[:len(original)] == original, f'Existing {table} changed'
        for rule in ('strict', 'loose'):
            a = pd.read_csv(OUT / f'{rule}_cleaned_stop3_account.csv')
            rows = now.execute('SELECT day,symbol,qty,net FROM trades WHERE session_id LIKE ? ORDER BY day,symbol',
                               ('h17_panic_10y_' + rule + ':%',)).fetchall()
            assert len(rows) == len(a)
            assert abs(sum(r[3] for r in rows)-a.net.sum()) < 1e-8
    dump('validation.json', {'protected_hashes': 'unchanged', 'input_hashes': len(manifest),
         'rankings_and_accounts': 'reconciled', 'existing_sessions_trades_logs': 'unchanged',
         'other_campaigns': 'unchanged', 'status': 'ok'})
    print('H17 artifact, protected-source and History validation: PASS', flush=True)


if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['fetch', 'normalize', 'score', 'finalize', 'history', 'validate'])
    mode = p.parse_args().mode
    {'fetch': fetch, 'normalize': normalize, 'score': score, 'finalize': finalize_saved, 'history': history, 'validate': validate}[mode]()
