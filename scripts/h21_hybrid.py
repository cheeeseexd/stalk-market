"""H21's three registered hybrid daily-price studies. Offline; no order interface.

prepare saves signal-only pools; score is exclusively guarded; history/validate
consume saved results without reselecting or rescoring a portfolio.
"""
from __future__ import annotations

import argparse
from collections import Counter, deque
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_fetch import bhav_path
from scripts.h17_rebuild import historical_symbol
from scripts.horizon_ladder import dump
from scripts.panic_final import nifty
from scripts.panic_to_history import ETF, ts
from stalk.config import DEFAULT_UNIVERSE, IST
from stalk.market.costs import FEE_SCHEDULE_VERSION, round_trip_cost
from stalk.sim.price_basis import differs

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data/h21'
PROTECTED = ['stalk/sim/panic.py', 'stalk/research/validate.py', 'stalk/agent.py']
POOL_COLUMNS = ['day', 'symbol', 'open', 'prev_close', 'gap', 'std20',
                'price_source', 'lock_status', 'calendar_status']
TRADE_COLUMNS = ['day', 'symbol', 'gap', 'qty', 'entry', 'exit', 'gross', 'charges',
                 'net', 'net_return', 'reason', 'leg']
NOTE = ('H21: reused history, official daily-price assumed fills; not fresh confirmation. '
        'Entry is the 09:15 official open plus assumed slippage, not a measured 09:20 fill. '
        'Stop time and opening lock status unknown; flatten uses official close. '
        'Timestamped known results only on everyday days; calendar completeness/vintages unverified. '
        'H17 panic branch inherits its retrospective calendar and kept-lock assumptions. '
        'Fixed Rs5,000 slots may require replenishment. Missing price days are unavailable, not zero opportunities.')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def marker(path, value):
    """Exclusive, persistent guard, including across processes and failed attempts."""
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, indent=2, allow_nan=False)


def tstat(values):
    x = np.asarray(values, dtype=float)
    if len(x) < 2 or x.std(ddof=1) <= 1e-12:
        return None
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x))))


def dated_wide(frame):
    b = frame.copy()
    b['day'] = pd.to_datetime(b.ts, unit='s', utc=True).dt.tz_convert(IST).dt.strftime('%Y-%m-%d')
    return b.drop_duplicates('day', keep='last').set_index('day').sort_index()


def past_std(history, position, symbols=None):
    """Require 20 immediately preceding index sessions, including no stale rows."""
    names = history.keys() if symbols is None else set(symbols) & history.keys()
    return {s: float(np.std([v for _, v in history[s]], ddof=1)) for s in names
            if len(history[s]) == 20 and history[s][0][0] == position-20 and history[s][-1][0] == position-1
            and all(np.isfinite(v) for _, v in history[s])}


def update_history(history, official, position):
    """Called AFTER building that day's pool. Never supplies its own day's close."""
    for r in official[['close', 'prev_close']].itertuples():
        symbol = r.Index
        h = history.setdefault(symbol, deque(maxlen=20))
        if h and h[-1][0] != position-1:
            h.clear()
        valid = np.isfinite(r.close) and np.isfinite(r.prev_close) and r.close > 0 and r.prev_close > 0
        h.append((position, r.close/r.prev_close-1 if valid else float('nan')))


def known_results(events, day):
    """Only already-announced scheduled meetings, never future filing dates."""
    if events.empty:
        return set()
    cutoff = ts(day, '09:15')
    near = (pd.to_datetime(events.day) - pd.Timestamp(day)).abs().dt.days <= 3
    result = (events.purpose.fillna('') + ' ' + events.detail.fillna('')).str.contains('result', case=False)
    announced = pd.to_numeric(events.announced_ts, errors='coerce')
    return set(events.loc[near & result & (announced < cutoff), 'symbol'])


