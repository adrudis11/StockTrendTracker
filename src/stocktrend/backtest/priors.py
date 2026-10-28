"""Theme priors: how did a theme's members do after big moves in the theme's driver?

For each theme with a ``driver`` series (Brent for the oil theme, the defence ETF
for rearmament, ...):

1. A *shock* is a day when the driver's 5-day move exceeds 2 standard deviations in
   the theme's direction (``driver_sign``). Shocks closer than ``horizon`` sessions
   to the previous one are skipped, so samples don't overlap.
2. After each shock, the members' sign-weighted excess return over the next
   ``horizon`` sessions is measured against their regional benchmark. The driver
   itself is left out of the basket.
3. The mean across shocks is the prior: positive means members kept moving the
   theme's way (continuation), negative means they gave it back (reversal).

This uses market shocks, not news events, as a proxy: we can't backtest Claude's
reading of past news (it already knows what happened), but we can backtest how
these baskets behave once markets start pricing a theme. A prior is marked usable
only with at least ``MIN_SHOCKS`` shocks.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import numpy as np
import pandas as pd

from stocktrend.universe.exposures import Exposures

Z_THRESHOLD = 2.0
MIN_SHOCKS = 8
VOL_WINDOW = 250


def five_day_z(driver: pd.Series) -> pd.Series:
    """5-day log move divided by its typical size over the past year."""
    logs = pd.Series(np.log(driver.to_numpy(dtype=float)), index=driver.index)
    vol5 = logs.diff().rolling(VOL_WINDOW, min_periods=120).std() * math.sqrt(5)
    return (logs - logs.shift(5)) / vol5


def shock_z(driver: pd.Series) -> float | None:
    """Today's 5-day z-score of a driver series, or None without enough history."""
    driver = driver[driver > 0].dropna()
    if len(driver) < 130:
        return None
    z = five_day_z(driver).iloc[-1]
    return float(z) if pd.notna(z) else None


def shock_days(driver: pd.Series, sign: int, separation: int) -> list[pd.Timestamp]:
    z = five_day_z(driver)
    days: list[pd.Timestamp] = []
    last_i = -(10**9)
    for i, (t, v) in enumerate(zip(z.index, (sign * z).to_numpy(), strict=True)):
        if np.isfinite(v) and v > Z_THRESHOLD and i - last_i >= separation:
            days.append(pd.Timestamp(t))
            last_i = i
    return days


def theme_prior(
    closes: pd.DataFrame,
    members: dict[str, float],
    driver: str,
    driver_sign: int,
    regions: dict[str, str],
    benchmarks: dict[str, str],
    horizon: int,
) -> dict[str, Any]:
    if driver not in closes.columns:
        return {"n": 0, "usable": False, "note": "driver has no history"}
    basket = {t: w for t, w in members.items() if t in closes.columns and t != driver}
    if not basket:
        return {"n": 0, "usable": False, "note": "no members with history"}
    fwd = closes.shift(-horizon) / closes - 1
    fwd_rows: dict[Any, dict[Any, Any]] = fwd.to_dict("index")
    results = []
    for t in shock_days(closes[driver].dropna(), driver_sign, horizon):
        row = fwd_rows.get(t)
        if row is None:
            continue
        num = den = 0.0
        for m, w in basket.items():
            bench = benchmarks.get(regions.get(m, "US"), benchmarks["US"])
            r, b = row.get(m, np.nan), row.get(bench, np.nan)
            if pd.notna(r) and pd.notna(b):
                num += w * (float(r) - float(b))
                den += abs(w)
        if den:
            results.append(num / den)
    n = len(results)
    if n == 0:
        return {"n": 0, "usable": False, "note": "no shocks with forward data"}
    a = np.array(results)
    sd = float(a.std(ddof=1)) if n > 1 else math.nan
    return {
        "n": n,
        "mean_excess": round(float(a.mean()), 5),
        "hit_rate": round(float((a > 0).mean()), 3),
        "t_stat": round(float(a.mean()) / (sd / math.sqrt(n)), 2) if n > 1 and sd > 0 else None,
        "usable": n >= MIN_SHOCKS,
    }


def compute(
    closes: pd.DataFrame,
    exposures: Exposures,
    regions: dict[str, str],
    benchmarks: dict[str, str],
    horizons: tuple[int, ...] = (5, 20),
) -> dict[str, Any]:
    themes: dict[str, Any] = {}
    for tid, th in exposures.themes.items():
        if not th.driver:
            continue
        themes[tid] = {
            "driver": th.driver,
            **{
                str(h): theme_prior(
                    closes, th.members, th.driver, th.driver_sign, regions, benchmarks, h
                )
                for h in horizons
            },
        }
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "period": f"{closes.index[0].date()} to {closes.index[-1].date()}",
        "method": f"driver 5-day move > {Z_THRESHOLD} sigma in the theme direction; members' "
        "sign-weighted excess return vs regional benchmark over the next horizon; "
        f"usable with >= {MIN_SHOCKS} non-overlapping shocks",
        "themes": themes,
    }
