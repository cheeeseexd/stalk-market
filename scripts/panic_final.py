"""H7d: pre-registered final daily-data checks. Research only; see RUNBOOK.md.

  python scripts/panic_final.py --fetch

Writes separate research artifacts; never imports the agent or reads its holdout.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

from scripts.panic_bhav import CACHE, COST, MIN_VALUE, UA, bhav, results_symbols  # noqa: E402
from stalk.config import BASE_DIR, DEFAULT_UNIVERSE, IST  # noqa: E402
from stalk.market.costs import round_trip_cost  # noqa: E402

OUT = Path(BASE_DIR) / "data" / "panic_final"


def nifty() -> pd.DataFrame:
    b = pd.read_pickle(Path(BASE_DIR) / "data" / "wide_1d_10y.pkl")["NIFTY 50"]
    dates = pd.to_datetime(b["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.strftime("%Y-%m-%d")
    n = pd.DataFrame({"open": b["o"].values, "close": b["c"].values}, index=dates.values)
    n = n[~n.index.duplicated(keep="last")].sort_index()
    n["gap"] = n["open"] / n["close"].shift() - 1
    n["prev_day"] = pd.Series(n.index, index=n.index).shift()
    return n.loc["2016-11-01":"2026-10-31"].dropna()


def year(day: str) -> int:
    return int(day[:4]) + (int(day[5:7]) >= 11)


def tstat(x) -> float:
    x = np.asarray(x, dtype=float)
    return float(x.mean() / (x.std(ddof=1) / np.sqrt(len(x)))) if len(x) > 1 and x.std(ddof=1) > 0 else float("nan")


def fetch_day(item) -> tuple:
    day, prev = item
    errors = []
    with httpx.Client(headers=UA, timeout=20, follow_redirects=True) as client:
        for d in (day, prev):
            if not (Path(CACHE) / f"{d}.pkl").exists():
                try:
                    bhav(d, client)
                except Exception as e:
                    errors.append(f"{d}: {type(e).__name__}")
        if not (Path(CACHE) / f"bm_{day}.pkl").exists():
            try:
                client.get("https://www.nseindia.com/")
                results_symbols(day, client)
            except Exception as e:
                errors.append(f"calendar {day}: {type(e).__name__}")
    return day, errors


def pool(day: str, prev_day: str):
    paths = [Path(CACHE) / f"{d}.pkl" for d in (day, prev_day)]
    calendar = Path(CACHE) / f"bm_{day}.pkl"
    if not all(p.exists() for p in paths) or not calendar.exists():
        return None, "missing cache", 0
    today, prev = [pd.read_pickle(p) for p in paths]
    near = pd.read_pickle(calendar)
    if today.empty or prev.empty:
        return None, "empty bhavcopy", 0
    if not isinstance(near, (set, list, tuple)):
        return None, "unknown calendar", 0
    u = today.join(prev[["value"]].rename(columns={"value": "prev_value"}), how="inner")
    u = u[(u["prev_value"] >= MIN_VALUE) & (u["prev_close"] >= 50)
          & ~u.index.isin(DEFAULT_UNIVERSE) & ~u.index.isin(near)].copy()
    valid = np.isfinite(u[["open", "high", "low", "close", "prev_close"]]).all(axis=1)
    valid &= (u[["open", "high", "low", "close", "prev_close"]] > 0).all(axis=1)
    invalid = int((~valid).sum())
    u = u[valid].rename_axis("symbol").reset_index()
    u["gap"] = u["open"] / u["prev_close"] - 1
    stopped = u["low"] <= u["open"] * 0.97
    u["headline"] = u["close"] / u["open"] - 1 - COST
    u["net"] = np.where(stopped, -0.032, u["close"] / u["open"] - 1 - 0.0008) - COST
    u["day"], u["year"] = day, year(day)
    return u.sort_values(["gap", "symbol"], kind="stable"), "ok", invalid


def account(p: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for r in p.itertuples():
        entry = r.open * 1.001
        qty = int(5000 // entry)
        if qty <= 0:
            continue
        stopped = r.low <= entry * 0.97
        exit_px = (entry * 0.97 * 0.998 if stopped else r.close * 0.9992) * 0.999
        net = (exit_px - entry) * qty - round_trip_cost(qty, entry, exit_px)
        rows.append({"day": r.day, "year": r.year, "symbol": r.symbol, "qty": qty, "net": net})
    return pd.DataFrame(rows, columns=["day", "year", "symbol", "qty", "net"])


def bootstrap(x, counts, rng) -> dict:
    x = np.asarray(x, dtype=float)
    counts = np.broadcast_to(counts, (5000,))
    samples = rng.choice(x, size=(5000, int(counts.max())))
    totals = (samples * (np.arange(samples.shape[1])[None, :] < counts[:, None])).sum(axis=1)
    return dict(zip(("p5", "p50", "p95"), map(float, np.percentile(totals, [5, 50, 95]))),
                losing_probability=float((totals < 0).mean()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    n = nifty()
    wanted = n[n["gap"] < -0.003]
    print(f"Grid needs {len(wanted)} mornings; base has {(n['gap'] < -0.005).sum()}", flush=True)
    if args.fetch:
        # Three independent clients; bhav() retains the existing retry/cache policy.
        with ThreadPoolExecutor(max_workers=3) as workers:
            for i, (day, errors) in enumerate(workers.map(fetch_day, wanted["prev_day"].items()), 1):
                if errors or i % 25 == 0:
                    print(f"Fetch {i}/{len(wanted)} {day}: {errors or 'ok'}", flush=True)
    pools, coverage = {}, []
    for day, r in wanted.iterrows():
        p, status, invalid = pool(day, r["prev_day"])
        coverage.append({"day": day, "nifty_gap": r["gap"], "status": status, "invalid_rows": invalid})
        if p is not None:
            pools[day] = p
    pd.DataFrame(coverage).to_csv(OUT / "coverage.csv", index=False)
    base_days = list(n.index[n["gap"] < -0.005])
    active = [pools[d].loc[lambda p: p["gap"] < -0.02] for d in base_days if d in pools]
    selected = pd.concat([p.head(2) for p in active], ignore_index=True)
    selected.to_csv(OUT / "selected.csv", index=False)
    rng = np.random.default_rng(7004)
    random_totals = np.zeros(2000)
    trades = 0
    for p in active:
        x = p["net"].to_numpy()
        k = min(2, len(x))
        if k:
            draws = np.array([rng.choice(len(x), size=k, replace=False) for _ in range(2000)])
            random_totals += x[draws].sum(axis=1)
            trades += k
    random_means = random_totals / trades
    np.save(OUT / "random_means.npy", random_means)
    observed = float(selected["net"].mean())
    selection = {"trades": len(selected), "active_days": selected["day"].nunique(),
                 "net_bps": observed * 1e4, "headline_bps": selected["headline"].mean() * 1e4,
                 "t_day": tstat(selected.groupby("day")["net"].mean()),
                 "random_bps_p5_p50_p95": (np.percentile(random_means, [5, 50, 95]) * 1e4).tolist(),
                 "percentile": float((random_means < observed).mean() * 100),
                 "p_upper": float((1 + (random_means >= observed).sum()) / 2001),
                 "all_pool_bps": pd.concat(active)["net"].mean() * 1e4}
    grid = []
    for ng in (-0.003, -0.005, -0.007, -0.01):
        for sg in (-0.015, -0.02, -0.03):
            for k in (1, 2, 3):
                eligible_days = list(n.index[n["gap"] < ng])
                x = pd.concat([pools[d].loc[lambda p: p["gap"] < sg].head(k)
                               for d in eligible_days if d in pools], ignore_index=True)
                grid.append({"nifty_gap": ng, "stock_gap": sg, "picks": k, "trades": len(x),
                             "days": x["day"].nunique(), "expected_days": len(eligible_days),
                             "covered_days": sum(d in pools for d in eligible_days),
                             "net_bps": x["net"].mean() * 1e4, "t_day": tstat(x.groupby("day")["net"].mean()),
                             "years_positive": int((x.groupby("year")["net"].mean() > 0).sum())})
    grid = pd.DataFrame(grid)
    grid.to_csv(OUT / "grid.csv", index=False)
    a = account(selected)
    a.to_csv(OUT / "account.csv", index=False)
    covered_base = [d for d in base_days if d in pools]
    daily = a.groupby("day")["net"].sum().reindex(covered_base, fill_value=0)
    daily.to_csv(OUT / "account_days.csv")
    rng = np.random.default_rng(7005)
    frequency = len(daily) / 10
    counts = int(frequency) + (rng.random(5000) < frequency % 1)
    boot = bootstrap(daily.values, counts, rng)
    years = []
    for y in range(2017, 2027):
        x, ac = selected[selected["year"] == y], a[a["year"] == y]
        yd = daily[[year(d) == y for d in daily.index]]
        years.append({"year_ending_oct": y, "panic_days": len(yd), "trades": len(x),
                      "net_bps": x["net"].mean() * 1e4, "account_trades": len(ac),
                      "account_pnl": ac["net"].sum(), **bootstrap(yd.values, len(yd), rng)})
    years = pd.DataFrame(years)
    years.to_csv(OUT / "years.csv", index=False)
    day_returns = selected.groupby("day")["net"].mean()
    early = day_returns[[year(d) <= 2023 for d in day_returns.index]]
    late = day_returns[[year(d) >= 2024 for d in day_returns.index]]
    diff = late.mean() - early.mean()
    se = np.sqrt(early.var(ddof=1) / len(early) + late.var(ddof=1) / len(late))
    decay = {"first7_day_bps": early.mean() * 1e4, "last3_day_bps": late.mean() * 1e4,
             "difference_bps": diff * 1e4, "welch_t": diff / se,
             "first7_annual_pnl": years.iloc[:7]["account_pnl"].mean(),
             "last3_annual_pnl": years.iloc[7:]["account_pnl"].mean()}
    gates = {"selection": bool(observed > np.percentile(random_means, 95)),
             "grid": bool(((grid["net_bps"] > 0) & (grid["t_day"] >= 2)).sum() >= 24),
             "bootstrap": bool(boot["p5"] > -1500)}
    result = {"selection": selection, "grid_cells_pass": int(((grid["net_bps"] > 0) & (grid["t_day"] >= 2)).sum()),
              "coverage": pd.Series([c["status"] for c in coverage]).value_counts().to_dict(),
              "invalid_rows": sum(c["invalid_rows"] for c in coverage),
              "bootstrap": boot, "annual_frequency": frequency, "decay": decay, "gates": gates,
              "complete": len(pools) == len(wanted), "account_total": a["net"].sum()}
    (OUT / "summary.json").write_text(json.dumps(result, indent=2, default=float), encoding="utf-8")
    print(json.dumps(result, indent=2, default=float))
    print(grid.to_string(index=False))
    print(years.to_string(index=False))


if __name__ == "__main__":
    main()
