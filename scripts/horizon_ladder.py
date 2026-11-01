"""Describe fixed H17 accounts over overlapping calendar windows; never tune rules."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.panic_final import nifty
from scripts.h17_rebuild import streak

ROOT = Path(__file__).resolve().parents[1]
START, END = pd.Timestamp('2016-11-01'), pd.Timestamp('2026-11-01')
HORIZONS = [('5y', pd.DateOffset(years=5), 'MS'),
            ('2y', pd.DateOffset(years=2), 'MS'),
            ('1y', pd.DateOffset(years=1), 'MS'),
            ('1m', pd.DateOffset(months=1), '7D'),
            ('1w', pd.DateOffset(weeks=1), 'D')]


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2) + '\n', encoding='utf-8')


def trade_stats(trades):
    net = trades.net.to_numpy()
    wins, losses = net[net > 0], net[net < 0]
    return dict(trades=len(net), win_rate=float((net > 0).mean()),
                average_win=float(wins.mean()) if len(wins) else None,
                average_loss=float(-losses.mean()) if len(losses) else None,
                profit_factor=float(wins.sum() / -losses.sum()) if len(losses) else None,
                expectancy=float(net.mean()), longest_losing_streak=streak(net))


def windows(trades, qualifying, offset, freq, start=START, end=END):
    rows = []
    for left in pd.date_range(start, end, freq=freq):
        right = left + offset
        if right > end:
            continue
        lo, hi = str(left.date()), str(right.date())
        selected = trades[(trades.day >= lo) & (trades.day < hi)]
        rows.append(dict(start=lo, end_exclusive=hi, trades=len(selected),
                         qualifying_mornings=sum(lo <= d < hi for d in qualifying), net=float(selected.net.sum())))
    return pd.DataFrame(rows)


def window_stats(frame):
    active = frame[frame.trades > 0]
    return dict(windows=len(frame), windows_with_trades=len(active),
                share_with_trades=float((frame.trades > 0).mean()),
                share_positive_with_trades=float((active.net > 0).mean()) if len(active) else None,
                median_net=float(frame.net.median()), p10_net=float(frame.net.quantile(.1)),
                p90_net=float(frame.net.quantile(.9)),
                median_qualifying_mornings=float(frame.qualifying_mornings.median()),
                median_net_with_trades=float(active.net.median()) if len(active) else None,
                worst=frame.loc[frame.net.idxmin()].to_dict())


def main():
    n = nifty()
    output = dict(period=['2016-11-01', '2026-10-31'], account=10000,
                  basis='H17 cleaned stopped whole-share accounts, fixed daily open proxy; not executable 09:20 evidence',
                  windows='Full half-open calendar windows only; monthly starts for years, every 7 days from 2016-11-01 for months, daily starts for weeks. Net percentiles include quiet windows.',
                  caution='Overlapping windows reuse observations. Strict and loose are separate frozen experiments.', versions={})
    lines = ['# Fixed panic-rule horizon ladder', '',
             'H17 cleaned daily reconstruction, 1 November 2016 through 31 October 2026. ₹10,000 fixed allocation, whole shares, 3% stop and H17 costs. No compounding or rule changes.', '',
             output['windows'], '', output['caution'], '',
             'The daily open/low/close model cannot establish executable returns after a 09:20 decision. Quiet windows are retained as zero; positivity uses only windows with funded trades. Qualifying mornings count the NIFTY gate even if no pick can be funded.', '']
    for label, threshold in [('strict', -.005), ('loose', -.003)]:
        trades = pd.read_csv(ROOT / f'data/h17/{label}_cleaned_stop3_account.csv')
        qualifying = list(n.index[n.gap < threshold])
        stats = trade_stats(trades)
        stats['qualifying_mornings'] = len(qualifying)
        stats['funded_mornings'] = trades.day.nunique()
        horizons = {}
        lines += [f'## {label.title()}', '', '| Horizon | Windows | With trades | Positive among active | Median ₹ | P10 ₹ | P90 ₹ | Median qualifying mornings | Worst ₹ (start) |', '|---|---:|---:|---:|---:|---:|---:|---:|---|']
        for name, offset, freq in HORIZONS:
            frame = windows(trades, qualifying, offset, freq)
            s = horizons[name] = window_stats(frame)
            frame.to_csv(ROOT / f'data/h17/{label}_horizon_{name}.csv', index=False)
            lines.append(f"| {name} | {s['windows']} | {s['windows_with_trades']} ({s['share_with_trades']:.1%}) | {s['share_positive_with_trades']:.1%} | {s['median_net']:.2f} | {s['p10_net']:.2f} | {s['p90_net']:.2f} | {s['median_qualifying_mornings']:g} | {s['worst']['net']:.2f} ({s['worst']['start']}) |")
        output['versions'][label] = dict(full_period=stats, horizons=horizons)
        lines += ['', f"Full period: {stats['trades']} funded trades on {stats['funded_mornings']} mornings; {len(qualifying)} qualifying mornings. Win rate {stats['win_rate']:.2%}; average win ₹{stats['average_win']:.2f}, average loss ₹{stats['average_loss']:.2f}; profit factor {stats['profit_factor']:.3f}; expectancy ₹{stats['expectancy']:.2f}/trade; longest losing sequence {stats['longest_losing_streak']} trades (saved day/rank order; same-day trades are correlated).", '']
    lines += ['## Lesson', '', 'Sparse weeks often contain no trade. The nested horizons describe the same fixed rule and do not create fresh confirmation or a reason to select between strict and loose.', '', 'Next: execution evidence at the decision timestamp, including quotes, failed fills and eligibility, reviewed on a scheduled research date.']
    dump(ROOT / 'data/horizons.json', output)
    (ROOT / 'docs/research/2026-11-01-horizons.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
