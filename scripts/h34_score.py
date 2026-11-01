"""H34 ML stock ranker, nested walk-forward on 2017-2020 (RUNBOOK "## H34:"). Scored once; paper research only.

    python scripts/h34_score.py --full        # guarded by data/h34/started.json
For each test year Y: the model is trained on rows before Y; the trading settings (hurdle 2/3/4x charges x stop 3%/5% x
target none / 8x the predicted move, top 2 a day) are chosen by the best net Rs on year Y-1 traded by a model trained on rows before Y-1. Nothing about Y is
used to choose anything. Results publish themselves to Sessions and Strategies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
import pandas as pd

from scripts import h30_capitulation as h
from scripts import h31_lab as lab
from scripts import h31_score as s31
from scripts import h34_ml as m
from stalk.config import IST
from stalk.research.orb import normalize

OUT = ROOT/'data/h34'
YEARS = [2017, 2018, 2019, 2020]
GRID = [(hu, st, tx) for hu in (2, 3, 4) for st in (0.03, 0.05) for tx in (None, 8.0)]     # tx: target = tx x |predicted move|


def build(start='2015-01-01', end='2020-12-31'):
    """H31's causal arrays for the window plus full-day close/volume and sectors (what features() needs)."""
    B = s31.build_range(start, end)
    cal, syms = B['calendar'], B['symbols']
    c = np.full((len(cal), len(h.GRID), len(syms)), np.nan, np.float32)
    v = np.full_like(c, np.nan)
    for j, sym in enumerate(syms):
        f = normalize(pd.read_pickle(ROOT/f'data/kite_5m/{sym}.pkl'))
        f = f[(f.day >= cal[0]) & (f.day <= cal[-1])]
        if f.empty:
            continue
        for k, arr in (('c', c), ('v', v)):
            arr[:, :, j] = f.pivot_table(index='day', columns='hm', values=k, aggfunc='first').reindex(index=cal, columns=h.GRID).to_numpy(np.float32)
    B['cf'], B['vf'] = c, v
    sec = json.loads((ROOT/'data/sectors.json').read_text())
    B['sector'] = np.array([sec.get(x, '') for x in syms])
    return B


def fit_predict(df, train_before, test_year):
    import lightgbm as lgb
    tr = df[(df.day < train_before) & df.yrel.notna()]
    te = df[df.day.str[:4] == str(test_year)].copy()
    model = lgb.LGBMRegressor(**m.PARAMS).fit(tr[m.FEATS_REL], tr.yrel.clip(-0.1, 0.1))
    te['pred'] = model.predict(te[m.FEATS_REL])
    return te, len(tr)


def trade(B, p, hurdle, stop, target_x=None, stress=0.0):
    rows = []
    for d, g in p.groupby('day'):
        g = g[g.pred.abs() > hurdle*m.COST]
        g = g.loc[g.pred.abs().sort_values(ascending=False).index].head(2)
        for x in g.itertuples():
            side = 'long' if x.pred > 0 else 'short'
            r = lab.walk(B, int(x.i), 930, int(x.k), side, stop, target_x*abs(x.pred) if target_x else None, stress=stress)
            if r:
                rows.append(dict(day=d, symbol=x.symbol, side=side, hm=930, pred=float(x.pred), **r))
    return pd.DataFrame(rows)


RT = {'nifty50': 0.0015, 'next50': 0.0015, 'midcap': 0.0025, 'smallcap': 0.0040}


