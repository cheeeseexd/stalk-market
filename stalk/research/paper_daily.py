"""Read-only daily observations of the two frozen panic experiments. No orders."""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import logging
import math
from pathlib import Path
import threading
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from stalk.config import BASE_DIR, IST
from stalk.sim.panic_live import NSE_UA, universe

logger = logging.getLogger('stalk.paper_daily')
VERSIONS = {'strict': ('panic', 'panic_paper.jsonl'), 'loose': ('panic03', 'panic_paper_loose.jsonl')}
CUES = {'sp500': '^GSPC', 'nasdaq': '^IXIC', 'brent': 'BZ=F', 'gold': 'GC=F', 'usdinr': 'INR=X'}


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return default


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)


def latest(root, folder, count):
    paths = sorted((Path(root)/'data'/folder).glob('????-??-??.json'), reverse=True)
    return [obj for p in paths if (obj := read_json(p)) is not None][:count]


def number(value):
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def nse_time(value):
    for fmt in ('%d-%b-%Y %H:%M:%S', '%d-%m-%Y %H:%M:%S'):
        try:
            return datetime.strptime(str(value), fmt).replace(tzinfo=IST)
        except ValueError:
            pass
    return None


def current_preopen(value, now):
    t = nse_time(value)
    return bool(t and t.date() == now.date() and 900 <= t.hour*100+t.minute < 915 and t <= now)


def completed_cue(payload, now, us_close=False):
    """Use completed sessions; US cash closes can finish earlier on the local date."""
    result = payload['chart']['result'][0]
    exchange = ZoneInfo(result['meta']['exchangeTimezoneName'])
    cutoff = now.astimezone(exchange).date()
    q = result['indicators']['quote'][0]['close']
    rows = [(ts, px) for ts, raw in zip(result.get('timestamp', []), q)
            if (px := number(raw)) is not None and px > 0
            and (datetime.fromtimestamp(ts, exchange).date() < cutoff or
                 (us_close and datetime.fromtimestamp(ts, exchange).date() == cutoff
                  and now.astimezone(exchange).hour >= 16))]
    if len(rows) < 2:
        return None
    (prior_ts, prior), (ts, close) = sorted(rows)[-2:]
    return dict(close=close, change=close/prior-1,
                session_date=str(datetime.fromtimestamp(ts, exchange).date()),
                previous_session_date=str(datetime.fromtimestamp(prior_ts, exchange).date()),
                source='Yahoo daily', retrieved_at=now.isoformat())


