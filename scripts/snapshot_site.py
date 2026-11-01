"""Read-only website copy of the dashboard (for Netlify): saves the running app's answers as JSON files and builds dist/.

    python run.py                          # the app must be running on http://localhost:8080
    python scripts/snapshot_site.py        # -> snapshot/api/... (committed) and dist/ (what Netlify serves)
Away from localhost, web/lib.js reads these files instead of the API and refuses every action. Candle data (Kite) and personal
fields (the Kite user name) are never saved.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://localhost:8080'
OUT = ROOT/'snapshot'
PRIVATE = {'user', 'user_name', 'email', 'user_id', 'kite_user'}
SIMPLE = ['/api/state', '/api/sessions', '/api/campaigns/latest', '/api/replay/days', '/api/days?account=live:desk', '/api/panic',
          '/api/strategies', '/api/journal', '/api/market', '/api/campaigns/h17_panic_10y_strict', '/api/drex/context', '/api/research',
          '/api/brief', '/api/reviews', '/api/execution', '/api/orb', '/api/horizons', '/api/sim']


def file_for(path):
    """Same mapping as staticPath() in web/lib.js."""
    p, _, q = path.partition('?')
    return OUT/(p.lstrip('/')+('__'+re.sub(r'[^A-Za-z0-9_.-]', '_', q) if q else '')+'.json')


def scrub(x):
    if isinstance(x, dict):
        return {k: (None if k in PRIVATE else False if k in ('logged_in', 'kite_logged_in', 'kite_can_trade') else scrub(v)) for k, v in x.items()}
    if isinstance(x, list):
        return [scrub(v) for v in x]
    return x


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    saved, missing = 0, []
    with httpx.Client(base_url=BASE, timeout=60) as c:
        def get(path):
            nonlocal saved
            r = c.get(path)
            if r.status_code != 200:
                missing.append(f'{path} ({r.status_code})')
                return None
            data = scrub(r.json())
            f = file_for(path)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(data, separators=(',', ':')), encoding='utf-8')
            saved += 1
            return data
        for p in SIMPLE:
            get(p)
        for s in get('/api/sessions') or []:
            camp = get(f"/api/campaigns/{quote(s['id'])}")
            for acc in ((camp or {}).get('summary') or {}).get('accounts') or {}:
                get(f"/api/campaigns/{quote(camp['id'])}/trades?account={acc}")
    dist = ROOT/'dist'
    shutil.rmtree(dist, ignore_errors=True)
    shutil.copytree(ROOT/'web', dist/'web')
    shutil.copy(ROOT/'web/index.html', dist/'index.html')
    shutil.copytree(OUT, dist/'snapshot')
    size = sum(f.stat().st_size for f in OUT.rglob('*.json'))
    print(f'saved {saved} answers ({size/1e6:.1f} MB); not available: {len(missing)}', *missing[:10], sep='\n')


if __name__ == '__main__':
    main()
