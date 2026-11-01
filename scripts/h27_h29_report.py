"""Report and verify frozen H27-H29 artifacts; never reselect or score inputs."""
from __future__ import annotations
import json
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.h27_h29 import sha, dump, clustered_estimate

ROOT=Path(__file__).resolve().parents[1]


def table(headers,rows):
    return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|']+
                     ['|'+'|'.join(str(x) for x in row)+'|' for row in rows])


def append(path,text):
    with (ROOT/path).open('a',encoding='utf-8') as f:f.write('\n\n'+text+'\n')


def main():
    summary={c:json.loads((ROOT/'data'/c/'summary.json').read_text()) for c in ['h27','h28','h29']}
    validation={}
    for c in summary:
        out=ROOT/'data'/c
        start=json.loads((out/'started.json').read_text())
        assert sha(out/'scored_code.py')==start['code']
        assert all(sha(ROOT/p)==digest for p,digest in start['protected'].items())
        inputs=json.loads((out/'input_manifest.json').read_text())
        assert all(sha(ROOT/p)==digest for p,digest in inputs.items())
        outputs=json.loads((out/'output_manifest.json').read_text())
        assert all(sha(out/p)==digest for p,digest in outputs.items())
        inventory=pd.read_csv(out/'input_inventory.csv')
        failed=inventory[inventory.required&(inventory.missing_share>.01)]
        assert bool(len(failed)==0)==start['audit_passed']
        if c in ['h27','h28']:
            assert summary[c]['scoring_runs']==0 and summary[c]['verdict']=='Unresolved'
            assert not (out/'paired_returns.csv').exists() and not (out/'trades.csv').exists()
        validation[c]=dict(input_files=len(inputs),output_files=len(outputs),hashes='passed',
                           protected='unchanged',scoring_runs=summary[c]['scoring_runs'],failed_required_fields=len(failed))
    h29=summary['h29']
    for r in h29['rows']:
        x=pd.read_csv(ROOT/'data/h29'/f"{r['symbol']}_post1015.csv").abs_post1015_return.dropna()
        assert np.isclose(x.median()*1e4,r['median_post1015_bps'])
        assert np.isclose((x>.0035).mean(),r['share_post1015_gt035'])
        assert len(x)==r['hourly_days']
        assert np.isclose(r['median_post1015_bps']/17.5,r['move_to_cost'])
    for r in h29['pairs']:
        x=pd.read_csv(ROOT/'data/h29'/f"{r['a']}_{r['b']}_signals.csv")
        assert (x.prior_last_day<x.day).all() and (x.z.abs()>2).all()
        actual=clustered_estimate(x)
        assert actual['events']==r['events'] and actual['days']==r['days']
        assert np.isclose(actual['mean_bps'],r['mean_bps'])
        assert np.allclose(actual['ci95_bps'],r['ci95_bps'])
    coverage=pd.read_csv(ROOT/'data/h28/coverage.csv')
    covrows=[]
    for v in ['strict','loose']:
        x=coverage[coverage.version==v];overlap=x[x.in_cache_window];complete=x[x.complete]
        covrows.append([v,len(x),len(overlap),len(complete),complete.day.nunique(),f'{100*len(complete)/len(overlap):.2f}%'])
    audit29=pd.read_csv(ROOT/'data/h29/input_inventory.csv')
    maxmissing=float(audit29[audit29.required].missing_share.max())
    h27rows=[]
    for v,total in [('strict',427),('loose',679)]:
        a=summary['h27']['failed_fields']
        f={r['column']:r for r in a if r['source']==v+'_pick_features'}
        h27rows.append([v,total]+[f"{f[k]['missing_or_invalid']} ({100*f[k]['missing_share']:.2f}%)" for k in ['beta60','turn20','vol20']])
    ranked=table(['Rank','Name','Daily range %','Daily absolute open-close %','Post-10:15 move bps','Above 35bps','Move / planning cost','NIFTY corr','BANKNIFTY corr','Results days/year'],[
        [r['rank'],r['symbol'],f"{r['median_daily_range_pct']:.2f}",f"{r['median_abs_otc_pct']:.2f}",f"{r['median_post1015_bps']:.2f}",f"{100*r['share_post1015_gt035']:.1f}%",f"{r['move_to_cost']:.2f}",f"{r['corr_nifty']:.3f}",f"{r['corr_banknifty']:.3f}",f"{r['results_days_per_year']:.2f}"] for r in h29['rows']])
    pairs=table(['Pair','Hourly correlation','Events / independent days','Gross reversal bps','95% day-clustered interval bps'],[
        [r['a']+' / '+r['b'],f"{r['correlation']:.3f}",f"{r['events']} / {r['days']}",f"{r['mean_bps']:.2f}",f"{r['ci95_bps'][0]:.2f} to {r['ci95_bps'][1]:.2f}"] for r in h29['pairs']])
    report=f'''# H27–H29: attribution, entry timing and the observation list

Executed 1 November 2026. Paper research only, on reused historical data. The three cycles were registered and attempted in order. Each has one exclusive started.json guard. H27 and H28 stopped at the mandatory input audit: **zero return runs**, not failed profitability tests. H29 completed its one descriptive run. No tuning, confirmation, promotion or scoreboard addition.

## H27: Unresolved — required prior history is incomplete

Removing cached tiers fixes H23's specific coverage restriction, but does not supply the missing lagged features. The strict frozen list has 427 selections; loose has 679. Before any matched outcome calculation, more than 1% of each required feature was missing:

{table(['Version','Frozen picks','Beta missing','Turnover missing','Volatility missing'],h27rows)}

The same problem affects the full 142,909-row eligible control pool: beta 4,939 (3.46%), turnover 3,084 (2.16%), volatility 2,641 (1.85%) missing. Raw eligible OHLC/reference/turnover fields have no missing values. Early lookback warmup and incomplete individual stock histories remain unknown. No required missing field was filled or silently excluded to obtain a score. Tier metadata was inventoried but never required.

The registered design uses H17's exact historical eligibility and frozen strict/loose identities, with delisted-inclusive NSE EQ bhavcopy. Controls are five nearest distinct same-morning eligible names, excluding all picks and H17's known-results calendar. Ordinary controls may have **any gap greater than -2%**. The second set has gaps at least 2% down, below the top two. Only prior beta, volatility and official turnover enter the squared normalized distance; calipers are beta difference 0.5, volatility ratio 2, turnover ratio 3. No cached tier, gap-distance term or outcome input is used. The full design, including prior sample requirements, is frozen in RUNBOOK and data/h27/preregistration.md.

The audit stop comes before matching outcomes. Five-control coverage, matched independent mornings, excess bps, day t, raw ordinary-stock average and NIFTY's open-to-close average are therefore **unscored**, not zero. The requested >=80% matching coverage / >=40 mornings and excess >50bps / t>=2.5 cannot be evaluated. This says nothing about whether the panic association is stock selection or market rebound. Acquire missing prior histories before a separately authorized future design; do not trim frozen picks or reset this guard.

## H28: Unresolved — frozen names lack hourly paths

The cache really ends at the **14:15 bar label**. Registered exits use its actual open; it is not the market close. Index coverage is 16 November 2023–30 October 2026. Most frozen panic picks have no hourly path, even within that date window:

{table(['Version','All frozen picks','Picks in hourly window','Complete six-label paths','Complete mornings','Complete / window picks'],covrows)}

Strict required hourly fields are 94.38% missing across full history and 81.10% missing inside the cache window. Loose: 93.08% and 79.57%. All missing timestamps and prices are retained in the expected-path inventory. No stocks were substituted and no covered-only return sample was scored.

The registered comparison is official open versus 10:15 hourly open, both exiting at actual 14:15 open. A 3% hourly-low touch is explicitly a stop proxy, with gap-through checks, charges, assumed adverse slippage and another 0.5% adverse entry. Final 14:15 high/low/close cannot trigger a stop. The pass gate is >=40 trades / >=20 mornings, positive delayed stressed net and day t>=2.5, **only for finer-data investigation**. Because the input rule stops the cycle, gross/net returns, rupees, t, stressed results and the fraction of recovery lost by 10:15 remain unknown. The available subset cannot rescue this attempt.

## H29: descriptive complete — movement is not predictable profit

Each name has 2,472 daily observations in the ten-year window. Post-10:15 endpoints are known on 724 of 726 hourly dates, except ICICIBANK on 723. Required-field missingness never exceeds {100*maxmissing:.3f}%; daily required prices have no missing values. Full zero/missing counts for every column are saved in data/h29/input_inventory.csv. Volume is inventoried but unused. BANKNIFTY comes from 4,346 already-stored local hourly bars, exported through a read-only SQL query and frozen; no network request or five-minute substitution.

Daily range is (high-low)/open. Open-to-close movement is absolute. Post-10:15 movement is the absolute return from **10:15 open to 14:15 open**, a shorter horizon than the full afternoon. The ranking divides its median by the same **17.5bps planning hurdle**, whose double is 0.35%; actual quoted costs and impact remain unknown. This ranks movement capacity, not expected trade returns. All twelve median moves exceed the planning hurdle; that supplies no direction signal.

{ranked}

Correlations use consecutive, within-session hourly log open-to-open returns; overnight and missing-hour jumps are excluded. Results counts are observed distinct scheduled result-meeting dates, averaged over the nine full years 2017–2025. Annual counts are in ranked_table.json/CSV. Incomplete stored calendars can undercount actual results; these are not certified earnings-date frequencies. This is an intentionally fixed survivor watchlist, not a delisted-inclusive strategy sample.

The three most correlated same-sector pairs were frozen before reversal outcomes, from six bank combinations plus the IT and metals pairs. At each 10:15–13:15 open, a unit log-price spread is compared with all 120 hourly observations from exactly 20 preceding sessions, excluding the whole current day. Absolute z must exceed 2. A mechanical action anywhere in that prior/current window excludes the observation. Gross reversal fades the divergence with half notional in each opposing leg and exits at actual 14:15 open. There are no stops or costs. Repeated events share a date cluster; intervals use the registered cluster sandwich standard error and Student-t critical value.

{pairs}

All three intervals include zero. The pair choice and history are reused and descriptive. There is no strategy score or verdict, and no pair is promoted.

## Monday observation and checks

From Monday 2 November, H16 also requests all twelve names at each existing slot: 09:15:30, 09:20, 09:25, 11:15, 11:20, 15:05 and 15:10 IST. Rows carry the **watchlist** label; existing candidate labels remain, overlaps request one quote, missing quotes/depth/circuit/MIS evidence remain null. Existing H16 candidate aggregates and execution gates exclude observation-only rows, while the response retains every row and adds watchlist_count. This records displayed spreads and Rs5,000 depth/impact estimates; it does not establish realised fills.

All 261 unit tests pass, including 12 new causal/audit/watchlist tests. Saved output arithmetic and input/output/code hashes verify; all three protected source hashes are unchanged. Scoring counts are 0 / 0 / 1, because the user-required missing-data stop takes precedence over scoring an incomplete sample. data/research_summary.json adds these cycles at the top; insights and scoreboard are preserved exactly. No orders, arming, secrets, browser, computer use, servers or protected/web changes. Task commands have exited.

Artifacts: data/h27, data/h28, data/h29. Frozen code and registration are retained inside each. Source: scripts/h27_h29.py; saved-output verification/report: scripts/h27_h29_report.py; test log: data/h27_h29_unit_tests.log.
'''
    (ROOT/'docs/research/2026-11-01-h27-h29.md').write_text(report,encoding='utf-8')
    # Replace ONLY the cycles JSON value. Everything else, including the exact
    # serialized insights and scoreboard text, is kept byte for byte.
    path=ROOT/'data/research_summary.json'
    original=path.read_text(encoding='utf-8')
    before=json.loads(original)
    additions=[]
    texts={
      'H27':('Panic attribution: five controls','Unresolved: strict beta missing for37/427 picks (8.67%); >1% audit stop, no matched return score.'),
      'H28':('Panic delayed-entry coverage','Unresolved:81.10% of required strict hourly fields missing even in the date overlap; no return or decay score.'),
      'H29':('Twelve-name observation diagnostics','Descriptive only: BEL, TATASTEEL, HINDALCO, AXISBANK lead post10:15 move/cost; all three pair CIs include zero; watchlist recorder extended.')}
    for c,(name,text) in texts.items():
        additions.append(dict(id=c,name=name,verdict='Unclear',status=summary[c.lower()]['verdict'],
                              summary=text,note=text,result=text,trials=1,scoring_runs=summary[c.lower()]['scoring_runs']))
    assert not any(c['id'] in texts for c in before['cycles']), 'Do not duplicate cycle entries'
    at=original.index('"cycles"')+len('"cycles"')
    at=original.index(':',at)+1
    while original[at].isspace():at+=1
    _,end=json.JSONDecoder().raw_decode(original[at:])
    newcycles=additions+before['cycles']
    replacement=json.dumps(newcycles,ensure_ascii=False,indent=2)
    updated=original[:at]+replacement+original[at+end:]
    after=json.loads(updated)
    assert after['insights']==before['insights'] and after['scoreboard']==before['scoreboard']
    assert updated[:at]==original[:at] and updated[at+len(replacement):]==original[at+end:]
    (ROOT/'data/h27_h29_research_summary_before.json').write_text(original,encoding='utf-8')
    path.write_text(updated,encoding='utf-8')
    validation['research_summary']=dict(cycles_added=['H27','H28','H29'],other_serialized_text_unchanged=True,scoreboard_rows_added=0)
    validation['tests']=dict(command='python -m unittest discover -s tests -t .',tests=261,status='passed')
    dump(ROOT/'data/h27_h29_validation.json',validation)
    note=('H27/H28 Unresolved at the required input audit, zero return runs: strict beta37/427 missing (8.67%); strict hourly fields81.10% missing even within date overlap. '
          'H29 descriptive complete: BEL60.59, TATASTEEL57.90, HINDALCO56.54, AXISBANK50.47bps median absolute10:15-to14:15 move, against17.5bps planning cost; actual spreads unknown. '
          'Three pair95% day-clustered intervals include zero. H16 records all12 at every existing slot from Monday with watchlist labels; original candidate aggregates preserved, unknown stays null. '
          'One guard each, outcome runs0/0/1;261 tests and frozen hash/arithmetic checks pass, protected sources unchanged. No promotion/scoreboard row. '
          'Report: docs/research/2026-11-01-h27-h29.md; artifacts:data/h27,data/h28,data/h29; validation:data/h27_h29_validation.json.')
    append(Path('RUNBOOK.md'),'H29 result and H27–H29 completion (2026-11-01): '+note)
    append(Path('docs/research/HANDOFF.md'),'## H27–H29: review and Monday observation\n\n'+note+'\n\nReview the audit stops without manufacturing profitability verdicts; actual spread/impact evidence is still pending. H16 UI can show source=watchlist rows and watchlist_count. Existing H16 aggregate/gate fields still concern original candidate rows. No web edits were made. Missing data needs acquisition and a separately authorized registration, never a reset of these guards.')
    append(Path('docs/research/QUEUE.md'),'## H27–H29 completed, 2026-11-01\n\n'+note+'\n\nLesson: event-day price coverage does not imply prior-feature or frozen-name hourly coverage. Keep H16 verified execution collection and the existing H26/H19 prerequisites ahead of more strategies; acquire missing histories before any future attribution/decay design. No current guard may be reset.')
    append(Path('LOG.md'),'2026-11-01 — '+note)
    append(Path('INSIGHTS.md'),'2026-11-01 — '+note+'\n\nLesson: large absolute moves are observation capacity, not forecastable net edge; incomplete required histories cannot be rescued with available-only returns.')
    append(Path('MISTAKES.md'),'25. **Treating event-day coverage as lookback or frozen-name coverage.** H17 had complete eligible event-day prices, yet H27 lacks prior beta for37/427 strict picks; H28 lacks81.10% of required hourly fields even inside the cache window. An available-only subset would silently change the question.\n    **Rule:** inventory the full prior-session support and exact frozen-symbol execution paths before returns, including warmup and date-overlap denominators. Required fields over1% missing stop the cycle; never trim the frozen sample or substitute names to evade that stop.')
    print(json.dumps(validation,indent=2))


if __name__=='__main__':main()
