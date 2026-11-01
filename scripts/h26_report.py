"""Publish saved H26 pilot artifacts only; never executes a strategy or random draw."""
from __future__ import annotations
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from stalk.data.store import Store
from stalk.market.costs import CURRENT_FEE_SCHEDULE_VERSION
from scripts.h26_full import sha
from scripts.kite_backfill import atomic_json

OUT = ROOT/'data/h26/pilot'


def published_verdict(r):
    a = r['assumed']
    # MISTAKES12: under40 trades/20 days is a power check, never a strategy verdict.
    return 'Unclear' if a['trades'] < 40 or a['independent_days'] < 20 else r['verdict']


def saved():
    manifest = json.loads((OUT/'output_manifest.json').read_text())
    assert all(sha(OUT/p) == h for p, h in manifest.items())
    for p, h in json.loads((OUT/'input_manifest.json').read_text()).items():
        assert sha(ROOT/p) == h, p
    return json.loads((OUT/'summary.json').read_text())


def history(result):
    backup = ROOT/'data/h26/before_history.db'
    if not backup.exists():
        with sqlite3.connect(ROOT/'data/stalk.db') as source, sqlite3.connect(backup) as dest:
            source.backup(dest)
    store = Store()
    ids = []
    try:
        for v, r in result.items():
            cid = 'h26_pilot_'+v
            label = f'Stocks in play {v.upper()}, Aug-Oct 2026'
            account_id = cid+':orb'
            a = r['assumed']
            trades = pd.read_csv(OUT/f'{v}_assumed_trades.csv').to_dict('records')
            days, curve = [], []
            equity = 10000.
            # Retain every scored date, including unknown paths, as in H21 Sessions.
            calendar = json.loads((OUT/'coverage.json').read_text())['scored_calendar']
            for day in calendar:
                g = [t for t in trades if t['day'] == day]
                net = a['daily'].get(day)
                start = equity
                if net is not None:
                    equity += net
                row = dict(day=day, start_equity=start, end_equity=equity if net is not None else None,
                    net=net, gross=sum(t['gross'] for t in g) if net is not None else None,
                    costs=sum(t['costs'] for t in g) if net is not None else None,
                    trades=len(g), wins=sum(t['net'] > 0 for t in g), losses=sum(t['net'] <= 0 for t in g),
                    status='pilot_bar_reference' if net is not None else 'unknown_path')
                days.append(row)
                curve.append([day, equity if net is not None else None])
                sid = account_id+':'+day
                store.save_session(sid, 'orb', day, account_id, dict(variant=v, paper_only=True), row, ended=True)
                for t in g:
                    store.save_trade(dict(id=sid+':'+t['symbol'], session_id=sid, day=day,
                        strategy='orb_'+v, symbol=t['symbol'], side=t['side'], qty=int(t['qty']),
                        entry_ts=int(t['entry_ts']), exit_ts=int(t['exit_ts']), entry_price=t['entry'],
                        exit_price=t['exit'], stop_initial=t['stop'], gross=t['gross'], costs=t['costs'],
                        net=t['net'], reason=t['reason'], fee_version=CURRENT_FEE_SCHEDULE_VERSION, broker='backtest'))
            acct = dict(name='orb', strategies=['orb_'+v], require_validated=False, start=10000.,
                end=equity, net=a['net'], gross=sum(t['gross'] for t in trades), costs=sum(t['costs'] for t in trades),
                tax=0., after_tax=None, trades=len(trades), max_dd=a['max_drawdown_rs'],
                green_days=sum(d['net'] is not None and d['net'] > 0 for d in days),
                red_days=sum(d['net'] is not None and d['net'] < 0 for d in days),
                flat_days=sum(d['net'] == 0 for d in days), unknown_days=a['unknown_days'],
                stats=dict(n=len(trades), win=a['win_rate'], net=a['net'], t=a['day_t']),
                per_strategy={}, days=days, curve=curve)
            obj = dict(id=cid, first_day=calendar[0], last_day=calendar[-1], days=len(days), capital=10000.,
                accounts={'orb': acct}, weeks=[], kind='orb', label=label, research_status=published_verdict(r), variant=v,
                unknown_days=a['unknown_days'], note='Aug-Oct 2026 pilot only. 15 earlier sessions warm up features. '
                'Fixed slots may need replenishment; current survivor universe; assumed bar fills. '
                'Other entry stress scenarios and random controls are in the H26 report. No real orders.')
            store.save_campaign(cid, calendar[0], calendar[-1], dict(bar_minutes=5, label=label), obj)
            ids.append(cid)
    finally:
        store._conn.close()
    atomic_json(ROOT/'data/h26/history_update.json', dict(campaigns=ids, backup=str(backup.relative_to(ROOT))))


