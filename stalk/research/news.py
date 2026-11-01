"""News layer: NSE corporate announcements, sorted into routine vs material, and good vs bad for the share price.

Every announcement carries its exchange timestamp, so a backtest only ever sees news published before a signal.
Two ways to judge an announcement, kept side by side so they can be compared:
  keywords  transparent rules on the category and text
  drex      Drex reads the text and gives the probability it is good news (stored in news.drex_p)
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from stalk.config import IST
from stalk.data.store import Store

ROUTINE = {
    "Analysts/Institutional Investor Meet/Con. Call Updates", "Copy of Newspaper Publication", "ESOP/ESOS/ESPS", "Trading Window",
    "Shareholders meeting", "Certificate under SEBI (Depositories and Participants) Regulations, 2018", "Investor Presentation",
    "Allotment of Securities", "Record Date", "Incorporation", "Monitoring Agency Report", "Appointment", "Change in Director(s)",
    "Cessation", "Retirement", "Change in Auditors", "Disclosure under SEBI Takeover Regulations", "Book Closure",
    "Loss of Share Certificates", "Duplicate Share Certificate", "Statement of deviation(s) or variation(s) under Reg. 32",
    "Compliances-Certificate under Reg. 74 (5) of SEBI (DP) Regulations, 2018", "Investor Complaints", "Closure of Trading Window",
}
ALWAYS_BAD = {"Action(s) taken or orders passed", "Pendency of Litigation(s)/dispute(s) or the outcome impacting the Company",
              "Resignation", "Resignation of Director/KMP/SMP"}
ALWAYS_GOOD = {"Bagging/Receiving of orders/contracts"}
GOOD = re.compile(r"\b(order[s]?\b(?! passed)|contract|wins?\b|won\b|bag|award|selects?|selected|launch|partner|approv|upgrade[sd]?|"
                  r"record\b|expan|commission(ed|ing)|buyback|bonus|capacity|milestone|tie-?up|collaborat)", re.I)
BAD = re.compile(r"(penalt|\bfine[sd]?\b|show cause|demand notice|tax demand|\bdemand\b|litigat|dispute|resign|warning letter|"
                 r"observation|downgrade|fraud|default|suspen|search|raid|closure|shut ?down|strike|recall|import alert|"
                 r"\bloss\b|impair|investigation|probe|summon)", re.I)
MATERIAL_TEXT = re.compile(r"(press release|update|statement)", re.I)


def classify(category: str, text: str) -> Tuple[bool, int]:
    """(material, sentiment) with sentiment +1 good, −1 bad, 0 unclear. Results are left to the events layer (0)."""
    if category in ROUTINE:
        return False, 0
    if category in ALWAYS_BAD:
        return True, -1
    if category in ALWAYS_GOOD:
        return True, 1
    if category in ("Outcome of Board Meeting", "Financial Result Updates", "Integrated Filing- Financial"):
        return True, 0
    good, bad = bool(GOOD.search(text or "")), bool(BAD.search(text or ""))
    if good and not bad:
        return True, 1
    if bad and not good:
        return True, -1
    material = category not in ("General Updates", "Updates") or bool(MATERIAL_TEXT.search(text or ""))
    return material, 0


def session_news(store: Store, day: str, source: str = "keywords") -> Dict[str, List[Tuple[int, int, str]]]:
    """Material news per stock from the previous session's close (15:30, the last weekday before `day`) through `day`'s close.
    Each item is (timestamp, sentiment, text). With source="drex", sentiment comes from Drex where it has answered."""
    d = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST)
    prev = d - timedelta(days=1)
    while prev.weekday() >= 5:
        prev -= timedelta(days=1)
    lo = int(prev.replace(hour=15, minute=30).timestamp())
    hi = int(d.replace(hour=15, minute=30).timestamp())
    rows = store._rows("SELECT symbol, ts, category, text, drex_p FROM news WHERE ts > ? AND ts <= ? ORDER BY ts", (lo, hi))
    out: Dict[str, List[Tuple[int, int, str]]] = {}
    for r in rows:
        material, sent = classify(r["category"], r["text"])
        if not material:
            continue
        if source == "drex" and r["drex_p"] is not None:
            sent = 1 if r["drex_p"] >= 0.65 else -1 if r["drex_p"] <= 0.35 else 0
        out.setdefault(r["symbol"], []).append((int(r["ts"]), sent, (r["text"] or r["category"])[:140]))
    return out


def news_view(items: Optional[List[Tuple[int, int, str]]], until_ts: int) -> Tuple[int, List[str]]:
    """Net sentiment and headlines of the items published by until_ts."""
    seen = [x for x in (items or []) if x[0] <= until_ts]
    return sum(x[1] for x in seen), [x[2] for x in seen if x[1]]