def pool(day, opening, previous, *, wide_refs=None, results=(), actions=(),
         funds=(), std20=None, locks=None, announced_actions=()):
    """Signal-only builder: never accesses today's h/l/c, value, or later rows.

    locks contains only contemporaneously observed states: True excludes; absent
    is UNKNOWN, retained solely under this study's explicit assumed-fill basis.
    Today OHLC lock flags must never be passed as opening observations.
    """
    counts = Counter()
    p = opening[['open', 'prev_close']].join(previous[['value']].rename(columns={'value': 'prev_value'}), how='inner')
    counts['opening_rows'] = len(opening)
    p = p[(p.prev_value >= 1e8) & (p.prev_close >= 50) & ~p.index.isin(DEFAULT_UNIVERSE)]
    counts['liquid_nondefault'] = len(p)
    valid = np.isfinite(p[['open', 'prev_close']]).all(axis=1) & (p[['open', 'prev_close']] > 0).all(axis=1)
    counts['invalid_open_reference'] = int((~valid).sum())
    p = p[valid].copy()
    p['gap'] = p.open / p.prev_close - 1
    p = p[p.gap < -.02].copy()
    counts['gap_rows'] = len(p)
    excluded = set(results) | set(actions) | set(announced_actions) | set(funds)
    remove = p.index.isin(excluded) | p.index.to_series().str.contains(ETF, regex=True)
    counts['metadata_exclusions'] = int(remove.sum())
    p = p[~remove].copy()
    refs = wide_refs or {}
    mismatches = []
    for symbol, row in p.iterrows():
        ref = refs.get(symbol)
        mismatches.append(ref is not None and (differs(ref['open'], row.open)
                          or (ref.get('prev_close') is not None and differs(ref['prev_close'], row.prev_close))))
    counts['price_basis_exclusions'] = int(sum(mismatches))
    p = p.loc[~np.asarray(mismatches, dtype=bool)].copy()
    observed = locks or {}
    locked = np.array([observed.get(s) is True for s in p.index], dtype=bool)
    counts['known_locked_exclusions'] = int(locked.sum())
    p = p.loc[~locked].copy()
    p['day'], p['symbol'] = day, p.index
    p['std20'] = [((std20 or {}).get(s, np.nan)) for s in p.index]
    p['price_source'] = ['official_checked_wide' if s in refs else 'official_only' for s in p.index]
    p['lock_status'] = ['unlocked' if observed.get(s) is False else 'unknown' for s in p.index]
    p['calendar_status'] = 'known_announcements_only_incomplete'
    counts['candidates'] = len(p)
    return p[POOL_COLUMNS].reset_index(drop=True).sort_values(['gap', 'symbol'], kind='stable'), dict(counts)


def select(pools, index, variant):
    """Ordinary days only; panic priority is independent of variant."""
    if variant not in ('A', 'B', 'C'):
        raise ValueError(variant)
    p = pools[pools.day.isin(index.index[index.gap >= -.005])].copy()
    if variant == 'B':
        p = p[p.std20.notna() & (p.gap <= -2*p.std20)]
    elif variant == 'C':
        p = p[p.day.isin(index.index[index.gap <= .002])]
    return p.sort_values(['day', 'gap', 'symbol'], kind='stable').groupby('day').head(2).copy()


