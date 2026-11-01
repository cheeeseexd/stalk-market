"""Single-use historical Wednesday what-if: actual Drex ranks stop/target plans.

This is an exploratory paper scenario, not a production strategy or forecast.
No broker APIs, credential files, current market context or hidden model input.
"""
from __future__ import annotations
import argparse
import json
import math
import secrets
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pandas as pd
from scripts.tuesday_practice import inventory, read, write, sha, stamp, COLS
from scripts.publish_run import publish, record_cycle
from stalk.data.store import Store

OUT = ROOT/'data/synthetic/wednesday_20261101'
DAY = '2026-11-04'
CID = 'wednesday_drex_scenario'


def candidates(range_pct):
    result = []
    for side in ('long','short'):
        for band, multiplier in enumerate((.25,.5,.75)):
            stop = max(.004,min(.02,range_pct*multiplier))
            for target_r in (1.5,2.):
                result.append({'id':f'{side}_{band}_{int(target_r*10)}','side':side,
                               'stop_pct':stop,'target_pct':stop*target_r})
    return result


def check(hidden=False):
    m = read(OUT/'manifest.json')
    paths = {'visible':OUT/'visible.json','code':Path(__file__),
             'dependency':ROOT/'scripts/tuesday_practice.py'}
    if hidden:
        paths['sealed'] = OUT/'sealed.json'
    for k,p in paths.items():
        if sha(p) != m[k]:
            raise ValueError('Frozen artifact changed: '+k)


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    meta = pd.read_csv(ROOT/'data/wide_meta.csv').set_index('symbol')
    excluded = {'REDINGTON','POONAWALLA','M&M','RADICO','AXISBANK','IRFC'}
    pool = sorted(s for s in meta.index if s not in excluded and (ROOT/f'data/kite_5m/{s}.pkl').exists())
    rng=secrets.SystemRandom()
    symbols=rng.sample(pool,2)
    write(OUT/'draw.json',{'at':stamp(),'pool':pool,'symbols':symbols})
    groups={s:inventory(s) for s in symbols}
    dates=[{d for d in sorted(g)[20:] if datetime.fromisoformat(d).weekday()==2} for g in groups.values()]
    common=sorted(set.intersection(*dates))
    if not common:
        raise ValueError('No eligible common Wednesday; no redraw')
    day=rng.choice(common)
    visible={'clock':'Synthetic Wednesday, 09:35 IST; 09:30 candle complete',
        'purpose':'Exploratory stop/target what-if. Do not require panic or a large gap. No proven edge claimed.',
        'limits':['News, events, benchmark/sector moves and short availability unknown.',
                  'Recent movement is not proof of direction. No current facts may be substituted.',
                  'Max Rs5,000 notional and planned stop plus costs Rs50 per name; gaps can exceed this.'],
        'stocks':{}}
    hidden={'source_day':day,'stocks':{}}
    mapping={}; volume_bad=volume_total=0
    for i,s in enumerate(symbols):
        key=chr(65+i);mapping[key]=s
        g=groups[s][day];ds=sorted(groups[s]);idx=ds.index(day)
        prior=[groups[s][d] for d in ds[idx-20:idx]]
        required=pd.concat(prior+[g[g.hm<=930]])
        vol=required.v if 'v' in required else pd.Series([float('nan')]*len(required))
        volume_total+=len(vol);volume_bad+=int((vol.isna()|(~vol.map(lambda x: math.isfinite(x)))|(vol<=0)).sum())
        rate=.0015 if meta.loc[s,'index'] in ('nifty50','next50') else .0025 if meta.loc[s,'index']=='midcap' else .004
        bars=[[int(r.hm),*[float(getattr(r,k)) for k in ('o','h','l','c')]] for r in g.itertuples() if r.hm<=1510]
        ranges=[float((x.h.max()-x.l.min())/x.o.iloc[0]) for x in prior]
        facts={'previous_close':float(prior[-1].c.iloc[-1]),
            'prior20_ohlc':[[float(x.o.iloc[0]),float(x.h.max()),float(x.l.min()),float(x.c.iloc[-1])] for x in prior],
            'five_session_change':float(prior[-1].c.iloc[-1]/prior[-6].c.iloc[-1]-1),
            'prior20_mean_range':sum(ranges)/20,
            'prior20_mean_abs_open_close':sum(abs(x.c.iloc[-1]/x.o.iloc[0]-1) for x in prior)/20,
            'completed_candles':bars[:4], 'reference':bars[3][4], 'cost_rate':rate,
            'first20_volume':float(g.loc[g.hm<=930,'v'].sum()) if 'v' in g else None,
            'prior20_mean_first20_volume':sum(float(x.loc[x.hm<=930,'v'].sum()) for x in prior)/20 if 'v' in g else None}
        facts['candidates']=candidates(facts['prior20_mean_range'])
        visible['stocks'][key]=facts;hidden['stocks'][key]=bars
    if volume_bad/volume_total>.01:
        for facts in visible['stocks'].values():
            facts['first20_volume']=facts['prior20_mean_first20_volume']=None
    visible['volume_quality']={'missing_or_zero':volume_bad,'input_rows':volume_total,
                               'used':volume_bad/volume_total<=.01}
    write(OUT/'mapping.json',mapping)
    write(OUT/'visible.json',visible);write(OUT/'sealed.json',hidden)
    write(OUT/'manifest.json',{'visible':sha(OUT/'visible.json'),'sealed':sha(OUT/'sealed.json'),
        'code':sha(Path(__file__)),'dependency':sha(ROOT/'scripts/tuesday_practice.py'),
        'mapping':sha(OUT/'mapping.json'),'registration':sha(ROOT/'RUNBOOK.md')})
    shutil.copyfile(__file__,OUT/'scorer_frozen.py')
    shutil.copyfile(ROOT/'RUNBOOK.md',OUT/'registration.md')
    print(json.dumps({'symbols':symbols,'eligible_common_wednesdays':len(common),'visible':visible},indent=2))


