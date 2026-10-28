"""Smoke tests against the real sources. Run with ``uv run pytest -m live``."""

from datetime import UTC, date, datetime, timedelta

import pytest

from stocktrend.http import session
from stocktrend.ingest import federal_register, gdelt, prices, rss

pytestmark = pytest.mark.live


def test_yahoo_prices() -> None:
    df = prices.download(["SPY", "SAN.MC", "BT-A.L"], date.today(), history_days=10)
    assert set(df["ticker"]) == {"SPY", "SAN.MC", "BT-A.L"}


def test_rss_feed() -> None:
    r = session().get("https://feeds.bbci.co.uk/news/world/rss.xml", timeout=30)
    end = datetime.now(UTC)
    assert list(rss.parse_feed(r.content, feed_id="bbc", start=end - timedelta(days=3), end=end))


def test_federal_register() -> None:
    cfg = {"agencies": ["foreign-assets-control-office"], "document_types": ["NOTICE"]}
    _, results = federal_register.fetch(session(), cfg, datetime.now(UTC) - timedelta(days=7))
    assert all(r.ok for r in results)


def test_gdelt_event_files() -> None:
    end = datetime.now(UTC) - timedelta(minutes=30)
    events, _ = gdelt.download_events(session(), end - timedelta(hours=1), end)
    assert len(events) > 100