def prepare():
    OUT.mkdir(exist_ok=True)
    marker(OUT/'preparation_started.json', {'utc': datetime.now().astimezone().isoformat(), 'code': sha(__file__)})
    inputs = {}

    def source(path):
        path = Path(path)
        inputs[str(path.relative_to(ROOT))] = sha(path)
        return path

    registration = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split('## H21:')[1]
    (OUT/'preregistration.md').write_text('## H21:'+registration, encoding='utf-8')
    source(ROOT/'RUNBOOK.md')
    wide = pd.read_pickle(source(ROOT/'data/wide_1d_10y.pkl'))
    n = nifty()
    n.to_csv(OUT/'calendar.csv', index_label='day')
    wide_refs = {}
    # References pair today's open with yesterday's close; never today's close.
    for symbol, b in wide.items():
        if symbol in ('NIFTY 50', 'NIFTY BANK'):
            continue
        b = dated_wide(b).reindex(sorted(set(n.index) | set(n.prev_day)))
        refs = pd.DataFrame({'open': b.o, 'prev_close': b.c.shift()})
        for r in refs[refs.open.notna()].itertuples():
            wide_refs.setdefault(r.Index, {})[symbol] = {'open': float(r.open),
                'prev_close': float(r.prev_close) if pd.notna(r.prev_close) else None}
    changes = pd.read_csv(source(ROOT/'data/h17/symbolchange.csv.raw'))
    changes.columns = changes.columns.str.strip()
    for col in ('OLD SYMBOL', 'NEW SYMBOL'):
        changes[col] = changes[col].str.strip()
    changes['effective'] = pd.to_datetime(changes['EFFECTIVE DATE'], format='%d-%b-%Y').dt.strftime('%Y-%m-%d')
    changes = changes[changes['OLD SYMBOL'] != changes['NEW SYMBOL']].to_dict('records')
    extra = json.loads(source(OUT/'dated_aliases.json').read_text())
    for row in extra:
        evidence = json.loads(source(ROOT/row['source']).read_text())
        assert any(x.get('desc') == 'Name and Symbol Change' and
                   f"from {row['OLD SYMBOL']} to {row['NEW SYMBOL']}" in x.get('attchmntText', '')
                   for x in evidence['rows']), 'Dated alias lacks cached official evidence'
    changes = sorted(changes+extra, key=lambda x: x['effective'], reverse=True)
    events = pd.concat([pd.read_pickle(source(ROOT/'data'/f)) for f in
        ('wide_events_2016.pkl', 'wide_events_old.pkl', 'wide_events.pkl')], ignore_index=True).drop_duplicates()
    events['symbol'] = [historical_symbol(r.symbol, r.day, changes)[0] for r in events.itertuples()]
    event_rows = {}
    for d, g in events.groupby('day'):
        for offset in range(-3, 4):
            day = (pd.Timestamp(d)+pd.Timedelta(days=offset)).strftime('%Y-%m-%d')
            event_rows.setdefault(day, []).append(g)
    actions = pd.read_csv(source(ROOT/'data/h17/actions.csv'))
    actions['symbol'] = [historical_symbol(r.api_symbol, r.day, changes)[0] for r in actions.itertuples()]
    coverage = json.loads(source(ROOT/'data/h17/action_coverage.json').read_text())
    assert coverage['complete']
    action_by_day = {d: set(g.symbol) for d, g in actions[actions.exclude.astype(str).str.lower() == 'true'].groupby('day')}
    basis_actions = json.loads(source(ROOT/'data/price_basis_actions.json').read_text())
    funds = json.loads(source(ROOT/'data/h9/instrument_inventory.json').read_text())['etf_or_fund_symbols']
    for f in ('strict_cleaned_selected.csv', 'strict_cleaned_stop3_account.csv'):
        source(ROOT/'data/h17'/f)
    for f in PROTECTED + ['stalk/sim/price_basis.py', 'stalk/market/costs.py', 'scripts/h17_rebuild.py']:
        source(ROOT/f)
    pools, rows, history = [], [], {}
    dates = sorted(set(n.index) | set(n.prev_day))
    previous = pd.DataFrame(columns=['value'])
    for position, day in enumerate(dates):
        path = bhav_path(day)
        today = pd.read_pickle(source(path)) if path.exists() else pd.DataFrame()
        if today.index.duplicated().any():
            raise ValueError(f'duplicate official symbol {day}')
        if day in n.index:
            status = 'ok' if not today.empty and not previous.empty else 'missing_price_or_predecessor'
            row = {'day': day, 'nifty_gap': float(n.at[day, 'gap']), 'panic': bool(n.at[day, 'gap'] < -.005),
                   'status': status, 'opening_lock_evidence': 'unavailable',
                   'event_calendar': 'incomplete_timestamped_survivor_calendars'}
            if status == 'ok' and not row['panic']:
                scheduled = pd.concat(event_rows[day], ignore_index=True) if day in event_rows else events.iloc[:0]
                announced_actions = []
                for a in basis_actions:
                    if a['day'] == day:
                        published = pd.to_datetime(a['published'], format='%d-%b-%Y %H:%M:%S', errors='coerce')
                        if pd.notna(published) and published < pd.Timestamp(day+' 09:15'):
                            announced_actions.append(a['symbol'])
                p, audit = pool(day, today, previous, wide_refs=wide_refs.get(day),
                    results=known_results(scheduled, day), actions=action_by_day.get(day, ()), funds=funds,
                    announced_actions=announced_actions, std20=past_std(history, position,
                        today.index[today.open/today.prev_close-1 < -.02]))
                pools.append(p)
                row.update(audit)
            rows.append(row)
        if not today.empty:
            update_history(history, today, position)
        previous = today
        if position % 400 == 0:
            print(f'Prepared {position}/{len(dates)} sessions (signal fields only)', flush=True)
    pd.concat(pools, ignore_index=True).to_csv(OUT/'everyday_pools.csv', index=False)
    pd.DataFrame(rows).to_csv(OUT/'coverage.csv', index=False)
    source(OUT/'everyday_pools.csv')
    source(OUT/'coverage.csv')
    source(OUT/'calendar.csv')
    source(OUT/'preregistration.md')
    source(Path(__file__))
    dump(OUT/'input_manifest.json', inputs)
    dump(OUT/'preparation.json', {'sessions': len(n), 'ordinary_sessions': int((n.gap >= -.005).sum()),
        'coverage_status': pd.Series([r['status'] for r in rows]).value_counts().to_dict(),
        'candidate_rows': int(sum(len(p) for p in pools)), 'candidate_days': int(sum(bool(len(p)) for p in pools)),
        'wide_equities': len(set(wide)-{'NIFTY 50', 'NIFTY BANK'}), 'events': len(events), 'code': sha(__file__),
        'protected': {f: sha(ROOT/f) for f in PROTECTED}})
    print('Preparation saved. No trade returns scored.', flush=True)


