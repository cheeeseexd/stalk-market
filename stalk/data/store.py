"""SQLite persistence: bars, sessions, trades, logs, campaigns and the trials ledger."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from stalk.config import DB_FILE, IST

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, interval TEXT NOT NULL, ts INTEGER NOT NULL,
    o REAL NOT NULL, h REAL NOT NULL, l REAL NOT NULL, c REAL NOT NULL, v REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, interval, ts)
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, day TEXT NOT NULL, account TEXT,
    started_at INTEGER NOT NULL, ended_at INTEGER, settings TEXT, summary TEXT, state TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, day TEXT, strategy TEXT, symbol TEXT NOT NULL,
    side TEXT NOT NULL, qty INTEGER NOT NULL, entry_ts INTEGER, entry_price REAL, exit_ts INTEGER,
    exit_price REAL, stop_initial REAL, target REAL, gross REAL, costs REAL, net REAL, r REAL,
    reason TEXT, tm_target REAL, tm_stop REAL, tm_open REAL, drex_p REAL, mfe_r REAL, mae_r REAL,
    fee_version TEXT, broker TEXT
);
CREATE INDEX IF NOT EXISTS trades_session ON trades(session_id);
CREATE TABLE IF NOT EXISTS logs (
    session_id TEXT NOT NULL, ts INTEGER NOT NULL, kind TEXT, text TEXT
);
CREATE INDEX IF NOT EXISTS logs_session ON logs(session_id, ts);
CREATE TABLE IF NOT EXISTS campaigns (
    id TEXT PRIMARY KEY, created_at INTEGER NOT NULL, first_day TEXT, last_day TEXT,
    settings TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS events (
    symbol TEXT, day TEXT, purpose TEXT, announced_ts INTEGER, detail TEXT, PRIMARY KEY (symbol, day, purpose)
);
CREATE TABLE IF NOT EXISTS news (
    symbol TEXT, ts INTEGER, category TEXT, text TEXT, seq TEXT PRIMARY KEY, drex_p REAL
);
CREATE INDEX IF NOT EXISTS news_ts ON news(ts);
CREATE TABLE IF NOT EXISTS trials (
    ts INTEGER NOT NULL, strategy TEXT, params TEXT, as_of TEXT, n INTEGER, mean_r REAL, t REAL, note TEXT
);
"""


