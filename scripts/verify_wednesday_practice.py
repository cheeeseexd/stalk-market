"""Read-only audit of saved Wednesday results; never generates or scores a path."""
import json
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.wednesday_practice import OUT, read, sha, check


def verify():
    check(hidden=True)
    r=read(OUT/'result.json');h=read(OUT/'sealed.json');v=read(OUT/'visible.json')
    plans=read(OUT/'plans.json')['stocks'];commit=read(OUT/'commit.json')
    assert sha(OUT/'plans.json')==commit['plans']
    assert all(sha(OUT/f'drex_{k}.json')==digest for k,digest in commit['responses'].items())
    assert r['source_day'] < '2025-08-01'
    audit=[]
    for key,row in r['stocks'].items():
        bars=h['stocks'][key];by={b[0]:b for b in bars};plan=plans[key];rate=v['stocks'][key]['cost_rate']
        for label,worse in (('base',0),('worse_entry',.005)):
            result=row[label];t=result['trade'];lv=result['levels']
            if not t:
                assert math.floor(min(5000/lv['entry'],50/(lv['entry']*(plan['stop_pct']+rate))))<1
                continue
            sign=1 if t['side']=='long' else -1
            assert abs(t['entry']-by[935][1]*(1+sign*worse))<1e-8
            assert t['qty']==math.floor(min(5000/t['entry'],50/(t['entry']*(plan['stop_pct']+rate))))
            assert result['planned_loss_including_costs']<=50
            assert abs(t['gross']-round(sign*(t['exit']-t['entry'])*t['qty'],2))<.001
            assert abs(t['costs']-round(t['entry']*t['qty']*rate,2))<.001
            assert abs(t['net']-t['gross']+t['costs'])<=.011
            assert by[t['exit_hm']][3]<=t['exit']<=by[t['exit_hm']][2]
            for b in bars:
                if 935<=b[0]<t['exit_hm']:
                    assert b[3]>lv['stop'] and b[2]<lv['target'] if sign==1 else b[2]<lv['stop'] and b[3]>lv['target']
            ei=bars.index(by[t['exit_hm']])
            audit.append({'symbol':t['symbol'],'scenario':label,'net':t['net'],'reason':t['reason'],
                'entry_neighbors':[b for b in bars if 930<=b[0]<=940],
                'exit_neighbors':bars[max(0,ei-1):ei+2]})
    for name in ('tuesday_compare_1','tuesday_compare_2'):
        p=OUT.parent/'tuesday_20261101'/name
        assert not (p/'started.json').exists() and not (p/'result.json').exists()
    return {'ok':True,'rows':audit,'result_sha256':sha(OUT/'result.json'),'reserved_tuesday_outcomes':'still sealed'}


if __name__=='__main__':
    print(json.dumps(verify(),indent=2))
