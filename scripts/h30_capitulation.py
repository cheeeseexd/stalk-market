"""H30 capitulation reversal (RUNBOOK "## H30:"), scored once on Kite 5-minute bars. Paper research only.

Every day, long and short: a stock that has moved >= 3% from its prior close by a 09:30-10:55 bar, on >= 3x that bar
time's usual volume, with the bar closing back off its extreme, while the market proxy is calm. Enter the next bar's
open, 1.5% stop, flatten at the 15:10 bar open. At most 2 a day. Compared with 1,000 random-stock histories.

    python scripts/h30_capitulation.py --full      # guarded: one run, writes data/h30/
Features use only earlier sessions (14-session averages are shifted) and the signal bar itself; entry is the next bar.
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

from stalk.config import IST
from stalk.market.costs import round_trip_cost
from stalk.research.orb import metrics, normalize, tstat, universe
from stalk.sim.panic import SLIP

GRID = [h*100+m for h in range(9, 16) for m in range(0, 60, 5) if 915 <= h*100+m <= 1525]   # 75 bars
SIGNAL = [hm for hm in GRID if 930 <= hm <= 1055]
EXIT_HM = 1510
MOVE, VOL_X, CALM, STOP, SLOT, MAX_TRADES = 0.03, 3.0, 0.005, 0.015, 5000.0, 2
STRESSES = {'assumed': 0.0, 'worse025': 0.0025, 'worse050': 0.005}
DESIGN_START, DESIGN_END, FINAL_START, FINAL_END = '2021-01-01', '2025-07-31', '2025-08-01', '2026-07-31'   # RUNBOOK amendment
CALENDAR_START = '2020-12-01'     # 14-session warm-up before the design window
COST = np.frompyfunc(round_trip_cost, 3, 1)


def symbol_tables(frame, calendar, causal=False):
    """Per-day prior close, eligibility inputs and same-bar-time volume averages, all from earlier sessions."""
    f = normalize(frame)
    g = f.groupby('day')
    daily = pd.DataFrame(dict(n=g.ts.size(), first=g.hm.min(), last=g.hm.max(), o=g.o.first(), c=g.c.last(),
                              turnover=(f.c*f.v).groupby(f.day).sum())).reindex(calendar)
    complete = (daily.n >= 70) & (daily['first'] == 915) & (daily['last'] >= 1525)
    # Prior close / turnover / bar volumes come from the last COMPLETE session before the day.
    pc = daily.c.where(complete).ffill().shift(1)
    turnover = daily.turnover.where(complete).rolling(14, min_periods=14).mean()
    turnover = turnover.where(complete).ffill().shift(1)
    vol = f.pivot_table(index='day', columns='hm', values='v', aggfunc='sum').reindex(index=calendar, columns=SIGNAL)
    vavg = vol.where(complete, axis=0).dropna(how='all').rolling(14, min_periods=14).mean()
    vavg = vavg.reindex(calendar).ffill().shift(1)
    vavg = vavg.where(pc.notna(), axis=0)
    bars = {k: f.pivot_table(index='day', columns='hm', values=k, aggfunc='first').reindex(index=calendar, columns=GRID if k in 'ohl' else SIGNAL)
            for k in ('o', 'h', 'l', 'c', 'v')}
    coherent = (daily.o/pc-1).abs() <= 0.10
    # H30 (scored) required TODAY to be complete, which is not known at 09:35 (Astra audit 2026-11-01). causal=True only needs
    # today's 09:15 bar; later missing bars are handled by the trade walk. H30's own default is unchanged.
    today = (daily['first'] == 915) if causal else complete
    eligible = today & coherent & (pc >= 50) & (turnover >= 1e8)
    return dict(pc=pc.to_numpy(float), eligible=eligible.to_numpy(bool), vavg=vavg.to_numpy(float),
                **{k: v.to_numpy(np.float32) for k, v in bars.items()})


def results_exclusions(days):
    """Same-day results notices known before 09:20 (stored calendar; completeness unknown)."""
    p = ROOT/'data/wide_events.pkl'
    if not p.exists():
        return {}
    ev = pd.read_pickle(p)
    ev = ev[(ev.purpose.fillna('')+' '+ev.detail.fillna('')).str.contains('result', case=False) & ev.announced_ts.notna()]
    out = {}
    for d in days:
        cutoff = int(datetime.fromisoformat(d+'T09:20:00').replace(tzinfo=IST).timestamp())
        out[d] = set(ev.loc[(ev.day == d) & (ev.announced_ts < cutoff), 'symbol'])
    return out


def build(frames, tiers, causal=False):
    """Fill (day, bar, stock) arrays one stock at a time to stay within memory. o/h/l cover every bar; c/v only signal bars."""
    symbols = sorted(set(frames) & set(tiers))
    calendar = sorted({d for s in symbols for d in normalize(frames[s]).day.unique() if d >= CALENDAR_START})
    D, S = len(calendar), len(symbols)
    B = dict(symbols=symbols, calendar=calendar, tiers=[tiers[s] for s in symbols],
             pc=np.full((D, S), np.nan), eligible=np.zeros((D, S), bool), vavg=np.full((D, len(SIGNAL), S), np.nan, np.float32),
             **{k: np.full((D, len(GRID), S), np.nan, np.float32) for k in 'ohl'},
             **{k: np.full((D, len(SIGNAL), S), np.nan, np.float32) for k in 'cv'})
    for j, s in enumerate(symbols):
        t = symbol_tables(frames[s], calendar, causal=causal)
        B['pc'][:, j], B['eligible'][:, j], B['vavg'][:, :, j] = t['pc'], t['eligible'], t['vavg']
        for k in 'ohlcv':
            B[k][:, :, j] = t[k]
    return B


def signals(B, excluded):
    """Every qualifying bar, then at most 2 a day by time, volume ratio, symbol. Uses the signal bar and earlier data only."""
    cal, syms = B['calendar'], B['symbols']
    elig = B['eligible'].copy()
    for i, d in enumerate(cal):
        for s in excluded.get(d, ()):
            if s in syms:
                elig[i, syms.index(s)] = False
    picked = []
    for i, d in enumerate(cal):
        if not elig[i].any():
            continue
        taken, count = set(), 0
        for j, hm in enumerate(SIGNAL):
            b = GRID.index(hm)
            h, l = B['h'][i, b], B['l'][i, b]
            c, v = B['c'][i, j], B['v'][i, j]
            ret = c/B['pc'][i]-1
            ok = elig[i] & np.isfinite(ret)
            if ok.sum() < 20:
                continue
            market = float(np.median(ret[ok]))
            ratio = v/B['vavg'][i, j]
            spike = ok & np.isfinite(ratio) & (ratio >= VOL_X)
            mid = (h+l)/2
            bar = []
            for side, hit in (('long', spike & (ret <= -MOVE) & (c >= mid) & (market > -CALM)),
                              ('short', spike & (ret >= MOVE) & (c <= mid) & (market < CALM))):
                bar += [(-float(ratio[k]), syms[k], side, int(k)) for k in np.flatnonzero(hit)]
            for nr, s, side, k in sorted(bar):          # same bar: higher volume ratio first, then symbol
                if count >= MAX_TRADES or s in taken:
                    continue
                taken.add(s)
                count += 1
                picked.append(dict(day=d, i=i, signal_hm=hm, symbol=s, k=k, side=side, volume_ratio=-nr, market=market,
                                   eligible=[int(x) for x in np.flatnonzero(ok)]))
            if count >= MAX_TRADES:
                break
    return picked


def walk_all(B, i, signal_hm, side, stress):
    """Vectorised trade for every stock on day i entering after signal_hm. Returns per-stock net, net_pct, reason, known."""
    e = GRID.index(signal_hm)+1
    x = GRID.index(EXIT_HM)
    o, h, l = B['o'][i, e:x+1].astype(float), B['h'][i, e:x+1].astype(float), B['l'][i, e:x+1].astype(float)
    known = np.isfinite(o).all(axis=0) & np.isfinite(h[:-1]).all(axis=0) & np.isfinite(l[:-1]).all(axis=0)
    slip = np.array([SLIP.get(t, 0.0006) for t in B['tiers']])
    sgn = 1.0 if side == 'long' else -1.0
    entry = o[0]*(1+sgn*slip)*(1+sgn*stress)
    stop = entry*(1-sgn*STOP)
    path_o, path_h, path_l = o[:-1], h[:-1], l[:-1]           # bars that can stop us; the exit bar's open is the flatten
    if side == 'long':
        gap, touch = path_o <= stop, path_l <= stop
    else:
        gap, touch = path_o >= stop, path_h >= stop
    hit = gap | touch
    first = np.where(hit.any(axis=0), hit.argmax(axis=0), -1)
    cols = np.arange(o.shape[1])
    stopped = first >= 0
    raw_exit = np.where(stopped, np.where(gap[np.maximum(first, 0), cols], path_o[np.maximum(first, 0), cols], stop), o[-1])
    exit_px = raw_exit*(1-sgn*slip)
    qty = np.floor(SLOT/np.where(entry > 0, entry, np.nan))
    qty = np.where(np.isfinite(qty), qty, 0)
    gross = sgn*(exit_px-entry)*qty
    buy, sell = (entry, exit_px) if side == 'long' else (exit_px, entry)
    costs = np.where(qty > 0, COST(qty, np.nan_to_num(buy), np.nan_to_num(sell)).astype(float), 0.0)
    net = gross-costs
    ok = known & (qty > 0)
    return dict(net=np.where(ok, net, np.nan), net_pct=np.where(ok, net/(qty*entry), np.nan),
                reason=np.where(stopped, 'stop', 'flatten'), entry=entry, exit=exit_px, qty=qty, costs=costs, gross=gross, ok=ok)


def score(B, picks, days, stress, rng_seed=3001, draws=1000):
    trades, unknown, pools = [], set(), []
    for p in picks:
        if p['day'] not in days:
            continue
        r = walk_all(B, p['i'], p['signal_hm'], p['side'], STRESSES[stress])
        k = p['k']
        if not r['ok'][k]:
            unknown.add(p['day'])
            continue
        trades.append(dict(day=p['day'], symbol=p['symbol'], side=p['side'], signal_hm=p['signal_hm'],
                           volume_ratio=round(p['volume_ratio'], 2), qty=int(r['qty'][k]), entry=round(float(r['entry'][k]), 2),
                           exit=round(float(r['exit'][k]), 2), reason=str(r['reason'][k]), gross=round(float(r['gross'][k]), 2),
                           costs=round(float(r['costs'][k]), 2), net=round(float(r['net'][k]), 2), net_pct=float(r['net_pct'][k])))
        pool = [j for j in p['eligible'] if r['ok'][j]]
        pools.append((p['day'], np.array([r['net_pct'][j] for j in pool]), np.array([r['net'][j] for j in pool])))
    measured = [t for t in trades if t['day'] not in unknown]
    obs = metrics(measured, days, unknown)
    rng = np.random.default_rng(rng_seed)
    usable = [(d, pct, rs) for d, pct, rs in pools if d not in unknown and len(pct)]
    random_means = []
    for _ in range(draws):
        picks_pct = [pct[rng.integers(len(pct))] for _, pct, _ in usable]
        random_means.append(float(np.mean(picks_pct)) if picks_pct else np.nan)
    vals = np.array([v for v in random_means if np.isfinite(v)])
    obs.pop('daily')
    obs['benchmark'] = dict(draws=draws, mean_net_pct=float(vals.mean()) if len(vals) else None,
                            p95_net_pct=float(np.percentile(vals, 95)) if len(vals) else None,
                            observed_percentile=float((vals < obs['net_pct']).mean()*100) if len(vals) and obs['net_pct'] is not None else None)
    for side in ('long', 'short'):
        st = [t for t in measured if t['side'] == side]
        obs[side] = dict(trades=len(st), net_pct=float(np.mean([t['net_pct'] for t in st])) if st else None,
                         win_rate=float(np.mean([t['net'] > 0 for t in st])) if st else None, net=float(sum(t['net'] for t in st)))
    return obs, trades


def gate(res, final=False):
    a = res['assumed']
    needed = ('assumed', 'worse025') if final else tuple(STRESSES)
    return bool(a['trades'] >= 40 and a['independent_days'] >= 20 and a['day_t'] is not None
                and a['day_t'] >= (2.5 if final else 2.0)
                and all(res[s]['net_pct'] is not None and res[s]['net_pct'] > 0 for s in needed)
                and a['benchmark']['observed_percentile'] is not None and a['benchmark']['observed_percentile'] >= 95)


def dump(path, obj):
    path.write_text(json.dumps(obj, indent=2, default=lambda x: x.item() if hasattr(x, 'item') else str(x)), encoding='utf-8')


def run():
    out = ROOT/'data/h30'
    out.mkdir(parents=True, exist_ok=True)
    with (out/'started.json').open('x', encoding='utf-8') as fh:       # one scored run; a failed attempt stays spent
        json.dump(dict(at=datetime.now(IST).isoformat(), trials=1), fh)
    reg = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split('## H30:', 1)[1].split('\n## ', 1)[0]
    (out/'preregistration.md').write_text(reg, encoding='utf-8')
    dump(out/'code_sha.json', {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest()
                               for p in ('scripts/h30_capitulation.py', 'stalk/research/orb.py', 'stalk/market/costs.py')})
    tiers = universe()
    frames = {p.stem: pd.read_pickle(p) for p in sorted((ROOT/'data/kite_5m').glob('*.pkl')) if p.stem in tiers}
    B = build(frames, tiers)
    design = [d for d in B['calendar'] if DESIGN_START <= d <= DESIGN_END and B['eligible'][B['calendar'].index(d)].sum() >= 20]
    final = [d for d in B['calendar'] if FINAL_START <= d <= FINAL_END and B['eligible'][B['calendar'].index(d)].sum() >= 20]
    # Input inventory (MISTAKES 23-25): share of missing o/h/l bars on eligible stock-days in the scored windows.
    rows = [B['calendar'].index(d) for d in design+final]
    elig = B['eligible'][rows]
    missing = {k: float(np.isnan(B[k][rows][np.broadcast_to(elig[:, None, :], B[k][rows].shape)]).mean()) for k in 'ohl'}
    missing['c_signal'] = float(np.isnan(B['c'][rows][np.broadcast_to(elig[:, None, :], B['c'][rows].shape)]).mean())
    dump(out/'input_inventory.json', dict(missing_share=missing, eligible_stock_days=int(elig.sum())))
    if max(missing.values()) > 0.01:
        dump(out/'summary.json', dict(verdict='Unresolved', reason='more than 1% of required bars missing', missing_share=missing))
        print('Unresolved: missing bars', missing)
        return
    picks = signals(B, results_exclusions(design+final))
    dump(out/'selections.json', [{k: v for k, v in p.items() if k != 'eligible'} for p in picks])     # frozen before returns
    summary = dict(symbols=len(B['symbols']), design_days=len(design), final_days=len(final))
    res = {}
    for s in STRESSES:
        res[s], trades = score(B, picks, set(design), s)
        pd.DataFrame(trades).to_csv(out/f'design_{s}_trades.csv', index=False)
    summary['design'] = res
    summary['design_gate'] = gate(res)
    if summary['design_gate']:
        fin = {}
        for s in STRESSES:
            fin[s], trades = score(B, picks, set(final), s)
            pd.DataFrame(trades).to_csv(out/f'final_{s}_trades.csv', index=False)
        summary['final'] = fin
        summary['verdict'] = 'Passed' if gate(fin, final=True) else 'Failed'
    else:
        summary['verdict'] = 'Failed'
        summary['final'] = 'not opened: design gate failed'
    dump(out/'summary.json', summary)
    a = res['assumed']
    print(json.dumps(dict(verdict=summary['verdict'], trades=a['trades'], days=a['independent_days'], net_pct=a['net_pct'],
                          day_t=a['day_t'], win=a['win_rate'], random_p95=a['benchmark']['p95_net_pct'],
                          worse025=res['worse025']['net_pct'], worse050=res['worse050']['net_pct']), indent=2))


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--full', action='store_true', required=True)
    ap.parse_args()
    run()
