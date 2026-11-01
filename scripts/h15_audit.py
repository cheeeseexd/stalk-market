"""H15 price/metadata audit and retained legacy replay; no H14 return scoring."""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch
import io
import zipfile

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h9_fetch import bhav_path
from stalk.config import BASE_DIR, IST
from stalk.sim import panic, panic_replay
from stalk.sim.price_basis import differs, resolve

ROOT = Path(BASE_DIR)
OUT = ROOT / "data/h15"


def dump(name, obj):
    (OUT / name).write_text(json.dumps(obj, indent=2, default=str, allow_nan=False), encoding="utf-8")


def scale(ratio):
    if pd.isna(ratio) or abs(ratio - 1) <= .01 + 1e-12:
        return "none"
    for value in (.1, .2, .25, 1/3, .4, .5, 2/3, .75, 1.25, 1.5, 2, 2.5, 3, 4, 5, 10):
        if abs(ratio / value - 1) <= .02:
            return f"{value:.6g}"
    return "other"


def actions():
    """Explicit dated split/bonus notices; record dates are ex-date proxies, not invented dates."""
    found = []
    for path in sorted((ROOT / 'data/h9/announcements').glob('*.json')):
        obj = json.loads(path.read_text(encoding="utf-8"))
        for row in obj.get("rows", []):
            text = str(row.get("attchmntText") or "")
            if not re.search(r"split|subdivi|bonus", text, re.I):
                continue
            match = re.search(r"Record date.*?\bis\s+(\d{1,2}-[A-Za-z]{3}-\d{4})", text, re.I)
            if not match:
                continue
            action_day = datetime.strptime(match[1], "%d-%b-%Y").strftime("%Y-%m-%d")
            published = row.get("exchdisstime") or row.get("an_dt")
            found.append({"symbol": row.get("symbol"), "day": action_day,
                          "published": published, "text": text,
                          "date_type": "announced_record_date_ex_date_proxy",
                          "source": str(path.relative_to(ROOT)),
                          "attachment": row.get("attchmntFile")})
    dump("corporate_actions.json", found)
    (ROOT / 'data/price_basis_actions.json').write_text(json.dumps(found, indent=2), encoding='utf-8')
    print("Explicit split/bonus notices:", len(found), flush=True)
    return found


