"""H34 ML ranker (RUNBOOK "H34 plan"): LightGBM ranks every eligible stock at 09:35 by its expected rest-of-day return.

Development mode uses the design window only (2021-01..2025-07), walk-forward by year: the model that trades year Y is
trained only on earlier years. Hyperparameters are fixed below and never tuned on test years. Each day the model may trade
up to 2 stocks, long or short, only when |predicted return| beats 2x round-trip charges (it can abstain). Trades are then
played with the same 3% stop and 15:10 flatten as H31, through scripts/h31_lab.walk.

    python scripts/h34_ml.py --dev
"""
from __future__ import annotations

import argparse
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
from scripts import h32_lab as h32

G = h.GRID
IX = {hm: n for n, hm in enumerate(G)}
COST = 0.00106
PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=200, subsample=0.8,
              subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, verbose=-1, random_state=34)
TIER = {'nifty50': 0, 'next50': 1, 'midcap': 2, 'smallcap': 3}


def weekly_expiry(d):
    t = pd.Timestamp(d)
    return int(t.weekday() == (3 if t < pd.Timestamp('2025-09-01') else 1))


def features(B, start=None):
    """One row per (day, eligible stock), all known after the 09:30 bar closes (09:35). Label: 09:35 open -> 15:10 open."""
    cal = B['calendar']
    H = np.nanmax(np.asarray(B['h'], float), axis=1)
    L = np.nanmin(np.asarray(B['l'], float), axis=1)
    C = np.asarray(B['cf'][:, IX[1525]], float)
    sec = B['sector']
    secs = sorted(set(sec))
    tier = np.array([TIER.get(t, 3) for t in B['tiers']])
    rows = []
    for i, d in enumerate(cal):
        if i < 21 or d < (start or h.DESIGN_START):
            continue
        pc = B['pc'][i]
        o0 = np.asarray(B['o'][i, IX[915]], float)
        c930, v930 = np.asarray(B['c'][i, 0], float), np.asarray(B['v'][i, 0], float)
        h930, l930 = np.asarray(B['h'][i, IX[930]], float), np.asarray(B['l'][i, IX[930]], float)
        hi3 = np.nanmax(np.asarray(B['h'][i, IX[915]:IX[930]+1], float), axis=0)
        lo3 = np.nanmin(np.asarray(B['l'][i, IX[915]:IX[930]+1], float), axis=0)
        entry, exit_ = np.asarray(B['o'][i, IX[935]], float), np.asarray(B['o'][i, IX[1510]], float)
        ok = B['eligible'][i] & np.isfinite(pc) & np.isfinite(c930) & np.isfinite(o0) & np.isfinite(entry)
        if ok.sum() < 20:
            continue
        r930 = c930/pc-1
        mkt = np.nanmedian(r930[ok])
        smed = np.full(len(pc), np.nan)
        for s in secs:
            m = ok & (sec == s)
            if m.sum() >= 3:
                smed[m] = np.median(r930[m])
        atr20 = np.nanmean(H[i-20:i]-L[i-20:i], axis=0)
        f = dict(gap=o0/pc-1, r930=r930, r_open=c930/o0-1, vr930=v930/np.asarray(B['vavg'][i, 0], float),
                 pos930=(c930-l930)/np.where(h930 > l930, h930-l930, np.nan), range3=(hi3-lo3)/pc,
                 mkt=np.full(len(pc), mkt), sector_rel=r930-smed, gap_atr=(o0-pc)/atr20,
                 ret5=C[i-1]/C[i-6]-1, ret20=C[i-1]/C[i-21]-1, prev_range_atr=(H[i-1]-L[i-1])/atr20,
                 logp=np.log(pc), tier=tier.astype(float))
        y = exit_/entry-1
        for k in np.flatnonzero(ok):
            rows.append(dict(day=d, i=i, k=int(k), symbol=B['symbols'][k], **{n: float(v[k]) for n, v in f.items()},
                             dow=pd.Timestamp(d).weekday(), expiry=weekly_expiry(d), month=int(d[5:7]),
                             mstart=int(int(d[8:]) <= 3), y=float(y[k])))
    return pd.DataFrame(rows)


FEATS = ['gap', 'r930', 'r_open', 'vr930', 'pos930', 'range3', 'mkt', 'sector_rel', 'gap_atr', 'ret5', 'ret20',
         'prev_range_atr', 'logp', 'tier', 'dow', 'expiry', 'month', 'mstart']


