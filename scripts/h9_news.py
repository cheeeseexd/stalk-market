"""Pre-registered H9 daily proxy. Run fetch helper first, then this script once.

No broker, credentials, agent, server, engine holdout or frozen strategy imports.
Raw confirmation inputs are not return-scored unless the primary design passes.
"""
from __future__ import annotations

import bisect
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys
import io
import zipfile

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_fetch import OUT, announcement_path, bhav_path, calendar, dump, windows
from stalk.config import DEFAULT_UNIVERSE, IST
from stalk.market.costs import fill_cost
from stalk.research.news import classify

ETF_SYMBOL = re.compile(r'BEES$|IETF$|^MON100$|^AXISGOLD$|^GOLDADD$|ETF', re.I)
ETF_NAME = re.compile(r'\bETF\b|exchange[ -]traded|mutual fund', re.I)
RESULT_CATEGORY = re.compile(r'result|board\s*meeting|integrated.*financial|financial.*integrated', re.I)
THRESHOLDS = (.03, .02, .05)
TRADE_COLUMNS = ['day', 'symbol', 'side', 'gap', 'qty', 'entry', 'exit', 'gross', 'charges',
                 'net', 'net_return', 'stop', 'locked', 'entry_outside_range']


def timestamp(value):
    if not value or value in ('-', 'NA', 'null'):
        return None
    for fmt in ('%d-%b-%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%d-%m-%Y %H:%M:%S'):
        try:
            return datetime.strptime(str(value).strip(), fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    return None


def publication(row):
    """Dissemination, when present, takes precedence over receipt/announcement."""
    value = row.get('exchdisstime')
    if value and value not in ('-', 'NA', 'null'):
        return timestamp(value)  # A malformed dissemination value cannot fall back to earlier receipt.
    return timestamp(row.get('an_dt'))


def is_material(category, text):
    return bool(not RESULT_CATEGORY.search(category) and classify(category, text)[0])


def fresh_day(ts, days, cutoffs, lower):
    # Strict upper boundary: a filing exactly at 09:08 is NOT available.
    pos = bisect.bisect_right(cutoffs, ts)
    return days[pos] if pos < len(days) and lower[pos] < ts < cutoffs[pos] else None


def instrument_inventory():
    """Read only security identity fields from historical raw files, never price returns."""
    etfs = set(pd.read_csv(OUT / 'etf.csv')['Symbol'])
    # Already identified as an ETF in the H8 result, before this H9 study.
    etfs.add('KOTAKNIFTY')
    equities, sources = set(), 0
    for path in sorted((OUT / 'raw_bhav').glob('*.zip')):
        with zipfile.ZipFile(path) as archive:
            raw = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])),
                              usecols=lambda c: c.strip() in ('SYMBOL', 'SERIES', 'TckrSymb', 'SctySrs', 'ISIN'))
        raw.columns = raw.columns.str.strip()
        symbol = 'TckrSymb' if 'TckrSymb' in raw else 'SYMBOL'
        series = 'SctySrs' if 'SctySrs' in raw else 'SERIES'
        if 'ISIN' not in raw:
            continue
        raw = raw[raw[series].astype(str).str.strip() == 'EQ']
        isin = raw.ISIN.astype(str).str.strip()
        etfs.update(raw.loc[isin.str.startswith('INF'), symbol].astype(str).str.strip())
        equities.update(raw.loc[isin.str.startswith('INE'), symbol].astype(str).str.strip())
        sources += 1
    dump(OUT / 'instrument_inventory.json', {'etf_or_fund_symbols': sorted(etfs),
         'ordinary_equity_symbols': sorted(equities), 'historical_raw_files': sources,
         'rule': 'current NSE ETF list + historical EQ ISIN prefix INF + pre-known H8 ETF KOTAKNIFTY; augmented with announcement identities',
         'limitation': 'shared legacy bhav frames omit ISIN; delisted identifiers only seen in those frames may remain uncertain'})
    return etfs


