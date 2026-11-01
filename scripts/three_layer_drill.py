"""Three-layer blind drill (owner's workflow, 2026-11-01): Claude and Astra pick independently, Drex scores the stocks they agree
on, both re-decide with Drex's numbers, and only a trade both still want is taken. Played on a sealed random past day.

    python scripts/three_layer_drill.py make                # random day (secret), 12 anonymous movers, visible packet only
    python scripts/three_layer_drill.py match <run>          # after l1_claude.json + l1_astra.json: agreed list -> Drex
    python scripts/three_layer_drill.py score <run>          # after l3_claude.json + l3_astra.json: plays the sealed day once
Day: drawn uniformly (secret seed, no redraw) from complete design-period sessions 2021-02..2025-07 (the sealed year is never used).
Pool: the 6 biggest gap-downs and 6 biggest gap-ups (>= 1%) among eligible liquid stocks, shown as Stock A..L with no names or date.
Visible: everything known by the 09:30 close. Trade: enter the 09:35 open, 3% stop, no target, out at the 15:10 open; realistic
costs by size (0.15/0.25/0.40% round trip); Rs5,000 a stock, at most 2 trades.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import string
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT/'.env')
import numpy as np  # noqa: E402

from scripts import h30_capitulation as h  # noqa: E402
from scripts import h32_lab as h32  # noqa: E402
from stalk.config import IST  # noqa: E402

OUT = ROOT/'data/drills'
IX = {hm: n for n, hm in enumerate(h.GRID)}
RT = {'nifty50': 0.0015, 'next50': 0.0015, 'midcap': 0.0025, 'smallcap': 0.0040}


def make():
    B = h32.load()
    cal = B['calendar']
    rng = np.random.default_rng(secrets.randbits(64))
    days = [i for i, d in enumerate(cal) if '2021-02-01' <= d <= '2025-07-31' and B['eligible'][i].sum() >= 100]
    i = int(days[rng.integers(len(days))])
    o, hi, lo = (np.asarray(B[k][i], float) for k in 'ohl')
    c, v = np.asarray(B['cf'][i], float), np.asarray(B['vf'][i], float)
    pc = B['pc'][i]
    elig = B['eligible'][i] & np.isfinite(pc) & np.isfinite(o[IX[935]]) & np.isfinite(o[IX[1510]])
    gap = o[0]/pc-1
    H = np.nanmax(np.asarray(B['h'][i-20:i], float), axis=1)
    L = np.nanmin(np.asarray(B['l'][i-20:i], float), axis=1)
    C = np.asarray(B['cf'][i-21:i, IX[1525]], float)
    downs = [k for k in np.argsort(np.where(elig, gap, np.nan)) if elig[k] and gap[k] <= -0.01][:6]
    ups = [k for k in np.argsort(np.where(elig, -gap, np.nan)) if elig[k] and gap[k] >= 0.01][:6]
    pool = list(downs)+list(ups)
    order = rng.permutation(len(pool))
    pool = [pool[j] for j in order]
    vol15 = np.nansum(v[:3], axis=0)
    vol15_avg = np.nanmean(np.nansum(np.asarray(B['vf'][i-14:i, :3], float), axis=1), axis=0)
    mkt_gap = float(np.nanmedian(gap[elig]))
    mkt_0930 = float(np.nanmedian((c[IX[925]]/pc-1)[elig]))
    visible, sealed, names = {}, {}, {}
    for n, k in enumerate(pool):
        tag = 'Stock '+string.ascii_uppercase[n]
        names[tag] = B['symbols'][k]
        visible[tag] = dict(
            size=B['tiers'][k], sector=str(B['sector'][k]), price_band=f"Rs{int(pc[k]//50*50)}-{int(pc[k]//50*50+50)}",
            gap_at_open_pct=round(float(gap[k]*100), 2),
            first_15min=dict(open_vs_prev_close_pct=round(float(gap[k]*100), 2), close_0930_vs_open_pct=round(float((c[IX[925], k]/o[0, k]-1)*100), 2),
                             high_vs_open_pct=round(float((np.nanmax(hi[:3, k])/o[0, k]-1)*100), 2), low_vs_open_pct=round(float((np.nanmin(lo[:3, k])/o[0, k]-1)*100), 2),
                             volume_vs_14day_avg=round(float(vol15[k]/vol15_avg[k]), 2) if vol15_avg[k] > 0 else None),
            prior_5d_return_pct=round(float((C[-1, k]/C[-6, k]-1)*100), 2), prior_20d_return_pct=round(float((C[-1, k]/C[0, k]-1)*100), 2),
            avg_daily_range_20d_pct=round(float(np.nanmean((H[:, k]-L[:, k])/C[1:, k])*100), 2))
        sealed[tag] = dict(tier=B['tiers'][k], bars=[[hm, float(o[IX[hm], k]), float(hi[IX[hm], k]), float(lo[IX[hm], k])]
                                                      for hm in h.GRID if 935 <= hm <= 1510])
    run = datetime.now(IST).strftime('%Y%m%d_%H%M%S')
    d = OUT/run
    d.mkdir(parents=True, exist_ok=True)
    market = dict(weekday=datetime.strptime(cal[i], '%Y-%m-%d').strftime('%A'), median_gap_of_liquid_stocks_pct=round(mkt_gap*100, 2),
                  median_move_by_0930_pct=round(mkt_0930*100, 2), stocks_in_universe=int(elig.sum()))
    packet = dict(market=market, stocks=visible, rules="Enter at the 09:35 open, 3% stop, no target, out at 15:10; Rs5,000 a stock; "
                  "at most 2 trades; realistic costs 0.15% large / 0.25% mid / 0.40% small-cap round trip.")
    blob = json.dumps(dict(day=cal[i], names=names, sealed=sealed), sort_keys=True).encode()
    (d/'sealed.json').write_bytes(blob)
    (d/'visible.json').write_text(json.dumps(packet, indent=1), encoding='utf-8')
    print(json.dumps(dict(run=run, sealed_sha256=hashlib.sha256(blob).hexdigest(), visible=packet), indent=1))


async def drex(packet, matched):
    from stalk.llm.drex import DrexClient
    qs = {f"q{n}": {"type": "noul", "instructions": f"A {m['side']} trade in {s}, entered at the 09:35 open with a 3% stop and closed at 15:10, "
                    "ends in profit after realistic costs.", "criteria": {"true": "Profitable after costs", "false": "Loss or flat"}}
          for n, (s, m) in enumerate(matched.items())}
    state = {"task": "Judge intraday trades in anonymous Indian stocks from facts known at 09:30.", "market": packet['market'],
             "stocks": {s: packet['stocks'][s] for s in matched}, "costs": packet['rules']}
    a = await DrexClient(timeout_s=60).ask(state, qs)
    return {s: (a.noul(f"q{n}") if a.ok else None) for n, s in enumerate(matched)}


def match(run):
    d = OUT/run
    packet = json.loads((d/'visible.json').read_text(encoding='utf-8'))
    c, a = (json.loads((d/f).read_text(encoding='utf-8')) for f in ('l1_claude.json', 'l1_astra.json'))
    both = {s: dict(side=c[s]['side'], claude=c[s]['confidence'], astra=a[s]['confidence'])
            for s in packet['stocks'] if c.get(s, {}).get('side') in ('long', 'short') and c[s]['side'] == a.get(s, {}).get('side')}
    top = dict(sorted(both.items(), key=lambda kv: -(kv[1]['claude']+kv[1]['astra']))[:10])
    p = asyncio.run(drex(packet, top)) if top else {}
    for s in top:
        top[s]['drex_p_profit'] = p.get(s)
    (d/'l2_matched_drex.json').write_text(json.dumps(top, indent=1), encoding='utf-8')
    print(json.dumps(top, indent=1))


def walk(bars, side, tier):
    e = bars[0][1]
    sg = 1 if side == 'long' else -1
    stop = e*(1-sg*0.03)
    px, why = bars[-1][1], '15:10'
    for hm, o, hh, ll in bars[:-1]:
        if (o <= stop) if sg > 0 else (o >= stop):
            px, why = o, 'stop (gap)'
            break
        if (ll <= stop) if sg > 0 else (hh >= stop):
            px, why = stop, 'stop'
            break
    qty = int(5000//e)
    gross = sg*(px-e)*qty
    return dict(entry=round(e, 2), exit=round(px, 2), how=why, qty=qty, net=round(gross-qty*e*RT.get(tier, 0.004), 2))


def score(run):
    d = OUT/run
    guard = d/'scored.json'
    with guard.open('x', encoding='utf-8') as fh:
        json.dump(dict(at=datetime.now(IST).isoformat()), fh)
    s = json.loads((d/'sealed.json').read_text())
    l1c, l1a = (json.loads((d/f).read_text(encoding='utf-8')) for f in ('l1_claude.json', 'l1_astra.json'))
    l2 = json.loads((d/'l2_matched_drex.json').read_text(encoding='utf-8'))
    l3c, l3a = (json.loads((d/f).read_text(encoding='utf-8')) for f in ('l3_claude.json', 'l3_astra.json'))

    def book(picks):
        return {t: dict(side=side, **walk(s['sealed'][t]['bars'], side, s['sealed'][t]['tier'])) for t, side in picks}

    def top2(l1):
        x = [(t, v['side'], v['confidence']) for t, v in l1.items() if v.get('side') in ('long', 'short')]
        return [(t, sd) for t, sd, _ in sorted(x, key=lambda z: -z[2])[:2]]
    final = [(t, l2[t]['side']) for t in l2 if l3c.get(t, {}).get('take') and l3a.get(t, {}).get('take')]
    final = sorted(final, key=lambda ts: -(l3c[ts[0]].get('confidence', 0)+l3a[ts[0]].get('confidence', 0)))[:2]
    drex_top = sorted([(t, v['side'], v.get('drex_p_profit') or 0) for t, v in l2.items()], key=lambda z: -z[2])[:2]
    agreed = sorted([(t, v['side'], v['claude']+v['astra']) for t, v in l2.items()], key=lambda z: -z[2])[:2]
    res = {"Consensus (3 layers)": book(final), "Agreed in layer 1, no Drex veto": book([(t, sd) for t, sd, _ in agreed]),
           "Claude alone (top 2)": book(top2(l1c)), "Astra alone (top 2)": book(top2(l1a)),
           "Drex alone (top 2 of the agreed list)": book([(t, sd) for t, sd, _ in drex_top])}
    every = {t: {sd: walk(v['bars'], sd, v['tier'])['net'] for sd in ('long', 'short')} for t, v in s['sealed'].items()}
    out = dict(day=s['day'], names=s['names'], results={k: dict(trades=v, net=round(sum(x['net'] for x in v.values()), 2)) for k, v in res.items()},
               every_stock_both_sides=every)
    (d/'result.json').write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    cmd = sys.argv[1]
    make() if cmd == 'make' else match(sys.argv[2]) if cmd == 'match' else score(sys.argv[2])
