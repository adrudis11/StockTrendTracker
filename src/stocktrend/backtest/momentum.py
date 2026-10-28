"""Historical test of the momentum baseline.

Every ``horizon`` sessions, buy the 10 stocks with the highest excess return over
the previous ``horizon`` sessions (and flag the 5 weakest as avoids), then measure
their excess return over the next ``horizon`` sessions. This is the bar the
geopolitical picks have to clear.

Caveats, stated in the output: today's index members only (survivorship bias
flatters results), close-to-close returns, no trading costs.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from stocktrend.scoring.model import MAX_AVOID, MAX_PICKS


def run(
    closes: pd.DataFrame, regions: dict[str, str], benchmarks: dict[str, str], horizon: int
) -> dict[str, Any]:
    stocks = [t for t in regions if t in closes.columns]
    past = closes / closes.shift(horizon) - 1
    fwd = closes.shift(-horizon) / closes - 1
    bench_of = {s: benchmarks[regions[s]] for s in stocks}

    def excess(r: pd.DataFrame, t: pd.Timestamp) -> pd.Series:
        row = r.loc[t]
        return pd.Series({s: row[s] - row[bench_of[s]] for s in stocks}).dropna()

    dates = closes.index[horizon : len(closes.index) - horizon : horizon]
    long_ex, avoid_ex, long_hits, avoid_hits, periods_beat = [], [], [], [], []
    for t in dates:
        p, f = excess(past, t), excess(fwd, t)
        common = p.index.intersection(f.index)
        if len(common) < 50:
            continue
        p = p[common].sort_values(ascending=False)
        top, bottom = p.index[:MAX_PICKS], p.index[-MAX_AVOID:]
        long_ex.append(float(f[top].mean()))
        avoid_ex.append(float(-f[bottom].mean()))  # positive = the avoids lagged
        long_hits += list((f[top] > 0).astype(float))
        avoid_hits += list((f[bottom] < 0).astype(float))
        periods_beat.append(float(f[top].mean() > 0))

    def summary(xs: list[float]) -> dict[str, Any]:
        a = np.array(xs)
        n = len(a)
        sd = float(a.std(ddof=1)) if n > 1 else math.nan
        return {
            "periods": n,
            "mean_excess": round(float(a.mean()), 5) if n else None,
            "t_stat": round(float(a.mean()) / (sd / math.sqrt(n)), 2) if n > 1 and sd > 0 else None,
        }

    def rate(xs: list[float]) -> float | None:
        return round(float(np.mean(xs)), 3) if xs else None

    return {
        "horizon": horizon,
        "start": str(closes.index[0].date()),
        "end": str(closes.index[-1].date()),
        "long": {
            **summary(long_ex),
            "hit_rate": rate(long_hits),
            "share_of_periods_beating_benchmark": rate(periods_beat),
        },
        "avoid": {**summary(avoid_ex), "hit_rate": rate(avoid_hits)},
        "caveats": "current index members only (survivorship bias); close-to-close; no costs",
    }