def news_index(n):
    days = list(n.index)
    upper = [datetime.fromisoformat(d).replace(hour=9, minute=8, tzinfo=IST) for d in days]
    lower = [datetime.fromisoformat(p).replace(hour=15, minute=30, tzinfo=IST) for p in n.prev_day]
    news, results = defaultdict(set), defaultdict(set)
    covered, unknown, etfs = set(), set(), instrument_inventory()
    counts, examples, daily_rows = Counter(), [], Counter()
    hashes = {}
    source_years = set()
    fresh_audit = []
    for start, end in windows():
        path = announcement_path(start, end)
        requested_dates = set(pd.date_range(start, end).strftime('%Y-%m-%d'))
        if not path.exists():
            unknown.update(requested_dates)
            counts['missing_windows'] += 1
            continue
        obj = json.loads(path.read_text(encoding='utf-8'))
        if obj['meta']['status'] != 'ok':
            unknown.update(requested_dates)
            continue
        rows = obj['rows']
        counts['windows'] += 1
        counts['raw_rows'] += len(rows)
        covered.update(requested_dates)
        digest = obj['meta']['sha256']
        if rows and digest in hashes:
            unknown.update(requested_dates | hashes[digest])
            counts['repeated_nonempty_responses'] += 1
        hashes[digest] = requested_dates
        seen = set()
        for row in rows:
            ann, pub = timestamp(row.get('an_dt')), publication(row)
            symbol = str(row.get('symbol') or '').strip()
            category = str(row.get('desc') or '').strip()
            text = str(row.get('attchmntText') or '')
            name, isin = str(row.get('sm_name') or ''), str(row.get('sm_isin') or '')
            if ETF_SYMBOL.search(symbol) or ETF_NAME.search(name) or isin.startswith('INF'):
                etfs.add(symbol)
            if ann is None or pub is None or not symbol:
                counts['invalid_timestamp_or_symbol'] += 1
                unknown.update(requested_dates)
                if len(examples) < 20:
                    examples.append({'window': [start, end], 'symbol': symbol,
                                     'an_dt': row.get('an_dt'), 'exchdisstime': row.get('exchdisstime')})
                continue
            if ann.strftime('%Y-%m-%d') not in requested_dates:
                counts['out_of_requested_range'] += 1
                unknown.update(requested_dates)
                continue
            source_years.add(ann.year)
            daily_rows[ann.strftime('%Y-%m-%d')] += 1
            key = (row.get('seq_id'), symbol, pub, category, text)
            if key in seen:
                counts['duplicates'] += 1
                continue
            seen.add(key)
            day = fresh_day(pub, days, upper, lower)
            if day is None:
                continue
            counts['fresh_rows'] += 1
            fresh_audit.append({'day': day, 'symbol': symbol, 'published_at': pub.isoformat(),
                                'received_at': ann.isoformat(), 'category': category,
                                'text': text, 'material_h9': is_material(category, text),
                                'results_or_board': bool(RESULT_CATEGORY.search(category)),
                                'seq_id': row.get('seq_id'), 'source_cache': path.name})
            if RESULT_CATEGORY.search(category):
                results[day].add(symbol)
                counts['fresh_results_or_board_rows'] += 1
            elif is_material(category, text):
                news[day].add(symbol)
                counts['fresh_material_rows'] += 1
    good = covered - unknown
    pd.DataFrame(fresh_audit).to_csv(OUT / 'fresh_filings.csv', index=False)
    coverage = {}
    for d, r in n.iterrows():
        needed = set(pd.date_range(r.prev_day, d).strftime('%Y-%m-%d'))
        coverage[d] = needed <= good
    dump(OUT / 'news_audit.json', {'counts': dict(counts), 'invalid_examples': examples,
                                  'earliest_source_year': min(source_years) if source_years else None,
                                  'source_years': sorted(source_years), 'excluded_symbols': sorted(etfs),
                                  'covered_calendar_dates': len(good), 'unknown_calendar_dates': sorted(unknown),
                                  'daily_announcement_counts': dict(sorted(daily_rows.items()))})
    pd.DataFrame([{'day': d, 'announcements_known': ok, 'fresh_material_symbols': len(news[d]),
                   'fresh_results_symbols': len(results[d])} for d, ok in coverage.items()]).to_csv(
                       OUT / 'news_day_coverage.csv', index=False)
    return news, results, etfs, coverage, sorted(y for y in source_years if 2017 <= y <= 2026)


def fees(side, qty, entry, exit_price):
    other = 'SELL' if side == 'BUY' else 'BUY'
    repo = fill_cost(side, qty, entry).total + fill_cost(other, qty, exit_price).total
    # Current published exchange fee 0.00307%, vs shared model's 0.00297%.
    return repo + qty * (entry + exit_price) * .000001 * 1.18


