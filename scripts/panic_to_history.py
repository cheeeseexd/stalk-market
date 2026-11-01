"""Put the 10-year panic-buy account (scripts/panic_account.py → data/panic_journal.csv) on the History page as its own run.

  python scripts/panic_to_history.py

ETFs are left out (the live runner never picks them). Entry is shown at 09:20 and day-end exits at 15:10, as the live rule trades.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from stalk.config import BASE_DIR, IST  # noqa: E402
from stalk.data.store import Store  # noqa: E402

ETF = r"BEES$|IETF$|^MON100$|^AXISGOLD$|^GOLDADD$|ETF"
CAPITAL = 10000.0


def ts(day: str, hm: str) -> int:
    return int(datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())


def main() -> None:
    t = pd.read_csv(os.path.join(BASE_DIR, "data", "panic_journal.csv"))
    t = t[~t["symbol"].str.contains(ETF, regex=True)].sort_values(["day", "gap"]).reset_index(drop=True)
    st = Store()
    cid = f"p{int(time.time())}"
    acct = f"{cid}:panic_buy"
    equity, days, curve, peak, max_dd = CAPITAL, [], [], CAPITAL, 0.0
    for day, g in t.groupby("day"):
        start = equity
        equity += g["net"].sum()
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        d = {"day": day, "start_equity": round(start, 2), "end_equity": round(equity, 2), "gross": round(g["gross"].sum(), 2),
             "costs": round(g["charges"].sum(), 2), "net": round(g["net"].sum(), 2), "trades": len(g),
             "wins": int((g["net"] > 0).sum()), "losses": int((g["net"] <= 0).sum())}
        days.append(d)
        curve.append([day, round(equity, 2)])
        st.save_session(f"{acct}:{day}", "panic_buy", day, acct, {}, d, ended=True)
        for k, r in g.iterrows():
            st.save_trade({"id": f"{acct}:{day}:{r['symbol']}", "session_id": f"{acct}:{day}", "day": day, "strategy": "panic_buy",
                           "symbol": r["symbol"], "side": "long", "qty": int(r["qty"]), "entry_ts": ts(day, "09:20"), "entry_price": r["entry"],
                           "exit_ts": ts(day, "15:10"), "exit_price": r["exit"], "stop_initial": round(r["entry"] * 0.97, 2),
                           "gross": r["gross"], "costs": r["charges"], "net": r["net"],
                           "reason": "stop" if r["result"] == "stopped" else "flatten", "fee_version": "zerodha-2026", "broker": "backtest"})
    net = t["net"]
    per_day = pd.Series([d["net"] for d in days])
    yearly = t.groupby(t["day"].str[:4])["net"].sum()
    tax = float((yearly.clip(lower=0) * 0.30).sum())
    account = {"name": "panic_buy", "strategies": ["panic_buy"], "require_validated": False, "start": CAPITAL, "end": round(equity, 2),
               "gross": round(t["gross"].sum(), 2), "costs": round(t["charges"].sum(), 2), "net": round(net.sum(), 2),
               "tax": round(tax, 2), "after_tax": round(net.sum() - tax, 2), "trades": len(t),
               "green_days": int((per_day > 0).sum()), "red_days": int((per_day < 0).sum()), "flat_days": int((per_day == 0).sum()),
               "max_dd": round(max_dd, 2),
               "stats": {"n": len(t), "win": round(float((net > 0).mean()), 3), "net": round(net.sum(), 2),
                         "t": round(float(per_day.mean() / (per_day.std() / np.sqrt(len(per_day)))), 2)},
               "per_strategy": {}, "days": days, "curve": curve}
    summary = {"id": cid, "first_day": days[0]["day"], "last_day": days[-1]["day"], "days": len(days), "capital": CAPITAL,
               "accounts": {"panic_buy": account}, "weeks": [], "kind": "panic_buy",
               "label": "Panic buy, 10 years", "note": "NSE official daily prices, every liquid stock including delisted ones; panic mornings only"}
    st.save_campaign(cid, days[0]["day"], days[-1]["day"], {"bar_minutes": 5, "label": "Panic buy, 10 years"}, summary)
    print(f"Saved {cid}: {len(t)} trades on {len(days)} panic mornings, net ₹{net.sum():,.0f}, worst drawdown ₹{max_dd:,.0f}")


if __name__ == "__main__":
    main()