class Store:
    def __init__(self, path: str = DB_FILE):
        if path != ":memory:":
            os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _rows(self, q: str, args: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def _exec(self, q: str, args: Sequence[Any] = ()) -> None:
        with self._lock:
            self._conn.execute(q, args)
            self._conn.commit()

    # ------------------------------------------------------------------ bars
    def upsert_bars(self, symbol: str, interval: str, rows: Iterable[Sequence[float]], source: str) -> int:
        payload = [(symbol, interval, int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]),
                    float(r[5] or 0.0), source) for r in rows]
        if not payload:
            return 0
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO bars (symbol, interval, ts, o, h, l, c, v, source) VALUES (?,?,?,?,?,?,?,?,?)",
                payload)
            self._conn.commit()
        return len(payload)

    def load_bars(self, symbol: str, interval: str, start_ts: Optional[int] = None,
                  end_ts: Optional[int] = None) -> pd.DataFrame:
        q = "SELECT ts, o, h, l, c, v FROM bars WHERE symbol=? AND interval=?"
        args: List[Any] = [symbol, interval]
        if start_ts is not None:
            q += " AND ts>=?"
            args.append(int(start_ts))
        if end_ts is not None:
            q += " AND ts<=?"
            args.append(int(end_ts))
        q += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return pd.DataFrame([tuple(r) for r in rows], columns=["ts", "o", "h", "l", "c", "v"])

    def load_universe(self, symbols: Sequence[str], interval: str, start_ts: Optional[int] = None,
                      end_ts: Optional[int] = None) -> Dict[str, pd.DataFrame]:
        out = {}
        for s in symbols:
            df = self.load_bars(s, interval, start_ts, end_ts)
            if not df.empty:
                out[s] = df
        return out

    def last_bar_ts(self, symbol: str, interval: str) -> Optional[int]:
        with self._lock:
            row = self._conn.execute("SELECT MAX(ts) FROM bars WHERE symbol=? AND interval=?",
                                     (symbol, interval)).fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def trading_days(self, symbol: str, interval: str) -> List[str]:
        """Days with a nearly full session: 60 of 75 five-minute bars, or 5 of 6 hourly bars."""
        need = 5 if interval == "60minute" else 60
        with self._lock:
            rows = self._conn.execute("SELECT ts FROM bars WHERE symbol=? AND interval=?", (symbol, interval)).fetchall()
        counts: Dict[str, int] = {}
        for (ts,) in rows:
            d = datetime.fromtimestamp(ts, IST).strftime("%Y-%m-%d")
            counts[d] = counts.get(d, 0) + 1
        return sorted(d for d, n in counts.items() if n >= need)

    def coverage(self, interval: str) -> List[Dict[str, Any]]:
        return self._rows("""SELECT symbol, COUNT(*) AS n, MIN(ts) AS first, MAX(ts) AS last,
                                    GROUP_CONCAT(DISTINCT source) AS sources
                             FROM bars WHERE interval=? GROUP BY symbol ORDER BY symbol""", (interval,))

    # -------------------------------------------------------------- sessions
    def save_session(self, sid: str, kind: str, day: str, account: str, settings: Dict[str, Any],
                     summary: Optional[Dict[str, Any]] = None, state: Optional[Dict[str, Any]] = None,
                     ended: bool = False) -> None:
        now = int(time.time())
        self._exec("""INSERT INTO sessions (id, kind, day, account, started_at, ended_at, settings, summary, state)
                      VALUES (?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(id) DO UPDATE SET ended_at=excluded.ended_at, summary=excluded.summary,
                                                    state=excluded.state""",
                   (sid, kind, day, account, now, now if ended else None, json.dumps(settings),
                    json.dumps(summary) if summary is not None else None,
                    json.dumps(state) if state is not None else None))

    def session(self, sid: str) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM sessions WHERE id=?", (sid,))
        return _decode_session(rows[0]) if rows else None

    def sessions(self, kind: Optional[str] = None, account: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
        q, args = "SELECT * FROM sessions WHERE 1=1", []
        if kind:
            q += " AND kind=?"
            args.append(kind)
        if account:
            q += " AND account=?"
            args.append(account)
        q += " ORDER BY day DESC, started_at DESC LIMIT ?"
        args.append(limit)
        return [_decode_session(r) for r in self._rows(q, args)]

    def delete_sessions(self, account: str) -> None:
        with self._lock:
            ids = [r[0] for r in self._conn.execute("SELECT id FROM sessions WHERE account=?", (account,)).fetchall()]
            for sid in ids:
                self._conn.execute("DELETE FROM trades WHERE session_id=?", (sid,))
                self._conn.execute("DELETE FROM logs WHERE session_id=?", (sid,))
            self._conn.execute("DELETE FROM sessions WHERE account=?", (account,))
            self._conn.commit()

    # ---------------------------------------------------------------- trades
    def save_trade(self, t: Dict[str, Any]) -> None:
        cols = ["id", "session_id", "day", "strategy", "symbol", "side", "qty", "entry_ts", "entry_price",
                "exit_ts", "exit_price", "stop_initial", "target", "gross", "costs", "net", "r", "reason",
                "tm_target", "tm_stop", "tm_open", "drex_p", "mfe_r", "mae_r", "fee_version", "broker"]
        self._exec(f"INSERT OR REPLACE INTO trades ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   [t.get(c) for c in cols])

    def trades(self, session_id: Optional[str] = None, account_prefix: Optional[str] = None,
               limit: int = 5000) -> List[Dict[str, Any]]:
        if session_id:
            return self._rows("SELECT * FROM trades WHERE session_id=? ORDER BY entry_ts", (session_id,))
        if account_prefix:
            # Session ids start with their account, so the id range lets SQLite use the trades_session index
            return self._rows("""SELECT t.* FROM trades t JOIN sessions s ON s.id=t.session_id
                                 WHERE t.session_id >= ? AND t.session_id < ? AND s.account LIKE ?
                                 ORDER BY t.entry_ts DESC LIMIT ?""",
                              (account_prefix, account_prefix + "￿", account_prefix + "%", limit))
        return self._rows("SELECT * FROM trades ORDER BY entry_ts DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ logs
    def log(self, session_id: str, ts: int, kind: str, text: str) -> None:
        self._exec("INSERT INTO logs (session_id, ts, kind, text) VALUES (?,?,?,?)", (session_id, ts, kind, text))

    def logs(self, session_id: str, limit: int = 300) -> List[Dict[str, Any]]:
        return self._rows("SELECT ts, kind, text FROM logs WHERE session_id=? ORDER BY ts DESC, rowid DESC LIMIT ?",
                          (session_id, limit))

    # ------------------------------------------------------------- campaigns
    def save_campaign(self, cid: str, first_day: str, last_day: str, settings: Dict[str, Any],
                      summary: Dict[str, Any]) -> None:
        self._exec("""INSERT OR REPLACE INTO campaigns (id, created_at, first_day, last_day, settings, summary)
                      VALUES (?,?,?,?,?,?)""",
                   (cid, int(time.time()), first_day, last_day, json.dumps(settings), json.dumps(summary)))

    def campaigns(self, limit: int = 20) -> List[Dict[str, Any]]:
        out = []
        for r in self._rows("SELECT * FROM campaigns ORDER BY created_at DESC LIMIT ?", (limit,)):
            r["settings"] = json.loads(r["settings"] or "{}")
            r["summary"] = json.loads(r["summary"] or "{}")
            out.append(r)
        return out

    def campaign(self, cid: str) -> Optional[Dict[str, Any]]:
        rows = self._rows("SELECT * FROM campaigns WHERE id=?", (cid,))
        if not rows:
            return None
        r = rows[0]
        r["settings"] = json.loads(r["settings"] or "{}")
        r["summary"] = json.loads(r["summary"] or "{}")
        return r

    def campaigns_version(self) -> tuple:
        """Changes whenever a run is added or replaced; lets callers cache what they build from campaigns()."""
        with self._lock:
            return tuple(self._conn.execute("SELECT COUNT(*), MAX(created_at) FROM campaigns").fetchone())

    # ---------------------------------------------------------------- events
    def event_symbols(self, day: str, before_ts: int, window_days: int = 1) -> set:
        """Stocks with a board meeting within window_days trading-calendar days of `day`, announced before before_ts."""
        from datetime import datetime as _dt, timedelta as _td
        d = _dt.strptime(day, "%Y-%m-%d")
        lo = (d - _td(days=window_days + 2)).strftime("%Y-%m-%d")    # a weekend can sit between
        hi = (d + _td(days=window_days + 2)).strftime("%Y-%m-%d")
        rows = self._rows("SELECT symbol, day FROM events WHERE day BETWEEN ? AND ? AND (announced_ts IS NULL OR announced_ts < ?)",
                          (lo, hi, before_ts))
        days = sorted({r["day"] for r in rows} | {day})
        out = set()
        for r in rows:
            gap = abs((_dt.strptime(r["day"], "%Y-%m-%d") - d).days)
            weekdays = sum(1 for k in range(1, gap + 1)
                           if (min(d, _dt.strptime(r["day"], "%Y-%m-%d")) + _td(days=k)).weekday() < 5)
            if weekdays <= window_days:
                out.add(r["symbol"])
        return out

    # ----------------------------------------------------------- trials ledger
    def add_trial(self, strategy: str, params: Dict[str, Any], as_of: str, n: int, mean_r: float, t: float,
                  note: str = "") -> None:
        self._exec("INSERT INTO trials (ts, strategy, params, as_of, n, mean_r, t, note) VALUES (?,?,?,?,?,?,?,?)",
                   (int(time.time()), strategy, json.dumps(params, sort_keys=True), as_of, n, mean_r, t, note))

    def trial_count(self) -> int:
        """Distinct strategy configurations ever evaluated (the number of shots at finding an edge)."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(DISTINCT strategy || params) FROM trials").fetchone()
        return int(row[0] or 0)

    def trials(self, limit: int = 200) -> List[Dict[str, Any]]:
        return self._rows("SELECT * FROM trials ORDER BY ts DESC LIMIT ?", (limit,))


def _decode_session(r: Dict[str, Any]) -> Dict[str, Any]:
    for k in ("settings", "summary", "state"):
        r[k] = json.loads(r[k]) if r.get(k) else None
    return r
