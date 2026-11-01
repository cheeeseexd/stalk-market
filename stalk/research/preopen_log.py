"""Prospective pre-open auction observations. Quote data only; no orders."""
from __future__ import annotations

import asyncio
from datetime import datetime
import json
import math
from pathlib import Path

from stalk.config import BASE_DIR, IST
from stalk.research.execution_log import WATCHLIST, candidates, positive, nonnegative, stamp
from stalk.research.paper_daily import DailySources

SLOTS = ('09:01:00', '09:04:00', '09:07:00', '09:08:30', '09:15:00')
START_DAY = '2026-11-02'
WINDOW_SECONDS = 60


def json_value(value):
    """Preserve returned depth order/count fields; absent/nonfinite stays null."""
    if isinstance(value, dict):
        return {str(k): json_value(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def read_records(root, day):
    path = Path(root)/f'data/execution/preopen_{day}.jsonl'
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()] if path.exists() else []


def auction_row(symbol, raw, labels, requested, received, scheduled, opening):
    raw = raw if isinstance(raw, dict) else {}
    exchange = stamp(raw.get('timestamp') or raw.get('exchange_timestamp'))
    current = (exchange.date() == received.date() and -5 <= (received-exchange).total_seconds() <= 60
               if exchange else None)
    timely = 0 <= (requested-scheduled).total_seconds() < WINDOW_SECONDS and 0 <= (received-scheduled).total_seconds() < WINDOW_SECONDS
    ohlc = raw.get('ohlc') if isinstance(raw.get('ohlc'), dict) else {}
    indicative = next((positive(raw[k]) for k in ('indicative_price','indicative_equilibrium_price','iep')
                       if positive(raw.get(k)) is not None), None)
    return dict(symbol=symbol, instrument='NSE:'+symbol, labels=labels,
        exchange_timestamp=exchange.isoformat() if exchange else None, quote_current=current,
        quote_status='unavailable' if not raw else 'late' if not timely else
                     'timestamp_unknown' if current is None else 'stale' if not current else 'available',
        indicative_price=indicative, last_price=positive(raw.get('last_price')),
        total_buy_quantity=nonnegative(raw.get('buy_quantity',raw.get('total_buy_quantity'))),
        total_sell_quantity=nonnegative(raw.get('sell_quantity',raw.get('total_sell_quantity'))),
        depth=json_value(raw.get('depth')),
        ohlc_open_as_returned=positive(ohlc.get('open')),
        open_price=positive(ohlc.get('open')) if opening and current and timely and exchange >= scheduled else None)


class PreopenLog:
    def __init__(self, kite, root=BASE_DIR, clock=None, candidate_source=None):
        self.root, self.kite = Path(root), kite
        self.clock = clock or (lambda: datetime.now(IST))
        # The 09:10 brief is too late for auction capture. Observe provisional
        # gap candidates NOW using the same live NSE adapter, never later picks.
        self.candidate_source = candidate_source or DailySources(root)
        self._lock = asyncio.Lock()

    async def tick(self, now):
        now = now.astimezone(IST)
        if now.weekday() >= 5 or str(now.date()) < START_DAY or not getattr(self.kite,'logged_in',False):
            return []  # No file, public-source call, login attempt or quote request.
        if now < stamp(str(now.date())+'T'+SLOTS[0]+'+05:30'):
            return []
        async with self._lock:
            day = str(now.date())
            records = read_records(self.root, day)
            done = {r['slot'] for r in records}
            added = []
            for slot in SLOTS:
                scheduled = stamp(day+'T'+slot+'+05:30')
                if slot in done or now < scheduled:
                    continue
                record = dict(day=day,slot=slot,scheduled_at=scheduled.isoformat(),
                    requested_at=None,observed_at=None,status='missed',rows=[],candidate_notes=[],
                    candidate_observation=None,reason='Snapshot window missed; never reconstructed.')
                if 0 <= (now-scheduled).total_seconds() < WINDOW_SECONDS:
                    record = await self._capture(record,scheduled,records)
                records.append(record)
                path = self.root/f'data/execution/preopen_{day}.jsonl'
                path.parent.mkdir(parents=True,exist_ok=True)
                temporary = path.with_suffix('.tmp')
                temporary.write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in records),encoding='utf-8')
                temporary.replace(path)
                added.append(record)
            return added

    async def _capture(self,record,scheduled,records):
        observed = self.clock().astimezone(IST)
        wanted, notes = candidates(self.root,observed)
        for s in WATCHLIST:
            wanted.setdefault(s,[]).append(dict(source='watchlist',observed_at=observed.isoformat()))
        wanted['NIFTY 50'] = [dict(source='index',observed_at=observed.isoformat())]
        # Carry already-observed morning names forward, including to the open.
        for previous in records:
            for row in previous['rows']:
                for label in row['labels']:
                    if label['source'] not in ('watchlist','index') and label not in wanted.setdefault(row['symbol'],[]):
                        wanted[row['symbol']].append(label)
        candidate_observation = None
        if record['slot'] != '09:15:00':
            try:
                market = await asyncio.to_thread(self.candidate_source.market,observed)
                seen = self.clock().astimezone(IST)
                # Adapter certifies same-day pre-open timestamps. Candidates are
                # provisional gap observations, never executable/final selections.
                if market.get('stock_snapshot_current') is not True:
                    raise ValueError('Current pre-open candidate snapshot unknown')
                discovered = []
                for row in market.get('candidates',[]):
                    if (row.get('symbol') in market.get('tiers',{}) and
                            row.get('gap') is not None and row['gap'] < -.02):
                        s = row['symbol']
                        wanted.setdefault(s,[]).append(dict(source='preopen_gap_candidate',
                            observed_at=seen.isoformat(),source_at=row.get('source_at'),gap=row['gap']))
                        discovered.append(s)
                candidate_observation = dict(observed_at=seen.isoformat(),symbols=sorted(set(discovered)),
                    status='observed',note='Provisional live gap candidates; final eligibility and locks unknown.')
            except Exception as exc:
                notes.append(f'Live pre-open candidates unknown: {type(exc).__name__}. No later ranking substituted.')
        requested = self.clock().astimezone(IST)
        payload, reason = {}, None
        # Recheck both clock and login after candidate discovery; never obtain a
        # later quote and relabel it as a missed auction slot.
        if not 0 <= (requested-scheduled).total_seconds() < WINDOW_SECONDS:
            record.update(candidate_notes=notes,candidate_observation=candidate_observation)
            return record
        if not getattr(self.kite,'logged_in',False):
            reason = 'Kite not logged in; quote unknown.'
        else:
            try:
                response = await self.kite.call_tool('get_quotes',{'instruments':['NSE:'+s for s in sorted(wanted)]})
                payload = response.get('data',response) if isinstance(response,dict) else {}
                if not isinstance(payload,dict):
                    payload = {}
                if not payload:
                    reason = 'Kite returned no quote rows.'
            except Exception as exc:
                reason = f'Kite quote unavailable: {type(exc).__name__}'
        received = self.clock().astimezone(IST)
        record.update(requested_at=requested.isoformat(),observed_at=received.isoformat(),
            status='observed' if (received-scheduled).total_seconds() < WINDOW_SECONDS else 'late',
            reason=reason,candidate_notes=notes,candidate_observation=candidate_observation,
            rows=[auction_row(s,payload.get('NSE:'+s),wanted[s],requested,received,scheduled,
                              record['slot']=='09:15:00') for s in sorted(wanted)])
        return record


def latest_summary(root=BASE_DIR):
    paths = sorted((Path(root)/'data/execution').glob('preopen_????-??-??.jsonl'),reverse=True)
    if not paths:
        return None
    day = paths[0].stem.removeprefix('preopen_')
    return dict(day=day,snapshots=read_records(root,day),
        note='Prospective Kite quote observations only. Indicative price may be absent. Depth and totals are as returned, not certified auction imbalance. Null means unknown; missed slots are never reconstructed. Open is quote OHLC.open observed at09:15, not a fill.')