def execute(selected):
    rows = []
    for day, g in selected.groupby('day', sort=True):
        official = pd.read_pickle(bhav_path(day))
        for r in g.itertuples():
            b = official.loc[r.symbol]
            values = b[['open', 'high', 'low', 'close']].to_numpy(dtype=float)
            if (not np.isfinite(values).all() or (values <= 0).any()
                    or b.high < max(b.open, b.close, b.low) or b.low > min(b.open, b.close, b.high)):
                raise ValueError(f'invalid execution OHLC {day} {r.symbol}; preserve scoring guard')
            entry = r.open*1.001
            qty = int(5000//entry)
            if qty <= 0:
                continue
            stopped = bool(b.low <= entry*.97)
            exit_px = (entry*.97*.998 if stopped else b.close*.9992)*.999
            gross = (exit_px-entry)*qty
            charges = round_trip_cost(qty, entry, exit_px)
            rows.append(dict(day=day, symbol=r.symbol, gap=r.gap, qty=qty, entry=entry, exit=exit_px,
                gross=gross, charges=charges, net=gross-charges, net_return=(gross-charges)/(qty*entry),
                reason='stop' if stopped else 'flatten', leg='everyday'))
    return pd.DataFrame(rows, columns=TRADE_COLUMNS)


def statistics(trades, covered_days, all_days):
    daily = trades.groupby('day').net.sum().reindex(covered_days, fill_value=0.)
    active = trades.groupby('day').net.sum()
    equity = np.r_[10000., 10000.+daily.cumsum().to_numpy()]
    wins, losses = trades.loc[trades.net > 0, 'net'], trades.loc[trades.net < 0, 'net']
    return dict(trades=len(trades), days_traded=int(trades.day.nunique()), days_traded_share=float(trades.day.nunique()/len(all_days)),
        requested_sessions=len(all_days), covered_sessions=len(covered_days), unknown_sessions=len(all_days)-len(covered_days),
        trades_per_year=len(trades)/10, net=float(trades.net.sum()), net_per_year=float(trades.net.sum()/10),
        win_rate=float((trades.net > 0).mean()) if len(trades) else None,
        average_win=float(wins.mean()) if len(wins) else None, average_loss=float(losses.mean()) if len(losses) else None,
        net_per_trade=float(trades.net.mean()) if len(trades) else None,
        net_bps_per_trade=float(trades.net_return.mean()*10000) if len(trades) else None,
        day_t=tstat(daily), active_day_t=tstat(active), max_drawdown=float((np.maximum.accumulate(equity)-equity).max()),
        minimum_start_equity=float(equity[:-1].min()) if len(daily) else 10000.,
        fully_funded=bool((equity[:-1] >= 10000).all()), charges=float(trades.charges.sum()),
        yearly_net=trades.groupby(trades.day.str[:4]).net.sum().to_dict())


def checks(everyday, hybrid, panic):
    return dict(everyday_net_positive=everyday['net'] > 0,
        everyday_day_t_ge_2=everyday['day_t'] is not None and everyday['day_t'] >= 2.,
        drawdown_le_1_5_panic=hybrid['max_drawdown'] <= 1.5*panic['max_drawdown'])


def verify_inputs():
    manifest = json.loads((OUT/'input_manifest.json').read_text())
    for f, digest in manifest.items():
        # RUNBOOK acquires results after scoring; use frozen registration below.
        if f == 'RUNBOOK.md' and (OUT/'summary.json').exists():
            continue
        assert sha(ROOT/f) == digest, f'Input changed: {f}'
    return manifest


def score():
    if (OUT/'started.json').exists():
        raise RuntimeError('H21 already started; do not rescore, tune, or overwrite outcomes.')
    manifest = verify_inputs()
    marker(OUT/'started.json', {'utc': datetime.now().astimezone().isoformat(), 'trials': 3,
        'code': sha(__file__), 'manifest': sha(OUT/'input_manifest.json'), 'inputs': len(manifest)})
    shutil.copy2(__file__, OUT/'scored_code.py')
    n = pd.read_csv(OUT/'calendar.csv', index_col='day')
    pools = pd.read_csv(OUT/'everyday_pools.csv')
    coverage = pd.read_csv(OUT/'coverage.csv')
    panic = pd.read_csv(ROOT/'data/h17/strict_cleaned_stop3_account.csv')
    panic['reason'] = np.where(panic.stopped, 'stop', 'flatten')
    panic['leg'] = 'panic'
    panic = panic[TRADE_COLUMNS]
    panic_days = list(n.index[n.gap < -.005])
    ordinary = list(n.index[n.gap >= -.005])
    covered_ordinary = list(coverage.loc[(coverage.status == 'ok') & ~coverage.panic, 'day'])
    covered = sorted(set(panic_days) | set(covered_ordinary))
    baseline = statistics(panic, list(n.index), list(n.index))
    panic.to_csv(OUT/'panic_trades.csv', index=False)
    result = dict(basis=NOTE, trials=3, scoring_runs=1, panic_only=baseline, variants={})
    for v in ('A', 'B', 'C'):
        picks = select(pools, n, v)
        picks.to_csv(OUT/f'{v}_everyday_selected.csv', index=False)
        everyday = execute(picks)
        hybrid = pd.concat([panic, everyday], ignore_index=True).sort_values(['day', 'gap', 'symbol'], kind='stable')
        everyday.to_csv(OUT/f'{v}_everyday_trades.csv', index=False)
        hybrid.to_csv(OUT/f'{v}_hybrid_trades.csv', index=False)
        e, h = statistics(everyday, covered_ordinary, ordinary), statistics(hybrid, covered, list(n.index))
        gates = checks(e, h, baseline)
        week = []
        for day in pd.date_range('2024-12-09', '2024-12-13').strftime('%Y-%m-%d'):
            g = hybrid[hybrid.day == day]
            leg = 'panic' if n.at[day, 'gap'] < -.005 else 'everyday'
            eligible_leg = leg if leg == 'panic' or v != 'C' or n.at[day, 'gap'] <= .002 else 'none (market gap above +0.2%)'
            selected = (pd.read_csv(ROOT/'data/h17/strict_cleaned_selected.csv') if leg == 'panic' else picks)
            week.append(dict(day=day, nifty_gap=float(n.at[day, 'gap']), leg=eligible_leg,
                traded_leg=leg if len(g) else 'none', selected_picks=list(selected.loc[selected.day == day, 'symbol']),
                net=float(g.net.sum()), trades=g.to_dict('records')))
        h['delta_net_per_year_vs_panic'] = h['net_per_year']-baseline['net_per_year']
        h['drawdown_ratio_vs_panic'] = h['max_drawdown']/baseline['max_drawdown']
        result['variants'][v] = dict(verdict='Passed' if all(gates.values()) else 'Failed',
            checks=gates, hybrid=h, everyday=e, week_net=float(sum(d['net'] for d in week)), week=week,
            selected_everyday=len(picks), unfunded_everyday=len(picks)-len(everyday))
        daily = hybrid.groupby('day').net.sum().reindex(n.index, fill_value=0.).rename('net').to_frame()
        daily['status'] = ['covered' if d in covered else 'unknown_price' for d in daily.index]
        daily.loc[daily.status != 'covered', 'net'] = np.nan
        daily.to_csv(OUT/f'{v}_days.csv', index_label='day')
    dump(OUT/'summary.json', result)
    paths = [p for p in OUT.glob('*.csv')] + [OUT/'summary.json', OUT/'scored_code.py', OUT/'preregistration.md']
    dump(OUT/'output_manifest.json', {p.name: sha(p) for p in sorted(paths)})
    print(json.dumps({v: {k: r[k] for k in ('verdict', 'week_net', 'hybrid', 'everyday')}
                      for v, r in result['variants'].items()}, indent=2, allow_nan=False), flush=True)


def table_digest(conn, table, where='', params=()):
    rows = conn.execute(f'SELECT * FROM {table} {where} ORDER BY rowid', params).fetchall()
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode()).hexdigest()


