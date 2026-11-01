"""Consensus desk (owner adopted 2026-11-01): every trading morning, Claude Opus and GPT-6 Astra each pick blind from the same
09:30 packet; only trades BOTH choose (same stock, same side) are paper-traded, at most 2; Drex ranks them but never vetoes.
Paper only: no orders, no Kite order tools.

    python scripts/consensus_desk.py morning [--day YYYY-MM-DD]   # after 09:30: packet -> Opus + Astra (blind) -> agreement -> Drex rank -> paper entries
    python scripts/consensus_desk.py settle  [--day YYYY-MM-DD]   # after 15:15: 09:35 entry, 3% stop, out at 15:10, realistic costs -> journal
Pool: the 6 biggest gap-downs and 6 biggest gap-ups (>= 1%) among the 10-year evidence's liquid universe (>= Rs10 crore traded the
previous session, price >= Rs50, not NIFTY 50). Gaps: NSE bhavcopy for past days, the pre-open auction (iep) for today. Candles: Kite 5-minute
(read-only historical). Journal: data/consensus_paper.jsonl (same shape as the panic journals, so it shows on Sessions).
Each model sees only the packet (anonymous Stock A.., no date, no names): Claude via the Claude CLI in an empty folder with no tools,
Astra via codex exec read-only in an empty folder. Both raw answers are saved before agreement is computed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import string
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT/'.env')
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from stalk.config import IST  # noqa: E402

JOURNAL = ROOT/'data/consensus_paper.jsonl'
WORK = ROOT/'data/consensus'
RT = {'nifty50': 0.0015, 'next50': 0.0015, 'midcap': 0.0025, 'smallcap': 0.0040}
SLOT, STOP, MAX_TRADES = 5000.0, 0.03, 2
RESEARCH = ("Research facts: panic buy (on down mornings, buy the biggest no-news gap-downs) works only at the 09:15 auction - by 09:20 the "
            "average edge is gone; intraday sell-offs tend to continue; over-extended run-ups tend to lag intraday; gap-up fades have no edge "
            "after 09:20 in liquid stocks; NIFTY's intraday drift is slightly negative; skipping weak setups has beaten forcing trades; a stock "
            "priced above Rs5,000 cannot be bought with one slot; a locked (open=high=low) stock is untradeable.")


def write(kind, day, **data):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    with JOURNAL.open('a', encoding='utf-8') as f:
        f.write(json.dumps(dict(at=datetime.now(IST).isoformat(timespec='seconds'), day=day, kind=kind, **data), default=str)+'\n')


def prev_session(day, client=None):
    from scripts.panic_bhav import bhav
    d = datetime.strptime(day, '%Y-%m-%d')
    for k in range(1, 8):
        p = (d-timedelta(days=k)).strftime('%Y-%m-%d')
        try:
            df = bhav(p, client) if client else pd.read_pickle(ROOT/f'data/bhav/{p}.pkl')
        except Exception:
            continue
        if df is not None and not df.empty:
            return p, df
    raise ValueError('No previous session bhavcopy found')


async def kite_bars(kite, symbol, day, interval='5minute', start=None):
    rows = await kite.historical(symbol, interval, f"{start or day} 09:00:00", f"{day} 15:35:00")
    f = pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
    if f.empty:
        return f
    t = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
    return f.assign(day=t.dt.strftime('%Y-%m-%d'), hm=t.dt.hour*100+t.dt.minute)


def ask_claude(prompt):
    exe = shutil.which('claude') or shutil.which('claude.exe')
    if not exe:
        return None, 'Claude CLI not found'
    with tempfile.TemporaryDirectory(prefix='stalk_consensus_claude_') as cwd:
        p = subprocess.run([exe, '--model', 'opus', '--tools', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
                            '--no-session-persistence', '--print', '--output-format', 'json'],
                           input=prompt, cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=300)
    if p.returncode:
        return None, f'Claude exit {p.returncode}: {p.stderr[:200]}'
    raw = json.loads(p.stdout)
    return parse(raw.get('structured_output') or raw.get('result')), ','.join((raw.get('modelUsage') or {}).keys())


def ask_astra(prompt):
    exe = shutil.which('codex') or shutil.which('codex.cmd')
    if not exe:
        return None, 'Codex CLI not found'
    with tempfile.TemporaryDirectory(prefix='stalk_consensus_astra_') as cwd:
        out = Path(cwd)/'reply.txt'
        p = subprocess.run([exe, 'exec', '-m', 'gpt-6-astra', '-c', 'model_reasoning_effort=high', '-s', 'read-only', '--skip-git-repo-check',
                            '-C', cwd, '-o', str(out), '-'], input=prompt, capture_output=True, text=True, encoding='utf-8', timeout=600)
        if p.returncode or not out.exists():
            return None, f'Codex exit {p.returncode}: {p.stderr[-200:]}'
        return parse(out.read_text(encoding='utf-8')), 'gpt-6-astra'


def parse(text):
    if isinstance(text, dict):
        return text
    s = (text or '').strip()
    if s.startswith('```'):
        s = '\n'.join(s.splitlines()[1:-1])
    return json.loads(s[s.find('{'):s.rfind('}')+1])


async def drex_rank(packet, agreed):
    from stalk.llm.drex import DrexClient
    qs = {f"q{n}": {"type": "noul", "instructions": f"A {v['side']} trade in {s}, entered at the 09:35 open with a 3% stop and closed at 15:10, "
                    "ends in profit after realistic costs.", "criteria": {"true": "Profitable after costs", "false": "Loss or flat"}}
          for n, (s, v) in enumerate(agreed.items())}
    a = await DrexClient(timeout_s=60).ask({"task": "Rank intraday trades in anonymous Indian stocks from facts known at 09:30.",
                                            "market": packet['market'], "stocks": {s: packet['stocks'][s] for s in agreed}}, qs)
    return {s: (a.noul(f"q{n}") if a.ok else None) for n, s in enumerate(agreed)}


async def build_packet(day, live):
    from stalk.data.kite_mcp import KiteMCP
    from stalk.research.orb import universe
    from stalk.sim.panic_live import nse_client, preopen
    tiers = universe()
    nse = nse_client() if live else None
    prev, pdf = prev_session(day, nse)
    from scripts.panic_bhav import MIN_VALUE
    from stalk.config import DEFAULT_UNIVERSE
    liquid = set(pdf.index[(pdf['value'] >= MIN_VALUE) & (pdf['close'] >= 50) & ~pdf.index.isin(DEFAULT_UNIVERSE)])
    if live:
        gaps = {s: op/pc-1 for s, (pc, op) in preopen(nse).items() if s in liquid}
    else:
        from scripts.panic_bhav import bhav
        today = bhav(day, None)
        gaps = {s: r['open']/r['prev_close']-1 for s, r in today.iterrows() if s in liquid and r['prev_close'] > 0}
    g = pd.Series(gaps).dropna()
    pool = list(g[g <= -0.01].sort_values().index[:6])+list(g[g >= 0.01].sort_values(ascending=False).index[:6])
    kite = KiteMCP()
    if not await kite.check():
        raise SystemExit('Kite login required (read-only candles)')
    rng = np.random.default_rng()
    pool = [pool[j] for j in rng.permutation(len(pool))]
    stocks, names = {}, {}
    start = (datetime.strptime(day, '%Y-%m-%d')-timedelta(days=40)).strftime('%Y-%m-%d')
    for n, s in enumerate(pool):
        b = await kite_bars(kite, s, day)
        d = await kite_bars(kite, s, prev, interval='day', start=start)
        t = b[b.day == day] if len(b) else b
        if len(t) < 3 or len(d) < 21 or t.hm.iloc[0] != 915:
            continue
        first = t[t.hm <= 925]
        o0, pc = float(first.o.iloc[0]), float(d.c.iloc[-1])
        tag = 'Stock '+string.ascii_uppercase[len(names)]
        names[tag] = s
        stocks[tag] = dict(size=tiers.get(s, 'smallcap'), price_band=f"Rs{int(pc//50*50)}-{int(pc//50*50+50)}", gap_at_open_pct=round((o0/pc-1)*100, 2),
                           first_15min=dict(close_0930_vs_open_pct=round((first.c.iloc[-1]/o0-1)*100, 2), high_vs_open_pct=round((first.h.max()/o0-1)*100, 2),
                                            low_vs_open_pct=round((first.l.min()/o0-1)*100, 2)),
                           locked_first_bar=bool(first.h.iloc[0] == first.l.iloc[0]),
                           prior_5d_return_pct=round((d.c.iloc[-1]/d.c.iloc[-6]-1)*100, 2), prior_20d_return_pct=round((d.c.iloc[-1]/d.c.iloc[-21]-1)*100, 2),
                           avg_daily_range_20d_pct=round(float(((d.h-d.l)/d.c).tail(20).mean()*100), 2))
    market = dict(weekday=datetime.strptime(day, '%Y-%m-%d').strftime('%A'), median_gap_of_liquid_stocks_pct=round(float(g.median()*100), 2),
                  stocks_in_universe=int(len(g)))
    return dict(market=market, stocks=stocks, rules="Enter at the 09:35 open, 3% stop, no target, out at 15:10; Rs5,000 a stock; at most 2 trades; "
                "realistic costs 0.15% large / 0.25% mid / 0.40% small-cap round trip."), names


def prompt_for(packet):
    keys = list(packet['stocks'])
    return (f"You are one of two independent traders on a paper desk. Decide BLIND, using only this 09:30 packet (anonymous stocks, no date):\n"
            f"{json.dumps(packet, indent=1)}\n{RESEARCH}\nFor EACH of {', '.join(keys)} give side \"long\", \"short\" or \"skip\", a confidence "
            f"0-100 and one short reason. Reply ONLY with a JSON object keyed by stock: {{\"{keys[0]}\": {{\"side\": \"...\", \"confidence\": 0, \"why\": \"...\"}}, ...}} "
            "covering every stock listed.")


def morning(day, live):
    if any(json.loads(ln).get('day') == day and json.loads(ln).get('kind') in ('morning', 'skip') for ln in JOURNAL.read_text(encoding='utf-8').splitlines()
           if ln.strip()) if JOURNAL.exists() else False:
        print('Morning already done for', day)
        return
    packet, names = asyncio.run(build_packet(day, live))
    d = WORK/day
    d.mkdir(parents=True, exist_ok=True)
    (d/'packet.json').write_text(json.dumps(packet, indent=1), encoding='utf-8')
    (d/'names.json').write_text(json.dumps(names, indent=1), encoding='utf-8')
    if not packet['stocks']:
        write('skip', day, why='no movers with candles')
        return
    prompt = prompt_for(packet)
    c, cm = ask_claude(prompt)
    a, am = ask_astra(prompt)
    (d/'claude.json').write_text(json.dumps(dict(model=cm, plan=c), indent=1), encoding='utf-8')
    (d/'astra.json').write_text(json.dumps(dict(model=am, plan=a), indent=1), encoding='utf-8')
    if not c or not a:
        write('skip', day, why=f'a model was unavailable (claude: {cm}; astra: {am}); no consensus, no trade')
        return
    agreed = {s: dict(side=c[s]['side'], claude=c[s].get('confidence'), astra=a[s].get('confidence'))
              for s in packet['stocks'] if c.get(s, {}).get('side') in ('long', 'short') and c[s]['side'] == a.get(s, {}).get('side')}
    p = asyncio.run(drex_rank(packet, agreed)) if agreed else {}
    for s in agreed:
        agreed[s]['drex'] = p.get(s)
    ranked = sorted(agreed.items(), key=lambda kv: (-(kv[1]['drex'] or 0), -((kv[1]['claude'] or 0)+(kv[1]['astra'] or 0))))[:MAX_TRADES]
    picks = [dict(stock=t, symbol=names[t], tier=packet['stocks'][t]['size'], **v) for t, v in ranked]
    write('morning', day, pool=len(packet['stocks']), agreed=len(agreed), picks=picks,
          claude_trades=sum(v.get('side') in ('long', 'short') for v in c.values()), astra_trades=sum(v.get('side') in ('long', 'short') for v in a.values()))
    if not picks:
        write('closed_all', day, net=0.0, note='no stock both models agreed on: no trade')
    print(json.dumps(dict(day=day, agreed=agreed, picks=picks), indent=1))


def settle(day):
    from stalk.data.kite_mcp import KiteMCP
    rows = [json.loads(ln) for ln in JOURNAL.read_text(encoding='utf-8').splitlines() if ln.strip()] if JOURNAL.exists() else []
    m = [r for r in rows if r['day'] == day and r['kind'] == 'morning']
    if not m or any(r['day'] == day and r['kind'] == 'closed_all' for r in rows):
        print('Nothing to settle for', day)
        return

    async def run():
        kite = KiteMCP()
        if not await kite.check():
            raise SystemExit('Kite login required (read-only candles)')
        total = 0.0
        for p in m[-1]['picks']:
            b = await kite_bars(kite, p['symbol'], day)
            b = b[b.day == day].set_index('hm')
            if 935 not in b.index or 1510 not in b.index:
                write('candidate_skip', day, symbol=p['symbol'], why='candles missing at 09:35 or 15:10')
                continue
            sg = 1 if p['side'] == 'long' else -1
            e = float(b.at[935, 'o'])
            stop = e*(1-sg*STOP)
            px, why = float(b.at[1510, 'o']), 'close'
            for hm, r in b.loc[935:1505].iterrows():
                if (r.o <= stop) if sg > 0 else (r.o >= stop):
                    px, why = float(r.o), 'stop'
                    break
                if (r.l <= stop) if sg > 0 else (r.h >= stop):
                    px, why = stop, 'stop'
                    break
            qty = int(SLOT//e)
            gross = sg*(px-e)*qty
            costs = qty*e*RT.get(p['tier'], 0.004)
            t = dict(day=day, symbol=p['symbol'], side=p['side'], qty=qty, entry=round(e, 2), exit=round(px, 2), reason=why,
                     gross=round(gross, 2), costs=round(costs, 2), net=round(gross-costs, 2))
            total += t['net']
            write('exit', day, trade=t)
        write('closed_all', day, net=round(total, 2))
        print('settled', day, round(total, 2))
    asyncio.run(run())


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('step', choices=['morning', 'settle'])
    ap.add_argument('--day', default=None)
    a = ap.parse_args()
    today = datetime.now(IST).strftime('%Y-%m-%d')
    day = a.day or today
    morning(day, live=(day == today)) if a.step == 'morning' else settle(day)
