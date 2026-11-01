"""Learning sessions: play a random week, review every trade, learn, then play the next week with what was learned.

Honesty rules (written before the first session):
  * Weeks are drawn at random (Drex yes/no, as on the Simulation page) from 2024-01..2025-07 and played in DATE ORDER, so a
    session can only learn from weeks before it.
  * The model is retrained before every session on all rows dated before that week (it absorbs the newest data).
  * The rule can change by at most ONE setting per session, chosen from a fixed menu (confidence bar, target, picks a day,
    trading with/against the 09:30 market move), and only if that change also beats the current rule over the 12 months
    before the next session (out-of-sample walk-forward predictions) in net rupees AND in at least 3 of its 4 quarters.
    A single week never changes a rule by itself; it only says where to look.
  * Every number is reported as it comes out.

    python scripts/learning_sessions.py
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import datetime, timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402
load_dotenv(ROOT/'.env')
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from scripts import h31_lab as lab, h32_lab as h32, h34_ml as m  # noqa: E402
from stalk.config import IST  # noqa: E402

OUT = ROOT/'data/learning'
START = dict(hurdle=4, stop=0.05, target_x=8.0, max_picks=2, align='both')
MENU = dict(hurdle=[3, 5], target_x=[None, 6.0, 10.0], max_picks=[1], align=['with_market', 'against_market'], stop=[0.03, 0.07])
N_SESSIONS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
FORWARD_DAYS = 28        # v2 (Astra r4): a change must also beat the current rule on the next 4 untouched weeks after the session


def pick(p, rule):
    """The day's trades under a rule: |pred| beats the bar, optional market alignment, top N by |pred|."""
    g = p[p.pred.abs() > rule['hurdle']*m.COST].copy()
    g['side'] = np.where(g.pred > 0, 'long', 'short')
    if rule['align'] == 'with_market':
        g = g[((g.side == 'long') & (g.mkt > 0)) | ((g.side == 'short') & (g.mkt < 0))]
    elif rule['align'] == 'against_market':
        g = g[((g.side == 'long') & (g.mkt < 0)) | ((g.side == 'short') & (g.mkt > 0))]
    return g.loc[g.pred.abs().sort_values(ascending=False).index].head(rule['max_picks'])


def play(B, p, rule):
    rows = []
    for d, g in p.groupby('day'):
        for x in pick(g, rule).itertuples():
            r = lab.walk(B, int(x.i), 930, int(x.k), x.side, rule['stop'], rule['target_x']*abs(x.pred) if rule['target_x'] else None)
            if r:
                rows.append(dict(day=d, symbol=x.symbol, side=x.side, hm=930, pred=float(x.pred), mkt=float(x.mkt), r930=float(x.r930),
                                 ret5=float(x.ret5), ret20=float(x.ret20), gap=float(x.gap), range3=float(x.range3), tier=float(x.tier), **r))
    return pd.DataFrame(rows)


def why(t):
    """Plain reason for each trade's result."""
    if t.reason == 'target':
        return 'hit its target'
    if t.reason == 'stop':
        return 'squeezed up into the stop' if t.side == 'short' else 'fell into the stop'
    if abs(t.gross) < 1.5*t.costs:
        return 'barely moved: lost the charges' if t.net <= 0 else 'small gain'
    return 'went the right way until 15:10' if t.net > 0 else 'drifted the wrong way until 15:10'


