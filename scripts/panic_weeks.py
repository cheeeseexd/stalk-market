"""All stored weeks and ten-year ISO-week reconstruction; paper/reused history only.

Daily proxy cannot reproduce 09:20 fills, first-bar locks, or intraday stop timing.
The -0.3% rule was already examined in H7d: these are not new independent trials.
ETFs are removed AFTER top-two selection, matching the frozen journal exclusion.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.panic_final import account, fetch_day, nifty, pool
from stalk.config import BASE_DIR
from stalk.data.store import Store
from stalk.sim import panic, panic_replay as replay

OUT = Path(BASE_DIR) / "data" / "panic_weeks"
ETF = r"BEES$|IETF$|^MON100$|^AXISGOLD$|^GOLDADD$|ETF"


def stocks(t):
    return t[~t.symbol.str.contains(ETF, regex=True, na=False)].copy()


def weekly(t):
    t = t.copy()
    iso = pd.to_datetime(t.day).dt.isocalendar()
    t["week"] = iso.year.astype(str) + "-W" + iso.week.astype(str).str.zfill(2)
    return t.groupby("week").agg(trades=("net", "size"), net=("net", "sum"),
                                  won=("net", lambda x: (x > 0).sum()), lost=("net", lambda x: (x <= 0).sum()))


def stats(w, calendar_weeks):
    x = w.net
    return {"active_weeks": len(w), "calendar_weeks": calendar_weeks,
            "active_share": len(w) / calendar_weeks, "positive_share_active": float((x > 0).mean()),
            "positive_weeks": int((x > 0).sum()), "negative_weeks": int((x < 0).sum()),
            "flat_active_weeks": int((x == 0).sum()), "no_trade_weeks": calendar_weeks - len(w),
            "mean_active": float(x.mean()), "median_active": float(x.median()),
            "worst": float(x.min()), "worst_week": str(x.idxmin()),
            "best": float(x.max()), "best_week": str(x.idxmax()),
            "net": float(x.sum()), "mean_all": float(x.sum() / calendar_weeks), "trades": int(w.trades.sum())}


def write_report(summary, recent):
    lines = ["# All panic-buy weeks, 2026-10-31 — H15 corrected", "",
             "Reused history, paper only; this is a price-data repair, not new confirmation.", "",
             "## Every recent stored week", "",
             "Official same-day bhavcopy OPEN/PREVCLOSE drive equity gaps. Reject either Yahoo component >1% from its official reference; missing official rows use an explicitly logged Yahoo fallback. These are conservative consistency checks, not verified fills.", "",
             "| Week starting | Frozen trades | Net Rs | W/L | -0.3% trades | Net Rs | W/L |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for week in sorted(recent.week.unique()):
        a, b = [recent[(recent.week == week) & (recent.rule == label)].iloc[0] for label in ('frozen', 'loose')]
        lines.append(f"| {week} | {a.trades} | {a.net:+.2f} | {a.won}/{a.lost} | {b.trades} | {b.net:+.2f} | {b.won}/{b.lost} |")
    lines += ["", f"Totals: frozen {summary['recent']['frozen']}; loose {summary['recent']['loose']}.", "",
              "The same 24 w/v History ids are replaced, including removal of stale trades. Explicit index-threshold arguments also fix the old rerun script's Python default-argument bug; simply assigning panic.INDEX_GAP did not change market_qualifies' bound default. Archived baseline reconstructed with explicit thresholds matches every previously saved run.", "",
              "## Ten-year weekly distribution (original, not repaired)", "",
              "2016-11-01 to 2026-10-31, 522 intersecting ISO weeks. Original daily proxy and costs retained for audit; NOT certified corporate-action clean.", "",
              "| Rule | Trades | Active weeks | Positive / negative | Positive share | Mean active Rs | Median Rs | Original net Rs |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for label in ('frozen', 'loose'):
        s = summary[label]
        lines.append(f"| {label} | {s['trades']} | {s['active_weeks']} | {s['positive_weeks']}/{s['negative_weeks']} | {s['positive_share_active']:.1%} | {s['mean_active']:.2f} | {s['median_active']:.2f} | {s['net']:.2f} |")
    lines += ["", "## H15 interpretation and limits", "",
              "Official bhavcopy PREVCLOSE is not guaranteed to be corporate-action adjusted: TDPOWERSYS on 2026-08-24 retains the prior raw Rs1,534.80 close against a Rs775 open. Thus H7c/H7d avoid the Yahoo scale-mixing bug but remain exposed to false split/bonus gaps in their rankings. H15 finds dated announcement matches among original ten-year selections; see [the audit](../../docs/research/2026-10-31-h15-audit.md). The original ten-year numbers above are retained, not endorsed as clean evidence. A separately registered action-neutral rerun is required.", "",
              "NIFTY uses its existing Yahoo five-minute open/previous last bar. Comparing with cached daily index closes changes no strict/loose gate in these sessions; those daily data are also Yahoo, not an independent official-index certification. Coverage is measured against the stored index sessions. Historical MIS, quotes/depth, actual fills, event vintage and survivorship limitations remain. A <=1% agreement is a tolerance check, not proof that every economic gap is real; fallback days are explicitly unverified. H14 returns/hold-out were not rescored.", "",
              "Keep both authorized forward paper versions; no real-money promotion. Next cycle: repair and revalidate ten-year corporate-action denominators before H11 diversification."]
    (OUT / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    n = nifty()
    selected, coverage, strict_selected = [], [], []
    for day, row in n[n.gap < -0.003].iterrows():
        p, status, invalid = pool(day, row.prev_day)
        if p is None:
            _, errors = fetch_day((day, row.prev_day))
            p, status, invalid = pool(day, row.prev_day)
            print(f"Fetch {day}: {errors or status}", flush=True)
        coverage.append({"day": day, "status": status, "invalid": invalid})
        if p is not None:
            picks = p[p.gap < -0.02].head(2)
            selected.append(picks)
            if row.gap < -0.005:
                strict_selected.append(picks)
    pd.DataFrame(coverage).to_csv(OUT / "coverage.csv", index=False)
    if any(c["status"] != "ok" for c in coverage):
        raise RuntimeError("Incomplete coverage: no complete-population verdict allowed")
    journal = stocks(pd.read_csv(Path(BASE_DIR) / "data" / "panic_journal.csv"))
    loose = stocks(account(pd.concat(selected, ignore_index=True)))
    loose["net"] = loose.net.round(2)
    loose.to_csv(OUT / "loose_trades.csv", index=False)
    reconstructed = stocks(account(pd.concat(strict_selected, ignore_index=True)))
    check = journal.merge(reconstructed, on=["day", "symbol"], how="outer", suffixes=("_journal", "_reconstructed"), indicator=True)
    if not (check._merge == "both").all() or (check.net_journal - check.net_reconstructed).abs().max() > .0051:
        raise RuntimeError("Strict daily reconstruction does not reconcile to journal")
    iso = pd.date_range("2016-11-01", "2026-10-31").isocalendar()
    calendar_weeks = len(iso[["year", "week"]].drop_duplicates())
    summary = {"notice": "Reused H7d reconstruction, not independent evidence. Daily proxy differs from 09:20 replay. ETFs filtered after picks; no replacement.",
               "coverage": {"eligible_days": len(coverage), "covered_days": len(coverage), "strict_reconciliation_max": float((check.net_journal-check.net_reconstructed).abs().max())}}
    for label, t in (("frozen", journal), ("loose", loose)):
        w = weekly(t)
        w.to_csv(OUT / f"{label}_10y_weeks.csv")
        summary[label] = stats(w, calendar_weeks)
    grouped, tiers, results = replay.load_cache()
    weeks, recent, store = replay.weeks_available(grouped), [], Store()
    recent_trades, scans = [], []
    try:
        for label, gap, suffix, prefix in (("frozen", -.005, "", "w"), ("loose", -.003, " −0.3%", "v")):
            for week in weeks:
                frames = replay.build_week(grouped, tiers, results, week, index_gap=gap)
                last = frames[-1]
                recent_trades.extend({"rule": label, "week": week, **t} for t in last["trades"])
                scans.extend({"rule": label, "day": f["day"], **f["funnel"]} for f in frames if f["clock"] == "09:20" and f["funnel"] is not None)
                cid = replay.save_to_history(store, frames, week, label_suffix=suffix, prefix=prefix)
                recent.append({"rule": label, "week": week, "trades": len(last["trades"]), "net": last["realized"],
                               "won": last["won"], "lost": last["lost"], "history_id": cid})
                print(f"{label} {week}: {len(last['trades'])} trades, {last['realized']:+.2f}, {last['won']}/{last['lost']}", flush=True)
    finally:
        store._conn.close()
    r = pd.DataFrame(recent)
    r.to_csv(OUT / "recent_weeks.csv", index=False)
    pd.DataFrame(recent_trades).to_csv(OUT / "recent_trades.csv", index=False)
    pd.DataFrame(scans).to_csv(OUT / "recent_scans.csv", index=False)
    summary["recent"] = {label: {"weeks": len(g), "trades": int(g.trades.sum()), "net": round(float(g.net.sum()), 2),
        "positive": int((g.net > 0).sum()), "negative": int((g.net < 0).sum()), "flat": int((g.net == 0).sum()),
        "won": int(g.won.sum()), "lost": int(g.lost.sum())} for label, g in r.groupby("rule")}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_report(summary, r)
    print(json.dumps(summary, indent=2), flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    main()