def history():
    from stalk.data.store import Store
    result = json.loads((OUT/'summary.json').read_text())
    saved = json.loads((OUT/'output_manifest.json').read_text())
    assert all(sha(OUT/p) == digest for p, digest in saved.items())
    backup = OUT/'before_history.db'
    if not backup.exists():
        with sqlite3.connect(ROOT/'data/stalk.db') as conn, sqlite3.connect(backup) as dest:
            conn.backup(dest)
    st = Store()
    n = pd.read_csv(OUT/'calendar.csv', index_col='day')
    coverage = pd.read_csv(OUT/'coverage.csv').set_index('day')
    ids = []
    try:
        for v, r in result['variants'].items():
            full = pd.read_csv(OUT/f'{v}_hybrid_trades.csv')
            for horizon in ('week', '10y'):
                cid = f'h21_hybrid_{v.lower()}_{horizon}'
                acct = cid+':panic_buy'
                st.delete_sessions(acct)
                ids.append(cid)
                dates = list(n.index) if horizon == '10y' else list(pd.date_range('2024-12-09', '2024-12-13').strftime('%Y-%m-%d'))
                trades = full[full.day.isin(dates)]
                days, curve, equity, peak, dd = [], [], 10000., 10000., 0.
                for day in dates:
                    g = trades[trades.day == day]
                    covered = n.at[day, 'gap'] < -.005 or coverage.at[day, 'status'] == 'ok'
                    start = equity
                    equity += float(g.net.sum())
                    peak = max(peak, equity)
                    dd = max(dd, peak-equity)
                    d = dict(day=day, start_equity=round(start, 2), end_equity=round(equity, 2),
                        gross=round(float(g.gross.sum()), 2) if covered else None,
                        costs=round(float(g.charges.sum()), 2) if covered else None,
                        net=round(float(g.net.sum()), 2) if covered else None,
                        trades=len(g), wins=int((g.net > 0).sum()), losses=int((g.net <= 0).sum()),
                        status='daily_proxy' if covered else 'unknown_price',
                        nifty_gap=float(n.at[day, 'gap']), leg='panic' if n.at[day, 'gap'] < -.005 else 'everyday')
                    days.append(d)
                    curve.append([day, round(equity, 2)])
                    st.save_session(acct+':'+day, 'panic_buy', day, acct, {'daily_proxy': True, 'variant': v}, d, ended=True)
                    for t in g.itertuples():
                        st.save_trade(dict(id=acct+':'+day+':'+t.symbol, session_id=acct+':'+day,
                            day=day, strategy='panic_buy', symbol=t.symbol, side='long', qty=int(t.qty),
                            entry_ts=ts(day, '09:15'), entry_price=t.entry,
                            exit_ts=None if t.reason == 'stop' else ts(day, '15:30'), exit_price=t.exit,
                            stop_initial=t.entry*.97, gross=t.gross, costs=t.charges, net=t.net,
                            reason=t.reason, fee_version=FEE_SCHEDULE_VERSION, broker='backtest'))
                yearly = trades.groupby(trades.day.str[:4]).net.sum()
                tax = float(yearly.clip(lower=0).sum()*.3)
                account = dict(name='panic_buy', strategies=['panic_buy'], require_validated=False,
                    start=10000., end=round(equity, 2), gross=round(float(trades.gross.sum()), 2),
                    costs=round(float(trades.charges.sum()), 2), net=round(float(trades.net.sum()), 2),
                    tax=round(tax, 2), after_tax=round(float(trades.net.sum())-tax, 2), trades=len(trades),
                    green_days=sum(d['net'] is not None and d['net'] > 0 for d in days),
                    red_days=sum(d['net'] is not None and d['net'] < 0 for d in days),
                    flat_days=sum(d['net'] == 0 for d in days), unknown_days=sum(d['net'] is None for d in days),
                    max_dd=round(dd, 2), stats=dict(n=len(trades), win=float((trades.net > 0).mean()) if len(trades) else None,
                        net=round(float(trades.net.sum()), 2), t=tstat([d['net'] for d in days if d['net'] is not None])),
                    per_strategy={}, days=days, curve=curve)
                label = f'Hybrid {v}, '+('10 years' if horizon == '10y' else 'week of 9 Dec 2024')
                summary = dict(id=cid, first_day=dates[0], last_day=dates[-1], days=len(days), capital=10000.,
                    accounts={'panic_buy': account}, weeks=[], kind='panic_buy', label=label,
                    note=NOTE, research_status=r['verdict'], daily_proxy=True, variant=v,
                    unknown_days=account['unknown_days'])
                st.save_campaign(cid, dates[0], dates[-1], {'bar_minutes': 5, 'label': label, 'daily_proxy': True}, summary)
        dump(OUT/'history_update.json', {'campaigns': ids, 'backup': backup.name})
    finally:
        st._conn.close()
    print('Saved six H21 campaigns with standard stop/flatten reasons.', flush=True)