def fmt(x, digits=3):
    return 'unknown' if x is None else f'{x:.{digits}f}'


def report(result):
    coverage = json.loads((OUT/'coverage.json').read_text())
    text = '''# H26: Can stocks in play make an everyday India strategy?

Three registered stop variants, one guarded pilot score, no tuning. This is a power/pilot window, not confirmation. Nothing can pass on three months of intraday data. All prices are paper bar references, after current Zerodha MIS charges and tier slippage, before income tax.

The proposed mechanism follows informed news flow still spreading after the open. Slower participants may continue buying or selling after our entry. This is a hypothesis, not liquidity provision or a tested explanation. The user's requested test explicitly overrides the usual gross-edge screen: the paper's average move is already smaller than Indian costs.

Claude's registered prediction: "With the paper's 10%-ATR stop, R is ~0.3% of price, so Indian round-trip costs (~0.17% + slippage) are ~0.5R against a ~0.08R published edge: the paper version should LOSE in India. Only a wider stop or extreme relative volume could survive."

## Frozen rule and coverage

Price at least Rs50, prior14-session mean turnover proxy at least Rs10 crore, prior14-session simple ATR at least1.5% of opening price. Turnover is the sum of five-minute close times volume, not exchange-reported exact turnover. Relative volume compares today's first five-minute volume with exactly the preceding14 sessions' first five-minute volumes. Skip known same-day results announced before09:20, first-bar locks and doji. Take the top TWO with RV>=1, ties by symbol, direction from the first candle. No replacement, re-entry, leverage or compounding: each slot is floor(Rs5,000/fill) shares. Buy stop at the opening high or sell stop at the opening low; triggers09:20..11:30, stops10/25/50%ATR from slipped fill, flatten15:10 open. Ambiguous entry/stop candles stop out. Gap-through fills are adverse. Missing earlier path/flatten prices remain unknown. The0.25% and0.5% worse entries recompute stops and whole-share size; realised outcomes need not worsen monotonically because stop placement changes.

'''
    text += f"Stored {coverage['symbols']} EQ stocks of {coverage['requested_symbols']} requested, {len(coverage['calendar'])} sessions ({coverage['calendar'][0]}..{coverage['calendar'][-1]}). Fifteen sessions warm up features; {len(coverage['scored_calendar'])} dates scored ({coverage['scored_calendar'][0]}..{coverage['scored_calendar'][-1]}). Frequency denominators below are scored dates, not warmup days. Missing history is not an idle trading day. Actual15:10 bars were inventoried before registration.\n\n"
    audit = json.loads((ROOT/'data/h26/volume_audit.json').read_text())
    text += (f"**Data quality failure found after the score:** {audit['zero_opening_volume']:,} of {audit['opening_stock_days']:,} opening stock-days have zero volume; {audit['nonflat_zero_volume']:,} have a nonflat candle despite zero volume. In the saved eligible pool, only9 of1,362 stock-days have RV>=1. Those create9 picks on8 days, only5 triggered trades on5 days. The cache cannot establish opening-volume selection or daily opportunity frequency. Do not interpret the other zero-pick days as genuine absence of stocks in play. No data replacement, threshold change, return repair or second score was performed. The original artifacts retain the mechanical Failed screen; all published strategy verdicts are **Unclear**, power/data quality only, because5 trades/5 days are below MISTAKES12. The scheduled full run must use verified Kite volume.\n\n")
    text += '| Stop | Verdict | Trades / active days | Trades/day | Days traded | Win rate | Avg win Rs | Avg loss Rs | Net Rs | Day t | Max drawdown Rs |\n|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n'
    for v, r in result.items():
        a = r['assumed']
        text += f"| {v.upper()} | {published_verdict(r)} | {a['trades']} / {a['independent_days']} | {fmt(a['trades_per_day'])} | {fmt(100*a['days_traded_share'],1)}% | {fmt(100*a['win_rate'],1)}% | {fmt(a['avg_win_rs'],2)} | {fmt(a['avg_loss_rs'],2)} | {fmt(a['net'],2)} | {fmt(a['day_t'])} | {fmt(a['max_drawdown_rs'],2)} |\n"
    text += '\nAll metrics, including frequency/payoffs/drawdown, at every entry scenario:\n\n| Stop / fill | Trades | Active days | Unknown days | Trades/day | Days traded % | Win % | Avg win Rs | Avg loss Rs | Net %/trade | Net Rs | Day t | Drawdown Rs | Random mean % | Random95 % | Selection percentile |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n'
    for v, r in result.items():
        for s in ('assumed', 'worse025', 'worse050'):
            a, b = r[s], r[s]['benchmark']
            text += '| '+v.upper()+' / '+s+' | '+' | '.join([
                str(a['trades']), str(a['independent_days']), str(a['unknown_days']), fmt(a['trades_per_day']),
                fmt(100*a['days_traded_share'],1), fmt(100*a['win_rate'],1), fmt(a['avg_win_rs'],2), fmt(a['avg_loss_rs'],2),
                fmt(a['net_pct'],4), fmt(a['net'],2), fmt(a['day_t']), fmt(a['max_drawdown_rs'],2),
                fmt(b['mean_net_pct'],4), fmt(b['p95_net_pct'],4), fmt(b['observed_percentile'],1)])+' |\n'
    text += '''
The 1,000 controls draw two stocks without replacement from that day's pool BEFORE the RV/direction screen, then choose long/short by fair coin and apply the same boundaries, timing, stops and costs. The draw seed is2601. They test the joint choice of RV ranking and candle direction. Random trigger counts can differ. Unknown-day returns are excluded, not zero-filled; only common known dates enter paired daily excess. Complete per-draw coverage, paired excess rupees, p-values, picks and scenario trades are saved under data/h26/pilot. Above-random performance alone does not establish a positive net edge. Day t uses all known scored days, including genuine no-trigger days; drawdown uses day-close fixed-slot equity, not intraday marked exposure. Any unknown dates limit the full-path interpretation.

## Limits and next evidence

Current constituents create survivor bias. Stored EQ/fund identities cannot recover dead names; intraday turnover is approximate. Calendar completeness and original notice vintages are incomplete; only dated, already-announced same-day results are excluded. Unverified>10% opening/prior-close price-scale jumps make the ATR lookback unavailable. This prevents a split-scale ATR from creating eligibility but does not certify all corporate actions. There is no claim of executable MIS short availability, circuit capacity, measured slippage or bid/ask fills. H16 remains the execution prerequisite. Fixed slots are independent-trade measurements; losses may require account replenishment.

The full scorer is NOT run. After Kite login, run `python scripts/kite_backfill.py`, inspect data/kite_5m/coverage.json for failed chunks, then run `python scripts/h26_full.py --full` ONCE. The downloader starts in2015, uses99-calendar-day chunks, paced calls, atomic symbol files and resumable exact chunk checkpoints, including valid empty history. It refuses logged-out sessions and contains no order calls. Delisted token coverage remains incomplete.

Full scoring freezes inputs, registration and source hashes, requires at least two years, and uses fixed history through2026-07-31. Design ends2025-07-31; final12 months2025-08-01..2026-07-31 stay unscored unless that version clears the registered design gate. This avoids overlap with this Aug-Oct pilot, but the historical calendar has been used by other studies; it is not pristine independent confirmation. There is one guard per window, no alternative split or retuning. No real-money approval follows from these descriptive results.

From Monday2 Nov the normal app starts a fifth background loop for H26, separate from panic/brief/review/execution loops. Three versions and deterministic same-day random picks have separate ledgers in data/orb_paper.jsonl. Kite history supplies live five-minute bars while logged in; Yahoo fallback uses its own prior history and is labelled, with provider pinned after selection. Only completed candles are consumed; the15:10 open may flatten without using its incomplete range. Full-universe missing data cannot establish the top two; a missed09:30 decision skips as unknown. A scan observed after09:21 is a labelled historical paper bar reference, never proof that a09:20 fill was achievable. Restarts recover decisions/fills/exits from the journal; stale or missing exit paths remain unknown. The API and15:35 review display null for incomplete outcomes. This attempts the frozen rule daily; it does not force a trade when the rule has no trigger.

## Sources and artifacts

- [Zarattini, Barbon & Aziz paper](https://concretumgroup.com/wp-content/uploads/2026/02/A-Profitable-Day-Trading-Strategy-For-The-U.S.-Equity-Market.pdf): five-minute stocks in play, prior14-session relative opening volume, top20 US portfolio. India uses two fixed slots as registered.
- [Zerodha charges](https://zerodha.com/charges/): H26 uses NSE exchange0.00307%, intraday brokerage, sell STT, buy stamp, SEBI and GST. costs.py exposes this current rate explicitly while preserving the legacy default for older frozen research.
- [Kite historical data](https://kite.trade/docs/connect/v3/historical/), [broker request limits](https://kite.trade/forum/discussion/3105/historical-api-frequency-and-limit-on-number-of-candles-fetched), [history availability](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/charts/articles/historical-data-on-kite-charts): multi-year archived bars,100-day five-minute request ceiling, equity intraday history from2015.
- data/h26/pilot: started guard, pre-return registration/source/input hashes, selections, 1,000 controls per setting/scenario, raw trades/statuses, summaries and output hashes. Full/backfill have not been run.
- Sessions: Stocks in play V1/V2/V3, Aug-Oct2026 (assumed fills); report tables retain both worse-fill scenarios. Saved records use only stop/flatten.

Lesson: a daily opportunity and an above-random ranking still need a positive net return that survives adverse entry fills; three months cannot establish that edge.
'''
    (ROOT/'docs/research/2026-11-01-h26-orb.md').write_text(text, encoding='utf-8')


