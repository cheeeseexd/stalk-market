"""Settings for Stalk Market, persisted to data/settings.json.

Every money value is in rupees. R is the rupee amount risked on one trade.
Defaults are the ₹10,000 MIS account.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from datetime import timedelta, timezone
from typing import Any, Dict, List

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
DB_FILE = os.path.join(DATA_DIR, "stalk.db")
RUNBOOK_FILE = os.path.join(BASE_DIR, "RUNBOOK.md")

IST = timezone(timedelta(hours=5, minutes=30))
SESSION_OPEN_MIN = 9 * 60 + 15    # 09:15 IST
SESSION_CLOSE_MIN = 15 * 60 + 30  # 15:30 IST
BAR_MINUTES = 5
INTERVAL = "5minute"
HOURLY = "60minute"       # Yahoo serves about two years of hourly bars, used for long backtests


def bar_interval(bar_minutes: int) -> str:
    """Stored interval the features are built from: hourly bars are stored as such, 15-minute bars are resampled."""
    return HOURLY if bar_minutes == 60 else INTERVAL

# Liquid NIFTY 50 constituents that allow intraday (MIS) longs and shorts.
# This is today's list, so every backtest on it carries survivorship bias.
DEFAULT_UNIVERSE: List[str] = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "BHARTIARTL", "ITC",
    "LT", "KOTAKBANK", "AXISBANK", "HINDUNILVR", "BAJFINANCE", "MARUTI", "SUNPHARMA",
    "M&M", "TITAN", "ULTRACEMCO", "NTPC", "POWERGRID", "TATASTEEL", "HCLTECH", "ONGC",
    "COALINDIA", "ADANIENT", "ADANIPORTS", "JSWSTEEL", "WIPRO", "TECHM", "BAJAJFINSV",
    "GRASIM", "HINDALCO", "CIPLA", "DRREDDY", "EICHERMOT", "HEROMOTOCO", "BEL", "TRENT",
    "INDUSINDBK", "ETERNAL",
]
SECTORS: Dict[str, str] = {
    "HDFCBANK": "Banks", "ICICIBANK": "Banks", "SBIN": "Banks", "KOTAKBANK": "Banks", "AXISBANK": "Banks", "INDUSINDBK": "Banks",
    "BAJFINANCE": "Finance", "BAJAJFINSV": "Finance",
    "INFY": "IT", "TCS": "IT", "HCLTECH": "IT", "WIPRO": "IT", "TECHM": "IT",
    "MARUTI": "Auto", "M&M": "Auto", "EICHERMOT": "Auto", "HEROMOTOCO": "Auto",
    "SUNPHARMA": "Pharma", "CIPLA": "Pharma", "DRREDDY": "Pharma",
    "TATASTEEL": "Metals", "JSWSTEEL": "Metals", "HINDALCO": "Metals", "COALINDIA": "Metals",
    "RELIANCE": "Energy", "ONGC": "Energy", "ADANIENT": "Energy", "NTPC": "Power", "POWERGRID": "Power",
    "ITC": "FMCG", "HINDUNILVR": "FMCG", "TITAN": "Consumer", "TRENT": "Consumer", "ETERNAL": "Consumer",
    "LT": "Capital goods", "BEL": "Capital goods", "ADANIPORTS": "Capital goods",
    "ULTRACEMCO": "Cement", "GRASIM": "Cement", "BHARTIARTL": "Telecom",
}
VIX_SYMBOL = "INDIA VIX"
INDEX_SYMBOL = "NIFTY 50"
BANK_INDEX = "NIFTY BANK"
INDICES: List[str] = [INDEX_SYMBOL, BANK_INDEX]

# Research may not look at these days until the one planned look (see RUNBOOK.md).
HOLDOUT_START = "2026-10-19"


def hhmm_to_min(value: str) -> int:
    h, m = value.split(":")
    return int(h) * 60 + int(m)


@dataclass
class Settings:
    # Account (rupees)
    capital: float = 10000.0
    leverage: float = 1.0              # rule 4: no leverage (MIS allows up to 5×; RUNBOOK 2026-11-01 run B)
    max_notional: float = 10000.0
    risk_per_trade: float = 100.0
    loss_halt: float = 300.0           # stop for the day after this net loss
    max_trades_per_day: int = 1
    max_positions: int = 1
    max_consecutive_losses: int = 2    # in one session: done for the day
    lockout_after_losses: int = 0      # across sessions: N losses in a row starts a timeout (0 = off). Off since 2026-11-01:
                                       # losing streaks don't predict the next day; the pause only cut H31's profit (RUNBOOK H31)
    equity_floor: float = 5000.0       # account kill switch: at or below this equity, no new trades until a person reviews
    lockout_sessions: int = 3          # sessions without trading after that streak
    cooldown_min: int = 45             # no re-entry in the same stock for this long after an exit
    cost_gate_mult: float = 3.0        # target move must be at least this many times the round-trip charges
    tax_slab: float = 0.30             # intraday profit is speculative business income, taxed at slab

    # Trade shape
    stop_mode: str = "range"          # "range": stop = stop_range × average daily range (reachable intraday); "atr": stop_atr × ATR
    stop_range: float = 0.35
    stop_atr: float = 3.0            # 1.5 tested first; 3.0 halves charges in R (RUNBOOK 2026-10-31)
    target_r: float = 2.0
    breakeven_at_r: float = 99.0       # rule 2: the stop never moves (99R means the breakeven move never triggers)
    slippage_bps: float = 3.0
    bar_minutes: int = 15              # 5 or 15, built from stored 5-minute bars; 15 lost 62% less (RUNBOOK 2026-10-31)

    # Session timing (IST)
    entry_start: str = "10:00"         # learned in the October day-by-day run (JOURNAL.md, 6 Oct review)
    entry_end: str = "14:45"
    lunch_start: str = "11:30"         # no new entries in the midday lull
    lunch_end: str = "13:00"
    flatten_at: str = "15:10"

    # Strategy selection
    strategies: List[str] = field(default_factory=lambda: ["orb_breakout", "momentum_52w", "vwap_pullback", "gap_fade", "rsi_reversion", "supertrend", "pdh_breakout"])
    require_validated: bool = True     # only trade strategies that pass validation as of the day
    avoid_events: bool = True          # layer: skip stocks with a board meeting (results) the day before, of, or after
    breadth_filter: bool = True        # layer: longs only when most stocks are above VWAP, shorts only when most are below
    sector_filter: bool = True         # layer: trade only when the stock's sector peers move the same way today
    vix_filter: bool = False           # layer: no entries on days after a fear spike (VIX > 1.15 × its 20-day average)
    news_filter: bool = False          # layer: no trade against today's material company news; agreeing news counts in the ranking
    news_source: str = "keywords"      # how news is judged good or bad: "keywords" or "drex"
    mood_gate: bool = False            # read the market's mood first: trade only its direction, nothing when it's unclear
    mood_chop: bool = False            # mood is unclear when NIFTY crossed its day's average price 2+ times in the last 4 bars
    min_score: int = 0                 # only take setups where at least this many of index, breadth, sector agree (3 = all)
    min_gap_min: int = 0               # minutes between entries (take trades slowly)
    drex_veto: bool = False            # layer: Drex checks each queued setup against named factors and may block it
    rank_by_score: bool = True         # layer: when setups clash, pick the one with the most factors agreeing
    with_index: bool = True            # only trade in NIFTY's direction today (October run, 15 Oct review)

    # Drex (decision API). It votes and is scored; it never places or blocks orders on its own.
    drex_enabled: bool = False

    # Execution
    mode: str = "paper"                # paper | live
    kite_mcp_url: str = "https://mcp.kite.trade/mcp"
    static_ip_confirmed: bool = False  # SEBI: API orders must come from the IP registered with Zerodha
    poll_seconds: int = 15
    universe: List[str] = field(default_factory=lambda: list(DEFAULT_UNIVERSE))

    @property
    def flatten_min(self) -> int:
        return hhmm_to_min(self.flatten_at)

    def validate(self) -> None:
        for k in ("capital", "risk_per_trade", "loss_halt", "max_notional", "leverage"):
            if getattr(self, k) <= 0:
                raise ValueError(f"{k} must be positive")
        if self.risk_per_trade > self.capital * 0.05:
            raise ValueError("risk_per_trade above 5% of capital")
        if self.max_notional > self.capital * self.leverage:
            raise ValueError("max_notional above capital × leverage")
        if self.max_trades_per_day < 0 or self.max_positions < 1:
            raise ValueError("trade limits out of range")
        if self.news_source not in ("keywords", "drex"):
            raise ValueError("news_source must be keywords or drex")
        if self.stop_mode not in ("range", "atr") or self.stop_range <= 0:
            raise ValueError("stop_mode must be range or atr, with a positive stop_range")
        if self.stop_atr <= 0 or self.target_r <= 0:
            raise ValueError("stop and target must be positive")
        if self.bar_minutes not in (5, 15, 60):
            raise ValueError("bar_minutes must be 5, 15 or 60")
        if self.mode not in ("paper", "live"):
            raise ValueError("mode must be paper or live")
        if not (0 <= self.tax_slab < 0.5):
            raise ValueError("tax_slab out of range")
        order = [hhmm_to_min(x) for x in (self.entry_start, self.entry_end, self.flatten_at)]
        if not (hhmm_to_min("09:20") <= order[0] < order[1] < order[2] <= hhmm_to_min("15:20")):
            raise ValueError("session times out of order")
        if hhmm_to_min(self.lunch_start) > hhmm_to_min(self.lunch_end):
            raise ValueError("lunch window out of order")
        from stalk.research.strategies import STRATEGIES
        unknown = [s for s in self.strategies if s not in STRATEGIES]
        if unknown:
            raise ValueError("unknown strategy: " + ", ".join(unknown))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Settings":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})

    def patched(self, patch: Dict[str, Any]) -> "Settings":
        d = self.to_dict()
        names = {f.name for f in fields(self)}
        for k, v in patch.items():
            if k not in names:
                raise ValueError(f"unknown setting {k}")
            cur = d[k]
            if isinstance(cur, bool):
                v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                v = int(v)
            elif isinstance(cur, float):
                v = float(v)
            d[k] = v
        s = Settings.from_dict(d)
        s.validate()
        return s


def load_settings(path: str = SETTINGS_FILE) -> Settings:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                s = Settings.from_dict(json.load(f))
            s.validate()
            return s
        except Exception:
            pass
    return Settings()


def save_settings(s: Settings, path: str = SETTINGS_FILE) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(s.to_dict(), f, indent=2)