def execute(row, direction, worse=0.):
    entry = float(row['open']) * (1 + direction * worse) * (1 + direction * .001)
    qty = int(5000 // entry)
    if qty < 1:
        return None
    stop_px = entry * (1 - direction * .03)
    stopped = row['low'] <= stop_px if direction == 1 else row['high'] >= stop_px
    # Signed 8bp earlier-exit debit for BOTH directions, before exit slippage.
    exit_ref = stop_px if stopped else float(row['close']) - direction * entry * .0008
    exit_price = exit_ref * (1 - direction * .001)
    charge = fees('BUY' if direction == 1 else 'SELL', qty, entry, exit_price)
    gross = direction * (exit_price - entry) * qty
    net = gross - charge
    return {'day': row['day'], 'symbol': row['symbol'], 'side': 'long' if direction == 1 else 'short',
            'gap': float(row['gap']), 'qty': qty, 'entry': entry, 'exit': exit_price,
            'gross': gross, 'charges': charge, 'net': net, 'net_return': net / (qty * entry),
            'stop': bool(stopped), 'locked': bool(row['open'] == row['high'] == row['low']),
            'entry_outside_range': bool(entry < row['low'] or entry > row['high'])}


def tstat(values):
    x = np.asarray(values, dtype=float)
    if len(x) < 2 or x.std(ddof=1) == 0:
        return None
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def stats(t, covered_days):
    daily = t.groupby('day')['net'].sum()
    net = float(t.net.sum())
    best = float(daily.max()) if len(daily) else 0.
    return {'trades': len(t), 'active_days': int(t.day.nunique()), 'covered_ordinary_days': len(covered_days),
            'net_bps': float(t.net_return.mean() * 1e4) if len(t) else None,
            'pnl': net, 't_trade': tstat(t.net_return), 't_day': tstat(daily / 10000),
            't_all_ordinary_days': tstat(daily.reindex(covered_days, fill_value=0.) / 10000),
            'best_day': best, 'best_day_share': best / net if net > 0 else None,
            'wins': int((t.net > 0).sum()), 'stops': int(t.stop.sum()), 'locks': int(t.locked.sum()),
            'entry_outside_range': int(t.entry_outside_range.sum())}


def eligible_pool(day, prev_day, etfs, blocked):
    paths = [bhav_path(d) for d in (day, prev_day)]
    if not all(p.exists() for p in paths):
        return None, {'status': 'missing_bhav'}
    today, prev = [pd.read_pickle(p) for p in paths]
    if today.empty or prev.empty:
        return None, {'status': 'empty_bhav'}
    if today.index.has_duplicates or prev.index.has_duplicates:
        return None, {'status': 'duplicate_symbol'}
    u = today.join(prev[['value']].rename(columns={'value': 'prev_value'}), how='inner')
    u = u[(u.prev_value >= 1e8) & (u.prev_close >= 50) & ~u.index.isin(DEFAULT_UNIVERSE)].copy()
    instrument_excluded = u.index.isin(etfs) | u.index.to_series().str.contains(ETF_SYMBOL).values
    etf_count = int(instrument_excluded.sum())
    u = u[~instrument_excluded & ~u.index.isin(blocked)]
    cols = ['open', 'high', 'low', 'close', 'prev_close']
    valid = np.isfinite(u[cols]).all(axis=1) & (u[cols] > 0).all(axis=1)
    valid &= (u.high >= u[['open', 'low', 'close']].max(axis=1))
    valid &= (u.low <= u[['open', 'high', 'close']].min(axis=1))
    invalid = int((~valid).sum())
    u = u[valid].rename_axis('symbol').reset_index()
    u['gap'] = u.open / u.prev_close - 1
    u['abs_gap'], u['day'] = u.gap.abs(), day
    return u.sort_values(['abs_gap', 'symbol'], ascending=[False, True], kind='stable'), {
        'status': 'ok', 'invalid_prices': invalid, 'etf_excluded': etf_count, 'pool': len(u)}


def phase(name, years, n, news, results, etfs, ann_known):
    dates = n[n.index.str[:4].astype(int).isin(years)]
    rows = defaultdict(list)
    coverage, skipped = [], Counter()
    for day, r in dates.iterrows():
        record = {'day': day, 'nifty_gap': float(r.gap), 'ordinary': bool(r.gap >= -.005),
                  'announcements_known': ann_known[day]}
        if not ann_known[day]:
            record['status'] = 'unknown_announcements'
            coverage.append(record)
            continue
        if r.gap < -.005:
            # Check input availability without constructing/ranking a return pool.
            record['status'] = 'panic_excluded'
            coverage.append(record)
            continue
        pool, info = eligible_pool(day, r.prev_day, etfs, results[day])
        record.update(info)
        coverage.append(record)
        if pool is None:
            continue
        for threshold in THRESHOLDS:
            candidates = pool[pool.abs_gap > threshold]
            for arm in ('news', 'no_news_continuation', 'no_news_fade'):
                has_news = candidates.symbol.isin(news[day])
                picks = candidates[has_news if arm == 'news' else ~has_news].head(2)
                for worse in (0., .005):
                    key = f'{arm}_{int(threshold*100)}pct_{int(worse*10000)}bp_worse'
                    for _, pick in picks.iterrows():
                        direction = (1 if pick.gap > 0 else -1) * (-1 if arm == 'no_news_fade' else 1)
                        trade = execute(pick, direction, worse)
                        if trade is None:
                            skipped[key] += 1
                        else:
                            rows[key].append(trade)
    cov = pd.DataFrame(coverage)
    cov.to_csv(OUT / f'{name}_coverage.csv', index=False)
    good_days = list(cov.loc[cov.status == 'ok', 'day'])
    all_results, annual = {}, []
    for threshold in THRESHOLDS:
        for arm in ('news', 'no_news_continuation', 'no_news_fade'):
            for worse in (0., .005):
                key = f'{arm}_{int(threshold*100)}pct_{int(worse*10000)}bp_worse'
                trades = pd.DataFrame(rows[key], columns=TRADE_COLUMNS)
                trades.to_csv(OUT / f'{name}_{key}_trades.csv', index=False)
                summary = stats(trades, good_days)
                summary['zero_share_picks'] = skipped[key]
                summary['sides'] = {side: stats(trades[trades.side == side], good_days)
                                    for side in ('long', 'short')}
                by_year = []
                for y in years:
                    ycov = cov[cov.day.str[:4] == str(y)]
                    yg = [d for d in good_days if d.startswith(str(y))]
                    yt = trades[trades.day.str[:4] == str(y)]
                    ys = stats(yt, yg)
                    covered_sessions = int(ycov.status.isin(['ok', 'panic_excluded']).sum())
                    yr = {'year': y, 'requested_sessions': len(ycov), 'covered_sessions': covered_sessions,
                          'ordinary_sessions': len(yg), 'days_per_245_sessions':
                          ys['active_days'] * 245 / covered_sessions if covered_sessions else None,
                          'partial_year': y == 2026, **ys}
                    by_year.append(yr)
                    annual.append({'arm': arm, 'threshold': threshold, 'worse': worse, **yr,
                                   'long_trades': int((yt.side == 'long').sum()),
                                   'long_days': int(yt.loc[yt.side == 'long', 'day'].nunique()),
                                   'long_pnl': float(yt.loc[yt.side == 'long', 'net'].sum()),
                                   'short_trades': int((yt.side == 'short').sum()),
                                   'short_days': int(yt.loc[yt.side == 'short', 'day'].nunique()),
                                   'short_pnl': float(yt.loc[yt.side == 'short', 'net'].sum())})
                summary['years'] = by_year
                summary['positive_years'] = sum(y['pnl'] > 0 for y in by_year)
                all_results[key] = summary
    pd.DataFrame(annual).to_csv(OUT / f'{name}_years.csv', index=False)
    summary = {'years': years, 'coverage': cov.status.value_counts().to_dict(),
               'complete_ordinary_coverage': bool((~cov.ordinary | (cov.status == 'ok')).all()),
               'invalid_prices': int(cov.get('invalid_prices', pd.Series(dtype=float)).sum()),
               'cells': all_results}
    dump(OUT / f'{name}_summary.json', summary)
    primary = all_results['news_3pct_0bp_worse']
    print(f'{name}: {json.dumps(primary, indent=2)}', flush=True)
    return summary


def design_pass(summary):
    p = summary['cells']['news_3pct_0bp_worse']
    return bool(p['pnl'] > 0 and (p['t_trade'] or 0) >= 1.5 and (p['t_day'] or 0) >= 1.5)


def confirmation_gates(summary):
    p = summary['cells']['news_3pct_0bp_worse']
    return {'net_positive': p['pnl'] > 0,
            't_trade': (p['t_trade'] or 0) >= 2.5, 't_day': (p['t_day'] or 0) >= 2.5,
            'at_least_100_trades': p['trades'] >= 100,
            'most_years_positive': p['positive_years'] > len(summary['years']) / 2,
            'best_day_at_most_25pct': p['best_day_share'] is not None and p['best_day_share'] <= .25,
            'neighbours_positive': all(summary['cells'][f'news_{v}pct_0bp_worse']['pnl'] > 0 for v in (2, 5)),
            'complete_data': summary['complete_ordinary_coverage']}


def write_report(result, design, confirmation=None):
    def fmt(value, digits=2):
        return 'n/a' if value is None else f'{value:,.{digits}f}'
    audit = json.loads((OUT / 'news_audit.json').read_text(encoding='utf-8'))
    bhav = json.loads((OUT / 'bhav_coverage.json').read_text(encoding='utf-8'))
    lines = ['# H9 — fresh non-results announcement gaps', '',
             f"Verdict: **{result['verdict']}**. Paper research; official-open/daily-bar proxy.", '',
             f"Data: {sum(x['status'] == 'ok' for x in bhav)}/{len(bhav)} bhavcopy input sessions; "
             f"{audit['counts']['windows']} announcement windows, {audit['counts']['raw_rows']:,} raw filings. "
             f"Supported years: {audit['source_years']}. Missing inputs never mean no news.", '',
             f"Design: {result['split']['design']}; confirmation reserved: {result['split']['confirmation']}. "
             f"Design gate passed: {result['design_gate_pass']}; confirmation scored: {result['confirmation_run']}.", '']
    for label, summary in [('Design', design), ('Confirmation', confirmation)]:
        if summary is None:
            continue
        lines += [f'## {label}', '', f"Coverage: `{summary['coverage']}`; invalid eligible OHLC rows: {summary['invalid_prices']}.", '',
                  '| Arm / gap / fill | Trades | Days | Net bps/trade | Net Rs | Trade t | Active-day t |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        keys = [f'news_{v}pct_{w}bp_worse' for v in (3, 2, 5) for w in (0, 50)]
        keys += [f'no_news_{arm}_3pct_{w}bp_worse' for arm in ('continuation', 'fade') for w in (0, 50)]
        for key in keys:
            p = summary['cells'][key]
            lines.append(f"| {key} | {p['trades']} | {p['active_days']} | {fmt(p['net_bps'])} | {fmt(p['pnl'])} | {fmt(p['t_trade'])} | {fmt(p['t_day'])} |")
        primary = summary['cells']['news_3pct_0bp_worse']
        lines += ['', '| Primary side contribution | Trades | Days (can overlap) | Net bps | Net Rs | Trade t | Day t |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for side, p in primary['sides'].items():
            lines.append(f"| {side} | {p['trades']} | {p['active_days']} | {fmt(p['net_bps'])} | {fmt(p['pnl'])} | {fmt(p['t_trade'])} | {fmt(p['t_day'])} |")
        lines += ['', '| Calendar year | Covered/all sessions | Ordinary covered | Trades | Active days | Days/245 sessions | Net bps | Net Rs |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        for p in primary['years']:
            yr = str(p['year']) + (' Jan–Oct' if p['partial_year'] else '')
            lines.append(f"| {yr} | {p['covered_sessions']}/{p['requested_sessions']} | {p['ordinary_sessions']} | {p['trades']} | {p['active_days']} | {fmt(p['days_per_245_sessions'],1)} | {fmt(p['net_bps'])} | {fmt(p['pnl'])} |")
        lines += ['', f"Positive years: {primary['positive_years']}/{len(summary['years'])}; "
                  f"best-day share of total net profit: {fmt(primary['best_day_share'] * 100 if primary['best_day_share'] is not None else None)}%; "
                  f"stops: {primary['stops']}; locked picks: {primary['locks']}; "
                  f"base slipped entries outside daily range: {primary['entry_outside_range']}; zero-share picks: {primary['zero_share_picks']}.", '']
    if result.get('confirmation_gates'):
        lines += ['Confirmation gates: `' + json.dumps(result['confirmation_gates']) + '`.', '']
    lines += ['## Interpretation limits', '',
              '- These are 09:20/15:10 proxies using official open/close, daily extremes and a symmetric 8bp earlier-exit debit. The official open is unavailable as a guaranteed fill after ranking. The 0.5% adverse-entry table is sensitivity analysis, not intraday evidence.',
              '- All-day locks remain. Daily extremes may precede the intended entry; stops and intraday short availability are unverified. Corporate actions may drive some gaps. No live or paper runner was added.',
              '- Current NSE charges are applied across history, using the published 0.00307% exchange fee via a local top-up; historical tax schedules are not reconstructed.',
              '- ETF exclusion uses current inventory, historical raw ISINs, pre-known KOTAKNIFTY and announcement identities. Legacy shared frames omit ISIN; residual historical identity uncertainty is explicit in instrument_inventory.json.',
              '- Confirmation years contain prices studied under older hypotheses. They are reserved for this H9 rule, not a pristine market history. No engine holdout is opened.',
              '- Session coverage is relative to the stored NIFTY index calendar. Independent official-calendar checks returned HTTP 503 / timeout; omitted or special sessions cannot be ruled out. See index_source_audit.json.',
              '- Material means the frozen category/text classifier, not proof of unexpected economically important news. Filing receipt/dissemination fields are audited; API content is downloaded retrospectively.', '',
              'Sources: [NSE announcements](https://www.nseindia.com/companies-listing/corporate-filings-announcements), '
              '[NSE ETF inventory](https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv), '
              '[Zerodha charges](https://zerodha.com/charges/).', '',
              'Commands: `python scripts/h9_fetch.py`, then `python scripts/h9_news.py`. '
              'A persisted confirmation marker prevents a second scoring run if confirmation is reached. '
              'Full per-trade/per-year/per-cell data and raw inputs are in `data/h9/`.', '']
    (OUT / 'report.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    if (OUT / 'confirmation_started.json').exists():
        raise SystemExit('Confirmation has already been opened; use saved artifacts, never rerun.')
    if not (OUT / 'bhav_coverage.json').exists() or not (OUT / 'ann_coverage.json').exists():
        raise SystemExit('Finish h9_fetch.py first; inputs are incomplete.')
    n = calendar()
    news, results, etfs, coverage, supported = news_index(n)
    if len(supported) < 2 or supported != list(range(supported[0], 2027)):
        raise SystemExit('Cannot establish a continuous supported period with >=2 calendar years.')
    midpoint = len(supported) // 2
    design_years, confirm_years = supported[:midpoint], supported[midpoint:]
    split = {'design': design_years, 'confirmation': confirm_years,
             'note': 'Split fixed from announcement coverage before H9 stock-return scoring.'}
    existing = OUT / 'split.json'
    if existing.exists() and json.loads(existing.read_text(encoding='utf-8')) != split:
        raise SystemExit('Supported-period split changed; resolve coverage without reopening returns.')
    dump(existing, split)
    design = phase('design', design_years, n, news, results, etfs, coverage)
    gate = design_pass(design)
    result = {'split': split, 'design_gate_pass': gate, 'confirmation_run': False,
              'verdict': 'DESIGN_REJECTED' if not gate else 'DESIGN_PASSED'}
    confirm = None
    if gate and design['complete_ordinary_coverage']:
        # Exclusive marker is created BEFORE any confirmation stock prices are scored.
        with (OUT / 'confirmation_started.json').open('x', encoding='utf-8') as f:
            json.dump({'opened_at': datetime.now().astimezone().isoformat(),
                       'design_sha256': hashlib.sha256((OUT / 'design_summary.json').read_bytes()).hexdigest(),
                       'years': confirm_years}, f, indent=2)
        confirm = phase('confirmation', confirm_years, n, news, results, etfs, coverage)
        gates = confirmation_gates(confirm)
        result.update(confirmation_run=True, confirmation_gates=gates,
                      verdict='CONFIRMATION_PASSED_DAILY_PROXY' if all(gates.values()) else 'CONFIRMATION_FAILED')
    elif not design['complete_ordinary_coverage']:
        result['verdict'] = 'DATA_INCOMPLETE_NO_PASS'
    dump(OUT / 'summary.json', result)
    write_report(result, design, confirm)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    main()
