"""Re-score every stored failed-strategy trade with zero brokerage (government charges and slippage unchanged). Descriptive only:
these are reused histories, so a variant that turns positive is a lead for a fresh test, never a pass."""
from __future__ import annotations

import glob
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stalk.market.costs import BROKERAGE_CAP, BROKERAGE_PCT, GST_PCT

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"h9": "News-driven gaps", "h12": "Sector panics", "h14": "Fade gaps with no news", "h18": "11:15 failed moves"}


def saved(qty, entry, exit_):
    leg = lambda v: np.minimum(BROKERAGE_CAP, v * BROKERAGE_PCT)
    return (leg(qty.abs() * entry) + leg(qty.abs() * exit_)) * (1 + GST_PCT)


def t(x):
    x = np.asarray(x, float)
    return float(x.mean() / x.std(ddof=1) * np.sqrt(len(x))) if len(x) > 2 and x.std(ddof=1) > 0 else None


rows = []
for path in sorted(glob.glob(str(ROOT / "data/h*/design_*_trades.csv"))):
    d = pd.read_csv(path)
    if not {"qty", "entry", "exit", "net"} <= set(d.columns) or d.empty:
        continue
    notional = d.qty.abs() * d.entry
    now = d.net / notional
    zero = (d.net + saved(d.qty, d.entry, d.exit)) / notional
    folder = Path(path).parent.name
    rows.append(dict(strategy=NAMES.get(folder, folder), variant=Path(path).stem.replace("design_", "").replace("_trades", ""), trades=len(d),
                     net_pct_now=float(now.mean() * 100), net_pct_zero=float(zero.mean() * 100), t_zero=t(zero)))
r = pd.read_pickle(ROOT / "data/results_trades_confirm.pkl")      # fractions, ₹10k positions: brokerage + GST was 0.0708%
saving = 2 * BROKERAGE_PCT * (1 + GST_PCT)
rows.append(dict(strategy="Results-day gaps", variant="240 unseen stocks", trades=len(r), net_pct_now=float(r.net.mean() * 100),
                 net_pct_zero=float((r.net + saving).mean() * 100), t_zero=t(r.net + saving)))
out = pd.DataFrame(rows).sort_values("net_pct_zero", ascending=False)
out.to_csv(ROOT / "data/zero_brokerage/rescore.csv", index=False)
best = out.groupby("strategy").head(1)
json.dump(dict(basis="Zero brokerage; STT, exchange, stamp, SEBI, GST on fees and assumed slippage unchanged. Reused history: descriptive only.",
               variants=len(out), turned_positive=int((out.net_pct_zero > 0).sum()), best_per_strategy=best.to_dict("records")),
          open(ROOT / "data/zero_brokerage/summary.json", "w"), indent=2)
pd.set_option("display.width", 200)
print(out.round(3).to_string(index=False))
