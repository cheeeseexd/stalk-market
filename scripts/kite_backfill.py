"""Read-only, resumable H26 five-minute download. Run only after Kite login.

python scripts/kite_backfill.py [--start 2015-01-01] [--end YYYY-MM-DD]
No order tools are used; authentication is checked before any history request.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd
from stalk.config import IST
from stalk.data.kite_mcp import KiteMCP, KiteAuthRequired
from stalk.research.orb import COLS, normalize, universe


def atomic_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False)+'\n', encoding='utf-8')
    tmp.replace(path)


def chunks(start, end):
    current = date.fromisoformat(start)
    finish = date.fromisoformat(end)
    if current > finish:
        raise ValueError('start must precede end')
    while current <= finish:
        last = min(finish, current+timedelta(days=98))
        yield current.isoformat(), last.isoformat()
        current = last+timedelta(days=1)


async def backfill(client, symbols, start, end, output, pause=.4, sleeper=asyncio.sleep):
    if not getattr(client, 'logged_in', False) or not await client.check():
        raise KiteAuthRequired('Kite login required; no historical requests made')
    if pause < .4:
        raise ValueError('minimum request spacing is 0.4 seconds')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint_path = output/'checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_text()) if checkpoint_path.exists() else {}
    coverage = {}
    for symbol in sorted(symbols):
        if not symbol.replace('-', '').replace('&', '').isalnum():
            raise ValueError('unsafe symbol')
        path = output/(symbol+'.pkl')
        frame = pd.read_pickle(path) if path.exists() else pd.DataFrame(columns=COLS)
        done = checkpoint.setdefault(symbol, {})
        errors = []
        for lo, hi in chunks(start, end):
            key = lo+':'+hi
            if key in done:
                if not path.exists():
                    raise ValueError('checkpoint without symbol data')
                continue
            for attempt in range(3):
                if not client.logged_in:
                    raise KiteAuthRequired('Kite login expired; resume after login')
                await sleeper(pause)
                try:
                    rows = await client.historical(symbol, '5minute', lo+' 00:00:00', hi+' 23:59:59')
                    # Validate responses rather than checkpointing a malformed/partial parse.
                    # Kite's old illiquid history has zero-price placeholder bars and Muhurat-session bars
                    # (17:35-18:00). Drop them and count every drop, instead of rejecting the whole chunk.
                    fresh = normalize(rows)[COLS]
                    dropped = len(rows)-len(fresh)
                    opening = normalize(fresh)
                    zero_open = int(((opening.hm == 915) & (opening.v == 0) & (opening.h > opening.l)).sum())
                    if len(fresh):
                        dates = pd.to_datetime(fresh.ts, unit='s', utc=True).dt.tz_convert(IST).dt.strftime('%Y-%m-%d')
                        if not dates.between(lo, hi).all():
                            raise ValueError('broker response outside requested dates')
                    frame = pd.concat([frame, fresh]).drop_duplicates('ts').sort_values('ts').reset_index(drop=True)
                    tmp = path.with_suffix('.tmp')
                    frame.to_pickle(tmp)
                    tmp.replace(path)  # commit data BEFORE checkpoint, replay safe after interruption
                    done[key] = dict(rows=len(rows), empty=not bool(rows), dropped=dropped, zero_volume_open=zero_open)
                    atomic_json(checkpoint_path, checkpoint)
                    break
                except KiteAuthRequired:
                    raise
                except Exception as exc:
                    if attempt == 2:
                        errors.append(dict(start=lo, end=hi, error=type(exc).__name__))
                    else:
                        await sleeper(2**attempt)
            if errors:
                break  # Resume first failed chunk; do not hide gaps behind a later max timestamp.
        f = normalize(frame)
        coverage[symbol] = dict(rows=len(frame), first_day=f.day.min() if len(f) else None,
                                last_day=f.day.max() if len(f) else None, sessions=int(f.day.nunique()),
                                opening_sessions=int(f[f.hm == 915].day.nunique()),
                                flatten_sessions=int(f[f.hm == 1510].day.nunique()),
                                opening_zero_volume=int(((f.hm == 915) & (f.v == 0)).sum()),
                                opening_nonflat_zero_volume=int(((f.hm == 915) & (f.v == 0) & (f.h > f.l)).sum()),
                                completed_chunks=len(done), errors=errors)
        atomic_json(output/'coverage.json', dict(start=start, end=end, source='Kite historical 5minute',
                    survivorship='Stored current EQ universe; delisted/missing tokens not reconstructed', symbols=coverage))
    return coverage


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='2015-01-01')
    parser.add_argument('--end', default=(datetime.now(IST).date()-timedelta(days=1)).isoformat())
    args = parser.parse_args()
    if args.end >= datetime.now(IST).date().isoformat():
        raise ValueError('end must be a completed prior calendar day')
    kite = KiteMCP()
    # A saved MCP session must be checked; constructing a client does not imply login.
    if not await kite.check():
        raise KiteAuthRequired('Log into Kite in the normal app first, then retry')
    result = await backfill(kite, universe(), args.start, args.end, ROOT/'data/kite_5m')
    print('Coverage written for', len(result), 'symbols; inspect coverage.json for gaps/errors.')


if __name__ == '__main__':
    asyncio.run(main())
