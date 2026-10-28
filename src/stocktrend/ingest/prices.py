"""Daily OHLCV prices from Yahoo Finance (via yfinance).

Prices are cached as one long-format Parquet file in ``cache/`` and re-downloaded
each session, since cloud containers don't keep files between sessions.
"""

from __future__ import annotations

import logging
import time
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pandas as pd
import yfinance as yf

from stocktrend.config import PRICES_PARQUET
from stocktrend.markets import exchange_for, previous_trading_day

log = logging.getLogger(__name__)

# yfinance re-enables DeprecationWarnings for itself on import; under pandas 3 that
# floods the console with notices about yfinance internals, not about our data.
warnings.filterwarnings("ignore", category=DeprecationWarning, module="^yfinance")

PRICE_COLUMNS = ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]
_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
}

# ~13 months: enough for 52-week highs, 60-day momentum and volatility.
DEFAULT_HISTORY_DAYS = 400
# Stocks House members traded that the universe doesn't cover (`stt trackers update`).
TRACKER_PRICES = PRICES_PARQUET.with_name("trackers.parquet")
BATCH_SIZE = 100


@dataclass
class PriceReport:
    requested: int = 0
    rows: int = 0
    missing: list[str] = field(default_factory=list)  # no data at all
    stale: list[str] = field(default_factory=list)  # stocks missing their latest session

    @property
    def ok(self) -> bool:
        # A few tickers vanishing (delistings, Yahoo hiccups) shouldn't stop the run.
        return self.requested > 0 and len(self.missing) <= max(5, self.requested // 50)


def to_long(wide: pd.DataFrame) -> pd.DataFrame:
    """Reshape ``yf.download(group_by="ticker")`` output to one row per (date, ticker)."""
    if wide.empty:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    long = cast(pd.DataFrame, wide.stack(level=0, future_stack=True)).rename(columns=_RENAME)
    long.index = long.index.set_names(["date", "ticker"])
    long = long.reset_index().dropna(subset=["close"])
    long.columns.name = None
    long["date"] = pd.to_datetime(long["date"]).dt.tz_localize(None).dt.normalize()
    for col in PRICE_COLUMNS:
        if col not in long.columns:
            long[col] = pd.NA
    return long[PRICE_COLUMNS].sort_values(["ticker", "date"], ignore_index=True)


def _download(tickers: list[str], start: date, end: date) -> pd.DataFrame:
    wide = yf.download(
        tickers,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # yfinance's end is exclusive
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        actions=False,
        progress=False,
        threads=True,
    )
    if wide is None:
        return pd.DataFrame(columns=PRICE_COLUMNS)
    if not isinstance(wide.columns, pd.MultiIndex):  # single ticker
        wide.columns = pd.MultiIndex.from_product([tickers, wide.columns])
    return to_long(wide)


def download(
    tickers: Iterable[str],
    as_of: date,
    history_days: int = DEFAULT_HISTORY_DAYS,
    batch_size: int = BATCH_SIZE,
) -> pd.DataFrame:
    tickers = list(dict.fromkeys(tickers))
    start = as_of - timedelta(days=history_days)
    frames = []
    for i in range(0, len(tickers), batch_size):
        frames.append(_download(tickers[i : i + batch_size], start, as_of))
        time.sleep(1)  # be gentle with Yahoo
    prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_COLUMNS)

    # One retry for tickers that came back empty; Yahoo drops some under load.
    missing = sorted(set(tickers) - set(prices["ticker"]))
    if missing:
        log.info("retrying %d tickers with no data", len(missing))
        time.sleep(5)
        prices = pd.concat([prices, _download(missing, start, as_of)], ignore_index=True)
    # Only completed sessions: if a market is open, Yahoo returns a partial bar for today.
    prices = prices[prices["date"] < pd.Timestamp(as_of)]
    return prices.drop_duplicates(["date", "ticker"], keep="last").sort_values(
        ["ticker", "date"], ignore_index=True
    )


def check(
    prices: pd.DataFrame, tickers: Iterable[str], stocks: set[str], as_of: date
) -> PriceReport:
    """Flag tickers with no data, and stocks whose last bar is older than expected."""
    tickers = list(dict.fromkeys(tickers))
    report = PriceReport(requested=len(tickers), rows=len(prices))
    last = prices.groupby("ticker")["date"].max()
    for t in tickers:
        if t not in last.index:
            report.missing.append(t)
        elif t in stocks:
            # Run before the open: the latest complete session is the one before today.
            expected = previous_trading_day(exchange_for(t).mic, as_of)
            if last[t].date() < expected:
                report.stale.append(t)
    return report


def save(prices: pd.DataFrame, path: Path = PRICES_PARQUET) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    prices.to_parquet(path, index=False)


def load(path: Path = PRICES_PARQUET) -> pd.DataFrame:
    return pd.read_parquet(path)