class DailySources:
    """Bounded public-data reads. Offline cues retain explicitly dated cached values."""
    def __init__(self, root):
        self.root = Path(root)

    def globals(self, now):
        path = self.root/'data/brief_cache/globals.json'
        cache = read_json(path, {})
        fresh = {}
        with httpx.Client(timeout=6, headers=NSE_UA, follow_redirects=True) as client:
            for name, symbol in CUES.items():
                try:
                    response = client.get('https://query1.finance.yahoo.com/v8/finance/chart/'+quote(symbol, safe=''),
                                          params={'interval': '1d', 'range': '1mo'})
                    response.raise_for_status()
                    cue = completed_cue(response.json(), now, us_close=name in ('sp500', 'nasdaq'))
                    if cue:
                        fresh[name] = cue
                except Exception:
                    pass  # Global context is optional; an offline brief is ordinary.
        cache.update(fresh)
        if fresh:
            write_json(path, cache)
        return {name: dict(cache[name], cached=name not in fresh) for name in CUES if name in cache}

    def market(self, now):
        with httpx.Client(headers=NSE_UA, timeout=8, follow_redirects=True) as c:
            c.get('https://www.nseindia.com/')
            response = c.get('https://www.nseindia.com/api/market-data-pre-open', params={'key': 'ALL'})
            response.raise_for_status()
            stocks = response.json()
            response = c.get('https://www.nseindia.com/api/allIndices')
            response.raise_for_status()
            indices = response.json()
            index = next((x for x in indices.get('data', []) if x.get('index') == 'NIFTY 50'), {})
            stamp = index.get('timestamp') or indices.get('timestamp')
            pc, indicative = number(index.get('previousClose')), number(index.get('indicativeClose'))
            gap = indicative/pc-1 if pc and pc > 0 and indicative and indicative > 0 and current_preopen(stamp, now) else None
            rows = []
            fresh = current_preopen(stocks.get('lastUpdateTime'), now)
            for row in stocks.get('data', []):
                m = row.get('metadata') or {}
                pc, op = number(m.get('previousClose')), number(m.get('iep'))
                if m.get('symbol') and pc and pc > 0 and op and op > 0 and fresh:
                    rows.append(dict(symbol=m['symbol'], previous_close=pc, indicative=op, gap=op/pc-1,
                                     source_at=stocks.get('lastUpdateTime')))
            # Failure means unknown eligibility, not an empty exclusion set.
            response = c.get('https://www.nseindia.com/api/corporate-board-meetings',
                             params={'index': 'equities', 'from_date': (now-timedelta(days=3)).strftime('%d-%m-%Y'),
                                     'to_date': (now+timedelta(days=3)).strftime('%d-%m-%Y')})
            response.raise_for_status()
            calendar = response.json()
            if not isinstance(calendar, list) or any(not isinstance(r, dict) for r in calendar):
                raise ValueError('Results calendar coverage unknown')
            if any('result' in f"{r.get('bm_purpose', '')} {r.get('bm_desc', '')}".lower()
                   and not r.get('bm_symbol') for r in calendar):
                raise ValueError('Results meeting has no symbol')
            near = {r.get('bm_symbol') for r in calendar
                    if 'result' in f"{r.get('bm_purpose', '')} {r.get('bm_desc', '')}".lower()}
        return dict(nifty_gap=gap, nifty_source_at=stamp, candidates=rows, results_near=sorted(near),
                    results_known=True, tiers=universe(), stock_snapshot_current=fresh)


