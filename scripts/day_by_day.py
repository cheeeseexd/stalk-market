"""Trade a stretch of past sessions one day at a time, as if live, and write a daily journal.

  python scripts/day_by_day.py --start 2026-10-01 --end 2026-10-30
  python scripts/day_by_day.py --start 2026-10-26 --end 2026-10-30 --set stop_range=0.4

The journal is written to JOURNAL.md (one section per run). A failed check stops the run and says why.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, load_settings  # noqa: E402
from stalk.data.store import Store  # noqa: E402
from stalk.sim.daybyday import CheckFailed, run_days  # noqa: E402

JOURNAL = os.path.join(BASE_DIR, "JOURNAL.md")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--label", default="", help="short name for this run, shown on the Backtest page")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    s = load_settings()
    if a.set:
        s = s.patched(dict(kv.split("=", 1) for kv in a.set))
    try:
        res = run_days(Store(), s, a.start, a.end, label=a.label, say=(lambda m: None) if a.quiet else print)
    except CheckFailed as e:
        sys.exit(f"CHECK FAILED: {e}")
    acc = next(iter(res["summary"]["accounts"].values()))
    head = (f"## Run {res['summary']['id']}{' (' + a.label + ')' if a.label else ''}: {a.start} → {a.end}, {s.bar_minutes}-min bars, "
            f"stop {s.stop_range} × daily range ({s.stop_mode}), target {s.target_r}R, written {datetime.now():%d %b %Y %H:%M}\n\n"
            f"Net ₹{acc['net']:,.2f} on ₹{s.capital:,.0f} · {acc['trades']} trades · win rate "
            f"{acc['stats']['win']:.0%} · {acc['green_days']} green / {acc['red_days']} red days · charges ₹{acc['costs']:,.2f}\n")
    text = head + "\n" + "\n\n".join(res["journal"]) + "\n"
    intro = ("# Daily trading journal\n\nOne section per run, newest first. Each day lists every trade with its stop and target, "
             "why it ended, the checks that passed, and any review after a 3-day streak.\n\n")
    old = open(JOURNAL, encoding="utf-8").read() if os.path.exists(JOURNAL) else ""
    runs = old[old.find("## Run"):] if "## Run" in old else ""
    with open(JOURNAL, "w", encoding="utf-8") as f:
        f.write(intro + text + "\n" + runs)
    print("\n" + head)
    print(f"Final rules: with_index={res['final_settings']['with_index']}, benched={ {k: v for k, v in res['bench'].items() if v > 0} }, "
          f"priority={res['priority']}")


if __name__ == "__main__":
    main()
