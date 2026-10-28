"""Next earnings dates, so picks can be flagged when results fall inside the horizon.

Only fetched for candidate stocks (one Yahoo request each). A failed lookup is
recorded as unknown, never as "no earnings".
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from datetime import date
from pathlib import Path

import yfinance as yf

from stocktrend.config import CACHE_DIR

log = logging.getLogger(__name__)


def cache_path(as_of: date) -> Path:
    return CACHE_DIR / "earnings" / f"{as_of.isoformat()}.json"


def next_earnings(tickers: Iterable[str], as_of: date) -> dict[str, str | None]:
    """ticker -> ISO date of the next earnings on/after ``as_of``, or None if unknown."""
    path = cache_path(as_of)
    cached: dict[str, str | None] = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    )
    yf_logger = logging.getLogger("yfinance")
    level = yf_logger.level
    yf_logger.setLevel(logging.CRITICAL)  # 404s for tickers without fundamentals
    try:
        for t in tickers:
            if t in cached:
                continue
            found: str | None = None
            try:
                cal = yf.Ticker(t).calendar or {}
                dates = sorted(d for d in cal.get("Earnings Date") or [] if d >= as_of)
                found = dates[0].isoformat() if dates else None
            except Exception as e:  # network or parsing trouble: unknown, not "none"
                log.info("earnings lookup failed for %s: %s", t, e)
            cached[t] = found
            time.sleep(0.2)
    finally:
        yf_logger.setLevel(level)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cached, indent=1), encoding="utf-8")
    return cached
