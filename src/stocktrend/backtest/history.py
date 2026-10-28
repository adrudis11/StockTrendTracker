"""Multi-year daily history for backtests (``cache/prices/history.parquet``)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from stocktrend.config import CACHE_DIR
from stocktrend.ingest import prices as price_store

HISTORY_PARQUET = CACHE_DIR / "prices" / "history.parquet"


def load_or_download(
    tickers: list[str], as_of: date, years: int, path: Path = HISTORY_PARQUET
) -> pd.DataFrame:
    if path.exists():
        cached = pd.read_parquet(path)
        span_days = (cached["date"].max() - cached["date"].min()).days
        if set(tickers) <= set(cached["ticker"]) and span_days >= 365 * years - 30:
            return cached
    df = price_store.download(tickers, as_of, history_days=365 * years + 30)
    price_store.save(df, path)
    return df


def wide_closes(prices: pd.DataFrame) -> pd.DataFrame:
    """Adjusted closes, one column per ticker, on the union of all trading dates.

    Gaps from exchange holidays are forward-filled for up to 5 days so US and
    European series line up; longer gaps stay missing.
    """
    px = prices.assign(px=prices["adj_close"].fillna(prices["close"]))
    wide = px.pivot_table(index="date", columns="ticker", values="px", aggfunc="last")
    return wide.sort_index().ffill(limit=5)