def questions_for(facts):
    questions={}
    for c in facts['candidates']:
        desc=f'{c["side"]}, stop {c["stop_pct"]*100:.3f}% from entry, target {c["target_pct"]*100:.3f}% from entry; enter 09:35, flatten 15:10.'
        questions[c['id']+'_quality']={'type':'score','instructions':
            'Rank this exploratory plan for a loss-conscious trader using only the supplied price/volume history: '+desc+
            ' Judge noise, room for target, costs and unsupported direction. Missing news is unknown, not invented. This is a scenario, not a proven strategy.',
            'criteria':['Poor fit','Weak fit','Neutral / uncertain','Reasonable exploratory fit','Strong exploratory fit']}
        questions[c['id']+'_target_first']={'type':'noul','instructions':
            'Estimate probability target is reached before stop and before 15:10 for '+desc+
            ' Timeout without target counts false. This opinion will be measured, not used as evidence.',
            'criteria':{'true':'Target first before deadline','false':'Stop first or no target before deadline'}}
    for hm in (1030,1200,1510):
        questions[f'above_{hm}']={'type':'noul','instructions':
            f'Price at {hm//100:02d}:{hm%100:02d} open will be above the reference Rs{facts["reference"]:.4f}. Use only the supplied historical packet.',
            'criteria':{'true':'Above reference','false':'At or below reference'}}
    return questions


def pick(facts, answers):
    ranked=[]
    for c in facts['candidates']:
        quality=answers.get(c['id']+'_quality',{}).get('score')
        prob=answers.get(c['id']+'_target_first',{}).get('noul')
        if not isinstance(quality,(int,float)) or not math.isfinite(quality) or not 1<=quality<=5:
            raise ValueError('Invalid Drex quality; no guessed replacement')
        if not isinstance(prob,(int,float)) or not math.isfinite(prob) or not 0<=prob<=1:
            raise ValueError('Invalid Drex probability')
        ranked.append({**c,'quality':quality,'target_first_p':prob})
    return sorted(ranked,key=lambda c:(-c['quality'],-c['target_first_p'],c['id']))[0]


