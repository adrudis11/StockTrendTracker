from datetime import date

import pandas as pd

from stocktrend.ingest.prices import PRICE_COLUMNS, check, to_long


def _wide() -> pd.DataFrame:
    dates = pd.to_datetime(["2026-10-26", "2026-10-27"])
    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    cols = pd.MultiIndex.from_product([["SAN.MC", "AAPL"], fields], names=["Ticker", "Price"])
    data = [
        [1, 2, 0.5, 1.5, 1.4, 100] * 2,
        [2, 3, 1.5, 2.5, 2.4, 200, None, None, None, None, None, None],
    ]
    return pd.DataFrame(data, index=pd.Index(dates, name="Date"), columns=cols)


def test_to_long_reshapes_and_drops_empty_rows() -> None:
    long = to_long(_wide())
    assert list(long.columns) == PRICE_COLUMNS
    assert len(long) == 3  # AAPL has no bar on 2026-10-27
    san = long[long["ticker"] == "SAN.MC"]
    assert list(san["close"]) == [1.5, 2.5]
    assert list(san["adj_close"]) == [1.4, 2.4]


def test_check_flags_missing_and_stale() -> None:
    long = to_long(_wide())
    report = check(long, ["SAN.MC", "AAPL", "GONE"], {"SAN.MC", "AAPL"}, date(2026, 10, 28))
    assert report.missing == ["GONE"]
    assert report.stale == ["AAPL"]  # last bar 26 Oct, expected 27 Oct
    assert report.ok  # one missing ticker out of three is tolerated (min 5)
