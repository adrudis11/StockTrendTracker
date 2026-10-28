from datetime import date

import pytest

from stocktrend.markets import exchange_for, is_trading_day, previous_trading_day, to_yahoo


@pytest.mark.parametrize(
    ("symbol", "suffix", "expected"),
    [
        ("BRK.B", None, "BRK-B"),  # US share class
        ("AAPL", None, "AAPL"),
        ("BT.A", ".L", "BT-A.L"),  # London share class
        ("NOVN", ".SW", "NOVN.SW"),
        ("ADS.DE", None, "ADS.DE"),  # already has a suffix
        ("NDA-FI.HE", None, "NDA-FI.HE"),
        ("bbva.mc", ".MC", "BBVA.MC"),
    ],
)
def test_to_yahoo(symbol: str, suffix: str | None, expected: str) -> None:
    assert to_yahoo(symbol, suffix) == expected


def test_exchange_for() -> None:
    assert exchange_for("SAN.MC").mic == "XMAD"
    assert exchange_for("BT-A.L").currency == "GBp"
    assert exchange_for("NESN.SW").country == "Switzerland"
    assert exchange_for("BRK-B").mic == "XNYS"


def test_trading_days() -> None:
    assert is_trading_day("XMAD", date(2026, 10, 28))
    assert not is_trading_day("XMAD", date(2026, 12, 25))
    assert not is_trading_day("XNYS", date(2026, 11, 26))  # Thanksgiving
    assert is_trading_day("XETR", date(2026, 11, 26))
    # Monday -> previous Friday; a holiday -> the session before it
    assert previous_trading_day("XNYS", date(2026, 11, 2)) == date(2026, 10, 30)
    assert previous_trading_day("XNYS", date(2026, 11, 27)) == date(2026, 11, 25)
    assert previous_trading_day("XLON", date(2026, 12, 26)) == date(2026, 12, 24)