def review(t, hist):
    """Session trades vs the 12 months before: what the winners and losers had in common."""
    if t.empty:
        return dict(note='No trades this week: the model never cleared its confidence bar.')
    t = t.assign(why=t.apply(why, axis=1), won=t.net > 0)
    def profile(x):
        return dict(n=int(len(x)), long_share=round(float((x.side == 'long').mean()), 2), with_market=round(float((np.sign(x.pred) == np.sign(x.mkt)).mean()), 2),
                    pred=round(float(x.pred.abs().mean()*100), 2), move_0930=round(float(x.r930.abs().mean()*100), 2), ret20=round(float(x.ret20.mean()*100), 1))
    out = dict(trades=int(len(t)), won=int(t.won.sum()), net=round(float(t.net.sum()), 2), charges=round(float(t.costs.sum()), 2),
               reasons=t.why.value_counts().to_dict(), winners=profile(t[t.won]) if t.won.any() else None,
               losers=profile(t[~t.won]) if (~t.won).any() else None, history_12m=profile(hist) if len(hist) else None,
               history_win=round(float((hist.net > 0).mean()*100), 1) if len(hist) else None,
               history_rs=round(float(hist.net.mean()), 2) if len(hist) else None,
               per_trade=[dict(day=r.day, symbol=r.symbol, side=r.side, pred=round(r.pred*100, 2), market_0930=round(r.mkt*100, 2),
                               reason=r.why, net=round(r.net, 2)) for r in t.itertuples()])
    return out


def diagnose(rev):
    """Which settings the session's losses point at (Astra r4: the review must feed what gets tried)."""
    r = rev.get('reasons') or {}
    stops = r.get('squeezed up into the stop', 0)+r.get('fell into the stop', 0)
    flat = r.get('barely moved: lost the charges', 0)
    losers = rev.get('losers') or {}
    focus = set()
    if stops:
        focus.add('stop')
    if losers and losers.get('with_market', 1) < 0.5:
        focus.add('align')
    if flat:
        focus.add('hurdle')
    if r.get('hit its target', 0):
        focus.add('target_x')
    return sorted(focus or {'hurdle', 'max_picks'})


