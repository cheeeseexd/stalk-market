"""H16 prospective displayed-book observations. Read-only; no order interface."""
from __future__ import annotations

import asyncio
from datetime import datetime
from html.parser import HTMLParser
import json
import math
from pathlib import Path
from statistics import median

import httpx

from stalk.config import BASE_DIR, IST
from stalk.research.paper_daily import VERSIONS, number, read_json, write_json

SLOTS = ('09:15:30', '09:20:00', '09:25:00', '11:15:00', '11:20:00', '15:05:00', '15:10:00')
MIS_URL = 'https://zerodha.com/margin-calculator/Equity/'
WINDOW_SECONDS = 60
WATCHLIST = ('HDFCBANK', 'ICICIBANK', 'SBIN', 'AXISBANK', 'RELIANCE', 'BHARTIARTL',
             'INFY', 'TCS', 'LT', 'TATASTEEL', 'HINDALCO', 'BEL')
WATCHLIST_START = '2026-11-02'


def stamp(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (dt.replace(tzinfo=IST) if dt.tzinfo is None else dt.astimezone(IST))
    except (ValueError, TypeError):
        return None


def positive(value):
    x = number(value)
    return x if x is not None and x > 0 else None


def nonnegative(value):
    x = number(value)
    return x if x is not None and x >= 0 else None


def med(values):
    known = [v for v in values if v is not None]
    return median(known) if known else None


class MarginTable(HTMLParser):
    """Fail closed on a changed source; parse the public table's explicit attributes."""
    def __init__(self):
        super().__init__()
        self.symbols = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'tr' and a.get('data-scrip') and positive(a.get('data-mis_margin')):
            self.symbols.add(a['data-scrip'].strip().upper())


class MISSources:
    def fetch(self):
        with httpx.Client(timeout=8, follow_redirects=True) as client:
            response = client.get(MIS_URL)
            response.raise_for_status()
            return response.text


def mis_today(root, now, source):
    path = Path(root)/f'data/execution/mis/{now.date()}.json'
    existing = read_json(path)
    if existing is not None:
        return existing
    obj = dict(day=str(now.date()), retrieved_at=now.isoformat(), source=MIS_URL,
               symbols=None, status='unknown', reason=None)
    try:
        html = source.fetch()
        parser = MarginTable()
        parser.feed(html)
        if not parser.symbols or 'MIS' not in html:
            raise ValueError('Public MIS table empty or schema unrecognised')
        # Keep the source alongside its parsed membership for later audit.
        path.parent.mkdir(parents=True, exist_ok=True)
        path.with_suffix('.html').write_text(html, encoding='utf-8')
        obj.update(symbols=sorted(parser.symbols), status='available')
    except Exception as exc:
        obj['reason'] = f'MIS source unavailable: {type(exc).__name__}'
    write_json(path, obj)  # A failed daily attempt is cached too; no repeated requests.
    return obj


def levels(depth, side):
    raw = depth.get(side)
    if not isinstance(raw, list):
        return None
    result = []
    for row in raw:
        if not isinstance(row, dict):
            return None
        px, qty = nonnegative(row.get('price')), nonnegative(row.get('quantity'))
        if px is None or qty is None or qty != int(qty):
            return None
        if px > 0 and qty > 0:
            result.append(dict(price=px, quantity=int(qty)))
        elif qty > 0:  # Missing price with claimed size is not an empty book.
            return None
    return sorted(result, key=lambda r: r['price'], reverse=side == 'buy')


def walk(book, side, midpoint):
    empty = dict(notional=5000, quantity=None, filled_quantity=None, unfilled_quantity=None,
                 complete=None, vwap=None, slippage_bps=None, impact_bps=None)
    if book is None:
        return empty
    if not book:
        return dict(empty, complete=False, filled_quantity=0)
    best = book[0]['price']
    qty = math.floor(5000 / best)
    remaining, cost = qty, 0.
    for row in book:
        take = min(remaining, row['quantity'])
        cost += take * row['price']
        remaining -= take
        if not remaining:
            break
    complete = remaining == 0 and qty > 0
    vwap = cost/qty if complete else None
    direction = 1 if side == 'buy' else -1
    return dict(empty, quantity=qty, filled_quantity=qty-remaining, unfilled_quantity=remaining,
                complete=complete, vwap=vwap,
                slippage_bps=direction*(vwap/midpoint-1)*10000 if complete and midpoint else None,
                impact_bps=direction*(vwap/best-1)*10000 if complete else None)


def best_level(book, raw):
    if book:
        return book[0]['price'], book[0]['quantity']
    if book is None and isinstance(raw, list) and raw and isinstance(raw[0], dict):
        # Preserve a supplied best price even if size/deeper levels are unknown.
        px, qty = positive(raw[0].get('price')), nonnegative(raw[0].get('quantity'))
        if px and qty != 0:
            return px, int(qty) if qty is not None and qty == int(qty) else None
    return None, None


def quote_row(symbol, raw, received, scheduled, requested, mis, labels):
    raw = raw if isinstance(raw, dict) else {}
    depth = raw.get('depth') if isinstance(raw.get('depth'), dict) else {}
    bids, asks = levels(depth, 'buy'), levels(depth, 'sell')
    bid, bid_size = best_level(bids, depth.get('buy'))
    ask, ask_size = best_level(asks, depth.get('sell'))
    midpoint = (bid+ask)/2 if bid and ask and ask >= bid else None
    book_valid = not (bid and ask and ask < bid)
    exchange = stamp(raw.get('timestamp') or raw.get('exchange_timestamp'))
    current = (exchange.date() == received.date() and -5 <= (received-exchange).total_seconds() <= 60
               if exchange else None)
    timely = 0 <= (requested-scheduled).total_seconds() < WINDOW_SECONDS and (received-scheduled).total_seconds() < WINDOW_SECONDS
    last = positive(raw.get('last_price'))
    usable = bool((last or bid or ask) and current and timely)
    lower = positive(raw.get('lower_circuit_limit', raw.get('lower_circuit')))
    upper = positive(raw.get('upper_circuit_limit', raw.get('upper_circuit')))
    if lower and upper and lower > upper:
        lower, upper = None, None
    at_lower = math.isclose(last, lower, abs_tol=1e-6, rel_tol=1e-8) if last and lower and usable else None
    at_upper = math.isclose(last, upper, abs_tol=1e-6, rel_tol=1e-8) if last and upper and usable else None
    at_circuit = (True if at_lower or at_upper else False if at_lower is False and at_upper is False else None)
    # Lower lock prevents selling; upper lock prevents buying. Missing depth is unknown.
    locked = (True if (at_lower is True and bids == []) or (at_upper is True and asks == [])
              else False if at_circuit is False or (at_lower is True and bids) or (at_upper is True and asks)
              else None)
    members = mis.get('symbols')
    status = ('unavailable' if not raw or not (last or bid or ask) else 'late' if not timely else
              'timestamp_unknown' if current is None else 'stale' if not current else 'available')
    return dict(symbol=symbol, scheduled_at=scheduled.isoformat(), requested_at=requested.isoformat(),
                timestamp=received.isoformat(), exchange_timestamp=exchange.isoformat() if exchange else None,
                labels=labels, quote_status=status, quotes_available=usable, quote_current=current,
                last_price=last, bid=bid, ask=ask, bid_size=bid_size,
                ask_size=ask_size, depth=dict(buy=bids, sell=asks),
                total_buy_quantity=nonnegative(raw.get('buy_quantity', raw.get('total_buy_quantity'))),
                total_sell_quantity=nonnegative(raw.get('sell_quantity', raw.get('total_sell_quantity'))),
                lower_circuit=lower, upper_circuit=upper,
                distance_to_lower_bps=(last-lower)/last*10000 if last and lower and usable else None,
                distance_to_upper_bps=(upper-last)/last*10000 if last and upper and usable else None,
                at_circuit=at_circuit, locked=locked,
                mis_eligible=symbol in members if members is not None else None,
                spread_bps=(ask-bid)/midpoint*10000 if usable and midpoint else None,
                buy_estimate=walk(asks if usable and book_valid else None, 'buy', midpoint),
                sell_estimate=walk(bids if usable and book_valid else None, 'sell', midpoint))


def read_records(root, day):
    path = Path(root)/f'data/execution/{day}.jsonl'
    if not path.exists():
        return []
    # Fail visibly on corrupt evidence; never silently replace a partial/corrupt file.
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def candidates(root, now):
    """Only observations already available by request time; no retrospective picks."""
    day = str(now.date())
    found, notes = {}, []

    def add(symbol, label, observed_at):
        if isinstance(symbol, str) and symbol:
            found.setdefault(symbol, [])
            item = dict(source=label, observed_at=observed_at)
            if item not in found[symbol]:
                found[symbol].append(item)

    brief = read_json(Path(root)/f'data/briefs/{day}.json', {})
    observed = stamp(brief.get('observed_at'))
    if observed and observed <= now:
        for row in brief.get('candidates', [])[:8]:
            add(row.get('symbol'), 'brief_top8', brief['observed_at'])
    if not found:
        notes.append('Brief top-eight candidates unavailable at request time.')
    for label, (_, filename) in VERSIONS.items():
        path = Path(root)/'data'/filename
        events = []
        if path.exists():
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    row = json.loads(line)
                    at = stamp(row.get('at'))
                    if row.get('day') == day and row.get('kind') == 'morning' and at and at <= now:
                        events.append(row)
                except (ValueError, TypeError):
                    notes.append(f'{label} runner log has an unreadable line.')
        if not events:
            notes.append(f'{label} picks/ranking not yet observed; no decision inferred.')
            continue
        event = events[-1]
        for row in (event.get('ranking') or event.get('candidates', []))[:8]:
            add(row.get('symbol') if isinstance(row, dict) else row[0], label+'_top8', event['at'])
        for s in event.get('picks', []):
            add(s, label+'_pick', event['at'])
    return found, notes


class ExecutionLog:
    def __init__(self, kite, root=BASE_DIR, mis_source=None, clock=None, h19_source=None):
        self.root, self.kite = Path(root), kite
        self.mis_source = mis_source or MISSources()
        self.clock = clock or (lambda: datetime.now(IST))
        # Optional certified live input adapter returns {status, reason, candidates}.
        # No historical CSV/cache is substituted for a live decision.
        self.h19_source = h19_source
        self._lock = asyncio.Lock()
        from stalk.research.preopen_log import PreopenLog
        self.preopen = PreopenLog(kite, root, clock=lambda: self.clock())

    async def tick(self, now):
        now = now.astimezone(IST)
        if now.weekday() >= 5:
            return []
        await self.preopen.tick(now)
        async with self._lock:
            day = str(now.date())
            records = read_records(self.root, day)
            done = {r['slot'] for r in records}
            added = []
            for slot in SLOTS:
                scheduled = stamp(day+'T'+slot+'+05:30')
                if slot in done or now < scheduled:
                    continue
                requested = self.clock().astimezone(IST)
                delay = (requested-scheduled).total_seconds()
                record = dict(day=day, slot=slot, scheduled_at=scheduled.isoformat(), requested_at=requested.isoformat(),
                              observed_at=None, status='missed', reason='Snapshot window missed; no backfill.',
                              candidate_notes=[], h19=None, mis=None, rows=[])
                if 0 <= delay < WINDOW_SECONDS:
                    record = await self._capture(record, requested, scheduled, records)
                records.append(record)
                path = self.root/f'data/execution/{day}.jsonl'
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(''.join(json.dumps(r, ensure_ascii=False, allow_nan=False)+'\n' for r in records), encoding='utf-8')
                temporary.replace(path)  # One complete envelope is the idempotency unit.
                added.append(record)
            return added

    async def _capture(self, record, requested, scheduled, records):
        wanted, notes = candidates(self.root, requested)
        slot = record['slot']
        h19 = None
        if slot == '11:15:00':
            h19 = dict(status='skipped', candidates=None,
                       reason='Certified live completed 10:15 candle, prior official range and point-in-time eligibility/calendar pipeline unavailable.')
            if self.h19_source:
                try:
                    h19 = await self.h19_source(requested)
                except Exception as exc:
                    h19 = dict(status='skipped', candidates=None, reason=f'Live H19 inputs unavailable: {type(exc).__name__}')
        elif slot in ('11:20:00', '15:05:00', '15:10:00'):
            decision = next((r for r in records if r['slot'] == '11:15:00'), {})
            h19 = decision.get('h19') or dict(status='skipped', candidates=None, reason='11:15 prospective H19 decision unavailable.')
        if h19 and h19.get('status') == 'available':
            for row in h19.get('candidates') or []:
                wanted.setdefault(row['symbol'], []).append(dict(source='h19_'+row['side'], observed_at=requested.isoformat()))
        # Observation only: never a runner pick, order, or inferred quote.
        existing = sorted(wanted)
        if str(requested.date()) >= WATCHLIST_START:
            for symbol in WATCHLIST:
                wanted.setdefault(symbol, []).append(dict(source='watchlist', observed_at=requested.isoformat()))
        row_order = existing + sorted(set(wanted)-set(existing))
        # Quotes have priority over public MIS HTTP; slow public data must not consume the slot.
        payload, reason = {}, None
        if wanted and getattr(self.kite, 'logged_in', False):
            try:
                response = await self.kite.call_tool('get_quotes', {'instruments': ['NSE:'+s for s in sorted(wanted)]})
                payload = response.get('data', response) if isinstance(response, dict) else {}
                if not isinstance(payload, dict):
                    payload = {}
                if not payload:
                    reason = 'Kite returned no quote rows.'
            except Exception as exc:
                reason = f'Kite quotes unavailable: {type(exc).__name__}'
        else:
            reason = 'Kite not logged in.' if wanted else 'No candidates available at request time.'
        received = self.clock().astimezone(IST)
        mis = await asyncio.to_thread(mis_today, self.root, requested, self.mis_source)
        rows = [quote_row(s, payload.get('NSE:'+s), received, scheduled, requested, mis, wanted[s]) for s in row_order]
        record.update(observed_at=received.isoformat(), status='observed' if (received-scheduled).total_seconds() < WINDOW_SECONDS else 'late',
                      reason=reason, rows=rows, candidate_notes=notes, h19=h19,
                      mis={k: v for k, v in mis.items() if k != 'symbols'})
        return record


def summarize(record):
    all_rows = record['rows']
    # Keep H16 candidate aggregates comparable; observation-only names have
    # their own rows and must not dilute the frozen panic execution screen.
    rows = [r for r in all_rows if any(x.get('source') != 'watchlist' for x in r.get('labels', []))]
    if not any(any(x.get('source') == 'watchlist' for x in r.get('labels', [])) for r in all_rows):
        rows = all_rows
    n = len(rows)
    known_mis = [r['mis_eligible'] for r in rows if r['mis_eligible'] is not None]
    flags = [r['locked'] is True or r['at_circuit'] is True for r in rows
             if r['locked'] is not None or r['at_circuit'] is not None]
    return dict(slot=record['slot'], scheduled_at=record['scheduled_at'], observed_at=record['observed_at'],
                status=record['status'], reason=record['reason'], candidate_notes=record['candidate_notes'],
                h19=record['h19'], mis=record['mis'], symbols_seen=n if record['status'] != 'missed' else None,
                median_spread_bps=med(r['spread_bps'] for r in rows),
                spread_known_count=sum(r['spread_bps'] is not None for r in rows),
                share_with_quotes=sum(r['quotes_available'] for r in rows)/n if n else None,
                share_mis_eligible=sum(known_mis)/len(known_mis) if known_mis else None,
                mis_known_count=len(known_mis), mis_known_share=len(known_mis)/n if n else None,
                circuit_known_count=len(flags),
                any_locked_or_at_circuit=True if any(flags) else False if n and len(flags) == n else None,
                flagged_symbols=[r['symbol'] for r in rows if r['locked'] is True or r['at_circuit'] is True],
                watchlist_count=sum(any(x.get('source') == 'watchlist' for x in r.get('labels', [])) for r in all_rows),
                rows=all_rows)


def latest_summary(root=BASE_DIR):
    paths = sorted((Path(root)/'data/execution').glob('????-??-??.jsonl'), reverse=True)
    if not paths:
        return None
    day = paths[0].stem
    return dict(day=day, snapshots=[summarize(r) for r in read_records(root, day)],
                note='Displayed-book estimates only. Public MIS membership is not broker acceptance. Null means unknown; missed slots are never reconstructed.')


def review_evidence(root, day, runners):
    records = read_records(root, day)
    snapshot = next((r for r in records if r['slot'] == '09:20:00'), {})
    by_symbol = {r['symbol']: r for r in snapshot.get('rows', [])}
    versions = {}
    for label, runner in runners.items():
        picks = runner['process']['picks']
        per_pick = []
        for symbol in picks:
            row = by_symbol.get(symbol, {})
            per_pick.append(dict(symbol=symbol, quote_status=row.get('quote_status', 'not_captured'),
                                 spread_bps=row.get('spread_bps'),
                                 buy_estimate=row.get('buy_estimate'), mis_eligible=row.get('mis_eligible'),
                                 locked=row.get('locked'), at_circuit=row.get('at_circuit')))
        versions[label] = dict(picks=picks, per_pick=per_pick,
                               median_spread_bps=med(r['spread_bps'] for r in per_pick),
                               median_buy_slippage_bps=med((r['buy_estimate'] or {}).get('slippage_bps') for r in per_pick),
                               median_spread_plus_impact_bps=med(r['spread_bps']+r['buy_estimate']['impact_bps'] for r in per_pick
                                    if r['spread_bps'] is not None and r['buy_estimate'] and r['buy_estimate']['impact_bps'] is not None),
                               missing_quote_picks=[r['symbol'] for r in per_pick if r['quote_status'] != 'available'],
                               incomplete_depth_picks=[r['symbol'] for r in per_pick if (r['buy_estimate'] or {}).get('complete') is not True],
                               locked_picks=[r['symbol'] for r in per_pick if r['locked'] is True],
                               non_mis_picks=[r['symbol'] for r in per_pick if r['mis_eligible'] is False],
                               unknown_mis_picks=[r['symbol'] for r in per_pick if r['mis_eligible'] is None],
                               unknown_lock_picks=[r['symbol'] for r in per_pick if r['locked'] is None])
    return dict(snapshot_status=snapshot.get('status', 'not_captured'), versions=versions,
                note='09:20 captured symbols matched to later recorded picks; missing picks remain unknown. Medians describe known rows only and cannot pass the preregistered gate with missing costs. Hypothetical visible-depth fills, not realised slippage.')