def mechanism_desk(B, df):
    """H35 (RUNBOOK "## H35:"): setting B's picks (4x, top 2, 5% stop, no target) from each outer-year model, kept only when a
    mechanism agrees (short: 09:30 close >= +5% and not small cap; long: gapped >= 1.5% down); realistic costs by size."""
    from stalk.research.orb import universe
    tiers = universe()
    rows = []
    for Y in YEARS:
        outer, _ = fit_predict(df, f'{Y}-01-01', Y)
        t = trade(B, outer, 4, 0.05)
        if t.empty:
            continue
        t = t.merge(outer[['day', 'symbol', 'r930', 'gap']], on=['day', 'symbol'], how='left')
        t['tier'] = t.symbol.map(tiers)
        keep = ((t.side == 'short') & (t.r930 >= 0.05) & (t.tier != 'smallcap')) | ((t.side == 'long') & (t.gap <= -0.015))
        t = t[keep].copy()
        t['real'] = t.gross-t.qty*t.entry*t.tier.map(RT).fillna(0.004)
        t['real_pct'] = t.real/(t.qty*t.entry)
        rows.append(t)
    t = pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()
    t.to_csv(OUT/'h35_trades.csv', index=False)
    if t.empty:
        return dict(trades=0, verdict='Failed')
    yrs = t.groupby(t.day.str[:4]).real.sum()
    g = [x.real_pct.to_numpy() for _, x in t.groupby('day')]
    rng = np.random.default_rng(35)
    lower = float(np.percentile([np.concatenate([g[i] for i in rng.integers(len(g), size=len(g))]).mean() for _ in range(5000)], 2.5))
    res = dict(trades=len(t), real_net_rs=float(t.real.sum()), real_rs_trade=float(t.real.mean()), real_pct=float(t.real_pct.mean()),
               boot_lower95_real_pct=lower, win=float((t.real > 0).mean()), by_year={k: round(float(v), 2) for k, v in yrs.items()},
               years_positive=int((yrs > 0).sum()), shorts=int((t.side == 'short').sum()), longs=int((t.side == 'long').sum()))
    gates = dict(trades=res['trades'] >= 100, net=res['real_net_rs'] > 0, bootstrap=lower > 0, years=res['years_positive'] >= 3)
    res.update(gates=gates, verdict='Passed' if all(gates.values()) else 'Failed')
    return res