# Dev trial 2: rank stocks against each other. Day-level inputs (market move, calendar) made trial 1 learn "is today an up
# day" instead of "which stock beats the rest", so the target becomes the stock's return minus that day's average.
FEATS_REL = ['gap', 'r930', 'r_open', 'vr930', 'pos930', 'range3', 'sector_rel', 'gap_atr', 'ret5', 'ret20',
             'prev_range_atr', 'logp', 'tier']


def walk_forward(df, test_years, feats=None, target='y'):
    import lightgbm as lgb
    preds = []
    for y in test_years:
        tr, te = df[(df.day < f'{y}-01-01') & df.y.notna()], df[df.day.str[:4] == str(y)].copy()
        if len(tr) < 50000 or te.empty:
            continue
        feats = feats or FEATS
        m = lgb.LGBMRegressor(**PARAMS).fit(tr[feats], tr[target].clip(-0.1, 0.1))
        te['pred'] = m.predict(te[feats])
        te['train_end'] = f'{y-1}-12-31'
        preds.append(te)
        imp = sorted(zip(m.feature_importances_, feats), reverse=True)[:6]
        print(y, 'trained on', len(tr), 'rows; top features', [f for _, f in imp], flush=True)
    return pd.concat(preds) if preds else pd.DataFrame()


def trade(B, p, k=2, hurdle=2*COST, sides=('long', 'short')):
    """Each day: up to k stocks with |pred| > hurdle, side by sign; played with a 3% stop through h31_lab.walk."""
    rows = []
    for d, g in p.groupby('day'):
        g = g[g.pred.abs() > hurdle].copy()
        g['side'] = np.where(g.pred > 0, 'long', 'short')
        g = g[g.side.isin(sides)]
        g = g.loc[g.pred.abs().sort_values(ascending=False).index].head(k)
        for _, x in g.iterrows():
            r = lab.walk(B, int(x.i), 930, int(x.k), x.side, 0.03, None)
            if r:
                rows.append(dict(day=d, symbol=x.symbol, side=x.side, hm=930, pred=x.pred, **r))
    return pd.DataFrame(rows)


def report(t, name):
    if t.empty:
        print(name, 'no trades'); return {}
    days = t.groupby('day').net.sum()
    res = dict(name=name, trades=len(t), days=int(days.size), net_pct=round(t.net_pct.mean()*100, 3),
               gross_pct=round(t.gross_pct.mean()*100, 3), win=round((t.net > 0).mean()*100, 1),
               avg_win=round(t.loc[t.net > 0, 'net'].mean(), 1), avg_loss=round(t.loc[t.net <= 0, 'net'].mean(), 1),
               net_rs=round(t.net.sum()), day_t=round(float(days.mean()/days.std()*np.sqrt(len(days))), 2),
               long_n=int((t.side == 'long').sum()), short_n=int((t.side == 'short').sum()),
               per_year={y: round(g.net_pct.mean()*100, 3) for y, g in t.groupby(t.day.str[:4])})
    print(json.dumps(res), flush=True)
    return res


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--dev', action='store_true', required=True)
    ap.parse_args()
    B = h32.load()
    path = lab.OUT/'h34_features_dev.pkl'
    df = pd.read_pickle(path) if path.exists() else features(B)
    df.to_pickle(path)
    print('rows', len(df), 'days', df.day.nunique(), flush=True)
    df['yrel'] = df.y - df.groupby('day').y.transform('mean')
    p = walk_forward(df, [2023, 2024, 2025], feats=FEATS_REL, target='yrel')
    p.to_pickle(lab.OUT/'h34_preds_dev2.pkl')
    # relative predictions: the hurdle is on the stock's expected lead over the day's average
    out = [report(trade(B, p), 'ML-rel top-2, both sides'), report(trade(B, p, sides=('short',)), 'ML-rel top-2, shorts only'),
           report(trade(B, p, sides=('long',)), 'ML-rel top-2, longs only')]
    log = lab.OUT/'trials_h34.csv'
    prior = pd.read_csv(log) if log.exists() else pd.DataFrame()
    pd.concat([prior, pd.DataFrame([dict(r, per_year=json.dumps(r.get('per_year')), at=datetime.now().isoformat(timespec='seconds'))
                                    for r in out if r])], ignore_index=True).to_csv(log, index=False)
