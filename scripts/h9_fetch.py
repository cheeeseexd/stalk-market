"""H9 inputs only: resumable, paced NSE downloads; never calculate stock returns."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import panic_bhav as pb
from scripts.panic_final import nifty
from stalk.config import BASE_DIR

OUT = Path(BASE_DIR) / 'data' / 'h9'
ANN = 'https://www.nseindia.com/api/corporate-announcements'
ETF = 'https://nsearchives.nseindia.com/content/equities/eq_etfseclist.csv'


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, default=str, allow_nan=False), encoding='utf-8')
    tmp.replace(path)


class PacedNSE:
    """Shared thread-safe request-start pacing, at most three in-flight requests."""
    def __init__(self):
        self.client = httpx.Client(headers=pb.UA, timeout=35, follow_redirects=True)
        self.lock = threading.Lock()
        self.last = 0.0
        self.calls = 0
        self.audit = []

    def get(self, url, **kwargs):
        with self.lock:
            if self.calls % 40 == 0:
                time.sleep(max(0, .35 - (time.monotonic() - self.last)))
                self.last = time.monotonic()
                try:
                    r = self.client.get('https://www.nseindia.com/')
                    self.audit.append({'home_call': self.calls, 'status': r.status_code})
                except httpx.HTTPError as e:
                    self.audit.append({'home_call': self.calls, 'error': str(e)})
            time.sleep(max(0, .35 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            self.calls += 1
        response = self.client.get(url, **kwargs)
        if url.endswith('.csv.zip') and response.status_code == 200:
            dest = OUT / 'raw_bhav' / url.rsplit('/', 1)[-1]
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(response.content)
        return response

    def close(self):
        self.client.close()
        dump(OUT / 'request_audit.json', {'calls_this_run': self.calls, 'homepage_refreshes': self.audit,
                                         'minimum_request_start_interval_seconds': .35})


def calendar():
    return nifty().loc['2017-01-01':'2026-10-31'].copy()


def windows():
    # Three calendar days per call, including intervening weekends and holidays.
    n = calendar()
    start = pd.Timestamp(n.iloc[0]['prev_day'])
    end = pd.Timestamp('2026-10-31')
    while start <= end:
        stop = min(start + pd.Timedelta(days=2), end)
        yield start.strftime('%Y-%m-%d'), stop.strftime('%Y-%m-%d')
        start = stop + pd.Timedelta(days=1)


def announcement_path(start, end):
    return OUT / 'announcements' / f'{start}_{end}.json'


def fetch_ann(c, start, end):
    dest = announcement_path(start, end)
    if dest.exists():
        return json.loads(dest.read_text(encoding='utf-8'))['meta']
    params = {'index': 'equities', 'from_date': pd.Timestamp(start).strftime('%d-%m-%Y'),
              'to_date': pd.Timestamp(end).strftime('%d-%m-%Y')}
    meta = {'start': start, 'end': end, 'url': ANN, 'params': params, 'status': 'unknown'}
    for attempt in range(3):
        try:
            r = c.get(ANN, params=params)
            raw = dest.with_suffix('.raw')
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(r.content)
            meta.update(http_status=r.status_code, sha256=hashlib.sha256(r.content).hexdigest(),
                        retrieved_utc=datetime.now().astimezone().isoformat())
            r.raise_for_status()
            rows = r.json()
            if not isinstance(rows, list) or any(not isinstance(x, dict) for x in rows):
                raise ValueError('expected a list of announcement objects')
            # Validate range and timestamps in analysis too; retain raw objects unaltered.
            meta.update(status='ok', rows=len(rows))
            dump(dest, {'meta': meta, 'rows': rows})
            return meta
        except (httpx.HTTPError, ValueError) as e:
            meta['error'] = f'{type(e).__name__}: {e}'
            time.sleep(1.0 * (attempt + 1))
    dump(dest.with_suffix('.error.json'), meta)
    return meta


def bhav_path(day):
    shared = Path(BASE_DIR) / 'data' / 'bhav' / f'{day}.pkl'
    return shared if shared.exists() else OUT / 'bhav' / f'{day}.pkl'


def fetch_bhav(c, day):
    path = bhav_path(day)
    result = {'day': day, 'path': str(path.relative_to(BASE_DIR))}
    try:
        frame = pd.read_pickle(path) if path.exists() else pb.bhav(day, c)
        if frame.empty:
            raise ValueError('empty/missing bhavcopy, not a zero-trade session')
        if not {'open', 'high', 'low', 'close', 'prev_close', 'value'} <= set(frame.columns):
            raise ValueError('unexpected bhavcopy schema')
        if frame.index.has_duplicates:
            raise ValueError('duplicate EQ symbols')
        result.update(status='ok', rows=len(frame), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    except Exception as e:
        result.update(status='unknown', error=f'{type(e).__name__}: {e}')
    return result


def fetch_etf(c):
    path = OUT / 'etf.csv'
    if not path.exists():
        r = c.get(ETF)
        r.raise_for_status()
        path.write_bytes(r.content)
    df = pd.read_csv(path)
    if not {'Symbol', 'SecurityName', 'ISINNumber'} <= set(df.columns):
        raise ValueError('invalid ETF inventory')
    dump(OUT / 'etf_source.json', {'url': ETF, 'rows': len(df),
                                 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                 'limitation': 'current inventory; historical/delisted ETFs also screened by symbol and announcement metadata'})
    return len(df)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['all', 'announcements', 'bhav'], default='all')
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'bhav').mkdir(exist_ok=True)
    pb.CACHE = str(OUT / 'bhav')  # Existing helper; new files stay in this study directory.
    n = calendar()
    n.to_csv(OUT / 'index_calendar.csv')
    c = PacedNSE()
    try:
        print(f'ETF inventory: {fetch_etf(c)} securities', flush=True)
        if args.kind in ('all', 'announcements'):
            tasks = list(windows())
            out = []
            with ThreadPoolExecutor(max_workers=3) as workers:
                pending = {workers.submit(fetch_ann, c, a, b): (a, b) for a, b in tasks}
                for future in as_completed(pending):
                    out.append(future.result())
                    if len(out) % 40 == 0 or out[-1]['status'] != 'ok':
                        dump(OUT / 'ann_coverage.json', sorted(out, key=lambda x: x['start']))
                        print(f'Announcements {len(out)}/{len(tasks)}: {out[-1]}', flush=True)
            dump(OUT / 'ann_coverage.json', sorted(out, key=lambda x: x['start']))
        if args.kind in ('all', 'bhav'):
            days = sorted(set(n.index) | set(n['prev_day']))
            out = []
            with ThreadPoolExecutor(max_workers=3) as workers:
                pending = {workers.submit(fetch_bhav, c, day): day for day in days}
                for future in as_completed(pending):
                    out.append(future.result())
                    if len(out) % 80 == 0 or out[-1]['status'] != 'ok':
                        dump(OUT / 'bhav_coverage.json', sorted(out, key=lambda x: x['day']))
                        print(f'Bhavcopy {len(out)}/{len(days)}: {out[-1]["day"]} {out[-1]["status"]}', flush=True)
            dump(OUT / 'bhav_coverage.json', sorted(out, key=lambda x: x['day']))
    finally:
        c.close()
    print('Input fetch finished; no stock returns calculated.', flush=True)


if __name__ == '__main__':
    if sys.platform == 'win32':
        sys.stdout.reconfigure(encoding='utf-8')
    main()