def audit():
    frames = pd.read_pickle(ROOT / "data/wide_5m.pkl")
    grouped = {}
    for symbol, frame in frames.items():
        dates = pd.to_datetime(frame.ts, unit='s', utc=True).dt.tz_convert(IST).dt.strftime('%Y-%m-%d')
        grouped[symbol] = {d: g.sort_values('ts') for d, g in frame.groupby(dates)}
    days = sorted(grouped['NIFTY 50'])
    symbols = sorted(set(grouped) - {'NIFTY 50', 'INDIA VIX'})
    official = {d: pd.read_pickle(bhav_path(d)) if bhav_path(d).exists() else pd.DataFrame() for d in days}
    ca = actions()
    ca_keys = {(x['symbol'], x['day']) for x in ca}
    records = []
    for i, day in enumerate(days):
        prev_day = days[i-1] if i else None
        for symbol in symbols:
            g, p = grouped[symbol].get(day), grouped[symbol].get(prev_day)
            b = official[day]
            row = {'day': day, 'prev_day': prev_day, 'symbol': symbol,
                   'yahoo_day_missing': g is None, 'yahoo_prev_missing': p is None,
                   'official_missing': symbol not in b.index,
                   'known_split_bonus_record_date': (symbol, day) in ca_keys}
            yo = float(g.iloc[0].o) if g is not None else None
            yp = float(p.iloc[-1].c) if p is not None else None
            row.update(yahoo_open=yo, yahoo_prev_last_close=yp,
                       first_bar_missing=g is not None and int(g.iloc[0].ts) != panic_replay.stamp(day, 915))
            if symbol in b.index:
                op, pc = map(float, (b.loc[symbol, 'open'], b.loc[symbol, 'prev_close']))
                row.update(official_open=op, official_prev_close=pc,
                           open_ratio=yo / op if yo is not None and op > 0 else None,
                           prev_ratio=yp / pc if yp is not None and pc > 0 else None,
                           open_difference_over_1pct=differs(yo, op) if yo is not None else False,
                           prev_difference_over_1pct=differs(yp, pc) if yp is not None else False,
                           official_gap=op / pc - 1 if pc > 0 else None,
                           yahoo_gap=yo / yp - 1 if yo is not None and yp is not None and yp > 0 else None)
                if prev_day and symbol in official[prev_day].index:
                    row['official_reference_to_prior_close'] = pc / float(official[prev_day].loc[symbol, 'close'])
                if yo is not None:
                    basis = resolve(day, symbol, yo, yp, official={symbol: {'open': op, 'prev_close': pc}})
                    row['price_mismatch'] = basis['mismatch']
            row['open_scale'], row['prev_scale'] = scale(row.get('open_ratio')), scale(row.get('prev_ratio'))
            records.append(row)
    a = pd.DataFrame(records)
    a.to_csv(OUT / 'stock_days.csv', index=False)
    flags = ['yahoo_day_missing', 'yahoo_prev_missing', 'official_missing', 'first_bar_missing',
             'known_split_bonus_record_date', 'open_difference_over_1pct', 'prev_difference_over_1pct', 'price_mismatch']
    counts = {k: int(a[k].fillna(False).astype(bool).sum()) for k in flags}
    by_symbol = a.groupby('symbol')[flags].sum().astype(int)
    by_symbol.to_csv(OUT / 'symbol_counts.csv')
    mismatches = a[a.price_mismatch.fillna(False).astype(bool)]
    mismatches.to_csv(OUT / 'mismatches.csv', index=False)
    scales = a[(a.open_scale != 'none') & (a.open_scale != 'other') | (a.prev_scale != 'none') & (a.prev_scale != 'other')]
    scales.to_csv(OUT / 'scale_ratios.csv', index=False)
    a[a.known_split_bonus_record_date].to_csv(OUT / 'action_days.csv', index=False)
    raw_evidence = []
    for day, g in a[a.official_missing].groupby('day'):
        path = ROOT / 'data/h9/raw_bhav' / f"BhavCopy_NSE_CM_0_0_0_{day.replace('-', '')}_F_0000.csv.zip"
        if not path.exists():
            continue
        with zipfile.ZipFile(path) as archive:
            raw = pd.read_csv(io.BytesIO(archive.read(archive.namelist()[0])))
        for symbol in g.symbol:
            for row in raw[raw.TckrSymb == symbol].itertuples():
                raw_evidence.append({'day': day, 'symbol': symbol, 'series': row.SctySrs,
                                     'open': row.OpnPric, 'prev_close': row.PrvsClsgPric,
                                     'source': str(path.relative_to(ROOT))})
    pd.DataFrame(raw_evidence).to_csv(OUT / 'non_eq_raw_evidence.csv', index=False)
    daily = pd.read_pickle(ROOT / 'data/wide_1d_10y.pkl')['NIFTY 50']
    daily_dates = pd.to_datetime(daily.ts, unit='s', utc=True).dt.tz_convert(IST).dt.strftime('%Y-%m-%d')
    daily = dict(zip(daily_dates, daily.c))
    index_rows = []
    for day, prev in zip(days[1:], days):
        op = float(grouped['NIFTY 50'][day].iloc[0].o)
        last = float(grouped['NIFTY 50'][prev].iloc[-1].c)
        close = float(daily[prev])
        index_rows.append({'day': day, 'lastbar_gap': op / last - 1, 'daily_close_gap': op / close - 1,
                           'strict_gate_changed': (op / last - 1 < -.005) != (op / close - 1 < -.005),
                           'loose_gate_changed': (op / last - 1 < -.003) != (op / close - 1 < -.003)})
    pd.DataFrame(index_rows).to_csv(OUT / 'index_check.csv', index=False)
    result = {'sessions': len(days), 'first_day': days[0], 'last_day': days[-1], 'symbols': len(symbols),
              'stock_days': len(a), 'counts': counts,
              'mismatch_symbols': sorted(mismatches.symbol.unique()),
              'scale_symbols': sorted(scales.symbol.unique()),
              'open_scales': a.open_scale.value_counts().to_dict(), 'prev_scales': a.prev_scale.value_counts().to_dict(),
              'index_gate_changes': {k: sum(r[k] for r in index_rows) for k in ('strict_gate_changed', 'loose_gate_changed')},
              'official_prev_equals_prior_close': int((a.official_reference_to_prior_close.sub(1).abs() < 1e-10).sum()),
              'official_prev_prior_close_comparable': int(a.official_reference_to_prior_close.notna().sum())}
    result.update(simple_ratio_relative_tolerance=.02,
                  mismatch_symbol_count=int(mismatches.symbol.nunique()),
                  first_session_without_cached_predecessor=len(symbols),
                  subsequent_missing_yahoo_predecessors=int(a.loc[a.day != days[0], 'yahoo_prev_missing'].sum()))
    dump('audit_summary.json', result)
    print(json.dumps(result, indent=2), flush=True)
    # Document direct exposure of the existing ten-year selections. No return retest.
    picks = pd.read_csv(ROOT / 'data/panic_final/selected.csv')
    action_frame = pd.DataFrame(ca).drop_duplicates(['symbol', 'day'])
    match = picks.merge(action_frame, on=['symbol', 'day'])
    match.to_csv(OUT / 'h7d_known_action_selections.csv', index=False)
    extreme = picks[picks.gap < -.25]
    extreme.to_csv(OUT / 'h7d_extreme_gap_selections.csv', index=False)
    journal = pd.read_csv(ROOT / 'data/panic_journal.csv')
    journal_matches = journal.merge(action_frame, on=['symbol', 'day'])
    journal_matches.to_csv(OUT / 'h7_journal_known_action_trades.csv', index=False)
    h7c = pd.read_pickle(ROOT / 'data/panic_bhav_trades.pkl')
    h7c_matches = h7c[h7c.panic].merge(action_frame, on=['symbol', 'day'])
    h7c_matches.to_csv(OUT / 'h7c_known_action_selections.csv', index=False)
    dump('h7_exposure.json', {'h7d_selected': len(picks), 'explicit_record_date_matches': len(match),
                             'matches_symbols': sorted(match.symbol.unique()), 'gaps_below_minus25pct': len(extreme),
                             'h7c_original_panic_trades': int(h7c.panic.sum()),
                             'h7c_explicit_record_date_matches': len(h7c_matches),
                             'h7c_match_symbols': sorted(h7c_matches.symbol.unique()),
                             'journal_explicit_record_date_matches': len(journal_matches),
                             'ca_complete': False, 'retrospective_corrected_pnl_scored': False})