class PaperDaily:
    def __init__(self, root=BASE_DIR, sources=None):
        self.root = Path(root)
        self.sources = sources or DailySources(root)
        self._lock = threading.Lock()

    def brief(self, now):
        now = now.astimezone(IST)
        if now.weekday() >= 5 or now.hour*100+now.minute < 910:
            return None
        day = now.date().isoformat()
        path = self.root/f'data/briefs/{day}.json'
        with self._lock:
            existing = read_json(path)
            if existing:
                return existing
            missed = now.hour*100+now.minute >= 915
            market, notes = {}, []
            try:
                cues = self.sources.globals(now)
            except Exception:
                cues = {}
            if missed:
                notes.append('Pre-open observation missed; later prices cannot reconstruct it.')
            else:
                try:
                    market = self.sources.market(now)
                except Exception:
                    notes.append('NSE pre-open or results calendar unavailable; eligibility unknown.')
            tiers, near = market.get('tiers', {}), set(market.get('results_near', []))
            candidates = []
            for row in sorted(market.get('candidates', []), key=lambda r: (r['gap'], r['symbol'])):
                if row['gap'] >= 0:
                    continue
                s = row['symbol']
                reasons = []
                if s not in tiers:
                    reasons.append('Outside the frozen forward universe')
                if row['gap'] >= -.02:
                    reasons.append('Gap is not more than 2% down')
                if not market.get('results_known'):
                    reasons.append('Results calendar unknown')
                elif s in near:
                    reasons.append('Nearby results meeting')
                eligible = not reasons
                reasons.append('First five-minute lock and price-basis checks await 09:20')
                candidates.append(dict(row, rank=len(candidates)+1, preliminary_eligible=eligible, reasons=reasons))
                if len(candidates) == 8:
                    break
            gap = market.get('nifty_gap')
            obj = dict(day=day, observed_at=now.isoformat(), scheduled_at=day+'T09:10:00+05:30',
                       status='missed' if missed else 'observed' if gap is not None else 'partial',
                       globals=cues, nifty=dict(indicative_gap=gap, source_at=market.get('nifty_source_at')),
                       would_qualify={label: gap < threshold if gap is not None else None
                                      for label, threshold in [('strict', -.005), ('loose', -.003)]},
                       candidates=candidates, notes=notes+['Indicative context only; both rules remain frozen. Final eligibility and executable entry require later evidence.'])
            write_json(path, obj)
            return obj

    def _events(self, filename):
        path = self.root/'data'/filename
        events, bad = [], 0
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except ValueError:
                    bad += 1
        return events, bad

    def _runner_review(self, label, filename, day):
        events, bad = self._events(filename)
        today = [e for e in events if e.get('day') == day]
        mornings = [e for e in today if e.get('kind') == 'morning']
        morning = mornings[-1] if mornings else {}
        entries = [e for e in today if e.get('kind') == 'entry']
        # Restart duplicates are one observation, not extra trades.
        trades = {e['trade']['symbol']: e['trade'] for e in today if e.get('kind') == 'exit' and 'trade' in e}
        picks = morning.get('picks', [])
        skips = [e for e in today if e.get('kind') in ('skip', 'candidate_skip')]
        deviations = []
        if bad:
            deviations.append(f'{bad} unreadable log lines; coverage is incomplete.')
        if not today:
            deviations.append('No runner observation recorded; this is not a verified quiet day.')
        if len(mornings) > 1:
            deviations.append('Multiple morning decisions recorded; inspect restart duplicates.')
        ranking = morning.get('ranking') or morning.get('candidates', [])
        if morning and not morning.get('ranking'):
            deviations.append('Legacy log has only a partial candidate ranking.')
        if morning.get('at', '')[11:16] > '09:20':
            deviations.append('Ranking observed after 09:20; the earlier bar open is a retrospective paper reference.')
        missed = sorted(set(picks)-set(trades))
        if missed:
            deviations.append('Picked stocks lack completed paper fills.')
        complete = bool(today) and not bad and not missed and (
            any(e.get('kind') == 'closed_all' for e in today) or
            (not picks and any(e.get('kind') == 'skip' for e in today)) or
            (bool(morning) and not picks and not entries and not trades))
        per_trade = []
        for e in entries:
            quotes = e.get('quotes', {})
            for p in e.get('picks', []):
                t = trades.get(p['symbol'], {})
                fill = t.get('entry')
                official, at920 = p.get('official_open'), p.get('entry_bar_open')
                q = (quotes.get('quotes') or {}).get(p['symbol'], {})
                bid, ask = number(q.get('bid')), number(q.get('ask'))
                per_trade.append(dict(symbol=p['symbol'], observed_at=e.get('at'),
                                      official_open=official, open_0920=at920, reference_bar_at=p.get('entry_bar_at'),
                                      paper_fill=fill, trade=t or None,
                                      vs_official_bps=(fill/official-1)*10000 if fill and official else None,
                                      vs_0920_bps=(fill/at920-1)*10000 if fill and at920 else None,
                                      quotes=q or None, quote_source=quotes.get('source'),
                                      quote_observed_at=quotes.get('observed_at', e.get('at')),
                                      spread=ask-bid if bid and ask and ask >= bid else None))
        for s, t in trades.items():
            if not any(p['symbol'] == s for p in per_trade):
                per_trade.append(dict(symbol=s, trade=t, paper_fill=t.get('entry'), official_open=None, open_0920=None))
        historical = read_json(self.root/'data/h20/summary.json', {})
        benchmark = historical.get('versions', {}).get(label, {}).get('2', {}).get('net_per_morning')
        daily = {}
        closed = {}
        for e in events:
            d = e.get('day', '')
            if not d or d > day:
                continue
            if e.get('kind') == 'exit' and 'trade' in e:
                t = e['trade']
                closed[(d, t['symbol'])] = t['net']
        for (d, _), value in closed.items():
            daily[d] = daily.get(d, 0)+value
        peak, equity, drawdown = 10000., 10000., 0.
        for d in sorted(daily):
            equity += daily[d]
            peak = max(peak, equity)
            drawdown = max(drawdown, peak-equity)
        observed_net = sum(t.get('net', 0) for t in trades.values())
        net = observed_net if complete else None
        return dict(status='complete' if complete else 'incomplete',
                    process=dict(eligible_count=morning.get('eligible'), ranking=ranking,
                                 ranking_observed_at=morning.get('at'), picks=picks, skips=skips, deviations=deviations),
                    execution=dict(per_trade=per_trade, missed_fills=missed,
                                   note='Paper fills and bar opens do not measure actual slippage or prove available size. Missing quotes remain unknown.'),
                    outcome=dict(net=net, recorded_closed_net=observed_net, per_trade=list(trades.values()),
                                 drawdown_to_date=drawdown, drawdown_basis='Recorded closed paper trades in day order; gaps in coverage are not zero-return observations.',
                                 exposure_rupees=sum(t.get('qty', 0)*t.get('entry', 0) for t in trades.values()),
                                 expected_panic_morning_net=benchmark,
                                 difference_from_expectation=net-benchmark if net is not None and benchmark is not None and morning else None,
                                 benchmark_basis='Ten-year H17 daily open proxy on all qualifying mornings, not executable 09:20 evidence.'))

    def _journal(self, obj):
        path = self.root/'JOURNAL.md'
        text = path.read_text(encoding='utf-8') if path.exists() else '# Daily trading journal\n'
        heading = '## Paper days\n'
        if heading not in text:
            i = text.find('\n## ')
            text = (text[:i].rstrip()+'\n\n'+heading+'\n'+text[i+1:] if i >= 0
                    else text.rstrip()+'\n\n'+heading+'\n')
        before, paper = text.split(heading, 1)
        section, sep, tail = paper.partition('\n## ')
        marker = f"### {obj['day']}\n"
        if marker not in section:
            facts = []
            for label, r in obj['runners'].items():
                value = r['outcome']['net']
                facts.append(f"{label}: {'net Rs'+format(value, '.2f') if value is not None else 'outcome incomplete'}, {len(r['process']['picks'])} picks, {len(r['execution']['missed_fills'])} missing completed fills")
            entry = marker+'\n'+'; '.join(facts)+'. Rules unchanged; facts only; rule decisions wait for scheduled research review.\n'
            # ISO headings sort newest first, even if an older day is imported later.
            blocks = [b.strip() for b in section.split('### ') if b.strip()]
            blocks.append(entry.strip()[4:])
            section = '\n\n'+'\n\n'.join('### '+b for b in sorted(blocks, reverse=True))+'\n'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(before+heading+section+('\n## '+tail if sep else ''), encoding='utf-8')
            temporary.replace(path)

    def review(self, now):
        now = now.astimezone(IST)
        if now.weekday() >= 5 or now.hour*100+now.minute < 1535:
            return None
        day = now.date().isoformat()
        path = self.root/f'data/reviews/{day}.json'
        with self._lock:
            obj = read_json(path)
            if obj is None:
                from stalk.research.execution_log import review_evidence
                from stalk.sim.orb_live import summary as orb_summary
                runners = {label: self._runner_review(label, filename, day) for label, (_, filename) in VERSIONS.items()}
                obj = dict(day=day, observed_at=now.isoformat(), scheduled_at=day+'T15:35:00+05:30',
                           runners=runners, execution_evidence=review_evidence(self.root, day, runners),
                           orb=orb_summary(self.root, day),
                           note='Daily facts only. Keep experiments separate; change rules only in scheduled research reviews.')
                write_json(path, obj)
            self._journal(obj)  # Also recovers a journal write interrupted after JSON save.
            return obj
