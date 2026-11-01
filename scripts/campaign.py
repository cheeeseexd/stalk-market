"""Run a campaign from the command line and print the daily and per-strategy tables.

  python scripts/campaign.py                         # every session up to the holdout
  python scripts/campaign.py --until 2026-10-16 --capital 10000
  python scripts/campaign.py --open-holdout          # the one planned look at 19-30 Oct (log it in RUNBOOK.md)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import load_settings  # noqa: E402
from stalk.data.store import Store  # noqa: E402
from stalk.sim.campaign import run_campaign  # noqa: E402


def money(x: float) -> str:
    return f"{'−' if x < 0 else ''}₹{abs(x):,.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until")
    ap.add_argument("--start")
    ap.add_argument("--capital", type=float)
    ap.add_argument("--open-holdout", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a setting for this run only")
    a = ap.parse_args()
    s = load_settings()
    if a.capital:
        s = s.patched({"capital": a.capital})
    if a.set:
        s = s.patched(dict(kv.split("=", 1) for kv in a.set))
    last = [0.0]

    def progress(msg: str, frac: float) -> None:
        if not a.quiet and frac - last[0] >= 0.1:
            last[0] = frac
            print(f"  {frac:4.0%}  {msg}", flush=True)

    res = run_campaign(Store(), s, until=a.until, start=a.start, open_holdout=a.open_holdout, progress=progress)
    print(f"\nCampaign {res['id']}: {res['first_day']} → {res['last_day']} ({res['days']} sessions), "
          f"₹{res['capital']:,.0f} start, {res['trials']} configurations tried so far, {res['seconds']}s\n")
    print(f"{'account':24} {'net':>12} {'charges':>11} {'end':>12} {'trades':>6} {'mean R':>7} {'t':>6} {'green/red':>9} {'max DD':>10}")
    for name, acc in res["accounts"].items():
        st = acc["stats"]
        print(f"{name:24} {money(acc['net']):>12} {money(acc['costs']):>11} {money(acc['end']):>12} {acc['trades']:>6} "
              f"{st['mean_r']:>+7.3f} {st['t']:>6.2f} {acc['green_days']:>4}/{acc['red_days']:<4} {money(acc['max_dd']):>10}")
    v = res["accounts"].get("validated")
    if v:
        print("\nValidated account, day by day:")
        for r in v["days"]:
            print(f"  {r['day']}  {money(r['net']):>11}  trades {r['trades']}  charges {money(r['costs']):>9}  "
                  f"equity {money(r['end_equity']):>11}  active: {', '.join(r['validated']) or 'none'}")
    print("\nWeekly validation (as of each Monday):")
    for w in res["weeks"]:
        marks = "  ".join(f"{k}:{'✓' if x['validated'] else '·'}({x['main']['n']},{x['main']['mean_r']:+.2f}R)"
                          for k, x in w["strategies"].items())
        print(f"  {w['as_of']}  {marks}")
    if res["tm_calibration"]:
        print("\nTime Machine calibration (P(target first) vs realised):")
        for b in res["tm_calibration"]:
            print(f"  {b['bucket']:>9}  n {b['n']:>4}  predicted {b['predicted']:.0%}  realised {b['realised']:.0%}")


if __name__ == "__main__":
    main()