def baseline():
    spec = importlib.util.spec_from_file_location('h15_legacy_replay', OUT / 'before/stalk/sim/panic_replay.py')
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    grouped, tiers, results = panic_replay.load_cache()
    recent, trades = [], []
    market = panic.market_qualifies
    for label, gap in [('frozen', -.005), ('loose', -.003)]:
        # Explicit threshold reproduces the previously saved strict/loose intent.
        # Mutating INDEX_GAP alone cannot alter Python's bound default argument.
        with patch.object(panic, 'market_qualifies', side_effect=lambda op, pc: market(op, pc, gap)):
            for week in panic_replay.weeks_available(grouped):
                last = legacy.build_week(grouped, tiers, results, week)[-1]
                recent.append({'rule': label, 'week': week, 'trades': len(last['trades']), 'net': last['realized']})
                trades.extend({'rule': label, 'week': week, **t} for t in last['trades'])
    pd.DataFrame(recent).to_csv(OUT / 'legacy_reconstructed_weeks.csv', index=False)
    pd.DataFrame(trades).to_csv(OUT / 'before_trades.csv', index=False)
    saved = pd.read_csv(OUT / 'before/data/panic_weeks/recent_weeks.csv')
    check = saved.merge(pd.DataFrame(recent), on=['rule', 'week'], suffixes=('_saved', '_rebuilt'))
    if len(check) != 24 or (check.trades_saved != check.trades_rebuilt).any() or (check.net_saved-check.net_rebuilt).abs().max() > .005:
        raise RuntimeError('Legacy replay does not reconcile to saved results')
    dump('baseline_reconciliation.json', {'runs': len(check), 'max_net_difference': float((check.net_saved-check.net_rebuilt).abs().max()),
                                         'loose_default_argument_bug': True,
                                         'explanation': 'Passing the threshold explicitly reproduces all saved runs; mutating INDEX_GAP alone does not change market_qualifies default.'})
    print('Baseline matches all 24 saved runs', flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('mode', choices=['audit', 'baseline'])
    args = p.parse_args()
    {'audit': audit, 'baseline': baseline}[args.mode]()
