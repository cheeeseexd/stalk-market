"""Load stored bars and build features for the whole universe."""
from __future__ import annotations

from typing import Dict, Optional

import pandas as pd

from stalk.config import BANK_INDEX, INDEX_SYMBOL, Settings, bar_interval
from stalk.data.store import Store
from stalk.research.features import build_universe, resample


def load_features(store: Store, s: Settings, end_ts: Optional[int] = None,
                  start_ts: Optional[int] = None) -> Dict[str, pd.DataFrame]:
    m, iv = s.bar_minutes, bar_interval(s.bar_minutes)
    step = (lambda df: df) if iv != "5minute" else (lambda df: resample(df, m))
    bars = {k: step(v) for k, v in store.load_universe(s.universe, iv, start_ts, end_ts).items()}
    daily = store.load_universe(s.universe, "day", None, end_ts)
    index = step(store.load_bars(INDEX_SYMBOL, iv, start_ts, end_ts))
    bank = step(store.load_bars(BANK_INDEX, iv, start_ts, end_ts))
    return build_universe(bars, daily, index, bank)