def learn(B, preds, rule, before, focus=None):
    """Try each single change from the menu on the 12 months before `before`; keep the best one only if it wins on rupees and
    in at least 3 of 4 quarters. Returns (new rule, what was tried)."""
    a = (pd.Timestamp(before)-pd.Timedelta(days=365)).strftime('%Y-%m-%d')
    p = preds[(preds.day >= a) & (preds.day < before)]
    base = play(B, p, rule)
    qkey = lambda x: ((pd.to_datetime(x.day)-pd.Timestamp(a)).dt.days//92).clip(0, 3)     # four ~3-month blocks of the year
    qb = base.groupby(qkey(base)).net.sum() if len(base) else pd.Series(dtype=float)
    tried = [dict(change='keep current rule', net=round(float(base.net.sum())), trades=len(base))]
    best = None
    for key, values in MENU.items():
        if focus and key not in focus:
            continue                                   # the session review says where to look
        for v in values:
            if v == rule[key]:
                continue
            cand = dict(rule, **{key: v})
            t = play(B, p, cand)
            q = t.groupby(qkey(t)).net.sum() if len(t) else pd.Series(dtype=float)
            better_q = int(sum(q.get(k, 0.0) > qb.get(k, 0.0) for k in set(qb.index) | set(q.index)))
            row = dict(change=f'{key} → {v}', net=round(float(t.net.sum())), trades=len(t), better_quarters=better_q)
            tried.append(row)
            if t.net.sum() > base.net.sum() and better_q >= 3 and (best is None or t.net.sum() > best[1]):
                best = (cand, float(t.net.sum()), row['change'])
    return (best[0] if best else rule), (best[2] if best else 'no change passed'), tried


async def draw_weeks(pool, n=3):
    from stalk.llm.drex import DrexClient
    client, rng, chosen, draws = DrexClient(timeout_s=20), random.SystemRandom(), [], []
    q = {"pick": {"type": "noul", "instructions": "We are picking past trading weeks at random to test a strategy on. There is no right answer: "
                  "say yes or no at random so that the choice is not ours.", "criteria": {"true": "Yes, use this week", "false": "No, draw another"}}}
    while len(chosen) < n and len(draws) < 40:
        w = rng.choice(pool)
        if any(abs((pd.Timestamp(w)-pd.Timestamp(c)).days) < 42 for c in chosen):
            continue
        ans = await client.ask({"task": "Pick a random week", "week_starting": w}, q) if client.configured else None
        p = ans.noul("pick") if ans and ans.ok else None
        draws.append(dict(week=w, drex=p))
        if p is None or p >= 0.5:
            chosen.append(w)
    return sorted(chosen), draws


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    import lightgbm as lgb
    B = h32.load()
    feats = pd.read_pickle(lab.OUT/'h34_features_dev.pkl')
    feats['yrel'] = feats.y-feats.groupby('day').y.transform('mean')
    oos = pd.read_pickle(lab.OUT/'h34_preds_dev2.pkl')                       # walk-forward predictions for the 12-month checks
    oos = oos.merge(feats[['day', 'symbol', 'mkt']], on=['day', 'symbol'], how='left') if 'mkt' not in oos else oos
    days = sorted(feats.day.unique())
    pool = sorted({(pd.Timestamp(d)-pd.Timedelta(days=pd.Timestamp(d).weekday())).strftime('%Y-%m-%d') for d in days if '2024-01-08' <= d <= '2025-06-27'})
    weeks, draws = asyncio.run(draw_weeks(pool, N_SESSIONS))
    rule, log = dict(START), []
    for n, mon in enumerate(weeks, 1):
        fri = (pd.Timestamp(mon)+pd.Timedelta(days=4)).strftime('%Y-%m-%d')
        tr = feats[(feats.day < mon) & feats.yrel.notna()]
        model = lgb.LGBMRegressor(**m.PARAMS).fit(tr[m.FEATS_REL], tr.yrel.clip(-0.1, 0.1))          # retrained on everything before
        wk = feats[(feats.day >= mon) & (feats.day <= fri)].copy()
        wk['pred'] = model.predict(wk[m.FEATS_REL])
        t = play(B, wk, rule)
        a = (pd.Timestamp(mon)-pd.Timedelta(days=365)).strftime('%Y-%m-%d')
        hist = play(B, oos[(oos.day >= a) & (oos.day < mon)], rule)
        rev = review(t, hist)
        focus = diagnose(rev)
        after = (pd.Timestamp(fri)+pd.Timedelta(days=1)).strftime('%Y-%m-%d')          # includes the session's Friday
        cand, change, tried = learn(B, oos, rule, after, focus)
        forward = None
        if cand != rule:                               # champion vs challenger on the next 4 untouched weeks
            end = (pd.Timestamp(after)+pd.Timedelta(days=FORWARD_DAYS)).strftime('%Y-%m-%d')
            fw = oos[(oos.day >= after) & (oos.day < end)]
            champ, chall = play(B, fw, rule), play(B, fw, cand)
            forward = dict(window=[after, end], current=round(float(champ.net.sum()) if len(champ) else 0.0),
                           proposed=round(float(chall.net.sum()) if len(chall) else 0.0))
            if forward['proposed'] <= forward['current']:
                change = f"{change} (rejected: lost to the current rule on the next 4 weeks)"
                cand = rule
            else:
                change = f"{change} (kept: also beat the current rule on the next 4 weeks)"
        new_rule = cand
        entry = dict(session=n, week=[mon, fri], rule=dict(rule), train_rows=int(len(tr)), review=rev, focus=focus, learned=change, tried=tried,
                     forward=forward, next_rule=new_rule)
        log.append(entry)
        if len(t):
            t.to_csv(OUT/f'session_{n}_trades.csv', index=False)
        print(json.dumps(dict(session=n, week=mon, rule=rule, trades=rev.get('trades', 0), won=rev.get('won', 0), net=rev.get('net', 0),
                              reasons=rev.get('reasons'), focus=focus, learned=change, forward=forward), default=str), flush=True)
        rule = new_rule
    (OUT/f'sessions_v2_{datetime.now(IST).strftime("%H%M")}.json').write_text(json.dumps(dict(at=datetime.now(IST).isoformat(timespec='seconds'), draws=draws, sessions=log),
                                                 indent=1, default=str), encoding='utf-8')


if __name__ == '__main__':
    main()
