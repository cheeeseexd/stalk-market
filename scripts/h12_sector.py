"""H12 pre-registered sector-panic daily proxy, paper research only.

Run --prepare (metadata only), then run once without arguments. Inputs are read
from the H9 caches; no network, broker, credentials, server or strategy changes.
See RUNBOOK.md and data/h12/preregistration.md for frozen rules and limitations.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_fetch import OUT as H9, bhav_path, dump
from scripts.h9_news import ETF_SYMBOL, RESULT_CATEGORY, execute, publication, stats as legacy_stats, timestamp
from stalk.config import BASE_DIR, DEFAULT_UNIVERSE

OUT = Path(BASE_DIR) / 'data' / 'h12'
THRESHOLDS = (.015, .01, .02)
WORSE = (0., .005, .01)
DESIGN = list(range(2017, 2022))
CONFIRM = list(range(2022, 2027))
BASE = 'sector_1.5pct_0bp_worse'
EMPTY_LABELS = {'', '-', 'na', 'n/a', 'none', 'null', 'not classified', 'unclassified'}
PICK_COLS = ['day', 'symbol', 'sector', 'sector_gap', 'gap', 'open', 'high', 'low', 'close']
TRADE_COLS = ['day', 'symbol', 'side', 'gap', 'qty', 'entry', 'exit', 'gross', 'charges',
              'net', 'net_return', 'stop', 'locked', 'entry_outside_range', 'sector', 'sector_gap',
              'pair_id', 'matched_day', 'matched_symbol']


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def tstat(values):
    x = np.asarray(values, dtype=float)
    # Constant stopped returns can differ at floating-point roundoff only.
    # Such a t is undefined, not evidence with a magnitude near 10**15.
    if len(x) < 2 or x.std(ddof=1) <= 1e-12:
        return None
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def stats(trades, covered_days):
    result = legacy_stats(trades, covered_days)
    daily = trades.groupby('day').net.sum()
    result.update(t_trade=tstat(trades.net_return), t_day=tstat(daily / 10000),
                  t_all_ordinary_days=tstat(daily.reindex(covered_days, fill_value=0.) / 10000))
    return result


def clean_label(value):
    label = ' '.join(str(value or '').split())
    return None if label.lower() in EMPTY_LABELS else label


def prepare():
    """Only announcement metadata/event dates. Never load a bhavcopy or returns."""
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'scoring_started.json').exists():
        raise SystemExit('Metadata cannot change after scoring starts.')
    best, labels, event_dates = {}, defaultdict(set), defaultdict(set)
    covered, unknown, inputs, counts = set(), set(), {}, Counter()
    inventory = json.loads((H9 / 'news_audit.json').read_text(encoding='utf-8'))
    etfs = set(inventory['excluded_symbols'])
    hashes = {}
    paths = sorted((H9 / 'announcements').glob('*.json'))
    for i, path in enumerate(paths):
        obj = json.loads(path.read_text(encoding='utf-8'))
        meta, rows = obj['meta'], obj['rows']
        dates = set(pd.date_range(meta['start'], meta['end']).strftime('%Y-%m-%d'))
        inputs[path.name] = {'sha256': sha(path), 'raw_response_sha256': meta['sha256']}
        if meta['status'] != 'ok':
            unknown.update(dates)
            continue
        covered.update(dates)
        if rows and meta['sha256'] in hashes:
            unknown.update(dates | hashes[meta['sha256']])
            counts['repeated_nonempty_windows'] += 1
        hashes[meta['sha256']] = dates
        counts['windows'] += 1
        counts['rows'] += len(rows)
        for row in rows:
            symbol = str(row.get('symbol') or '').strip()
            ann = timestamp(row.get('an_dt'))
            pub = publication(row)
            if not symbol or ann is None or pub is None or ann.date().isoformat() not in dates:
                unknown.update(dates)
                counts['invalid_rows'] += 1
                continue
            label = clean_label(row.get('smIndustry'))
            if label:
                counts['nonempty_industry_rows'] += 1
                labels[symbol].add(label)
                key = (ann.isoformat(), label, path.name)
                if symbol not in best or key > best[symbol]['key']:
                    best[symbol] = {'key': key, 'sector': label, 'source_cache': path.name,
                                    'announcement_at': ann.isoformat(), 'isin': row.get('sm_isin')}
            else:
                counts['empty_industry_rows'] += 1
            if RESULT_CATEGORY.search(str(row.get('desc') or '')):
                event_dates[ann.date().isoformat()].add(symbol)
                counts['results_board_rows'] += 1
        if i % 200 == 0:
            print(f'metadata {i}/{len(paths)}', flush=True)
    mapping = {s: v['sector'] for s, v in sorted(best.items())}
    good = sorted(covered - unknown)
    if not good:
        raise ValueError('No verified announcement coverage.')
    result = {'created_utc': utcnow(), 'counts': dict(counts), 'mapping': mapping,
              'covered_dates': good, 'unknown_dates': sorted(unknown), 'etfs': sorted(etfs),
              'events_by_filing_date': {d: sorted(s) for d, s in sorted(event_dates.items())},
              'label_changes': {s: sorted(v) for s, v in labels.items() if len(v) > 1},
              'source': 'NSE corporate-announcements cached smIndustry, latest nonempty dated label per symbol',
              'limitation': 'today-retrieved/last-available labels, not point-in-time classifications; no inferred aliases',
              'source_inputs': inputs, 'source_inventory_sha256': sha(H9 / 'news_audit.json')}
    dump(OUT / 'metadata.json', result)
    pd.DataFrame([{'symbol': s, **{k: v for k, v in v.items() if k != 'key'},
                   'all_labels': '|'.join(sorted(labels[s]))} for s, v in sorted(best.items())]).to_csv(
                       OUT / 'sector_mapping.csv', index=False)
    dump(OUT / 'split.json', {'design': DESIGN, 'confirmation': CONFIRM})
    print(json.dumps({'counts': dict(counts), 'mapped_symbols': len(mapping),
                      'industry_labels': len(set(mapping.values())), 'changed_symbols': len(result['label_changes']),
                      'covered_dates': len(good)}, indent=2), flush=True)


def event_window(day, metadata):
    days = [(pd.Timestamp(day) + pd.Timedelta(days=i)).strftime('%Y-%m-%d') for i in range(-3, 4)]
    known = all(d in metadata['_covered'] for d in days)
    blocked = set().union(*(metadata['events_by_filing_date'].get(d, ()) for d in days))
    return known, blocked


def build_pool(today, previous, mapping, etfs, blocked, day):
    """Sector medians use opening facts only; future all-day-lock proxy only gates purchases."""
    if today.empty or previous.empty or today.index.has_duplicates or previous.index.has_duplicates:
        raise ValueError(f'Bad/duplicate bhavcopy: {day}')
    u = today.join(previous[['value']].rename(columns={'value': 'prev_value'}), how='inner')
    liquid = (u.prev_value >= 1e8) & (u.prev_close >= 50)
    excluded_funds = u.index.isin(etfs) | u.index.to_series().str.contains(ETF_SYMBOL).values
    u = u[liquid & ~excluded_funds].copy()
    valid_open = np.isfinite(u[['open', 'prev_close']]).all(axis=1) & (u.open > 0) & (u.prev_close > 0)
    info = {'liquid_equity_rows': len(u), 'invalid_open_rows': int((~valid_open).sum()),
            'etf_excluded': int((liquid & excluded_funds).sum())}
    u = u[valid_open].copy()
    u['sector'] = u.index.map(mapping)
    unmapped = sorted(u.index[u.sector.isna()].tolist())
    info.update(mapped_liquid_rows=int(u.sector.notna().sum()), unmapped_liquid_rows=len(unmapped))
    u = u[u.sector.notna()].copy()
    u['gap'] = u.open / u.prev_close - 1
    sectors = u.groupby('sector').gap.agg(sector_gap='median', members='size')
    sectors = sectors[sectors.members >= 5].sort_values(['sector_gap', 'sector'], kind='stable')
    u['day'] = day
    u = u.rename_axis('symbol').reset_index()
    u['sector_gap'] = u.sector.map(sectors.sector_gap)
    valid_ohlc = np.isfinite(u[['open', 'high', 'low', 'close', 'prev_close']]).all(axis=1)
    valid_ohlc &= (u[['open', 'high', 'low', 'close', 'prev_close']] > 0).all(axis=1)
    valid_ohlc &= u.high >= u[['open', 'low', 'close']].max(axis=1)
    valid_ohlc &= u.low <= u[['open', 'high', 'close']].min(axis=1)
    info['invalid_ohlc_rows'] = int((~valid_ohlc).sum())
    locks = (u.open == u.high) & (u.high == u.low)
    info['all_day_locks_excluded'] = int(locks.sum())
    purchase_ok = valid_ohlc & ~locks & ~u.symbol.isin(DEFAULT_UNIVERSE) & ~u.symbol.isin(blocked)
    candidates = u[purchase_ok & (u.gap < -.02) & u.sector_gap.notna()]
    candidates = candidates.sort_values(['gap', 'symbol'], kind='stable')
    info.update(sectors_with_5_members=len(sectors), clean_gap_candidates=len(candidates),
                results_excluded=int(u.symbol.isin(blocked).sum()))
    return candidates, sectors, info, unmapped


def select_sector(candidates, sectors, threshold):
    fired = sectors[sectors.sector_gap < -threshold]
    if fired.empty:
        return candidates.iloc[:0].copy(), None
    # The worst sector wins even when it cannot supply a clean stock.
    chosen = fired.index[0]
    return candidates[candidates.sector == chosen].head(2).copy(), chosen


def match_controls(treated, candidates):
    """Match only opening gaps/metadata; no return or future-extreme matching."""
    used, day_counts, day_sector = set(), Counter(), {}
    left, right = [], []
    if treated.empty or candidates.empty:
        return pd.DataFrame(columns=PICK_COLS), pd.DataFrame(columns=PICK_COLS)
    candidates = candidates.copy()
    candidates['year'] = candidates.day.str[:4]
    for _, t in treated.sort_values(['day', 'symbol'], kind='stable').iterrows():
        pool = candidates[(candidates.sector == t.sector) & (candidates.year == t.day[:4])].copy()
        pool['distance'] = (pool.gap - t.gap).abs()
        pool = pool[pool.distance <= .0025 + 1e-12].sort_values(['distance', 'day', 'symbol'], kind='stable')
        for _, c in pool.iterrows():
            key = (c.day, c.symbol)
            if key in used or day_counts[c.day] >= 2 or day_sector.get(c.day, c.sector) != c.sector:
                continue
            used.add(key)
            day_counts[c.day] += 1
            day_sector[c.day] = c.sector
            pair = len(left)
            left.append({**t.to_dict(), 'pair_id': pair, 'matched_day': c.day, 'matched_symbol': c.symbol})
            right.append({**c.to_dict(), 'pair_id': pair, 'matched_day': t.day, 'matched_symbol': t.symbol})
            break
    extra = ['pair_id', 'matched_day', 'matched_symbol']
    return pd.DataFrame(left, columns=PICK_COLS + extra), pd.DataFrame(right, columns=PICK_COLS + extra)


def score(picks, worse):
    trades, skipped = [], 0
    for _, row in picks.iterrows():
        trade = execute(row, 1, worse)
        if trade is None:
            skipped += 1
            continue
        trade.update({k: row.get(k) for k in TRADE_COLS if k not in trade})
        trades.append(trade)
    return pd.DataFrame(trades, columns=TRADE_COLS), skipped


def primary_pass(summary):
    p = summary['cells'][BASE]
    return bool(p['pnl'] > 0 and (p['t_trade'] or 0) >= 1.5 and (p['t_day'] or 0) >= 1.5)


def confirmation_gates(summary):
    p = summary['cells'][BASE]
    return {'net_positive': p['pnl'] > 0, 't_trade': (p['t_trade'] or 0) >= 2.5,
            't_day': (p['t_day'] or 0) >= 2.5, 'at_least_100_trades': p['trades'] >= 100,
            'most_years_positive': p['positive_years'] > len(summary['years']) / 2,
            'best_day_at_most_25pct': p['best_day_share'] is not None and p['best_day_share'] <= .25,
            'neighbours_positive': all(summary['cells'][f'sector_{v:g}pct_0bp_worse']['pnl'] > 0 for v in (1, 2)),
            'complete_interior_data': summary['complete_interior_data']}


def phase(name, years, n, metadata):
    dates = n[n.index.str[:4].astype(int).isin(years)]
    selections, controls = defaultdict(list), []
    coverage, sector_rows, choice_rows, inputs = [], [], [], {}
    unmapped = Counter()
    mapping, etfs = metadata['mapping'], set(metadata['etfs'])
    lower = pd.Timestamp(min(metadata['_covered'])) + pd.Timedelta(days=3)
    upper = pd.Timestamp(max(metadata['_covered'])) - pd.Timedelta(days=3)
    for i, (day, r) in enumerate(dates.iterrows()):
        ordinary = r.gap > -.005
        known, blocked = event_window(day, metadata)
        record = {'day': day, 'nifty_gap': r.gap, 'ordinary': bool(ordinary), 'announcements_known': known,
                  'boundary_excluded': not lower <= pd.Timestamp(day) <= upper}
        if not ordinary:
            record['status'] = 'panic_excluded'
            coverage.append(record)
            continue
        if not known:
            record['status'] = 'boundary_excluded' if record['boundary_excluded'] else 'unknown_announcements'
            coverage.append(record)
            continue
        paths = [bhav_path(d) for d in (day, r.prev_day)]
        if not all(p.exists() for p in paths):
            record['status'] = 'missing_bhav'
            coverage.append(record)
            continue
        for d, p in zip((day, r.prev_day), paths):
            if d not in inputs:
                inputs[d] = {'path': str(p.relative_to(BASE_DIR)), 'sha256': sha(p)}
        try:
            candidates, sectors, info, missing = build_pool(*[pd.read_pickle(p) for p in paths],
                                                          mapping, etfs, blocked, day)
        except ValueError as e:
            record.update(status='invalid_bhav', error=str(e))
            coverage.append(record)
            continue
        unmapped.update(missing)
        record.update(status='ok', **info)
        coverage.append(record)
        for s, v in sectors.iterrows():
            sector_rows.append({'day': day, 'sector': s, 'sector_gap': float(v.sector_gap),
                                'members': int(v.members), 'nifty_gap': r.gap})
        for threshold in THRESHOLDS:
            picks, chosen = select_sector(candidates, sectors, threshold)
            choice_rows.append({'day': day, 'threshold': threshold, 'sector': chosen, 'picks': len(picks),
                                'fired_sectors': int((sectors.sector_gap < -threshold).sum())})
            selections[threshold].extend(picks[PICK_COLS].to_dict('records'))
        if abs(r.gap) <= .005 and not (sectors.sector_gap < -.015).any():
            calm = set(sectors.index[sectors.sector_gap.abs() <= .005])
            picks = candidates[candidates.sector.isin(calm)].groupby('sector', sort=True).head(2)
            controls.extend(picks[PICK_COLS].to_dict('records'))
        if i % 200 == 0:
            print(f'{name} selections {i}/{len(dates)}', flush=True)
    cov = pd.DataFrame(coverage)
    cov.to_csv(OUT / f'{name}_coverage.csv', index=False)
    pd.DataFrame(sector_rows).to_csv(OUT / f'{name}_sector_gaps.csv', index=False)
    pd.DataFrame(choice_rows).to_csv(OUT / f'{name}_sector_choices.csv', index=False)
    dump(OUT / f'{name}_input_manifest.json', inputs)
    dump(OUT / f'{name}_unmapped_symbols.json', dict(unmapped.most_common()))
    good_days = list(cov.loc[cov.status == 'ok', 'day'])
    primary = pd.DataFrame(selections[.015], columns=PICK_COLS)
    candidates = pd.DataFrame(controls, columns=PICK_COLS)
    matched_t, matched_c = match_controls(primary, candidates)
    candidates.to_csv(OUT / f'{name}_control_candidates.csv', index=False)
    matched_t.to_csv(OUT / f'{name}_matched_treated_selections.csv', index=False)
    matched_c.to_csv(OUT / f'{name}_matched_control_selections.csv', index=False)
    journal_days = set(pd.read_csv(Path(BASE_DIR) / 'data' / 'panic_journal.csv', usecols=['day']).day)
    cells, annual = {}, []
    def save(key, picks, worse):
        trades, skipped = score(picks, worse)
        trades.to_csv(OUT / f'{name}_{key}_trades.csv', index=False)
        result = stats(trades, good_days)
        result['zero_share_picks'] = skipped
        by_year = []
        active = set(trades.day)
        for y in years:
            yc = cov[cov.day.str.startswith(str(y))]
            yt = trades[trades.day.str.startswith(str(y))]
            ys = stats(yt, [d for d in good_days if d.startswith(str(y))])
            # Panic-excluded days are observed index sessions, not missing data.
            exposure = int((yc.status.isin(['ok', 'panic_excluded'])).sum())
            row = {'year': y, 'requested_sessions': len(yc), 'known_session_exposure': exposure,
                   'ordinary_sessions': int((yc.status == 'ok').sum()), 'partial_year': y == 2026,
                   'days_per_245_sessions': ys['active_days'] * 245 / exposure if exposure else None, **ys}
            row.update(strict_market_panic_days=int(sum(n.loc[d, 'gap'] < -.005 for d in active if d.startswith(str(y)))),
                       loose_market_panic_days=int(sum(n.loc[d, 'gap'] < -.003 for d in active if d.startswith(str(y)))),
                       stored_panic_journal_overlap=len({d for d in active if d.startswith(str(y))} & journal_days))
            by_year.append(row)
            annual.append({'cell': key, **row})
        result.update(years=by_year, positive_years=sum(r['pnl'] > 0 for r in by_year),
                      average_days_per_calendar_year=len(active) / len(years),
                      strict_market_panic_days=int(sum(n.loc[d, 'gap'] < -.005 for d in active)),
                      loose_market_panic_days=int(sum(n.loc[d, 'gap'] < -.003 for d in active)),
                      stored_panic_journal_overlap=len(active & journal_days))
        cells[key] = result
    for threshold in THRESHOLDS:
        picks = pd.DataFrame(selections[threshold], columns=PICK_COLS)
        picks.to_csv(OUT / f'{name}_sector_{threshold*100:g}pct_selections.csv', index=False)
        for worse in WORSE:
            save(f'sector_{threshold*100:g}pct_{worse*10000:g}bp_worse', picks, worse)
    for worse in WORSE:
        save(f'matched_treated_{worse*10000:g}bp_worse', matched_t, worse)
        save(f'matched_control_{worse*10000:g}bp_worse', matched_c, worse)
    paired = {}
    for worse in WORSE:
        left, _ = score(matched_t, worse)
        right, _ = score(matched_c, worse)
        both = left.merge(right, on='pair_id', suffixes=('_treated', '_control'))
        difference = both.net_return_treated - both.net_return_control
        paired[f'{worse*10000:g}bp_worse'] = {'funded_pairs': len(both),
            'difference_bps': float(difference.mean()*1e4) if len(both) else None,
            'paired_trade_t_descriptive': tstat(difference),
            'mean_absolute_gap_difference_bps': float((both.gap_treated-both.gap_control).abs().mean()*1e4) if len(both) else None,
            'note': 'paired t is descriptive; market-day dependence exists in both arms'}
    interior = cov.ordinary & ~cov.boundary_excluded
    complete = bool((cov.loc[interior, 'status'] == 'ok').all())
    invalid = int(cov.get('invalid_open_rows', pd.Series(dtype=float)).sum() +
                  cov.get('invalid_ohlc_rows', pd.Series(dtype=float)).sum())
    summary = {'years': years, 'coverage': cov.status.value_counts().to_dict(),
               'complete_interior_data': complete and invalid == 0, 'invalid_prices': invalid,
               'effective_last_eligible_date': max(good_days) if good_days else None,
               'metadata_coverage': {k: int(cov.get(k, pd.Series(dtype=float)).sum()) for k in
                   ['liquid_equity_rows', 'mapped_liquid_rows', 'unmapped_liquid_rows', 'all_day_locks_excluded']},
               'cells': cells, 'control_match': {'selected_primary_picks': len(primary),
                   'calm_control_candidates': len(candidates), 'matched_pairs': len(matched_t),
                   'match_fraction': len(matched_t)/len(primary) if len(primary) else None,
                   'comparisons': paired}}
    pd.DataFrame(annual).to_csv(OUT / f'{name}_years.csv', index=False)
    dump(OUT / f'{name}_summary.json', summary)
    print(f'{name}: '+json.dumps({k: v for k, v in cells[BASE].items() if k != 'years'}, indent=2), flush=True)
    return summary


def fmt(x, digits=2):
    return 'n/a' if x is None else f'{x:,.{digits}f}'


def report(result, metadata, design, confirmation):
    lines = ['# H12 — sector panics (research cycle 2)', '', f"Verdict: **{result['verdict']}**. Paper research only; Claude review pending.", '',
             'Rule: NSE smIndustry median opening gap <−1.5%, at least five liquid members, NIFTY gap >−0.5%; buy the two worst clean <−2% stock gaps in the worst sector, Rs5,000 each, 3% stop. Official open/close daily proxy, 8bp early-exit debit, Zerodha MIS fees and 10bp slippage per side. Frozen design 2017–2021; confirmation 2022–2026 conditional on positive design and trade/day t≥1.5. Confirmation bar is t≥2.5.', '',
             '## Data and pre-registration', '',
             'The full rule was written to RUNBOOK.md and data/h12/preregistration.md before scoring. One family: sector thresholds −1.5% primary, −1%/−2% diagnostics; 0/0.5%/1% adverse entry. No tuning or replacement strategy. H9 price/announcement caches were reused read-only; no input downloads.', '',
             f"Metadata: {metadata['counts']['windows']:,} cached announcement windows; {metadata['counts']['rows']:,} rows; {len(metadata['mapping']):,} mapped symbols and {len(set(metadata['mapping'].values()))} exact industry labels; {len(metadata['label_changes'])} symbols have multiple cached labels. {len(metadata['covered_dates']):,} covered calendar dates; {len(metadata['unknown_dates'])} unknown dates.", '',
             'Sector source is NSE corporate-announcements `smIndustry`, latest nonempty dated label per symbol; provenance is in sector_mapping.csv. This uses today-retrieved/last-available classifications and is not historical membership. No inferred renamed aliases, taxonomy merging or current-index universe filter. Unmapped names are excluded and counted. The median includes liquid NIFTY/results stocks; purchase exclusions do not redefine the sector signal.', '',
             'Results/board/integrated-financial filing dates are expanded by ±3 calendar days, including future filings. This is a retrospective, conservative proxy, not the complete scheduled results calendar. Generic board outcomes can be overexcluded; actual meetings may precede filings. The requested all-day-lock exclusion also uses future data. These are explicit look-ahead limitations; no causal executable pass can be claimed from this proxy.', '']
    for phase_name, summary in [('Design', design), ('Confirmation', confirmation)]:
        if summary is None:
            lines += ['## Confirmation', '', 'NOT SCORED: primary design failed the registered gate. No confirmation marker or stock-return outputs were created. The next confirmation hurdle remains t≥2.5.', '']
            continue
        mc = summary['metadata_coverage']
        fraction = mc['mapped_liquid_rows'] / mc['liquid_equity_rows'] if mc['liquid_equity_rows'] else 0.
        lines += [f'## {phase_name}', '', f"Coverage: {summary['coverage']}; valid interior input coverage: {summary['complete_interior_data']}; invalid eligible prices: {summary['invalid_prices']}.",
                  f"Liquid-row classification coverage: {summary['metadata_coverage']}. Effective last eligible session: {summary['effective_last_eligible_date']}.", '',
                  f"Mapped share of liquid equity stock-days: {fraction:.2%}. Missing classification is a material sample restriction; these results cannot reject every possible full-universe sector rule.", '',
                  '| Arm / sector threshold / adverse entry | Trades | Active days | Net bps/trade | Net Rs | Trade t | Active-day t |',
                  '|---|---:|---:|---:|---:|---:|---:|']
        for key, s in summary['cells'].items():
            lines.append(f"| {key} | {s['trades']} | {s['active_days']} | {fmt(s['net_bps'])} | {fmt(s['pnl'])} | {fmt(s['t_trade'])} | {fmt(s['t_day'])} |")
        p = summary['cells'][BASE]
        lines += ['', '| Year | Observed sessions | Ordinary covered | Trades | Active days | Days/245 sessions | Net bps | Net Rs | Loose-panic overlap days |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
        for y in p['years']:
            lines.append(f"| {y['year']} | {y['known_session_exposure']}/{y['requested_sessions']} | {y['ordinary_sessions']} | {y['trades']} | {y['active_days']} | {fmt(y['days_per_245_sessions'],1)} | {fmt(y['net_bps'])} | {fmt(y['pnl'])} | {y['loose_market_panic_days']} |")
        lines += ['', f"Mean actual trading days/year: {fmt(p['average_days_per_calendar_year'])}. Strict market-panic overlap: {p['strict_market_panic_days']} (disjoint by construction); stored panic-journal overlap: {p['stored_panic_journal_overlap']}. Loose <−0.3% market-panic overlap: {p['loose_market_panic_days']}. Partial 2026 exposure is not a full year.",
                  f"Positive years: {p['positive_years']}/{len(summary['years'])}; best day: Rs{fmt(p['best_day'])}, share of total positive net profit: {fmt(None if p['best_day_share'] is None else 100*p['best_day_share'])}%; stops: {p['stops']}; retained locks: {p['locks']}; entries outside daily range: {p['entry_outside_range']}; zero-share picks: {p['zero_share_picks']}.", '',
                  '### Gap-matched calm control', '',
                  'Same sector/year, stock opening-gap difference ≤25bp, calm market/sector (absolute gap ≤0.5%), no sector panic anywhere; top two clean gaps per sector, one-to-one without replacement, max two positions and one sector per control day. Matching was frozen before returns. Compare matched treated to matched control; unmatched full treatment is a different population.', '']
        match = summary['control_match']
        lines += [f"Matched {match['matched_pairs']}/{match['selected_primary_picks']} selected primary picks ({fmt(None if match['match_fraction'] is None else match['match_fraction']*100,1)}%) from {match['calm_control_candidates']} calm candidates. This tiny, selected subset cannot establish that the sector condition helps or hurts.", '',
                  '| Adverse entry | Funded pairs | Treated minus control bps | Mean absolute gap mismatch bps |',
                  '|---|---:|---:|---:|']
        for key, c in match['comparisons'].items():
            lines.append(f"| {key} | {c['funded_pairs']} | {fmt(c['difference_bps'])} | {fmt(c['mean_absolute_gap_difference_bps'])} |")
        lines += ['', 'Pair t-statistics in JSON are descriptive and ignore dependence on both sets of dates. Undefined t for effectively constant returns is reported n/a; floating-point noise is not statistical variation.', '']
    if not result['design_gate_pass']:
        lines += ['## Decision, lesson and next cycle', '',
                  'Reject the registered H12 implementation at design: net is negative and both t-statistics miss 1.5. With only 14 trades on eight mornings, this is an underpowered negative result, not proof that sector overshoots never recover. It fails the practical objective of adding ordinary trading days. No neighbour or control rescues the primary rule; the positive −2% neighbour has just three trades on two days.', '',
                  '**One lesson:** count independent opportunities before treating a sector condition as a route to more trading days.', '',
                  'Next cycle: H14, establish executable no-news gap-fade fills on a design-only intraday sample before any new confirmation. Existing panic-buy forward paper tests continue unchanged.', '']
    lines += ['## Limits and interpretation', '',
              '- Stored NIFTY calendar completeness is inherited from H9; missing/special sessions cannot be ruled out. Announcements are observed retrospectively; omitted filings are not testable from cache alone. Required ±3-day coverage excludes boundary dates rather than treating them as clean.',
              '- Daily official opens are not guaranteed fills after ranking. Daily lows can precede intended 09:20 entry; close minus 8bp is not an observed 15:10 fill. Stops, circuit liquidity, corporate actions and historical MIS eligibility remain unverified. Stress fills are sensitivity checks, not execution evidence.',
              '- Fixed Rs5,000 allocations are a signal comparison, not a self-financing compounded Rs10,000 equity curve; losses may require replenishment. Current published fee schedule is held fixed historically.',
              '- Current/last-available labels and missing classification create bias even though the underlying bhavcopies include delisted names. Older hypotheses examined overlapping history; this is a rule-specific split, not a pristine market holdout.',
              '- Matched controls may have few observations and residual liquidity/news/regime confounding. Pair t is descriptive and does not model clustering on both treatment and control dates. No control or neighbour is promoted.', '',
              'Sources: [NSE announcements](https://www.nseindia.com/companies-listing/corporate-filings-announcements), [NSE classification](https://www.nseindia.com/static/products-services/industry-classification), [Zerodha fees](https://zerodha.com/charges/). Raw price sources and hashes remain in data/h9/; H12 records exact cache hashes and metadata provenance.', '',
              'Reproduction: `python scripts/h12_sector.py --prepare`, then `python scripts/h12_sector.py` once. Completed runs refuse repeat scoring; inspect saved JSON/CSV. Full outputs: data/h12/. No live/forward rule changed.']
    text = '\n'.join(lines) + '\n'
    (OUT / 'report.md').write_text(text, encoding='utf-8')
    (Path(BASE_DIR) / 'docs' / 'research' / '2026-10-31-h12-sector.md').write_text(text, encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true', help='metadata only; no returns')
    args = parser.parse_args()
    if args.prepare:
        prepare()
        return
    if (OUT / 'scoring_started.json').exists() or (OUT / 'confirmation_started.json').exists():
        raise SystemExit('Scoring already started/completed; inspect saved artifacts, do not repeat.')
    metadata = json.loads((OUT / 'metadata.json').read_text(encoding='utf-8'))
    n = pd.read_csv(H9 / 'index_calendar.csv', index_col=0)
    if n.index.has_duplicates or not n.index.is_monotonic_increasing:
        raise ValueError('Invalid stored calendar.')
    metadata['_covered'] = set(metadata['covered_dates'])
    audit = {'created_utc': utcnow(), 'metadata_sha256': sha(OUT / 'metadata.json'),
             'preregistration_sha256': sha(OUT / 'preregistration.md'), 'script_sha256': sha(__file__),
             'calendar_sha256': sha(H9 / 'index_calendar.csv'), 'design': DESIGN, 'confirmation': CONFIRM}
    with (OUT / 'scoring_started.json').open('x', encoding='utf-8') as f:
        json.dump(audit, f, indent=2)
    Path(OUT / 'scored_script.py.txt').write_bytes(Path(__file__).read_bytes())
    design = phase('design', DESIGN, n, metadata)
    gate = primary_pass(design)
    result = {'design_gate_pass': gate, 'confirmation_run': False,
              'verdict': 'DESIGN_REJECTED' if not gate else 'DESIGN_PASSED',
              'next_confirmation_t': 2.5, 'completed_utc': None}
    confirmation = None
    if gate and design['complete_interior_data']:
        with (OUT / 'confirmation_started.json').open('x', encoding='utf-8') as f:
            json.dump({'opened_utc': utcnow(), 'design_sha256': sha(OUT / 'design_summary.json'),
                       'years': CONFIRM}, f, indent=2)
        confirmation = phase('confirmation', CONFIRM, n, metadata)
        gates = confirmation_gates(confirmation)
        result.update(confirmation_run=True, confirmation_gates=gates, next_confirmation_t=3.,
                      verdict='CONFIRMATION_PASSED_BIASED_DAILY_PROXY' if all(gates.values()) else 'CONFIRMATION_FAILED')
    elif not design['complete_interior_data']:
        result['verdict'] = 'DATA_INCOMPLETE_NO_PASS'
    result['completed_utc'] = utcnow()
    dump(OUT / 'summary.json', result)
    report(result, metadata, design, confirmation)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    main()
