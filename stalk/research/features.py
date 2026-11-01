"""Causal per-bar features for one stock.

Every value on the row for bar t uses only bars that closed at or before t (tests enforce this).
Daily bars supply the 52-week high; the indices supply the NIFTY / NIFTY BANK divergence flag.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import pandas as pd

from stalk.config import BAR_MINUTES, IST, SESSION_OPEN_MIN

OR_WINDOWS = (15, 30, 45)   # opening-range lengths in minutes
RVOL_DAYS = 20
DIV_MIN = 0.0015            # both indices must have moved this much, in opposite directions


def bar_size(df: pd.DataFrame) -> int:
    """Bar length in minutes, read from the data (5 for stored bars, 15 after resampling)."""
    d = np.diff(df["ts"].to_numpy()) if len(df) > 1 else np.array([])
    d = d[(d > 0) & (d <= 3600)]
    return int(np.median(d) // 60) if len(d) else BAR_MINUTES


def resample(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Combine stored 5-minute bars into `minutes`-long bars aligned to 09:15. Incomplete bars are dropped,
    so a bar that is still forming during the session never reaches a strategy."""
    if df.empty or minutes == BAR_MINUTES:
        return df
    dt = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    start = dt.dt.normalize() + pd.Timedelta(minutes=SESSION_OPEN_MIN)
    slot = ((dt - start).dt.total_seconds() // (minutes * 60)).astype(int)
    key = start + pd.to_timedelta(slot * minutes, unit="m")
    g = df.assign(_k=key).groupby("_k", sort=True)
    out = g.agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum"), n=("ts", "size"))
    out = out[out["n"] == minutes // BAR_MINUTES]
    return pd.DataFrame({"ts": (out.index.astype("int64") // 10**9).astype(int), "o": out["o"].values, "h": out["h"].values,
                         "l": out["l"].values, "c": out["c"].values, "v": out["v"].values})


def session_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values("ts").reset_index(drop=True).copy()
    dt = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(IST)
    df["day"] = dt.dt.strftime("%Y-%m-%d")
    df["minute"] = (dt.dt.hour * 60 + dt.dt.minute).astype(int)
    df["end_minute"] = df["minute"] + bar_size(df)
    return df


def _day_return(df: pd.DataFrame) -> pd.Series:
    return df["c"] / df.groupby("day")["o"].transform("first") - 1.0


def index_frame(index: Optional[pd.DataFrame], bank: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if index is None or index.empty:
        return None
    a = session_columns(index)
    out = pd.DataFrame({"ts": a["ts"], "idx_rday": _day_return(a)})
    # Choppiness: how often NIFTY crossed its time-weighted average price for the day in the last 4 bars.
    # (Yahoo's index data has no volume, so a true VWAP isn't available; the day's TWAP stands in.)
    tp = (a["h"] + a["l"] + a["c"]) / 3.0
    twap = tp.groupby(a["day"]).cumsum() / tp.groupby(a["day"]).cumcount().add(1)
    side = np.sign(a["c"] - twap)
    crossed = (side != side.groupby(a["day"]).shift(1)) & side.groupby(a["day"]).shift(1).notna()
    out["idx_cross4"] = crossed.astype(int).groupby(a["day"]).rolling(4, min_periods=1).sum().reset_index(level=0, drop=True)
    if bank is not None and not bank.empty:
        b = session_columns(bank)
        out = out.merge(pd.DataFrame({"ts": b["ts"], "bank_rday": _day_return(b)}), on="ts", how="left")
    else:
        out["bank_rday"] = np.nan
    return out


def build_features(stock: pd.DataFrame, daily: Optional[pd.DataFrame] = None,
                   idx: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    f = session_columns(stock)
    o, h, l, c, v = (f[k].astype(float) for k in ("o", "h", "l", "c", "v"))
    day = f["day"]

    prev_c = c.shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    f["atr"] = tr.rolling(14, min_periods=14).mean()

    tp = (h + l + c) / 3.0
    cum_v = v.groupby(day).cumsum()
    f["vwap"] = (tp * v).groupby(day).cumsum() / cum_v.replace(0, np.nan)
    f["day_open"] = o.groupby(day).transform("first")
    f["rday"] = c / f["day_open"] - 1.0
    f["c_prev"] = c.groupby(day).shift(1)        # previous bar's close, same day only
    f["ema9"] = c.ewm(span=9, adjust=False).mean()
    f["ema21"] = c.ewm(span=21, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    f["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))

    # Previous close, opening gap, and the gap in units of recent daily volatility.
    closes = c.groupby(day).last()
    prev_close = closes.shift(1)
    sd = closes.pct_change().rolling(20, min_periods=10).std().shift(1)
    f["prev_close"] = day.map(prev_close)
    day_range = h.groupby(day).max() - l.groupby(day).min()
    f["adr"] = day.map(day_range.rolling(10, min_periods=5).mean().shift(1))
    f["pdh"] = day.map(h.groupby(day).max().shift(1))
    f["pdl"] = day.map(l.groupby(day).min().shift(1))
    gap = f["day_open"] / f["prev_close"] - 1.0
    f["gap"] = gap
    f["gap_sigma"] = gap / day.map(sd)

    # Relative volume: today's cumulative volume vs the average at the same time on the prior 20 days.
    f["cum_v"] = cum_v
    piv = pd.DataFrame({"day": day, "minute": f["minute"], "cv": cum_v}).pivot_table(
        index="day", columns="minute", values="cv", aggfunc="last")
    base = piv.rolling(RVOL_DAYS, min_periods=5).mean().shift(1)
    base_long = base.stack().rename("cv_base").reset_index()
    f = f.merge(base_long, on=["day", "minute"], how="left")
    f["rvol"] = f["cum_v"] / f["cv_base"].replace(0, np.nan)

    # Opening ranges. A row only sees the range once the range's last bar has closed.
    for w in OR_WINDOWS:
        end = SESSION_OPEN_MIN + w
        inside = f["minute"] < end
        hi = f["h"].where(inside).groupby(f["day"]).transform("max")
        lo = f["l"].where(inside).groupby(f["day"]).transform("min")
        known = f["end_minute"] >= end
        f[f"or_hi_{w}"] = hi.where(known)
        f[f"or_lo_{w}"] = lo.where(known)

    # 52-week high from daily bars, excluding today.
    f["hi52"] = np.nan
    if daily is not None and not daily.empty:
        d = session_columns(daily)
        hi = d.set_index("day")["h"].rolling(250, min_periods=120).max().shift(1)
        f["hi52"] = f["day"].map(hi)

    if idx is not None:
        f = f.merge(idx, on="ts", how="left")
        a, b = f["idx_rday"], f["bank_rday"]
        f["idx_div"] = (np.sign(a) != np.sign(b)) & (np.minimum(a.abs(), b.abs()) > DIV_MIN)
        f["idx_div"] = f["idx_div"].fillna(False)
    else:
        f["idx_rday"] = np.nan
        f["bank_rday"] = np.nan
        f["idx_div"] = False

    f["warm"] = f["atr"].notna() & f["vwap"].notna() & f["adr"].notna()
    return f.sort_values("ts").reset_index(drop=True)


def build_universe(bars: Dict[str, pd.DataFrame], daily: Dict[str, pd.DataFrame],
                   index: Optional[pd.DataFrame], bank: Optional[pd.DataFrame]) -> Dict[str, pd.DataFrame]:
    idx = index_frame(index, bank)
    return {s: build_features(df, daily.get(s), idx) for s, df in bars.items() if len(df) > 100}
