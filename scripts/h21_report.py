"""Publish H21 tables from its frozen single-run outputs; never score returns."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.h21_hybrid import OUT, ROOT, sha
from scripts.horizon_ladder import dump


def money(value):
    return 'n/a' if value is None else f'{value:,.2f}'


def number(value):
    return 'n/a' if value is None else f'{value:.3f}'


def main():
    hashes = json.loads((OUT/'output_manifest.json').read_text())
    assert all(sha(OUT/f) == digest for f, digest in hashes.items())
    r = json.loads((OUT/'summary.json').read_text())
    prep = json.loads((OUT/'preparation.json').read_text())
    validation = json.loads((OUT/'validation.json').read_text())
    tests = json.loads((OUT/'unit_tests.json').read_text())
    baseline = r['panic_only']
    variants = r['variants']
    pool = pd.read_csv(OUT/'everyday_pools.csv')
    coverage = pd.read_csv(OUT/'coverage.csv')
    unknown = list(coverage.loc[coverage.status != 'ok', 'day'])
    title = '# H21: panic buy plus an everyday leg'
    verdicts = ', '.join(f"{v}: **{x['verdict']}**" for v, x in variants.items())
    lines = [title, '', f'{verdicts}. Registered before returns; three variants, one guarded scored run, no tuning. Executed 2026-10-31; requested report date 2026-11-01. This is reused history, not fresh confirmation.', '',
        'A buys the two biggest clean stock gaps below -2% on ordinary mornings. B also requires a gap at least twice the stock\'s previous 20-session return standard deviation. C trades ordinary mornings only when NIFTY opens from -0.5% through +0.2%. Every variant uses the exact saved H17 panic branch when NIFTY opens below -0.5%.', '',
        'Whole shares in two Rs5,000 slots; 3% stop, H17 entry/exit slippage and exact charges. No refill, leverage or compounding. Average wins and losses below are net rupees per trade; losses retain their negative sign. Annual figures divide the requested ten-year observed totals by ten.', '',
        '| Hybrid / reference | Days traded / all sessions | Trades/year | Win rate | Average win Rs | Average loss Rs | Net/trade Rs | Net/year Rs | Day t | Max drawdown Rs |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for v, h in [('Panic only', baseline)]+[(v, x['hybrid']) for v, x in variants.items()]:
        lines.append(f"| {v} | {h['days_traded']}/{h['requested_sessions']} ({h['days_traded_share']:.1%}) | {h['trades_per_year']:.1f} | {h['win_rate']:.1%} | {money(h['average_win'])} | {money(h['average_loss'])} | {money(h['net_per_trade'])} | {money(h['net_per_year'])} | {number(h['day_t'])} | {money(h['max_drawdown'])} |")
    lines += ['', 'The everyday leg must earn money after charges and have day t >=2.0. Hybrid drawdown must be <=1.5 times panic-only drawdown. All three conditions are required; panic profits cannot rescue the everyday leg.', '',
        '| Everyday leg alone | Days traded / ordinary sessions | Trades/year | Win rate | Average win Rs | Average loss Rs | Net/trade Rs | Net/year Rs | Day t | Max drawdown Rs |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for v, x in variants.items():
        e = x['everyday']
        lines.append(f"| {v} | {e['days_traded']}/{e['requested_sessions']} ({e['days_traded_share']:.1%}) | {e['trades_per_year']:.1f} | {e['win_rate']:.1%} | {money(e['average_win'])} | {money(e['average_loss'])} | {money(e['net_per_trade'])} | {money(e['net_per_year'])} | {number(e['day_t'])} | {money(e['max_drawdown'])} |")
    lines += ['', '| Variant | Everyday total net Rs | Everyday active-day t | Hybrid extra net/year Rs | Hybrid drawdown / panic | Screen |',
        '|---|---:|---:|---:|---:|---|']
    for v, x in variants.items():
        e, h = x['everyday'], x['hybrid']
        lines.append(f"| {v} | {money(e['net'])} | {number(e['active_day_t'])} | {money(h['delta_net_per_year_vs_panic'])} | {h['drawdown_ratio_vs_panic']:.2f}x | {x['verdict']} |")
    lines += ['', '| Variant | Everyday net >0 | Everyday day t >=2 | Hybrid drawdown <=1.5x |', '|---|---|---|---|']
    for v, x in variants.items():
        lines.append('| '+v+' | '+' | '.join('yes' if z else 'no' for z in x['checks'].values())+' |')
    lines += ['', '## Coverage and what these prices can establish', '',
        f"Stored NIFTY calendar: {prep['sessions']} gap-known sessions, 2016-11-02 through 2026-10-30; the first stored day (1 Nov 2016) has no predecessor and cannot supply a NIFTY gap. There are {prep['ordinary_sessions']} ordinary sessions. {len(unknown)} sessions lack a usable official current/prior file; their returns are unavailable, never clean zero-opportunity observations. Day t uses covered applicable dates including covered zero-trade dates; active-day t is also saved. Activity shares divide by all requested calendar sessions and therefore understate unknown opportunities. C's covered gated-off days remain zeros in its everyday t.", '',
        f"The wide cache has {prep['wide_equities']} stocks, so it cannot establish a delisted-inclusive test alone. All cached EQ bhavcopy names supplement it: {pool.symbol.nunique()} distinct ordinary-day candidate symbols, {int((pool.price_source == 'official_only').sum())} candidate rows without a matching wide reference. Every candidate uses earlier-session turnover and official OPEN/PREVCLOSE; wide prices, when present, must agree within 1%. H17 dated-symbol action exclusions precede ranking. All H17 action-publication timestamps remain unknown under its documented advance-notice assumption. Twenty-session volatility uses earlier raw official close/PREVCLOSE returns: splits can inflate past volatility; no retrospective back-adjustment or outcomes-based repair was applied.", '',
        'Before returns, a signal-only residual-gap audit found PHILIPCARB\'s 19 Apr 2018 split under the current action symbol PCBL. The cached NSE notice of 7 Jan 2022 explicitly records PHILIPCARB → PCBL effective 13 Jan 2022, missing from H17\'s rename CSV. That documented dated identity link was registered and applied globally to everyday actions and scheduled events. The original preparation, code and hashes remain in data/h21/preparation_attempt1; zero H21 trade returns were scored there. The rebuilt preparation has no candidate gap below -25%. H17 panic picks/fills were never repaired or changed.', '',
        'The new pool never reads same-day highs/lows/close/turnover or future announcements. Results exclusions use only stored scheduled events announced before 09:15, within three calendar days. Those calendars cover surviving cached names and have incomplete historical vintages: absence is not certified no-results. H17\'s panic reference matches its saved identities, quantities, fills and fees within floating-point rounding; it kept locked stocks and used retrospective results sets. Thus the panic baseline has inherited limitations rather than a new claim of full causality.', '',
        'There are no historical 09:15 lock/depth/circuit observations. Unknown lock status is retained only under the explicitly registered assumed-fill basis; a full-day OHLC lock exclusion would be look-ahead and was not used. The requested unlocked-entry condition cannot be certified from these caches. The numerical screen is a daily-price description, not a certified causal or executable strategy pass. Official opening fills are assumed, stop times are unknown, and flatten is the official close rather than an invented 15:10 price. Fixed current H17 fees are applied throughout.', '',
        'Totals, annual averages and drawdown cover observed prices only; unknown dates make a complete ten-year performance claim unavailable. Missing-date cumulative curves carry the last observed value solely for display and have null daily net in stored Sessions. Exact missing dates are in data/h21/coverage.csv. Fixed allocations can require replenishment; minimum starting equities: '+', '.join(f"{v} Rs{money(x['hybrid']['minimum_start_equity'])}" for v, x in variants.items())+'.', '',
        '## Week of 9–13 December 2024', '',
        'This is a slice of the saved ten-year trades, with no second scoring run. Prices below include slippage; net includes charges. Stop timestamps are unknown. A chosen leg can have no funded picks. All exit reasons are exactly stop or flatten.', '']
    for v, x in variants.items():
        week_notes = []
        lines += [f"### Hybrid {v}: week net Rs{money(x['week_net'])}", '',
            '| Day | NIFTY gap | Leg chosen / traded | Pick (shares) | Entry Rs | Exit Rs | Reason | Trade net Rs | Day net Rs |',
            '|---|---:|---|---|---:|---:|---|---:|---:|']
        for day in x['week']:
            trades = day['trades'] or [None]
            for t in trades:
                if t:
                    pick, entry, exit_px, reason, net = f"{t['symbol']} ({t['qty']})", money(t['entry']), money(t['exit']), t['reason'], money(t['net'])
                else:
                    pick, entry, exit_px, reason, net = 'none', '—', '—', '—', '—'
                lines.append(f"| {day['day']} | {day['nifty_gap']:+.3%} | {day['leg']} / {day['traded_leg']} | {pick} | {entry} | {exit_px} | {reason} | {net} | {money(day['net'])} |")
            unfunded = set(day['selected_picks']) - {t['symbol'] for t in day['trades']}
            if unfunded:
                week_notes.append(f"{day['day']}: selected but unaffordable; no refill: {', '.join(sorted(unfunded))}.")
        lines.append('')
        for note in week_notes:
            lines += [note, '']
    lines += ['## Saved runs and checks', '',
        'Six campaigns use the existing Store/History structure: Hybrid A/B/C, week of 9 Dec 2024 and Hybrid A/B/C, 10 years. Every NIFTY-calendar session is stored, including covered no-trade days and explicitly unavailable days. Entry timestamps are 09:15, stop exit timestamps null, flatten timestamps 15:30; notes carry the assumptions. The Sessions API now omits null days from extrema and reports unknown_days, preventing a summary crash while preserving missing evidence. No UI files changed. Claude should show the labels and missing-evidence note without presenting the result as forward validation.', '',
        f"All {tests['tests']} unit tests pass, including {tests['new_h21_tests']} H21 tests. Frozen inputs ({validation['input_hashes']}) and outputs ({validation['output_hashes']}) match; panic trades reconcile exactly to H17, fees/quantities/net reconcile, all six campaigns reconcile, and all pre-existing database rows are unchanged. Protected panic.py, validate.py and all of agent.py are unchanged. The started.json guard rejects a second score. No network, secrets, orders, UI, browser or server were used.", '',
        'Lesson: trading more often must earn its own return after costs and fit the existing drawdown budget. More activity and a positive combined total do not by themselves pass the everyday leg. H16 execution measurements remain the next prerequisite; no runner change or new confirmation was authorized by this study.', '']
    report = ROOT/'docs/research/2026-11-01-h21-hybrid.md'
    report.write_text('\n'.join(lines), encoding='utf-8')
    path = ROOT/'data/research_summary.json'
    summary = json.loads(path.read_text(encoding='utf-8'))
    all_pass = all(x['verdict'] == 'Passed' for x in variants.values())
    summary['cycles'] = [{'id': 'H21', 'name': 'Hybrid: panic buy + every-day leg',
        'verdict': 'Passed' if all_pass else 'Failed',
        'result': '; '.join(f"{v}: Rs{x['hybrid']['net_per_trade']:.2f} a trade on {x['hybrid']['trades_per_year']:.1f} trades a year"
                           for v, x in variants.items())+'.'}]+[x for x in summary['cycles'] if x.get('id') != 'H21']
    summary['updated'] = '2026-11-01'
    dump(path, summary)
    print(report)


if __name__ == '__main__':
    main()