def bootstrap_lower(t, draws=5000, seed=34):
    g = [x.net_pct.to_numpy() for _, x in t.groupby('day')]
    rng = np.random.default_rng(seed)
    return float(np.percentile([np.concatenate([g[i] for i in rng.integers(len(g), size=len(g))]).mean() for _ in range(draws)], 2.5))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--full', action='store_true', required=True)
    ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT/'started.json').open('x', encoding='utf-8') as fh:
        json.dump(dict(at=datetime.now(IST).isoformat()), fh)
    reg = (ROOT/'RUNBOOK.md').read_text(encoding='utf-8').split('## H34: ML stock ranker, nested', 1)[1].split('\n## ', 1)[0]
    (OUT/'preregistration.md').write_text(reg, encoding='utf-8')
    (OUT/'code_sha.json').write_text(json.dumps({p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in
        ('scripts/h34_score.py', 'scripts/h34_ml.py', 'scripts/h31_lab.py', 'scripts/h31_score.py', 'scripts/h30_capitulation.py')}, indent=1))
    B = build()
    df = m.features(B, start='2015-01-01')
    df['yrel'] = df.y-df.groupby('day').y.transform('mean')
    df.to_pickle(OUT/'features.pkl')
    trades, chosen = [], {}
    for Y in YEARS:
        inner, n_in = fit_predict(df, f'{Y-1}-01-01', Y-1)               # choose settings on Y-1 only
        scores = {(hu, st, tx): float(trade(B, inner, hu, st, tx).net.sum() if len(inner) else 0.0) for hu, st, tx in GRID}
        hu, st, tx = max(scores, key=scores.get)
        outer, n_out = fit_predict(df, f'{Y}-01-01', Y)
        t = trade(B, outer, hu, st, tx)
        w = trade(B, outer, hu, st, tx, stress=0.0025)
        t['net_pct_worse025'] = w.set_index(['day', 'symbol']).net_pct.reindex(list(zip(t.day, t.symbol))).to_numpy() if len(w) else np.nan
        chosen[Y] = dict(hurdle=hu, stop=st, target_x=tx, inner_rows=n_in, train_rows=n_out,
                         inner_scores={f'{a}x/{b:.0%}/{c or "none"}': round(v) for (a, b, c), v in scores.items()})
        print(Y, 'settings', hu, st, tx, 'trades', len(t), 'net', round(t.net.sum()), flush=True)
        trades.append(t)
    t = pd.concat(trades, ignore_index=True)
    t.to_csv(OUT/'trades.csv', index=False)
    h35 = mechanism_desk(B, df)
    (OUT/'h35_summary.json').write_text(json.dumps(h35, indent=1, default=float))
    print('H35', json.dumps({k: v for k, v in h35.items() if k != 'trades_list'}, default=float), flush=True)
    days = t.groupby('day').net.sum()
    wk = t.assign(wk=pd.to_datetime(t.day).dt.to_period('W')).groupby('wk').net.sum()
    yrs = t.groupby(t.day.str[:4]).net.sum()
    res = dict(trades=len(t), days=int(days.size), net_pct=float(t.net_pct.mean()), gross_pct=float(t.gross_pct.mean()),
               worse025_pct=float(np.nanmean(t.net_pct_worse025)), boot_lower95_net_pct=bootstrap_lower(t),
               win=float((t.net > 0).mean()), avg_win=float(t.loc[t.net > 0, 'net'].mean()), avg_loss=float(t.loc[t.net <= 0, 'net'].mean()),
               net_rs=float(t.net.sum()), day_t=float(days.mean()/days.std()*np.sqrt(len(days))), weeks_green=float((wk > 0).mean()),
               by_year={k: round(float(v), 2) for k, v in yrs.items()}, years_positive=int((yrs > 0).sum()),
               long=dict(n=int((t.side == 'long').sum()), rs=float(t.loc[t.side == 'long', 'net'].mean())),
               short=dict(n=int((t.side == 'short').sum()), rs=float(t.loc[t.side == 'short', 'net'].mean())), chosen=chosen)
    gates = dict(trades=res['trades'] >= 400, net=res['net_pct'] > 0, worse=res['worse025_pct'] > 0,
                 bootstrap=res['boot_lower95_net_pct'] > 0, years=res['years_positive'] >= 3)
    res.update(gates=gates, verdict='Passed' if all(gates.values()) else 'Failed')
    (OUT/'summary.json').write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps({k: v for k, v in res.items() if k != 'chosen'}, indent=1, default=float))
    from scripts.publish_run import publish, record_cycle
    t['strategy'] = 'h34_ml'
    publish('h34_test_2017_2020', 'ML stock ranker (H34 test), 2017-2020', t, '2017-01-01', '2020-12-31', res['verdict'],
            'Nested walk-forward scored once as registered: each year traded by a model trained only on earlier years, with settings chosen on the year before.')
    record_cycle('H34', 'ML stock ranker (LightGBM)', res['verdict'] if res['verdict'] == 'Failed' else 'Pending',
                 f"Fresh test on 2017-2020 (settings picked only from past years): {res['trades']} trades, "
                 f"{res['net_pct']*100:+.2f}% a trade after charges, win {res['win']*100:.0f}%, {res['years_positive']} of 4 years positive, "
                 f"{res['weeks_green']*100:.0f}% of weeks green. {res['verdict']}.",
                 json.dumps({k: res[k] for k in ('trades', 'net_pct', 'gross_pct', 'worse025_pct', 'boot_lower95_net_pct', 'by_year', 'gates')}, default=float),
                 34, 1, score=('ML stock ranker (H34)', round(res['net_pct']*100, 2), f"{res['verdict']} · 2017-2020 fresh test"))
    if h35.get('trades'):
        t5 = pd.read_csv(OUT/'h35_trades.csv')
        t5['net'], t5['strategy'] = t5.real, 'h35_mechanism'
        publish('h35_test_2017_2020', 'Mechanism-gated ML desk (H35 test), 2017-2020', t5, '2017-01-01', '2020-12-31', h35['verdict'],
                'ML picks kept only when a known mechanism agrees (fade a 5%+ spike, buy a real gap-down); realistic costs by stock size. Scored once.')
    record_cycle('H35', 'Mechanism-gated ML desk', h35['verdict'],
                 f"ML picks kept only when they fade a 5%+ spike or buy a real gap-down, at realistic costs. Fresh 2017-2020: {h35.get('trades', 0)} trades, "
                 f"₹{h35.get('real_rs_trade', 0):+.1f} a trade, {h35.get('years_positive', 0)} of 4 years positive. {h35['verdict']}.",
                 json.dumps({k: v for k, v in h35.items()}, default=float), 41, 1,
                 score=('Mechanism-gated ML desk (H35)', round(h35.get('real_pct', 0)*100, 2), f"{h35['verdict']} · 2017-2020 fresh test, realistic costs"))


if __name__ == '__main__':
    main()
