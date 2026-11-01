"""Publish saved research facts and append the handoff; never score returns."""
import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.h22_h25 import ROOT, dump, sha


def number(value,places=2):
    return 'unknown' if value is None else f'{value:,.{places}f}'


def append(path,text,tag):
    old=path.read_text(encoding='utf-8')
    if tag in old: raise RuntimeError('Completion already appended: '+str(path))
    with path.open('a',encoding='utf-8') as f: f.write('\n\n'+text+'\n')


def main():
    summaries={c:json.loads((ROOT/'data'/c/'summary.json').read_text()) for c in ['h23','h22','h25']}
    h23,h22,h25=[summaries[c] for c in ['h23','h22','h25']]
    validation=json.loads((ROOT/'data/h22/saved_output_validation.json').read_text())
    log=(ROOT/'data/h22/unit_tests_final.log').read_bytes()
    tests=log.decode('utf-16' if log.startswith(b'\xff\xfe') else 'utf-8-sig',errors='replace')
    assert 'Ran 227 tests' in tests and '\nOK' in tests
    primary=h22['variants']['k0.25']; a=primary['fills']['assumed']; w=primary['fills']['worse_05']
    s=h23['comparisons']['small_gap']['assumed'];g=h25['gross']
    paired=h25['paired_excess']; v=validation['h25']
    components=json.loads((ROOT/'data/h25/matched_components.json').read_text())
    h25_outcome=('Clears the descriptive hurdle for a separately registered future scored test.' if h25['graduates_to_scored_test']
        else 'Does not clear the 60bps descriptive hurdle; no scored strategy test follows from this cycle.')
    lines=['# H23, H22 and H25', '',
        'Executed 2026-10-31; report label 2026-11-01. Paper research on reused history. Registered and run separately in this order, once each, with started.json guards. No tuning or fresh confirmation.', '',
        '**H23: Failed the mechanism screen.** Picks did not beat matched small-gap stocks. The matched sample is too narrow to explain the full panic edge, and neither leg bounced on average. Market rebound remains a possible explanation, not an established one.', '',
        '| Same-morning control | Pairs / dates | Pick minus control, bps | Day t | Both entries 0.5% worse, bps | Day t |',
        '|---|---:|---:|---:|---:|---:|']
    for key,label in [('small_gap','Gap -1% to 0%'),('event_gap','Similar gap, known results')]:
        r=h23['comparisons'][key];x=r['assumed'];y=r['worse_05']
        lines.append(f"| {label} | {r['pairs']} / {r['days']} | {number(x['mean_bps'])} | {number(x['day_t'])} | {number(y['mean_bps'])} | {number(y['day_t'])} |")
    lines += ['',f"Primary 95% interval: [{number(s['ci95_bps'][0])}, {number(s['ci95_bps'][1])}]bps; required >=50bps and t>=2. Raw open-to-close excess is {number(h23['comparisons']['small_gap']['raw_gross']['mean_bps'])}bps. Both-leg fill stress mostly cancels: if only picks fill worse, the trade-weighted excess is {number(h23['comparisons']['small_gap']['worse_05']['pick_only_worse_diff_bps'])}bps.", '',
        'Only 48/427 frozen picks match small-gap controls on 44/218 strict mornings; 323 picks lack a cached tier. No unknown tier was guessed. Mean absolute beta distance is 0.113; median pick/control turnover and volatility ratios are 0.990 and 1.001. The event comparison has one pair and cannot support inference. Matching used the same current tier label, earlier 60-session beta, earlier 20-session turnover/volatility and fixed calipers. Current tiers and sparse result calendars limit this subset; controls include historical EQ names where metadata permits.', '',
        '**H22: Failed for both fixed k values.** Original panic mornings contribute no profit to these figures. The primary k=0.25 is execution-dependent and misses t>=2. The diagnostic k=0.50 misses t and the frequency gate.', '',
        '| k | Added trigger / funded mornings per year | Trades | Net/trade bps (Rs) | Day t | Net/trade bps (Rs), 0.5% worse | Day t | Verdict |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for key,r in h22['variants'].items():
        x,y=r['fills']['assumed'],r['fills']['worse_05']
        lines.append(f"| {r['k']:.2f} | {r['added_trigger_mornings_per_year']:.1f} / {x['added_mornings_per_year']:.1f} | {x['trades']} | {number(x['net_bps'])} ({number(x['net_rupees_per_trade'])}) | {number(x['day_t'])} | {number(y['net_bps'])} ({number(y['net_rupees_per_trade'])}) | {number(y['day_t'])} | {r['verdict']} |")
    lines += ['', 'NIFTY volatility is the sample SD of 20 completed daily returns before the open. Pass requires positive added-day net after charges, day t>=2, nonnegative net at worse entry, and at least 10 funded added mornings/year. Annual counts divide by ten. Two primary trigger dates lack prices; they are unknown. The same frozen H21 candidate pool and H17 stop/fees were used. Two added-only Sessions runs are saved as “Panic, volatility-scaled, 10 years” with k suffixes, each retaining 30 calendar days with null net.', '',
        '**H25: descriptive only; no strategy verdict.** '+h25_outcome, '',
        '| Gross observation | Estimate bps | 95% interval bps | Dates |',
        '|---|---:|---:|---:|',
        f"| All observed stress stocks | {number(g['mean_bps'])} | {number(g['ci95_bps'][0])} to {number(g['ci95_bps'][1])} | {g['n']} |",
        f"| Entry 0.5% worse | {number(h25['worse_05']['mean_bps'])} | {number(h25['worse_05']['ci95_bps'][0])} to {number(h25['worse_05']['ci95_bps'][1])} | {h25['worse_05']['n']} |",
        f"| Matched stress subset | {number(components['matched_stress']['mean_bps'])} | {number(components['matched_stress']['ci95_bps'][0])} to {number(components['matched_stress']['ci95_bps'][1])} | 47 |",
        f"| Matched ordinary days | {number(components['matched_ordinary']['mean_bps'])} | {number(components['matched_ordinary']['ci95_bps'][0])} to {number(components['matched_ordinary']['ci95_bps'][1])} | 47 |",
        f"| Stress minus matched ordinary days | {number(paired['mean_bps'])} | {number(paired['ci95_bps'][0])} to {number(paired['ci95_bps'][1])} | {paired['n']} |", '',
        f"{h25['observed']} observed stock-days on {h25['days']} dates; {h25['matched']} matched stock-days on {h25['matched_days']} dates; {h25['missing_outcomes']} missing paths. Day-bootstrap 95% interval: {number(h25['day_bootstrap_ci95_bps'][0])} to {number(h25['day_bootstrap_ci95_bps'][1])}bps. Equal weight dates, then stocks; stock-weighted gross is {number(h25['trade_weighted_gross_bps'])}bps. Intervals cluster stocks by stress date; ordinary control dates can be reused, so the paired interval is approximate.", '',
        f"Actual eligible signal coverage: {v['first_signal_day']} to {v['last_signal_day']}, within the requested Nov2023–Oct2026 window. The 11:15-labelled hour closes at 12:15 IST; entry is the next 12:15 open and exit is the official daily close at 15:30. Earlier same-stock ordinary dates match the morning fall, opening gap and lagged volatility. All stress stocks are measured, with no two-pick selection, stop, charges or strategy score.", '',
        'All studies have incomplete event vintages and assumed historical fills; H23 inherits frozen H17 retrospective exclusions. H22 uses delisted-inclusive bhavcopy; H23 tier matching and H25 hourly membership retain survivor bias. Missing opening depth/MIS/lock observations remain unknown. Action dates are excluded before new gap signals using the existing price-basis approach; no synthetic adjustment or full-day lock filter. Fixed Rs5,000 slots may need replenishment. These results authorize no live change.', '',
        '227 tests pass, including nine new causal tests. Saved prices, matches, costs, statistics, Sessions and frozen hashes reconcile; pre-existing database rows and all protected sources are unchanged. The initial H25 attempt found no final hourly candle and produced no finite returns; its files are preserved. A registered source correction completed the same saved selections with the official close, without rebuilding signals or matches. MISTAKES rule23 records the timestamp-inventory lesson. No orders, arming, secrets, browser, server, forbidden strategy or web edits. Artifacts: data/h23, data/h22 and data/h25; scorers: scripts/h22_h25.py; audit: scripts/h22_h25_verify.py.', '']
    report=ROOT/'docs/research/2026-11-01-h22-h25.md'
    if report.exists(): raise RuntimeError('Report already exists')
    report.write_text('\n'.join(lines),encoding='utf-8')

    path=ROOT/'data/research_summary.json'
    before=path.read_bytes()
    backup=ROOT/'data/h22/research_summary_before.json'
    if backup.exists(): raise RuntimeError('Summary already updated')
    backup.write_bytes(before)
    summary=json.loads(before)
    preserved_insights=summary['insights'];old_scoreboard=summary['scoreboard'][:]
    entries=[dict(id='H25',name='Intraday stress reversal (description)',verdict='Unclear',
        result=f"Gross recovery was {g['mean_bps']:.1f} bps; this was descriptive only and no strategy was scored."),
        dict(id='H22',name='Volatility-scaled panic (added days)',verdict=h22['verdict'],
        result=f"Added {a['added_mornings_per_year']:.0f} mornings a year but failed at worse fills despite {a['net_bps']:.1f} bps per trade."),
        dict(id='H23',name='Panic-buy matched controls',verdict=h23['verdict'],
        result=f"Matched picks trailed small-gap controls by {abs(s['mean_bps']):.1f} bps, so the mechanism was not supported.")]
    summary['cycles']=entries+[x for x in summary['cycles'] if x['id'] not in ['H23','H22','H25']]
    name='Volatility-scaled panic (added days)'
    summary['scoreboard']=[x for x in summary['scoreboard'] if x['name']!=name]+[dict(name=name,net_pct=a['net_bps']/100,
        note=f"{a['added_mornings_per_year']:.1f} added mornings a year")]
    summary['scoreboard'].sort(key=lambda x:x['net_pct'],reverse=True)
    assert summary['insights']==preserved_insights
    assert [x for x in summary['scoreboard'] if x['name']!=name]==sorted([x for x in old_scoreboard if x['name']!=name],key=lambda x:x['net_pct'],reverse=True)
    dump(path,summary)
    tag='H23-H22-H25 completion'
    facts=(f"H23 Failed the mechanism screen: {s['mean_bps']:.2f}bps excess, day t{s['day_t']:.3f},48 pairs/44 dates; only one event pair and323 unknown tiers, so full-edge attribution remains unresolved. "
        f"H22 both settings Failed: primary k0.25 adds24.0 funded mornings/year,+27.20bps (t1.242), -23.05bps at0.5%-worse; k0.50 adds9.0/year,+57.32bps (t1.513),worse+17.82bps. "
        f"H25 descriptive gross{g['mean_bps']:.2f}bps,95% CI[{g['ci95_bps'][0]:.2f},{g['ci95_bps'][1]:.2f}],{h25['observed']} stock-days/{h25['days']} dates; {h25_outcome} No strategy verdict.")
    lesson='Lesson: matching coverage limits mechanism claims, and extra panic mornings must survive worse entry prices on their own.'
    append(ROOT/'RUNBOOK.md',f"### {tag} (2026-10-31; report2026-11-01)\n\n"+facts+'\n\n'+lesson+' Nine new causal tests;227 full-suite tests pass. Each guard used once; hashes/arithmetic/History reconcile. Two added-only Sessions saved,30 unknown dates each, all pre-existing rows unchanged. No protected/UI edits or fresh confirmation.',tag)
    append(ROOT/'docs/research/HANDOFF.md',f"## {tag}: review requested\n\n"+facts+'\n\nH25 missing closing-hour attempt produced no finite returns; completion used the registered official-close proxy from saved matches, preserving started.json and original files. MISTAKES rule23 added. Review docs/research/2026-11-01-h22-h25.md, the three RUNBOOK registrations, data/h23/h22/h25 frozen manifests and data/h22/saved_output_validation.json. Show two Sessions labels Panic, volatility-scaled, 10 years (k=0.25/0.50, added days only), preserve30 null-net dates each, explain original panic profits are excluded. H25 uses completed11:15-labelled candle at12:15 and next12:15 open. Current-tier attrition/event coverage prevent full H23 attribution; no claim that market rebound is proven.227 tests pass; protected sources/all pre-existing DB rows unchanged; no tuning/confirmation/live promotion/UI work. H16 remains the prospective execution prerequisite. '+lesson,tag)
    append(ROOT/'docs/research/QUEUE.md',f"## {tag} (2026-10-31)\n\n"+facts+'\n\n'+lesson+' H25 initial missing-path attempt and before-return official-close source correction preserved; no signal/matching rerun. No candidate promoted; keep H16 execution measurements first, H19 fresh test subject to its existing prerequisites, then existing queued research. Forced-flow/previous-day losers/euphoria/ORB not run. Report: docs/research/2026-11-01-h22-h25.md.',tag)
    append(ROOT/'LOG.md',f"### {tag} (2026-10-31; report2026-11-01)\n\n"+facts+'\n\nAdded offline scripts/h22_h25.py, scripts/h25_complete.py, saved-output verifier/report scripts, nine causal tests, data/h23/h22/h25 and the report. Three sequential registrations/guards; one run each, H22 two frozen k values. Two added-only Sessions, null prices retained; summary cycles prepended, insights and existing scoreboard rows preserved, one primary H22 row added in descending net order.227 tests pass; matching/timing/fill/fee/hash/History audits pass; no pre-existing DB row or protected/web file changed. No network, secrets, orders, arming, browser, server or remaining task process. '+lesson,tag)
    append(ROOT/'INSIGHTS.md',f"{tag}: H23 excess -8.17bps (t-0.115) in only48/427 matched picks; mechanism unsupported, whole-edge attribution unresolved. H22 added days fail t or frequency/execution.\nH25 gross {g['mean_bps']:.2f}bps is descriptive only; "+h25_outcome+' '+lesson,tag)
    dump(ROOT/'data/h22/publication_validation.json',dict(report_sha256=sha(report),cycles=[x['id'] for x in summary['cycles'][:3]],
        insights_unchanged=summary['insights']==preserved_insights,existing_scoreboard_rows_unchanged=True,
        scoreboard_sorted=True,scoreboard_primary_net_pct=a['net_bps']/100,unit_tests=227))
    print('Report, three summary cycles, one primary scoreboard row and five appended handoff/log files saved.')


if __name__=='__main__': main()
