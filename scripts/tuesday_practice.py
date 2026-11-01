"""Paper-only blind practice. No broker API, credentials or live-strategy mutation.

Prepare once, commit actual decisions, review, score once, then publish saved results.
Claude is invoked only by the explicit `claude PACKET` command, in an isolated folder.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from stalk.config import IST
from scripts.publish_run import publish, record_cycle

OUT = ROOT / 'data/synthetic/tuesday_20261101'
IDS = ('tuesday_solo_1', 'tuesday_solo_2', 'tuesday_compare_1', 'tuesday_compare_2')
DAY = '2026-11-03'  # Display clock only; not a forecast for this date.
COLS = ['day', 'symbol', 'side', 'hm', 'entry_hm', 'exit_hm', 'qty', 'entry', 'exit',
        'reason', 'gross', 'costs', 'net', 'net_pct']


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write(path, obj):
    with path.open('x', encoding='utf-8') as f:
        json.dump(obj, f, indent=2, allow_nan=False, ensure_ascii=False)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stamp():
    return datetime.now(IST).isoformat()


def folder(packet):
    if packet not in IDS:
        raise ValueError('Unknown practice packet')
    return OUT / packet


def check_manifest(p, hidden=False):
    m = read(p / 'manifest.json')
    checks = {'visible.json': p / 'visible.json', 'scorer.py': Path(__file__)}
    if 'claude_prompt.txt' in m:
        checks['claude_prompt.txt'] = p / 'claude_prompt.txt'
    if hidden:
        checks['sealed.json'] = p / 'sealed.json'
    for name, path in checks.items():
        if sha(path) != m[name]:
            raise ValueError(f'Frozen artifact changed: {name}')


def inventory(symbol):
    raw = pd.read_pickle(ROOT / f'data/kite_5m/{symbol}.pkl')
    dt = pd.to_datetime(raw.ts, unit='s', utc=True).dt.tz_convert(IST)
    f = raw.loc[(dt >= '2024-01-01') & (dt < '2025-08-01')].copy()
    del raw
    dt = pd.to_datetime(f.ts, unit='s', utc=True).dt.tz_convert(IST)
    f['day'], f['hm'] = dt.dt.strftime('%Y-%m-%d'), dt.dt.hour * 100 + dt.dt.minute
    required = [h*100+m for h in range(9,16) for m in range(0,60,5) if 915 <= h*100+m <= 1525]
    groups = {}
    for day, g in f.groupby('day'):
        g = g.sort_values('hm')
        values = g[['o','h','l','c']].to_numpy().flatten()
        if (g.hm.tolist() == required and all(math.isfinite(float(x)) and x > 0 for x in values)
                and (g.h >= g[['o','c']].max(axis=1)).all()
                and (g.l <= g[['o','c']].min(axis=1)).all() and (g.h >= g.l).all()):
            groups[day] = g
    return groups


def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    rng = secrets.SystemRandom()
    symbols = [rng.choice(['SBIN','HDFCBANK','ICICIBANK','AXISBANK']), rng.choice(['IRCTC','IRFC','RVNL'])]
    write(OUT / 'draw.json', {'at': stamp(), 'symbols': symbols, 'rule': 'uniform bank + railway; no redraw'})
    frames = {s: inventory(s) for s in symbols}
    eligible = {s: {d for d in sorted(g)[20:] if datetime.fromisoformat(d).weekday() == 1} for s,g in frames.items()}
    common = sorted(set.intersection(*eligible.values()))
    if len(common) < 4:
        raise ValueError('Insufficient common Tuesdays; no replacement draw')
    selected = rng.sample(common, 4)
    meta = pd.read_csv(ROOT / 'data/wide_meta.csv').set_index('symbol')
    for packet, day in zip(IDS, selected):
        p = folder(packet)
        p.mkdir()
        visible = {'packet': packet, 'kind': 'solo' if 'solo' in packet else 'comparison',
                   'clock': 'Synthetic Tuesday, decision after 09:30 candle closes at 09:35 IST',
                   'objective': 'Minimize losses; fewer trades and missed winners acceptable. No required trade.',
                   'execution': {'entry': '09:35 next open', 'exit': '15:10 open', 'stop_pct': .01,
                                 'max_notional': 5000, 'planned_loss_including_costs': 50,
                                 'gap_warning': 'Stop gaps can exceed Rs50', 'stress_entry_worse_pct': .005},
                   'evidence_limits': ['Historical practice, not a forecast or fresh validation.',
                                       'Market panic, universe ranking, news and events are not supplied.',
                                       'No validated directional edge for this 09:35 entry is supplied.',
                                       'Prior movement is not a forecast of direction.'], 'stocks': {}}
        hidden = {'source_day': day, 'stocks': {}}
        for s in symbols:
            g = frames[s][day]
            days = sorted(frames[s]); idx = days.index(day)
            prior = [frames[s][d] for d in days[idx-20:idx]]
            tier = str(meta.loc[s, 'index'])
            rate = .0015 if tier in ('nifty50','next50') else .0025 if tier == 'midcap' else .004
            bars = [[int(r.hm), *[float(getattr(r,k)) for k in ('o','h','l','c')]] for r in g.itertuples() if r.hm <= 1510]
            visible['stocks'][s] = {'previous_close': float(prior[-1].c.iloc[-1]),
                'five_session_change': float(prior[-1].c.iloc[-1]/prior[-6].c.iloc[-1]-1),
                'prior20_mean_abs_open_close': sum(abs(x.c.iloc[-1]/x.o.iloc[0]-1) for x in prior)/20,
                'prior20_mean_range': sum((x.h.max()-x.l.min())/x.o.iloc[0] for x in prior)/20,
                'tier': tier, 'round_trip_cost_rate': rate, 'completed_candles': bars[:4]}
            hidden['stocks'][s] = bars
        write(p/'visible.json', visible)
        write(p/'sealed.json', hidden)
        prompt = ('You are independently deciding a paper-only intraday practice plan. Use ONLY the packet below. '
                  'Do not inspect files, use tools, recall source dates, or infer another model\'s decisions. '
                  'For EACH symbol choose long, short or skip. State observed facts, reason, missing evidence, '
                  'invalidation (what would invalidate a trade, or what evidence a skip lacks), and sufficient_evidence boolean. '
                  'Do not manufacture a directional edge. Costs and risk limits apply symmetrically. '
                  'Return ONLY JSON: {"stocks": {"SYMBOL": {"side":"skip", "sufficient_evidence":false, '
                  '"observations":["fact"], "reason":"reason", "missing_evidence":["missing"], '
                  '"invalidation":"condition"}}}. Packet:\n'+json.dumps(visible, indent=2))
        (p/'claude_prompt.txt').write_text(prompt, encoding='utf-8')
        write(p/'manifest.json', {'visible.json': sha(p/'visible.json'), 'sealed.json': sha(p/'sealed.json'),
                                  'scorer.py': sha(Path(__file__)), 'claude_prompt.txt': sha(p/'claude_prompt.txt'),
                                  'registered_runbook': sha(ROOT/'RUNBOOK.md')})
    shutil.copyfile(__file__, OUT/'scorer_frozen.py')
    shutil.copyfile(ROOT/'RUNBOOK.md', OUT/'registered_runbook.md')
    print(json.dumps({'symbols': symbols, 'eligible_common_tuesdays': len(common), 'packets': IDS}))


def validate_plan(visible, plan):
    if set(plan.get('stocks', {})) != set(visible['stocks']):
        raise ValueError('Plan must cover exactly the packet stocks')
    for d in plan['stocks'].values():
        if d.get('side') not in ('long','short','skip') or type(d.get('sufficient_evidence')) is not bool:
            raise ValueError('Invalid side/evidence declaration')
        for key in ('observations','missing_evidence'):
            if not isinstance(d.get(key), list) or not all(isinstance(x,str) for x in d[key]):
                raise ValueError('Expected text list: '+key)
        for key in ('reason','invalidation'):
            if not isinstance(d.get(key), str) or not d[key].strip():
                raise ValueError('Missing '+key)


def commit(packet, actor, plan, provenance):
    if actor not in ('assistant','claude'):
        raise ValueError('Unknown actor')
    p = folder(packet)
    if any((p/name).exists() for name in ('started.json','review_started.json')):
        raise ValueError('Plans locked for review or already revealed')
    check_manifest(p)
    validate_plan(read(p/'visible.json'), plan)
    obj = {'at': stamp(), 'actor': actor, 'provenance': provenance,
           'visible_sha256': sha(p/'visible.json'), **plan}
    write(p/f'{actor}.json', obj)
    write(p/f'{actor}_commit.json', {'sha256': sha(p/f'{actor}.json')})


def required_plans(p):
    v = read(p/'visible.json')
    actors = ('assistant','claude') if v['kind'] == 'comparison' else ('assistant',)
    plans = {}
    for actor in actors:
        # Missing second model blocks before sealed file is read or hashed.
        if not (p/f'{actor}_commit.json').exists():
            raise ValueError(f'Pending actual {actor} plan; outcomes remain sealed')
        if sha(p/f'{actor}.json') != read(p/f'{actor}_commit.json')['sha256']:
            raise ValueError('Committed plan changed')
        plan = read(p/f'{actor}.json')
        if plan['visible_sha256'] != sha(p/'visible.json'):
            raise ValueError('Visible packet changed')
        validate_plan(v, plan)
        plans[actor] = plan
    return v, plans


def review_input(packet):
    p = folder(packet)
    check_manifest(p)
    v, plans = required_plans(p)
    # Never reads sealed paths, source dates, app market facts or other dossiers.
    masked = {chr(65+i): {'stocks': plan['stocks']} for i,plan in enumerate(plans.values())}
    return {'task': 'Critique the evidence in these independently locked paper plans using ONLY this packet. '
                    'Do not forecast from external context. Probability is an uncalibrated opinion, never an order veto.',
            'packet': v, 'anonymous_plans': masked}


async def review(packet, client):
    p = folder(packet)
    state = review_input(packet)
    write(p/'review_started.json', {'at': stamp(), 'input': state})
    questions = {f'{name}_supported': {'type': 'noul',
        'instructions': f'Plan {name} honestly respects the supplied evidence and loss-control objective, including abstaining if evidence is missing.',
        'criteria': {'true': 'Decisions are justified by the supplied facts and limitations',
                     'false': 'Decisions invent evidence or mistake recent movement for a validated direction'}}
        for name in state['anonymous_plans']}
    answer = await client.ask(state, questions)
    result = {'at': stamp(), 'provider': client.provider, 'model': client.model,
              'ok': answer.ok, 'answers': answer.answers, 'ms': answer.ms,
              'error': None if answer.ok else 'Provider review unavailable; no invented substitute',
              'role': 'Advisory evidence critique only; does not alter locked plans or agreement rule'}
    write(p/'review.json', result)
    return result


def agreement(a, b):
    return {s: {'side': x['side'] if x['side'] == b[s]['side'] and x['side'] != 'skip'
                and x['sufficient_evidence'] and b[s]['sufficient_evidence'] else 'skip'} for s,x in a.items()}


def simulate(symbol, bars, side, rate, worse=0):
    if side == 'skip':
        return None
    sign = 1 if side == 'long' else -1
    seq = [b for b in bars if b[0] >= 935]
    if not seq or seq[0][0] != 935 or seq[-1][0] != 1510:
        raise ValueError('Incomplete execution path')
    entry = seq[0][1] * (1+sign*worse)
    qty = math.floor(min(5000/entry, 50/(entry*(.01+rate))))
    if qty < 1:
        return None
    stop = entry*(1-sign*.01)
    for hm, op, high, low, close in seq:
        if hm == 1510:
            end, reason = op, 'flatten'
            break
        if (low <= stop if side == 'long' else high >= stop):
            # A stressed entry changes entry/stop, not the observed subsequent bar path.
            end, reason = (min(op,stop) if side == 'long' else max(op,stop)), 'stop'
            break
    gross = sign*(end-entry)*qty
    cost = entry*qty*rate
    return dict(day=DAY,symbol=symbol,side=side,hm=930,entry_hm=935,exit_hm=hm,qty=qty,
                entry=entry,exit=end,reason=reason,gross=round(gross,2),costs=round(cost,2),
                net=round(gross-cost,2),net_pct=(gross-cost)/(entry*qty)*100)


def score(packet):
    p = folder(packet)
    v, plans = required_plans(p)
    check_manifest(p, hidden=True)
    if not (p/'review.json').exists():
        raise ValueError('Record Drex review or explicit provider unavailability before revealing')
    write(p/'started.json', {'at': stamp(), 'manifest': read(p/'manifest.json'),
                            'plans': {a: sha(p/f'{a}.json') for a in plans}, 'review_sha256': sha(p/'review.json')})
    sealed = read(p/'sealed.json')
    accounts = {a: plan['stocks'] for a,plan in plans.items()}
    if len(plans) == 2:
        accounts['agreement'] = agreement(accounts['assistant'], accounts['claude'])
    results = {}
    for account, plan in accounts.items():
        results[account] = {}
        for label, worse in (('base',0),('worse_entry',.005)):
            trades = [t for s,d in plan.items() if (t := simulate(s,sealed['stocks'][s],d['side'],v['stocks'][s]['round_trip_cost_rate'],worse))]
            results[account][label] = {'trades': trades, 'net': round(sum(t['net'] for t in trades),2)}
    counterfactuals = {s: {side: simulate(s,bars,side,v['stocks'][s]['round_trip_cost_rate'])
                           for side in ('long','short')} for s,bars in sealed['stocks'].items()}
    write(p/'result.json', {'packet':packet,'source_day':sealed['source_day'], 'accounts':results,
                            'counterfactuals':counterfactuals,
                            'note':'Both hypothetical directions shown, never credited as model predictions.'})
    print(json.dumps(read(p/'result.json'),indent=2))


def publish_saved(packet):
    p = folder(packet)
    if not (p/'result.json').exists():
        record_cycle(packet, 'Tuesday comparison '+packet[-1], 'Pending',
                     'Independent Claude + current assistant, then anonymous Drex review.',
                     'Current assistant committed. Waiting for actual Claude. Outcomes sealed; no scored P&L.', 1, 0)
        return
    r = read(p/'result.json'); v, plans = required_plans(p)
    review_data = read(p/'review.json')
    review_note = ('Drex reviewed the locked plan using only the visible packet. ' if review_data['ok'] else
                   'Drex review unavailable; no substitute opinion. ')
    for account, result in r['accounts'].items():
        decisions = []
        for s in v['stocks']:
            d = plans[account]['stocks'][s] if account in plans else {
                'side': agreement(plans['assistant']['stocks'],plans['claude']['stocks'])[s]['side'],
                'observations': [], 'reason':'Trade only when both models independently agree and both claim sufficient evidence.'}
            cf = r['counterfactuals'][s]
            outcome = '; '.join(f'If {side}: Rs{cf[side]["net"]:+.2f}' if cf[side] else f'If {side}: no affordable size' for side in ('long','short'))
            decisions.append({'symbol':s,'action':d['side'].title(),'reason':d['reason'],
                              'points':d['observations'][:1]+[d['reason']],
                              'outcome':outcome+' (known after reveal; not predictions)' if d['side']=='skip' else ''})
        label = f'Tuesday {"solo" if v["kind"]=="solo" else "comparison"} {packet[-1]} · {account}'
        note = ('Historical Tuesday practice; display date 3 Nov is a simulation clock, not a forecast. '
                f'Revealed source date {r["source_day"]}; reused design history only. '
                '09:35 entry, 1% stop, 15:10 exit; max Rs5,000 and planned stop + tier costs <=Rs50 per stock. '
                'Gap losses can exceed the budget. No directional edge supplied. '+review_note+
                f'Worse-entry net Rs{result["worse_entry"]["net"]:+.2f}. '
                'No trades means no participation, not demonstrated forecasting skill. '+
                ('Solo current assistant decision; Claude did not participate.' if v['kind']=='solo' else
                 'Independent plans; agreement requires same non-skip side and sufficient evidence from both.'))
        publish(packet+'_'+account,label,pd.DataFrame(result['base']['trades'],columns=COLS),DAY,DAY,'Practice',note,
                session_days=[DAY],kind='synthetic',decisions=decisions)
    totals = ', '.join(f'{a}: Rs{x["base"]["net"]:+.2f}, {len(x["base"]["trades"])} trades' for a,x in r['accounts'].items())
    record_cycle(packet,'Tuesday '+('solo ' if v['kind']=='solo' else 'comparison ')+packet[-1],'Practice',
                 'Loss control; both directions need evidence. No forced positions.',totals+'. '+review_note+'No strategy verdict.',1,1)


def run_claude(packet):
    p = folder(packet)
    check_manifest(p)
    if read(p/'visible.json')['kind'] != 'comparison':
        raise ValueError('Claude reserved for comparison packets')
    if any((p/n).exists() for n in ('claude.json','started.json','claude_attempt.json')):
        raise ValueError('Already attempted or revealed; inspect saved status, do not retry silently')
    exe = shutil.which('claude') or shutil.which('claude.exe')
    if not exe:
        raise ValueError('Claude CLI unavailable; comparison remains pending')
    write(p/'claude_attempt.json',{'at':stamp(),'prompt_sha256':sha(p/'claude_prompt.txt')})
    # No cwd access to the project, no tools/MCP, no supplied peer plan or outcome.
    with tempfile.TemporaryDirectory(prefix='stalk_blind_claude_') as cwd:
        proc = subprocess.run([exe,'--restricted','--tools','','--strict-mcp-config',
            '--mcp-config','{"mcpServers":{}}','--no-session-persistence','--print','--output-format','json'],
            input=(p/'claude_prompt.txt').read_text(encoding='utf-8'),cwd=cwd,
            capture_output=True,text=True,encoding='utf-8',timeout=240)
    (p/'claude_response.txt').write_text(proc.stdout,encoding='utf-8')
    if proc.returncode:
        write(p/'claude_status.json',{'ok':False,'exit_code':proc.returncode,'note':'No plan committed; do not substitute another model.'})
        raise ValueError('Claude unavailable; comparison pending. Raw response saved locally.')
    raw = json.loads(proc.stdout)
    if raw.get('is_error'):
        raise ValueError('Claude reported error; comparison pending')
    response = raw.get('structured_output') or raw.get('result')
    if isinstance(response,str):
        response = response.strip()
        if response.startswith('```'):
            response = '\n'.join(response.splitlines()[1:-1])
        response = json.loads(response)
    commit(packet,'claude',response,{'provider':'Actual Claude CLI','response_sha256':sha(p/'claude_response.txt'),
           'reported_models':list((raw.get('modelUsage') or {}).keys()),'isolated':True})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','commit','claude','review','score','publish'])
    parser.add_argument('packet',nargs='?',choices=IDS)
    parser.add_argument('--plan',type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    elif not args.packet:
        parser.error('packet required')
    elif args.action == 'commit':
        if not args.plan:
            parser.error('--plan required')
        commit(args.packet,'assistant',read(args.plan),{'provider':'Current assistant, manually reasoned in conversation; not a scripted signal'})
    elif args.action == 'claude':
        run_claude(args.packet)
    elif args.action == 'review':
        import httpx
        response = httpx.post(f'http://127.0.0.1:8080/api/practice/{args.packet}/review',timeout=70)
        response.raise_for_status()
        print(json.dumps(response.json(),indent=2))
    elif args.action == 'score':
        score(args.packet)
    elif args.action == 'publish':
        publish_saved(args.packet)