async def propose(client):
    check()
    v=read(OUT/'visible.json')
    write(OUT/'drex_started.json',{'at':stamp(),'visible_sha256':sha(OUT/'visible.json')})
    responses={};plans={}
    for key,facts in v['stocks'].items():
        state={'task':v['purpose'],'clock':v['clock'],'limits':v['limits'],'anonymous_stock':key,
               'facts':facts,'volume_quality':v['volume_quality']}
        q=questions_for(facts)
        answer=await client.ask(state,q)
        responses[key]={'ok':answer.ok,'provider':client.provider,'model':client.model,
                        'answers':answer.answers,'ms':answer.ms,'state':state,'questions':q}
        write(OUT/f'drex_{key}.json',responses[key])
        if not answer.ok:
            raise ValueError('Drex unavailable; scenario remains unscored, no fallback')
        for hm in (1030,1200,1510):
            prob=answer.answers.get(f'above_{hm}',{}).get('noul')
            if not isinstance(prob,(int,float)) or not math.isfinite(prob) or not 0<=prob<=1:
                raise ValueError('Invalid timed Drex probability; keep outcome sealed')
        plans[key]=pick(facts,answer.answers)
    write(OUT/'plans.json',{'at':stamp(),'stocks':plans,
        'selection':'Highest actual Drex quality, then target-first opinion; exploratory only',
        'visible_sha256':sha(OUT/'visible.json')})
    write(OUT/'commit.json',{'plans':sha(OUT/'plans.json'),
        'responses':{k:sha(OUT/f'drex_{k}.json') for k in responses}})
    return {'plans':plans,'provider':client.provider,'model':client.model,'committed':True}


def simulate(symbol,bars,plan,rate,worse=0):
    sign=1 if plan['side']=='long' else -1
    seq=[b for b in bars if b[0]>=935]
    if not seq or seq[0][0]!=935 or seq[-1][0]!=1510:
        raise ValueError('Incomplete path')
    previous=next(b for b in bars if b[0]==930)[4]
    if abs(seq[0][1]/previous-1)>.2:
        raise ValueError('Data-error entry discontinuity; do not redraw')
    entry=seq[0][1]*(1+sign*worse)
    qty=math.floor(min(5000/entry,50/(entry*(plan['stop_pct']+rate))))
    stop=entry*(1-sign*plan['stop_pct']);target=entry*(1+sign*plan['target_pct'])
    if qty<1:
        return {'trade':None,'note':'No affordable whole-share size','levels':{'entry':entry,'stop':stop,'target':target}}
    ambiguous=False
    for hm,op,hi,lo,cl in seq:
        if not (0<lo<=min(op,cl)<=max(op,cl)<=hi):
            raise ValueError('Malformed OHLC; do not redraw')
        if hm==1510:
            end,reason=op,'flatten';break
        stop_open=op<=stop if sign==1 else op>=stop
        target_open=op>=target if sign==1 else op<=target
        stop_hit=lo<=stop if sign==1 else hi>=stop
        target_hit=hi>=target if sign==1 else lo<=target
        if stop_open:
            end,reason=op,'stop';break
        if target_open:
            end,reason=target,'target';break
        if stop_hit:
            end,reason=stop,'stop';ambiguous=target_hit;break
        if target_hit:
            end,reason=target,'target';break
    gross=sign*(end-entry)*qty;cost=entry*qty*rate
    t=dict(day=DAY,symbol=symbol,side=plan['side'],hm=930,entry_hm=935,exit_hm=hm,qty=qty,
           entry=entry,exit=end,reason=reason,gross=round(gross,2),costs=round(cost,2),
           net=round(gross-cost,2),net_pct=(gross-cost)/(entry*qty)*100)
    return {'trade':t,'levels':{'entry':entry,'stop':stop,'target':target},
            'ambiguous_bar_stop_first':ambiguous,'planned_loss_including_costs':entry*qty*(plan['stop_pct']+rate)}


def score():
    check(hidden=True)
    c=read(OUT/'commit.json')
    if c['plans']!=sha(OUT/'plans.json') or any(h!=sha(OUT/f'drex_{k}.json') for k,h in c['responses'].items()):
        raise ValueError('Committed plan/provider response changed')
    if read(OUT/'manifest.json')['mapping']!=sha(OUT/'mapping.json'):
        raise ValueError('Stock mapping changed')
    write(OUT/'started.json',{'at':stamp(),'commit':c,'manifest':read(OUT/'manifest.json')})
    hidden=read(OUT/'sealed.json');v=read(OUT/'visible.json');plans=read(OUT/'plans.json')['stocks'];mapping=read(OUT/'mapping.json')
    results={}
    for key,plan in plans.items():
        bars=hidden['stocks'][key];facts=v['stocks'][key]
        result={label:simulate(mapping[key],bars,plan,facts['cost_rate'],worse)
                for label,worse in (('base',0),('worse_entry',.005))}
        result['timed_opinions']={str(hm):{'p':read(OUT/f'drex_{key}.json')['answers'][f'above_{hm}']['noul'],
             'realized_above':next(b[1] for b in bars if b[0]==hm)>facts['reference']} for hm in (1030,1200,1510)}
        results[key]=result
    write(OUT/'result.json',{'source_day':hidden['source_day'],'stocks':results})
    print(json.dumps(read(OUT/'result.json'),indent=2))


