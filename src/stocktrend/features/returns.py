"""Trailing returns per ticker, and the market snapshot shown in the digest."""

from __future__ import annotations

from typing import Any

import pandas as pd

WINDOWS = (1, 5, 20, 60)

# Shown to Claude next to the news so it can judge what markets already priced in.
SNAPSHOT_TICKERS = [
    "SPY", "QQQ", "IWM", "EXSA.DE", "^STOXX50E", "^GDAXI", "^FCHI", "^IBEX", "^FTSE",
    "FTSEMIB.MI", "^VIX", "^TNX", "DX-Y.NYB", "EURUSD=X", "BZ=F", "TTF=F", "NG=F", "GC=F",
    "HG=F", "ZW=F", "ITA", "WDEF.MI", "SMH", "FXI", "EWT", "EIS", "EPOL", "BOAT", "JETS",
]  # fmt: skip


def trailing_returns(prices: pd.DataFrame, windows: tuple[int, ...] = WINDOWS) -> pd.DataFrame:
    """One row per ticker: last adjusted close, its date, and r{n}d simple returns.

    Counts each ticker's own sessions, so exchange holidays don't distort windows.
    """
    longest = max(windows)
    rows = []
    for ticker, g in prices.sort_values("date").groupby("ticker", sort=False):
        closes = g["adj_close"].fillna(g["close"]).dropna().to_numpy()
        if len(closes) < 2:
            continue
        row: dict[str, Any] = {
            "ticker": ticker,
            "last": float(closes[-1]),
            "last_date": g["date"].iloc[-1],
        }
        for n in windows:
            row[f"r{n}d"] = float(closes[-1] / closes[-1 - n] - 1) if len(closes) > n else None
        row["sessions"] = min(len(closes), longest + 1)
        rows.append(row)
    return pd.DataFrame(rows).set_index("ticker")


def snapshot(
    prices: pd.DataFrame,
    names: dict[str, str],
    stocks: pd.DataFrame,
    benchmarks: dict[str, str],
    movers: int = 8,
) -> dict[str, Any]:
    """Key markets plus the stocks that most out- or under-performed over 5 days."""
    rets = trailing_returns(prices)
    instruments = []
    for t in SNAPSHOT_TICKERS:
        if t not in rets.index or pd.isna(rets.at[t, "r20d"]):
            continue
        r = rets.loc[t]
        instruments.append(
            {
                "ticker": t,
                "name": names.get(t, t),
                "last": r["last"],
                "r1d": 100 * r["r1d"],
                "r5d": 100 * r["r5d"],
                "r20d": 100 * r["r20d"],
            }
        )
    r5 = rets["r5d"].astype(float)
    excess = []
    for ticker, region in zip(stocks["ticker"], stocks["region"], strict=True):
        bench = benchmarks[region]
        if ticker in r5.index and bench in r5.index:
            e = float(r5[ticker] - r5[bench])
            if pd.notna(e):
                excess.append((str(ticker), round(100 * e, 1)))
    excess.sort(key=lambda x: x[1], reverse=True)
    last_session = prices["date"].max()
    return {
        "last_session": last_session.date().isoformat() if pd.notna(last_session) else None,
        "instruments": instruments,
        "leaders_5d": excess[:movers],
        "laggards_5d": excess[-movers:][::-1],
    }