def validate():
    manifest = verify_inputs()
    frozen = json.loads((OUT/'started.json').read_text())
    assert frozen['code'] == sha(__file__) == sha(OUT/'scored_code.py')
    assert frozen['manifest'] == sha(OUT/'input_manifest.json')
    outputs = json.loads((OUT/'output_manifest.json').read_text())
    assert all(sha(OUT/f) == digest for f, digest in outputs.items())
    r = json.loads((OUT/'summary.json').read_text())
    old = pd.read_csv(ROOT/'data/h17/strict_cleaned_stop3_account.csv').sort_values(['day', 'symbol'])
    for v in ('A', 'B', 'C'):
        t = pd.read_csv(OUT/f'{v}_hybrid_trades.csv')
        panic = t[t.leg == 'panic'].sort_values(['day', 'symbol'])
        assert list(zip(panic.day, panic.symbol)) == list(zip(old.day, old.symbol))
        for c in ('qty', 'entry', 'exit', 'gross', 'charges', 'net'):
            np.testing.assert_allclose(panic[c], old[c], rtol=1e-12, atol=1e-9)
        assert set(t.reason) <= {'stop', 'flatten'}
        assert (t.qty*t.entry <= 5000+1e-8).all()
        assert t.groupby('day').size().max() <= 2
        assert abs(t.net.sum()-r['variants'][v]['hybrid']['net']) < 1e-7
        assert (t.gross-t.charges-t.net).abs().max() < 1e-8
        for a in t.itertuples():
            assert abs(round_trip_cost(a.qty, a.entry, a.exit)-a.charges) < 1e-8
    with sqlite3.connect(OUT/'before_history.db') as before, sqlite3.connect(ROOT/'data/stalk.db') as after:
        for table in ('campaigns', 'sessions', 'trades', 'logs', 'trials', 'bars', 'events', 'news'):
            pre_rows = before.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            if table == 'campaigns':
                post_rows = after.execute("SELECT * FROM campaigns WHERE id NOT LIKE 'h21_hybrid_%' ORDER BY rowid").fetchall()
            elif table == 'sessions':
                post_rows = after.execute("SELECT * FROM sessions WHERE id NOT LIKE 'h21_hybrid_%' ORDER BY rowid").fetchall()
            elif table == 'trades':
                post_rows = after.execute("SELECT * FROM trades WHERE id NOT LIKE 'h21_hybrid_%' ORDER BY rowid").fetchall()
            else:
                post_rows = after.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall()
            assert pre_rows == post_rows, f'Existing {table} changed'
        rows = after.execute("SELECT reason FROM trades WHERE id LIKE 'h21_hybrid_%'").fetchall()
        assert all(x[0] in ('stop', 'flatten') for x in rows)
        for v in ('a', 'b', 'c'):
            for horizon in ('week', '10y'):
                cid = f'h21_hybrid_{v}_{horizon}'
                sm = json.loads(after.execute('SELECT summary FROM campaigns WHERE id=?', (cid,)).fetchone()[0])
                totals = after.execute('SELECT COUNT(*),COALESCE(SUM(net),0) FROM trades WHERE session_id LIKE ?', (cid+':%',)).fetchone()
                account = sm['accounts']['panic_buy']
                assert totals[0] == account['trades'] and abs(totals[1]-account['net']) < .0051
                assert abs(totals[1]-(r['variants'][v.upper()]['week_net'] if horizon == 'week'
                           else r['variants'][v.upper()]['hybrid']['net'])) < 1e-7
    dump(OUT/'validation.json', {'status': 'ok', 'input_hashes': len(manifest), 'output_hashes': len(outputs),
        'protected_sources': 'unchanged', 'panic_branch': 'exact H17 rows/fills/fees',
        'history': 'six campaigns reconciled; all pre-existing rows unchanged',
        'limit': 'Arithmetic and provenance only; missing causal lock/calendar evidence remains unresolved.'})
    print('H21 saved-output, History, input and protected-source checks passed.', flush=True)


if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prepare', 'score', 'history', 'validate'])
    {'prepare': prepare, 'score': score, 'history': history, 'validate': validate}[parser.parse_args().mode]()