def summary_json(result):
    path = ROOT/'data/research_summary.json'
    obj = json.loads(path.read_text(encoding='utf-8'))
    best_v, best = max(result.items(), key=lambda x: x[1]['assumed']['net_pct'] if x[1]['assumed']['net_pct'] is not None else -1e9)
    all_failed = all(published_verdict(r) == 'Failed' for r in result.values())
    entry = dict(id='H26', name='Stocks in play breakout (pilot)', verdict='Failed' if all_failed else 'Unclear',
                 summary='Only five trades per stop: cached opening volumes are mostly zero; no daily-frequency or profitability conclusion. Kite history awaits login.')
    # Follow the existing cycle prose schema as well.
    entry['note'] = entry['summary']
    entry['result'] = entry['summary']
    obj['cycles'] = [entry]+[c for c in obj['cycles'] if c.get('id') != 'H26']
    obj['scoreboard'] = [r for r in obj['scoreboard'] if r['name'] != 'Stocks-in-play breakout (pilot)']
    obj['scoreboard'].append(dict(name='Stocks-in-play breakout (pilot)', net_pct=best['assumed']['net_pct'], note='Aug-Oct 2026 pilot only'))
    obj['scoreboard'].sort(key=lambda r: r['net_pct'] if r.get('net_pct') is not None else -1e9, reverse=True)
    obj['updated'] = '2026-11-01'
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


if __name__ == '__main__':
    result = saved()
    history(result)
    report(result)
    summary_json(result)
    print('Saved H26 Sessions, report and overview from frozen artifacts only.')
