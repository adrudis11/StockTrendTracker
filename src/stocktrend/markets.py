"""Exchanges, currencies and trading calendars for the markets we cover."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import cache

import exchange_calendars as xcals
import pandas as pd


@dataclass(frozen=True)
class Exchange:
    mic: str  # exchange_calendars code, e.g. XETR
    country: str
    currency: str  # as Yahoo quotes it; London is in pence (GBp)


US_EXCHANGE = Exchange("XNYS", "United States", "USD")

# Yahoo ticker suffix -> exchange.
SUFFIX_EXCHANGES: dict[str, Exchange] = {
    ".DE": Exchange("XETR", "Germany", "EUR"),
    ".PA": Exchange("XPAR", "France", "EUR"),
    ".MC": Exchange("XMAD", "Spain", "EUR"),
    ".MI": Exchange("XMIL", "Italy", "EUR"),
    ".AS": Exchange("XAMS", "Netherlands", "EUR"),
    ".BR": Exchange("XBRU", "Belgium", "EUR"),
    ".HE": Exchange("XHEL", "Finland", "EUR"),
    ".IR": Exchange("XDUB", "Ireland", "EUR"),
    ".LS": Exchange("XLIS", "Portugal", "EUR"),
    ".VI": Exchange("XWBO", "Austria", "EUR"),
    ".L": Exchange("XLON", "United Kingdom", "GBp"),
    ".SW": Exchange("XSWX", "Switzerland", "CHF"),
    ".CO": Exchange("XCSE", "Denmark", "DKK"),
    ".ST": Exchange("XSTO", "Sweden", "SEK"),
    ".OL": Exchange("XOSL", "Norway", "NOK"),
}


def split_suffix(ticker: str) -> tuple[str, str | None]:
    """``"BBVA.MC" -> ("BBVA", ".MC")``; ``"BRK-B" -> ("BRK-B", None)``."""
    for suffix in SUFFIX_EXCHANGES:
        if ticker.endswith(suffix):
            return ticker[: -len(suffix)], suffix
    return ticker, None


def exchange_for(ticker: str) -> Exchange:
    """Exchange of a Yahoo stock ticker; tickers without a known suffix are US."""
    _, suffix = split_suffix(ticker)
    return SUFFIX_EXCHANGES[suffix] if suffix else US_EXCHANGE


def to_yahoo(symbol: str, suffix: str | None = None) -> str:
    """Normalise an index-table symbol to Yahoo's format.

    Share classes use a dash on Yahoo (``BRK.B`` -> ``BRK-B``, ``BT.A`` -> ``BT-A.L``).
    Symbols that already carry a known exchange suffix are returned unchanged.
    """
    symbol = symbol.strip().upper()
    if split_suffix(symbol)[1]:
        return symbol
    symbol = symbol.replace(".", "-").replace(" ", "-")
    return f"{symbol}{suffix}" if suffix else symbol


@cache
def calendar(mic: str) -> xcals.ExchangeCalendar:
    return xcals.get_calendar(mic)


def is_trading_day(mic: str, day: date) -> bool:
    return bool(calendar(mic).is_session(pd.Timestamp(day)))


def previous_trading_day(mic: str, day: date) -> date:
    """Last session strictly before ``day``."""
    cal = calendar(mic)
    ts = pd.Timestamp(day)
    prev = cal.previous_session(ts) if cal.is_session(ts) else cal.date_to_session(ts, "previous")
    result: date = prev.date()
    return result
