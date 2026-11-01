"""H26 daily paper observations. No broker orders, no live guard calls.

All three registered stops and a seeded random-stock/random-side control run
separately. Late bar references are explicitly labelled, never realised fills.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from stalk.config import BASE_DIR, IST
from stalk.data.yahoo import fetch
from stalk.research.orb import (COLS, VERSIONS, normalize, prepare, select, random_picks,
                                 walk, universe, event_exclusions)


NOTE = ('Paper only, fixed Rs5,000 slots; V1/V2/V3 stops 10/25/50% ATR. '
        'Kite five-minute bars when logged in; Yahoo fallback is labelled. '
        'Late observations are historical bar references, not realised stop-order fills. '
        'Null means unknown/incomplete. Random control uses the same day and execution model.')


def records(root=BASE_DIR):
    path = Path(root)/'data/orb_paper.jsonl'
    if not path.exists():
        return []
    # A partial write is not silently interpreted as an empty paper day.
    return [json.loads(s) for s in path.read_text(encoding='utf-8').splitlines() if s.strip()]


def summary(root=BASE_DIR, day=None):
    rows = [r for r in records(root) if day is None or r['day'] == day]
    def ledger(v, group):
        relevant = [r for r in rows if r.get('group') == group and r.get('version') == v]
        finished = [r for r in relevant if r['kind'] == 'day_end']
        trades = [r['trade'] for r in relevant if r['kind'] == 'exit']
        latest = max((r['day'] for r in rows), default=None)
        complete = bool(finished) and all(r.get('complete') for r in finished)
        # Any captured unfinished day makes aggregate net unknown, even if old days closed.
        decisions = {r['day'] for r in rows if r['kind'] in ('picks', 'skip')}
        complete = complete and {r['day'] for r in finished} == decisions
        return dict(days=len(finished) if relevant else None, trades=len(trades) if relevant else None,
                    net=sum(t['net'] for t in trades) if complete else None,
                    wins=sum(t['net'] > 0 for t in trades) if complete else None,
                    losses=sum(t['net'] <= 0 for t in trades) if complete else None,
                    last_day_trades=[t for t in trades if t['day'] == latest] if relevant else None)
    return dict(versions={v: ledger(v, 'strategy') for v in VERSIONS},
                benchmark={v: ledger(v, 'benchmark') for v in VERSIONS}, note=NOTE)


class BarSources:
    """Only market-data methods. Keep prior and current bars on the same provider."""
    def __init__(self, kite=None):
        self.kite = kite
        self.cache = {}
        self.semaphore = asyncio.Semaphore(8)

    async def bars(self, symbol, now, provider=None):
        async with self.semaphore:
            key = (symbol, str(now.date()))
            logged = self.kite is not None and self.kite.logged_in
            if provider == 'Kite' and not logged:
                raise RuntimeError('Kite unavailable; do not change price basis after picks')
            if logged and provider != 'Yahoo fallback':
                try:
                    prior = self.cache.get(key)
                    lo = str(now.date()) if prior is not None else str((now-timedelta(days=65)).date())
                    rows = await self.kite.historical(symbol, '5minute', lo+' 00:00:00', str(now.date())+' 15:30:00')
                    f = pd.DataFrame(rows, columns=COLS)
                    if prior is not None:
                        f = pd.concat([prior, f]).drop_duplicates('ts', keep='last')
                    self.cache[key] = f
                    return f, 'Kite'
                except Exception:
                    if provider == 'Kite':
                        raise
            # fetch() filters partial candles. Label explicitly and use its full history.
            rows = await asyncio.to_thread(fetch, symbol, '5m', '60d')
            return pd.DataFrame(rows, columns=COLS), 'Yahoo fallback'


class ORBPaper:
    def __init__(self, store=None, kite=None, root=BASE_DIR, sources=None, tiers=None, clock=None):
        self.store, self.root = store, Path(root)
        self.sources = sources or BarSources(kite)
        self.tiers = tiers if tiers is not None else universe(root)
        self.clock = clock or (lambda: datetime.now(IST))
        self._lock = asyncio.Lock()

    def write(self, now, kind, **data):
        path = self.root/'data/orb_paper.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        row = dict(day=str(now.date()), at=now.isoformat(), kind=kind, **data)
        with path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, allow_nan=False)+'\n')
            f.flush()
        return row

    async def _fetch(self, symbols, now, providers=None):
        async def one(s):
            try:
                f, source = await self.sources.bars(s, now, (providers or {}).get(s))
                f = normalize(f)
                # Completed bars only. A visible 15:10 open is sufficient to flatten;
                # hide its incomplete high/low/close/volume.
                complete = f[f.ts+300 <= now.timestamp()].copy()
                current = f[(f.day == str(now.date())) & (f.hm == 1510) & (f.ts <= now.timestamp())].copy()
                if len(current):
                    for c in ('h', 'l', 'c'):
                        current[c] = current.o
                    current['v'] = 0
                    complete = pd.concat([complete, current]).drop_duplicates('ts', keep='first')
                return s, complete[COLS], source, None
            except Exception as exc:
                return s, None, None, type(exc).__name__
        return await asyncio.gather(*(one(s) for s in symbols))

    async def tick(self, now=None):
        now = (now or self.clock()).astimezone(IST)
        hm = now.hour*100+now.minute
        if now.weekday() >= 5 or str(now.date()) < '2026-11-02' or hm < 920 or hm > 1535:
            return
        async with self._lock:
            rows = [r for r in records(self.root) if r['day'] == str(now.date())]
            if any(r['kind'] in ('complete', 'skip') for r in rows):
                return
            decision = next((r for r in rows if r['kind'] == 'picks'), None)
            if decision is None:
                if hm >= 930:
                    self.write(now, 'skip', reason='Opening decision missed; no retrospective reconstruction', net=None)
                    return
                fetched = await self._fetch(self.tiers, now)
                # Actual completion time, not the timestamp before a long network scan.
                observed = self.clock().astimezone(IST)
                if observed.date() != now.date() or observed.hour*100+observed.minute >= 930:
                    self.write(observed, 'skip', reason='Universe scan missed decision window', net=None)
                    return
                frames = {s: f for s, f, _, err in fetched if err is None}
                missing = [s for s, f, _, err in fetched if err or f is None or not
                           ((normalize(f).day == str(now.date())) & (normalize(f).hm == 915)).any()]
                if missing:
                    # A partial universe cannot establish the top two. Retry while timely.
                    self.write(observed, 'data_missing', symbols=missing, net=None)
                    return
                bad_volume = []
                for s, f in frames.items():
                    bars = normalize(f)
                    opening = bars[bars.hm == 915].tail(15)
                    if ((opening.v == 0) & (opening.h > opening.l)).any():
                        bad_volume.append(s)
                if bad_volume:
                    self.write(observed, 'data_missing', symbols=bad_volume,
                               reason='Nonflat opening candle has zero volume; RV source invalid', net=None)
                    return
                data, features = await asyncio.to_thread(prepare, frames, self.tiers)
                excluded, calendar = event_exclusions(self.root, str(now.date()))
                # Store events supplement locally cached notices, causal announcement cutoff.
                if self.store:
                    cutoff = int(now.replace(hour=9, minute=20, second=0, microsecond=0).timestamp())
                    ev = self.store._rows("SELECT symbol FROM events WHERE day=? AND announced_ts IS NOT NULL "
                        "AND announced_ts < ? AND lower(coalesce(purpose,'') || ' ' || coalesce(detail,'')) LIKE '%result%'",
                        (str(now.date()), cutoff))
                    excluded |= {r['symbol'] for r in ev}
                picks, pool = select(str(now.date()), data, features, self.tiers, excluded)
                if not any(str(now.date()) in f.index and not f.loc[str(now.date())].isna().any() for f in features.values()):
                    self.write(observed, 'skip', reason='Prior14-session features unavailable', net=None)
                    return
                rng = np.random.default_rng(2601+int(now.strftime('%Y%m%d')))
                controls = random_picks(pool, rng)
                decision = self.write(observed, 'picks', picks=picks, benchmark_picks=controls,
                    providers={s: source for s, _, source, _ in fetched}, calendar=calendar, eligible=len(pool),
                    late_reference=observed > now.replace(hour=9, minute=21, second=0), note=NOTE)
                rows.append(decision)
            else:
                symbols = sorted({p['symbol'] for p in decision['picks']+decision['benchmark_picks']})
                fetched = await self._fetch(symbols, now, decision['providers'])
                frames = {s: f for s, f, _, err in fetched if err is None}
                data = {s: {d: g for d, g in normalize(f).groupby('day')} for s, f in frames.items()}
            now = self.clock().astimezone(IST)
            rows = [r for r in records(self.root) if r['day'] == str(now.date())]
            final = now.hour*100+now.minute >= 1515
            all_done = True
            for group, picks in (('strategy', decision['picks']), ('benchmark', decision['benchmark_picks'])):
                for v, fraction in VERSIONS.items():
                    if any(r['kind'] == 'day_end' and r.get('group') == group and r.get('version') == v for r in rows):
                        continue
                    statuses = []
                    for pick in picks:
                        events = [r for r in rows if r.get('group') == group and r.get('version') == v and r.get('symbol') == pick['symbol']]
                        if any(r['kind'] == 'exit' for r in events):
                            statuses.append('closed')
                            continue
                        frame = data.get(pick['symbol'], {}).get(str(now.date()))
                        r = walk(pick, frame, fraction, final=final) if frame is not None else dict(status='unknown', fill=None, trade=None)
                        statuses.append(r['status'])
                        tags = dict(group=group, version=v, symbol=pick['symbol'])
                        if r['fill'] and not any(e['kind'] == 'fill' for e in events):
                            self.write(now, 'fill', **tags, fill=r['fill'], paper_reference=True,
                                       source=decision['providers'][pick['symbol']], late_reference=decision['late_reference'])
                        if r['trade']:
                            self.write(now, 'exit', **tags, trade=r['trade'], reason=r['trade']['reason'])
                    done = all(s in ('closed', 'no_trigger', 'unfundable') for s in statuses)
                    if done or final:
                        self.write(now, 'day_end', group=group, version=v, complete=done, statuses=statuses)
                    else:
                        all_done = False
            if all_done or final:
                self.write(now, 'complete', note='Unknown paths retained' if final else 'All observations resolved')
