"""Market regime: is the market calm, nervous or in risk-off mode?"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _series(prices: pd.DataFrame, ticker: str) -> pd.Series:
    g = prices[prices["ticker"] == ticker].sort_values("date")
    return pd.Series(g["close"].to_numpy(dtype=float), index=g["date"])


def _ret(s: pd.Series, n: int) -> float | None:
    return float(s.iloc[-1] / s.iloc[-1 - n] - 1) if len(s) > n else None


def compute(prices: pd.DataFrame) -> dict[str, Any]:
    vix = _series(prices, "^VIX")
    spy = _series(prices, "SPY")
    stoxx = _series(prices, "EXSA.DE")
    tnx = _series(prices, "^TNX")
    out: dict[str, Any] = {}
    if len(vix):
        out["vix"] = round(float(vix.iloc[-1]), 2)
        out["vix_percentile_1y"] = round(float((vix.iloc[-252:] < vix.iloc[-1]).mean()), 2)
    for name, s in (("spy", spy), ("stoxx600", stoxx)):
        if len(s) > 50:
            out[f"{name}_r20d"] = _ret(s, 20)
            out[f"{name}_above_50dma"] = bool(s.iloc[-1] > s.iloc[-50:].mean())
    for name, t in (("brent", "BZ=F"), ("ttf", "TTF=F"), ("gold", "GC=F"), ("eurusd", "EURUSD=X")):
        s = _series(prices, t)
        if len(s) > 20:
            out[f"{name}_r20d"] = _ret(s, 20)
    if len(tnx) > 20:
        out["us10y"] = round(float(tnx.iloc[-1]), 3)
        out["us10y_change_20d_bp"] = round(float(tnx.iloc[-1] - tnx.iloc[-21]) * 100, 1)

    vix_level = out.get("vix", np.nan)
    spy_r20 = out.get("spy_r20d") or 0.0
    if vix_level >= 25 or (out.get("vix_percentile_1y", 0) >= 0.8 and spy_r20 < -0.03):
        label = "risk_off"
    elif vix_level < 16 and out.get("spy_above_50dma", False):
        label = "risk_on"
    else:
        label = "neutral"
    out["label"] = label
    return out