def publish_saved():
    r=read(OUT/'result.json');v=read(OUT/'visible.json');plans=read(OUT/'plans.json')['stocks'];mapping=read(OUT/'mapping.json');h=read(OUT/'sealed.json')
    trades=[x['base']['trade'] for x in r['stocks'].values() if x['base']['trade']]
    stressed=sum(x['worse_entry']['trade']['net'] for x in r['stocks'].values() if x['worse_entry']['trade'])
    decisions=[];charts=[]
    for key,x in r['stocks'].items():
        p=plans[key];t=x['base']['trade'];facts=v['stocks'][key];sign=1 if p['side']=='long' else -1
        decisions.append({'symbol':mapping[key],'action':p['side'].title(),
            'points':[f'Drex ranked this {p["side"]} plan highest: quality {p["quality"]:.2f}/5.',
                      f'Stop distance {p["stop_pct"]*100:.2f}%; target distance {p["target_pct"]*100:.2f}%. Exploratory, not a proven edge.'],
            'reason':'Actual Drex numeric ranking; no generated explanation attributed to the provider.',
            'outcome':f'{t["reason"].title()} at Rs{t["exit"]:.2f}; net Rs{t["net"]:+.2f}.' if t else 'No affordable whole-share size.'})
        charts.append({'symbol':mapping[key],'side':p['side'],'levels':x['base']['levels'],
            'reference':facts['reference'],'planned_stop':facts['reference']*(1-sign*p['stop_pct']),
            'planned_target':facts['reference']*(1+sign*p['target_pct']),
            'target_first_p':p['target_first_p'],'timed_opinions':x['timed_opinions'],
            'path':h['stocks'][key], 'exit_hm':t['exit_hm'] if t else None,'exit_price':t['exit'] if t else None,
            'ambiguous':x['base']['ambiguous_bar_stop_first'] if t else False})
    note=(f'Exploratory historical Wednesday; source {r["source_day"]}, design history only. 4 Nov is a synthetic clock, not a forecast. '
          'Drex ranked 12 candidate direction/stop/target combinations per anonymous stock using only prior20 and first20-minute data. '
          'Highest quality wins, then target-first opinion. No large-gap condition; no verified edge required for this what-if. '
          'Unknown news/market/events/short availability; cache universe has survivorship. Drex opinions are uncalibrated. '
          'Next09:35 open; levels re-anchor to fill; stop-first for unresolved dual-touch candles; flat15:10. '
          'Max Rs5,000 each and planned stop+costs Rs50; gaps can exceed. Total tier costs, no leverage/re-entry. '
          f'Extra0.5%-worse entry net Rs{stressed:+.2f}. Two trades cannot validate a strategy or rank models. Claude did not participate.')
    publish(CID,'Wednesday · Drex stop and target',pd.DataFrame(trades,columns=COLS),DAY,DAY,'Practice',note,
            session_days=[DAY],kind='synthetic',decisions=decisions)
    st=Store();camp=st.campaign(CID);camp['summary']['practice_charts']=charts
    st.save_campaign(CID,DAY,DAY,camp['settings'],camp['summary'])
    net=round(sum(t['net'] for t in trades),2)
    record_cycle(CID,'Wednesday Drex stop/target','Practice',
                 f'{", ".join(mapping.values())}: {len(trades)} exploratory trades, net Rs{net:+.2f}; worse entry Rs{stressed:+.2f}.',note,1,1)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','propose','score','publish'])
    action=parser.parse_args().action
    if action=='prepare':prepare()
    elif action=='score':score()
    elif action=='publish':publish_saved()
    else:
        import httpx
        response=httpx.post('http://127.0.0.1:8080/api/practice/wednesday/propose',timeout=100)
        response.raise_for_status();print(json.dumps(response.json(),indent=2))
