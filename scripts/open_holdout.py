"""Open the held-back test (19–30 Oct 2026) once, after the prediction is written in RUNBOOK.md.

  python scripts/open_holdout.py

Refuses unless RUNBOOK.md has a "## Holdout prediction" section with text under it. After this, campaigns
can include the holdout (`scripts/campaign.py --open-holdout`) and its days can be replayed. There is no
way back: once seen, those days are no longer a clean test.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stalk.agent import HOLDOUT_FILE  # noqa: E402
from stalk.config import HOLDOUT_START, RUNBOOK_FILE  # noqa: E402


def main() -> None:
    if os.path.exists(HOLDOUT_FILE):
        print("The holdout is already open.")
        return
    text = open(RUNBOOK_FILE, encoding="utf-8").read() if os.path.exists(RUNBOOK_FILE) else ""
    body = text.split("## Holdout prediction", 1)[1].strip() if "## Holdout prediction" in text else ""
    if len(body) < 40 or body.startswith("(Write here"):
        sys.exit("Write the prediction under '## Holdout prediction' in RUNBOOK.md first.")
    with open(HOLDOUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"opened_at": int(time.time()), "holdout_start": HOLDOUT_START}, f)
    print(f"Holdout from {HOLDOUT_START} is open. Run: python scripts/campaign.py --open-holdout")


if __name__ == "__main__":
    main()
