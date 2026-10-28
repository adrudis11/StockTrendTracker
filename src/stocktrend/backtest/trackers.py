"""Historical test of the congressional-trades tracker.

Every ``horizon`` sessions: take the stocks with the strongest net House buying
disclosed in the previous 30 days (only filings public before that morning), hold
them for ``horizon`` sessions, and measure their excess return over SPY. Also tests
the opposite end (net selling) and, for comparison, momentum over the same dates.

The result decides how much weight the tracker gets in the live score
(``weight_from_evidence``): none unless it beat the benchmark with some statistical
support. Caveats: current index members only, close-to-close returns, no costs.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from stocktrend.backtest import momentum
from stocktrend.trackers import congress

MAX_WEIGHT = 0.2  # the most a tracker can scale conviction, at full evidence


def _summary(per_period: list[float], hits: list[float]) -> dict[str, Any]:
    a = np.array(per_period)
    n = len(a)
    sd = float(a.std(ddof=1)) if n > 1 else math.nan
    return {
        "periods": n,
        "picks": len(hits),
        "mean_excess": round(float(a.mean()), 5) if n else None,
        "t_stat": round(float(a.mean()) / (sd / math.sqrt(n)), 2) if n > 1 and sd > 0 else None,
        "hit_rate": round(float(np.mean(hits)), 3) if hits else None,
    }


def run(
    closes: pd.DataFrame,
    trades: pd.DataFrame,
    known: set[str],
    horizon: int,
    start: date,
    benchmark: str = "SPY",
    top_n: int = 10,
) -> dict[str, Any]:
    fwd = closes.shift(-horizon) / closes - 1
    dates = [t for t in closes.index[: len(closes.index) - horizon : horizon] if t.date() >= start]
    buys_ex, buys_hits, sells_ex, sells_hits = [], [], [], []
    for t in dates:
        sig = congress.signal(trades, t.date(), known)
        if sig.empty:
            continue
        row = fwd.loc[t]
        bench = row.get(benchmark)
        if bench is None or pd.isna(bench):
            continue
        tradable = [s for s in sig.index if s in row.index and pd.notna(row[s])]
        sig = sig.loc[tradable]
        top = sig[sig["score"] > 0].head(top_n).index
        bottom = sig[sig["score"] < 0].sort_values("score").head(top_n).index
        if len(top):
            ex = row[top] - bench
            buys_ex.append(float(ex.mean()))
            buys_hits += list((ex > 0).astype(float))
        if len(bottom):
            ex = bench - row[bottom]  # positive = the sold stocks lagged
            sells_ex.append(float(ex.mean()))
            sells_hits += list((ex > 0).astype(float))
    return {
        "horizon": horizon,
        "start": start.isoformat(),
        "end": str(closes.index[-1].date()),
        "buys": _summary(buys_ex, buys_hits),
        "sells": _summary(sells_ex, sells_hits),
    }


def compare_momentum(
    closes: pd.DataFrame,
    regions: dict[str, str],
    benchmarks: dict[str, str],
    horizon: int,
    start: date,
) -> dict[str, Any]:
    """Momentum over the same window, US stocks only, as a fair yardstick."""
    window = closes[closes.index >= pd.Timestamp(start) - pd.Timedelta(days=60)]
    us = {t: r for t, r in regions.items() if r == "US"}
    return momentum.run(window, us, benchmarks, horizon)


def weight_from_evidence(result: dict[str, Any]) -> float:
    """0 unless buys beat the benchmark at 20 days; then up to MAX_WEIGHT by t-stat."""
    buys = result.get("buys", {})
    t, mean = buys.get("t_stat"), buys.get("mean_excess")
    if t is None or mean is None or mean <= 0 or buys.get("periods", 0) < 10:
        return 0.0
    return round(MAX_WEIGHT * min(1.0, max(0.0, float(t) - 1.0)), 3)  # t<=1: 0, t>=2: full
